import json
from collections.abc import Iterable
from pathlib import Path

from nonebot import logger

from xiaozu_bot.utils.json_storage import write_json_atomic

try:
    from ..paths import DATA_DIR, staged, staged_or_published
except ImportError:
    from updater.paths import DATA_DIR, staged, staged_or_published

from .metadata import enrich_levels_with_ids

DEFAULT_SOURCES = (
    "nlw_levels.json",
    "ids_levels.json",
    "lw_levels.json",
    "hds_levels.json",
    "idl.json",
)


def load_json_file(filepath):
    """读取 JSON 文件"""
    path = Path(filepath)
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"[GETMETADATA] 缓存文件不存在: {filepath}")
        return None

def save_json_file(filepath, data):
    """写入 JSON 文件；写失败必须让调用方知道，不能悄悄留下没补完的数据。"""
    write_json_atomic(Path(filepath), data, indent=4)

def main(sources: Iterable[str] | None = None) -> dict[str, object]:
    """给本轮成功抓到的榜单补 metadata。

    ``sources`` 由 runner 传入，只包含本轮成功获取的上游文件；失败的上游
    不读、不补、不写回 staging，于是发布时线上保留它的旧快照。单个源在
    补全过程中坏掉也只影响这一个源。

    返回 processed / failed 文件名，runner 只发布 processed 里的文件。
    metadata.json 是跨次运行的缓存，一直放在 DATA_DIR，不参与发布。
    """
    logger.info(f"[GETMETADATA] 开始补 metadata，缓存目录: {DATA_DIR}")
    selected = list(sources) if sources is not None else list(DEFAULT_SOURCES)

    processed: list[str] = []
    failed: dict[str, str] = {}
    for source in selected:
        try:
            data = load_json_file(staged_or_published(source))
            if not data:
                failed[source] = "no usable data"
                continue
            logger.info(f"[GETMETADATA] 开始补全 {source} 的 metadata")
            enrich_levels_with_ids(data["levels"], cache_dir=DATA_DIR)
            save_json_file(staged(source), data)
        except Exception as exc:
            logger.exception(f"[GETMETADATA] {source} 处理失败，本轮不发布这个文件")
            failed[source] = f"{type(exc).__name__}: {exc}"
            continue
        processed.append(source)
        logger.info(f"[GETMETADATA] {source} 已写回 staging，待发布")

    if not processed and not failed:
        logger.warning("[GETMETADATA] 一个数据源都没读到，跳过")
    return {"processed": processed, "failed": failed}

if __name__=="__main__":
    main()
