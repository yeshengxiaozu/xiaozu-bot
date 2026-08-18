import json
import time
from dataclasses import dataclass, field
from typing import Any

from nonebot import logger

from xiaozu_bot.utils.json_storage import write_json_atomic

try:
    from ..paths import staged, staged_or_published
except ImportError:
    from updater.paths import staged, staged_or_published

ID_FIX = {
    #标记错误的id手动修复
    "112363390": "112603907",
    #无效关卡id统一处理为0
    "104683046": "0",
    "127566338": "0",
    "Pending Removal": "0",
}

@dataclass
class PlatLevel:
    """平台难度关卡数据类"""
    name: str
    id: str | None = None
    tier: str | None = None
    tpl: str | None = None
    pemonlist: str | None = None
    creator: str | None = None
    tags: list[str] = field(default_factory=list)
    enjoyment: float | None = None
    video: str | None = None
    weight: str | None = None
    section: str | None = None
    # platdata_tier: Optional[str] = None 重复词条无须保存
    derived_from: str | None = None  # 如果是附属词条，指向主词条的名称
    derived_levels: list[str] = field(default_factory=list)  # 这个词条的所有附属词条

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'id': self.id,
            'tier': self.tier,
            'tpl': self.tpl,
            'pemonlist': self.pemonlist,
            'creator': self.creator,
            'tags': self.tags,
            'enjoyment': self.enjoyment,
            'video': self.video,
            'weight': self.weight,
            'section': self.section,
            'derived_from': self.derived_from,
            'derived_levels': self.derived_levels,
        }


def extract_base_name(name: str) -> tuple:
    """
    提取基础名称和附属词条
    例如：
    - "Null (Deathless)" -> ("Null", "Deathless")
    - "Moongrinder (Coin)" -> ("Moongrinder", "Coin")
    - "Normal Level" -> ("Normal Level", None)
    返回：(base_name, suffix)
    """
    if '(' in name and ')' in name:
        parts = name.rsplit('(', 1)
        if len(parts) == 2:
            base = parts[0].strip()
            suffix = parts[1].rstrip(')').strip()
            if base and suffix:  # 两部分都不能为空
                return base, suffix
    return name, None


def merge_plat_data() -> dict[str, PlatLevel]:
    """
    合并来自 TPL、Pemon List、platdiff 的数据。

    数据统一通过 GD Level ID 匹配：

    tpl.json:
        id       -> level.id
        name     -> level.name
        position -> level.tpl

    pemonlist.json:
        id       -> 匹配 level.id
        position -> level.pemonlist

    platdiff.json:
        id        -> 匹配 level.id
        tier      -> level.tier
        creator   -> level.creator
        tags      -> level.tags
        enjoyment -> level.enjoyment
        video     -> level.video
    """

    # =========================================================
    # 1. 加载 TPL
    # =========================================================

    logger.info("[PLATBATCH] loading tpl...")

    try:
        with staged_or_published("tpl.json").open("r", encoding="utf-8") as f:
            tpl_raw = json.load(f)
    except FileNotFoundError:
        logger.warning("[PLATBATCH] tpl.json not found, skip")
        tpl_raw = {}

    # =========================================================
    # 2. 加载 Pemon List
    # =========================================================

    logger.info("[PLATBATCH] loading pemonlist...")

    try:
        with staged_or_published("pemonlist.json").open("r", encoding="utf-8") as f:
            pemonlist_raw = json.load(f)
    except FileNotFoundError:
        logger.warning("[PLATBATCH] pemonlist.json not found, skip")
        pemonlist_raw = {}

    # =========================================================
    # 3. 加载 platdiff
    # =========================================================

    logger.info("[PLATBATCH] loading platdiff...")

    try:
        with staged_or_published("platdiff.json").open("r", encoding="utf-8") as f:
            platdiff_raw = json.load(f).get("entries", [])
    except FileNotFoundError:
        logger.warning("[PLATBATCH] platdiff.json not found, skip")
        platdiff_raw = []

    # =========================================================
    # 4. 以 ID 为主键建立数据
    # =========================================================

    merged_by_id: dict[str, PlatLevel] = {}

    # ---------------------------------------------------------
    # TPL
    # ---------------------------------------------------------

    if isinstance(tpl_raw, dict):
        for item in tpl_raw.values():
            if not isinstance(item, dict):
                continue

            level_id = item.get("id")
            name = item.get("name")

            if level_id is None or not name:
                continue

            level_id = str(level_id)
            name = str(name).strip()

            if not name:
                continue

            level = PlatLevel(
                name=name,
                id=level_id,
            )

            if item.get("position") is not None:
                level.tpl = str(item["position"])

            merged_by_id[level_id] = level

    logger.info(f"[PLATBATCH] loaded {len(merged_by_id)} TPL levels")

    # ---------------------------------------------------------
    # Pemon List
    # ---------------------------------------------------------

    pemon_matched = 0
    pemon_unmatched = 0

    if isinstance(pemonlist_raw, dict):
        for item in pemonlist_raw.values():
            if not isinstance(item, dict):
                continue

            level_id = item.get("id")

            if level_id is None:
                continue

            level_id = str(level_id)

            level = merged_by_id.get(level_id)

            if level is None:
                # Pemon 中存在，但 TPL 中不存在
                name = item.get("name")

                if not name:
                    continue

                level = PlatLevel(
                    name=str(name).strip(),
                    id=level_id,
                )

                merged_by_id[level_id] = level
                pemon_unmatched += 1
            else:
                pemon_matched += 1

            if item.get("position") is not None:
                level.pemonlist = str(item["position"])

    logger.info(
        f"[PLATBATCH] Pemon matched: {pemon_matched}, "
        f"unmatched: {pemon_unmatched}"
    )

    # ---------------------------------------------------------
    # platdiff
    # ---------------------------------------------------------

    platdiff_matched = 0
    platdiff_unmatched = 0

    for item in platdiff_raw:
        if not isinstance(item, dict):
            continue

        level_id = item.get("id")

        if level_id is None:
            continue

        level_id = str(level_id)

        level = merged_by_id.get(level_id)

        if level is None:
            # platdiff 中有，但 TPL / Pemon 都没有
            # 不主动创建，避免出现没有列表排名的孤立数据
            platdiff_unmatched += 1
            continue

        platdiff_matched += 1

        if "tier" in item:
            level.tier = item.get("tier")

        if "creator" in item:
            level.creator = item.get("creator")

        if "tags" in item:
            tags = item.get("tags")

            if isinstance(tags, str):
                tags = tags.strip()

                if tags:
                    level.tags = [
                        tag.strip()
                        for tag in tags.split(",")
                        if tag.strip()
                    ]
                else:
                    level.tags = []

            elif isinstance(tags, list):
                level.tags = [
                    str(tag).strip()
                    for tag in tags
                    if str(tag).strip()
                ]

        if "enjoyment" in item:
            level.enjoyment = item.get("enjoyment")

        if "video" in item:
            level.video = item.get("video")

    logger.info(
        f"[PLATBATCH] platdiff matched: {platdiff_matched}, "
        f"unmatched: {platdiff_unmatched}"
    )

    # =========================================================
    # 5. 最终转换为 name -> PlatLevel
    # =========================================================

    merged: dict[str, PlatLevel] = {}

    for level in merged_by_id.values():
        if level.name in merged:
            logger.warning(
                f"[PLATBATCH] duplicate level name: "
                f"{level.name!r}, ID={level.id}"
            )

        merged[level.name] = level

    return merged


