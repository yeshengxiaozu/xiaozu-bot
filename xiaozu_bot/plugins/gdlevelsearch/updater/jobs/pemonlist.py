from nonebot import logger

from xiaozu_bot.utils.json_storage import write_json_atomic

from ...api.http import request as http_request

try:
    from ..paths import staged
except ImportError:
    from updater.paths import staged


def build_level_mapping(data):
    """
    从 Pemon List API 数据中构建关卡信息映射。

    字段映射：
        level_id  -> id
        name      -> name
        placement -> position
        creator   -> creator

    返回:
        dict，key 为 level ID，value 为关卡信息。
    """
    mapping = {}

    if not isinstance(data, dict):
        return mapping

    levels = data.get("data", [])

    if not isinstance(levels, list):
        return mapping

    for level_info in levels:
        if not isinstance(level_info, dict):
            continue

        level_id = level_info.get("level_id")
        name = level_info.get("name")
        position = level_info.get("placement")
        creator = level_info.get("creator")

        # 缺少 ID 则无法建立映射
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
    url = "https://pemonlist.com/api/list"
    headers = {
        "Content-Type": "application/json?limit=1000",
    }

    try:
        response = http_request(
            "GET",
            url,
            headers=headers,
            timeout=30,
        )
    except Exception as exc:
        logger.error(f"[PEMON] 关卡列表下载失败，保留旧数据: {exc}")
        return

    if response.status_code != 200:
        logger.error(
            f"[PEMON] 关卡列表返回 HTTP {response.status_code}，保留旧数据"
        )
        return

    # 2. 解析 JSON
    try:
        data = response.json()
    except Exception as exc:
        logger.error(f"[PEMON] 关卡列表 JSON 解析失败，保留旧数据: {exc}")
        return

    # 3. 构建映射
    logger.info("[PEMON] 正在构建 level ID -> 关卡信息映射...")

    mapping = build_level_mapping(data)

    if not isinstance(mapping, dict) or not mapping:
        logger.error(
            "[PEMON] 新映射为空，疑似上游数据损坏，保留旧快照"
        )
        return

    logger.info(f"[PEMON] 共获取了 {len(mapping)} 个关卡")

    # 4. 保存到本地 JSON 文件
    output_file = staged("pemonlist.json")

    write_json_atomic(
        output_file,
        mapping,
        indent=4,
    )

    logger.info(f"[PEMON] 关卡列表已保存到 {output_file}")


if __name__ == "__main__":
    fetch()