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


def publish() -> list[str]:
    """把 staging 里的成品原子地搬进 DATA_DIR。

    用 os.replace，同一个文件系统上是原子的，不会出现读到写一半的文件。
    返回实际发布了哪些文件。
    """
    moved: list[str] = []
    for name in PUBLISHED_FILES:
        src = STAGING_DIR / name
        if not src.exists():
            continue
        dst = DATA_DIR / name
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
