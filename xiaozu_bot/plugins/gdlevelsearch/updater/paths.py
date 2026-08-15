# updater/paths.py

import os
from pathlib import Path

from nonebot import logger

# 当前 updater 文件所在目录
PLUGIN_DIR = Path(__file__).resolve().parent.parent

# data 统一目录 —— bot 读的是这里，只放"已经完整可用"的数据
DATA_DIR = PLUGIN_DIR / "data"

# 本次抓取的中间产物先落在这里，全部跑完才搬进 DATA_DIR。
# 这样中途挂了不会把线上数据冲掉 —— 以前 nlw/ids/lw/hds 是先写一份没有
# metadata 的进 DATA_DIR，等最后 getmetadata 再回填，中间任何一步失败
# （runner 默认 stop_on_error）都会让 bot 读到缺 metadata 的半成品。
STAGING_DIR = DATA_DIR / ".staging"

# 这些是 bot 真正会读的文件，只有它们需要"做完才发布"
PUBLISHED_FILES = (
    "gddl_levels.json",
    "nlw_levels.json",
    "ids_levels.json",
    "lw_levels.json",
    "hds_levels.json",
    "plat_combined.json",
    "nong_index.json",
)

# 这些只是中间数据（platbatch 的输入），bot 不读，留在 staging 就行
INTERMEDIATE_FILES = (
    "platdata.json",
    "platdiff.json",
    "platrank_weights.json",
)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)


def staged(name: str) -> Path:
    """本次运行要写到哪"""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    return STAGING_DIR / name


def staged_or_published(name: str) -> Path:
    """读的时候：本次已经抓到新的就用新的，否则退回上一次发布的。

    这样单独重跑某一个 job 时，下游还能读到上一轮的数据。
    """
    candidate = STAGING_DIR / name
    return candidate if candidate.exists() else DATA_DIR / name


#: 新文件的条目数至少要有旧文件的这个比例，否则拒绝发布。
#: 榜单每天的正常变动是个位数百分比（实测一次真实更新：hds -3.9%、ids +1.7%、
#: nlw +1.7%、plat +3.4%），留到 50% 已经非常宽松，只拦「基本被清空」这种。
MIN_KEEP_RATIO = 0.5


def _entry_count(path: Path) -> "int | None":
    """数一个数据文件里有多少条记录，读不了就返回 None（None = 不做判断）。"""
    import json

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        levels = data.get("levels")
        return len(levels) if isinstance(levels, list) else None
    return len(data) if isinstance(data, list) else None


def publish() -> list[str]:
    """把 staging 里的成品原子地搬进 DATA_DIR。

    用 os.replace，同一个文件系统上是原子的，不会出现读到写一半的文件。
    返回实际发布了哪些文件。

    **注意原子性的边界**：每个文件各自是原子的，但这里是 7 次独立的 replace。
    中途被 kill / 磁盘满，DATA_DIR 里会留下「一部分是新的、一部分是上一轮的」
    的混合状态。重跑一次就能收敛，但别把它当成一次全有或全无的事务。

    发布前会做一道**下限检查**：新文件的条目数不到旧文件的 MIN_KEEP_RATIO，
    就拒绝发布这一个文件（其余照常）。理由是上游是社区维护的在线表格，
    改个格式、插一行空行就可能让解析结果变成空列表或被截断，而那种情况
    **不会抛异常** —— job 报成功，然后一份 30 字节的空文件就把几百 KB 的
    线上数据盖掉了，而 data/ 不在 git 里，盖掉就没了。
    """
    moved: list[str] = []
    for name in PUBLISHED_FILES:
        src = STAGING_DIR / name
        if not src.exists():
            continue
        dst = DATA_DIR / name

        new_n = _entry_count(src)
        old_n = _entry_count(dst) if dst.exists() else None
        if new_n is not None and old_n and new_n < old_n * MIN_KEEP_RATIO:
            logger.error(
                f"[UPDATER] 拒绝发布 {name}：新数据只有 {new_n} 条，"
                f"旧数据有 {old_n} 条（不到 {MIN_KEEP_RATIO:.0%}）。"
                f"多半是上游表格改了格式导致解析失败，"
                f"已保留旧数据，新文件留在 staging 里可以自己看一眼。"
            )
            continue

        os.replace(src, dst)
        moved.append(name)
    if moved:
        logger.info(f"[UPDATER] 已发布 {len(moved)} 个文件: {', '.join(moved)}")
    else:
        logger.warning("[UPDATER] staging 里没有可发布的文件")
    return moved


def clear_staging() -> None:
    """清掉 staging，跑之前调一次，免得混进上次失败留下的残渣"""
    if not STAGING_DIR.exists():
        return
    for path in STAGING_DIR.iterdir():
        if path.is_file():
            path.unlink()
