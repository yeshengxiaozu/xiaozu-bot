import json
import requests
from pathlib import Path

from nonebot import logger

try:
    from ..paths import DATA_DIR
except ImportError:
    from updater.paths import DATA_DIR

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
    data = load_json_file(DATA_DIR / "ids_levels.json")
    if data:
        flat_list = data["levels"]
        enrich_levels_with_ids(flat_list, cache_dir=DATA_DIR)

def main():
    logger.info(f"[GETMETADATA] 开始处理元数据缓存目录: {DATA_DIR}")
    # 定义要处理的数据源文件
    data_sources = ["nlw_levels.json", "ids_levels.json", "lw_levels.json", "hds_levels.json"]
    cache_dir = DATA_DIR
    
    # 加载所有数据
    all_data = {}
    for source in data_sources:
        filepath = cache_dir / source
        data = load_json_file(filepath)
        if data:
            logger.info(f"[GETMETADATA] 已加载数据源: {source}")
            all_data[source] = data
    
    # 处理所有数据源的 levels
    for source, data in all_data.items():
        logger.info(f"[GETMETADATA] 开始补全 {source} 的 metadata")
        enrich_levels_with_ids(data["levels"], cache_dir=cache_dir)
    
    # 保存所有数据
    for source, data in all_data.items():
        filepath = cache_dir / source
        save_json_file(filepath, data)
        logger.info(f"[GETMETADATA] 已保存更新后的数据源: {source}")

if __name__=="__main__":
    main()