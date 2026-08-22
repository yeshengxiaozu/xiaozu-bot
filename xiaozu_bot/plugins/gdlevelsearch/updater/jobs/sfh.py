from nonebot import logger

from xiaozu_bot.utils.json_storage import write_json_atomic

from ...api.http import request as http_request
from ...constants import USER_AGENT

try:
    from ..paths import staged
except ImportError:
    from updater.paths import staged

def build_level_to_song_mapping(data):
    """
    从 Song File Hub 索引数据中构建 level ID -> 歌曲信息的映射。
    返回: dict, key 为 level ID (int), value 为 {"name": ..., "artist": ..., "url": ...}
    """
    mapping = {}
    hosted = data.get("nongs", {}).get("hosted", {})

    for song_info in hosted.values():
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

def fetch():
    # 1. 从 URL 读取数据
    url = "https://raw.githubusercontent.com/FlafyDev/auto-nong-indexes/v2/sfh-index.min.json"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    try:
        response = http_request("GET", url, headers=headers, timeout=30)
    except Exception as exc:
        logger.error(f"[SFH] 索引下载失败，保留旧数据: {exc}")
        return
    if response.status_code != 200:
        logger.error(f"[SFH] 索引返回 HTTP {response.status_code}，保留旧数据")
        return
    try:
        data = response.json()
    except Exception as exc:
        logger.error(f"[SFH] 索引 JSON 解析失败，保留旧数据: {exc}")
        return
    # 2. 构建映射
    logger.info("[SFH] 正在构建 level ID -> 歌曲信息 映射...")
    mapping = build_level_to_song_mapping(data)
    if not isinstance(mapping, dict) or not mapping:
        logger.error("[SFH] 新映射为空，疑似上游数据损坏，保留旧快照")
        return
    logger.info(f"[SFH] 共映射了 {len(mapping)} 个 level ID")

    # 3. 保存到本地 JSON 文件
    output_file = staged("nong_index.json")
    write_json_atomic(output_file, mapping, indent=4)
    logger.info(f"[SFH] 映射已保存到 {output_file}")

if __name__ == "__main__":
    fetch()
