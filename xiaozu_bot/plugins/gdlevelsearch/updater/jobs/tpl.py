from nonebot import logger

from xiaozu_bot.utils.json_storage import write_json_atomic

from ...api.http import request as http_request

try:
    from ..paths import staged
except ImportError:
    from updater.paths import staged


def build_level_mapping(data):
    """
    从 GD Platformer List API 数据中构建关卡信息映射。

    字段映射：
        levelID  -> id
        name     -> name
        position -> position
        author   -> creator

    返回:
        dict，key 为 level ID，value 为关卡信息。
    """
    mapping = {}

    if not isinstance(data, list):
        return mapping

    for level_info in data:
        if not isinstance(level_info, dict):
            continue

        level_id = level_info.get("levelID")
        name = level_info.get("name")
        position = level_info.get("position")
        creator = level_info.get("author")

        # 缺少关键字段则跳过
        if level_id is None:
            continue

        try:
            level_id = int(level_id)
        except (ValueError, TypeError):
            continue

        mapping[level_id] = {
            "id": level_id,
            "name": name,
            "position": position,
            "creator": creator,
        }

    return mapping


def fetch():
    # 1. 从 URL 读取数据
    url = "https://gdplatformerlist.com/api/levels"
    headers = {
        "Content-Type": "application/json",
    }

    try:
        response = http_request(
            "GET",
            url,
            headers=headers,
            timeout=30,
        )
    except Exception as exc:
        logger.error(f"[TPL] 关卡列表下载失败，保留旧数据: {exc}")
        return

    if response.status_code != 200:
        logger.error(
            f"[TPL] 关卡列表返回 HTTP {response.status_code}，保留旧数据"
        )
        return

    # 2. 解析 JSON
    try:
        data = response.json()
    except Exception as exc:
        logger.error(f"[TPL] 关卡列表 JSON 解析失败，保留旧数据: {exc}")
        return

    # 3. 构建映射
    logger.info("[TPL] 正在构建 level ID -> 关卡信息映射...")

    mapping = build_level_mapping(data)

    if not isinstance(mapping, dict) or not mapping:
        logger.error("[TPL] 新映射为空，疑似上游数据损坏，保留旧快照")
        return

    logger.info(f"[TPL] 共获取了 {len(mapping)} 个关卡")

    # 4. 保存到本地 JSON 文件
    output_file = staged("tpl.json")

    write_json_atomic(
        output_file,
        mapping,
        indent=4,
    )

    logger.info(f"[TPL] 关卡列表已保存到 {output_file}")


if __name__ == "__main__":
    fetch()
