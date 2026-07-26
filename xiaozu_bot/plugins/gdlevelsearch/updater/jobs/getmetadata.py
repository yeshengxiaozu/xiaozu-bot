import json
from pathlib import Path

from nonebot import logger

try:
    from ..paths import DATA_DIR, staged, staged_or_published
except ImportError:
    from updater.paths import DATA_DIR, staged, staged_or_published

from .metadata import enrich_levels_with_ids

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
    """写入 JSON 文件"""
    path = Path(filepath)
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except IOError as e:
        logger.error(f"[GETMETADATA] 写入缓存失败: {filepath}: {e}")

def test():
    data = load_json_file(staged_or_published("ids_levels.json"))
    if data:
        flat_list = data["levels"]
        enrich_levels_with_ids(flat_list, cache_dir=DATA_DIR)

def main():
    """给 nlw/ids/lw/hds 补 metadata。

    读的是 staging 里刚抓下来的那份（没抓到就退回上一次发布的），
    补完之后写回 staging —— 只有整条流水线都跑完，runner 才会把 staging
    搬进 DATA_DIR。所以这一步挂了不会让 bot 读到缺 metadata 的数据。

    metadata.json 是跨次运行的缓存，一直放在 DATA_DIR，不参与发布。
    """
    logger.info(f"[GETMETADATA] 开始补 metadata，缓存目录: {DATA_DIR}")
    data_sources = ["nlw_levels.json", "ids_levels.json", "lw_levels.json", "hds_levels.json"]

    # 加载所有数据
    all_data = {}
    for source in data_sources:
        data = load_json_file(staged_or_published(source))
        if data:
            logger.info(f"[GETMETADATA] 已加载数据源: {source}")
            all_data[source] = data

    if not all_data:
        logger.warning("[GETMETADATA] 一个数据源都没读到，跳过")
        return

    # 处理所有数据源的 levels
    for source, data in all_data.items():
        logger.info(f"[GETMETADATA] 开始补全 {source} 的 metadata")
        enrich_levels_with_ids(data["levels"], cache_dir=DATA_DIR)

    # 补完写回 staging，等 runner 统一发布
    for source, data in all_data.items():
        save_json_file(staged(source), data)
        logger.info(f"[GETMETADATA] {source} 已写回 staging，待发布")

if __name__=="__main__":
    main()