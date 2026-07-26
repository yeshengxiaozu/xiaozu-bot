import json
import requests
from pathlib import Path

from nonebot import logger

try:
    from ..paths import DATA_DIR, staged
except ImportError:
    from updater.paths import DATA_DIR, staged

def build_level_to_song_mapping(data):
    """
    从 Song File Hub 索引数据中构建 level ID -> 歌曲信息的映射。
    返回: dict, key 为 level ID (int), value 为 {"name": ..., "artist": ..., "url": ...}
    """
    mapping = {}
    hosted = data.get("nongs", {}).get("hosted", {})

    for song_id, song_info in hosted.items():
        # 提取所需字段
        name = song_info.get("name")
        artist = song_info.get("artist")
        url = song_info.get("url")

        # 跳过缺少关键字段的条目
        if name is None or artist is None or url is None:
            continue

        verified_levels = song_info.get("verifiedLevelIDs", [])
        # 如果 verifiedLevelIDs 为空或不是列表，则跳过
        if not isinstance(verified_levels, list) or not verified_levels:
            continue

        # 为每个 verifiedLevelID 建立映射
        for level_id in verified_levels:
            # 确保 level_id 是整数，若不是则尝试转换
            try:
                level_id = int(level_id)
            except (ValueError, TypeError):
                continue
            mapping[level_id] = {
                "name": name,
                "artist": artist,
                "url": url
            }

    return mapping

def main():
    # 1. 从 URL 读取数据
    url = "https://raw.githubusercontent.com/FlafyDev/auto-nong-indexes/v2/sfh-index.min.json"
    headers = {
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = response.json()
    else:
        return
    # 2. 构建映射
    logger.info("[SFH] 正在构建 level ID -> 歌曲信息 映射...")
    mapping = build_level_to_song_mapping(data)
    logger.info(f"[SFH] 共映射了 {len(mapping)} 个 level ID")

    # 3. 保存到本地 JSON 文件
    output_file = staged("nong_index.json")
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=4, ensure_ascii=False)
    logger.info(f"[SFH] 映射已保存到 {output_file}")

if __name__ == "__main__":
    main()