def process_derived_levels(merged: dict[str, PlatLevel]) -> dict[str, PlatLevel]:
    """
    处理附属词条：
    1. 识别形如 "Null (Deathless)" 的词条
    2. 找到对应的主词条 "Null"
    3. 从主词条复制 id 到附属词条
    4. 在主词条中添加对附属词条的引用
    """
    # 先识别所有的附属词条
    derived_records: list[tuple] = []  # (derived_name, base_name)

    for name in list(merged.keys()):
        base_name, suffix = extract_base_name(name)

        if suffix and base_name in merged:
            # 这是一个附属词条，且找到了对应的主词条
            derived_records.append((name, base_name))
            merged[name].derived_from = base_name

            # 从主词条复制 id
            if merged[base_name].id:
                merged[name].id = merged[base_name].id

            # 在主词条中添加对附属词条的引用
            if name not in merged[base_name].derived_levels:
                merged[base_name].derived_levels.append(name)

    return merged


def clean_level_data(level: PlatLevel) -> None:
    """
    清理数据中的无效值：
    1. TPL 和 Pemonlist 中的 "-" 转为 None
    2. 从 Tags 中移除占位符 "---"，但保持为列表（即使为空）
    3. 确认关卡内容是否在ID_FIX中，若在其中则替换
    """
    # 清理 TPL
    if level.tpl == "-":
        level.tpl = None

    # 清理 Pemonlist
    if level.pemonlist == "-":
        level.pemonlist = None

    # 清理 Tags：移除占位符 "---"，但保持为列表类型（空列表也保留）
    if level.tags:
        level.tags = [tag for tag in level.tags if tag != "---"]

    if level.id in ID_FIX:
        level.id = ID_FIX[level.id]


def batch_process():
    """
    主函数：合并数据并处理附属词条
    """

    # 合并数据
    merged = merge_plat_data()
    logger.info(f"[PLATBATCH] merged: {len(merged)} levels")

    # 处理附属词条
    merged = process_derived_levels(merged)

    # 统计附属词条
    derived_count = sum(1 for level in merged.values() if level.derived_from)
    base_count = sum(1 for level in merged.values() if level.derived_levels)

    logger.info(f"[PLATBATCH] linked {derived_count} levels to {base_count} base levels")

    # 清理数据
    for level in merged.values():
        clean_level_data(level)

    # 移除清理之后id为0的条目
    merged = {name: level for name, level in merged.items() if level.id != "0"}

    # 保存结果
    output_data = {
        "timestamp": time.time(),
        "levels": [level.to_dict() for level in sorted(merged.values(), key=lambda x: x.name)]
    }

    output_path = staged("plat_combined.json")
    write_json_atomic(output_path, output_data, indent=4)

    logger.info(f"[PLATBATCH] saved to {output_path}")

    return merged


def batch():
    """
    在 batch.py 中调用的主函数
    """
    batch_process()


if __name__ == "__main__":
    batch_process()
