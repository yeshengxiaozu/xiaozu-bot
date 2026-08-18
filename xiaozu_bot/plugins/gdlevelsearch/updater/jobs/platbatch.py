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

    platdiff 是最终关卡全集。
    TPL / Pemon List 通过 level ID 为 platdiff 中的关卡
    补充对应榜单排名。

    数据来源：

    tpl.json:
        id       -> 匹配 level.id
        name     -> 备用名称
        position -> level.tpl

    pemonlist.json:
        id       -> 匹配 level.id
        name     -> 备用名称
        position -> level.pemonlist

    platdiff.json:
        id        -> level.id
        name      -> level.name
        tier      -> level.tier
        creator   -> level.creator
        tags      -> level.tags
        enjoyment -> level.enjoyment
        video     -> level.video

    注意：
        platdiff 中没有 id 的词条也必须保留。
        例如 "Null (Deathless)" 这类派生词条可能没有自己的 ID，
        后续由 process_derived_levels() 从主词条继承 ID。
    """

    # =========================================================
    # 1. 加载 TPL
    # =========================================================

    logger.info("[PLATBATCH] loading tpl...")

    try:
        with staged_or_published("tpl.json").open(
            "r",
            encoding="utf-8",
        ) as f:
            tpl_raw = json.load(f)
    except FileNotFoundError:
        logger.warning("[PLATBATCH] tpl.json not found, skip")
        tpl_raw = {}

    # =========================================================
    # 2. 加载 Pemon List
    # =========================================================

    logger.info("[PLATBATCH] loading pemonlist...")

    try:
        with staged_or_published("pemonlist.json").open(
            "r",
            encoding="utf-8",
        ) as f:
            pemonlist_raw = json.load(f)
    except FileNotFoundError:
        logger.warning("[PLATBATCH] pemonlist.json not found, skip")
        pemonlist_raw = {}

    # =========================================================
    # 3. 加载 platdiff
    # =========================================================

    logger.info("[PLATBATCH] loading platdiff...")

    try:
        with staged_or_published("platdiff.json").open(
            "r",
            encoding="utf-8",
        ) as f:
            platdiff_raw = json.load(f).get("entries", [])
    except FileNotFoundError:
        logger.warning("[PLATBATCH] platdiff.json not found, skip")
        platdiff_raw = []

    # =========================================================
    # 4. 建立 TPL ID 索引
    # =========================================================

    tpl_by_id: dict[str, dict] = {}

    if isinstance(tpl_raw, dict):
        for item in tpl_raw.values():
            if not isinstance(item, dict):
                continue

            level_id = item.get("id")

            if level_id is None:
                continue

            tpl_by_id[str(level_id)] = item

    logger.info(
        f"[PLATBATCH] loaded {len(tpl_by_id)} TPL levels"
    )

    # =========================================================
    # 5. 建立 Pemon List ID 索引
    # =========================================================

    pemon_by_id: dict[str, dict] = {}

    if isinstance(pemonlist_raw, dict):
        for item in pemonlist_raw.values():
            if not isinstance(item, dict):
                continue

            level_id = item.get("id")

            if level_id is None:
                continue

            pemon_by_id[str(level_id)] = item

    logger.info(
        f"[PLATBATCH] loaded {len(pemon_by_id)} Pemon levels"
    )

    # =========================================================
    # 6. 以 platdiff 为全集
    # =========================================================

    merged_by_id: dict[str, PlatLevel] = {}

    tpl_matched = 0
    pemon_matched = 0
    no_id_count = 0

    for item in platdiff_raw:
        if not isinstance(item, dict):
            continue

        level_id = item.get("id")
        name = str(item.get("name", "")).strip()

        if not name:
            continue

        # -----------------------------------------------------
        # 基础数据来自 platdiff
        # -----------------------------------------------------

        level = PlatLevel(
            name=name,
            id=str(level_id) if level_id is not None else None,
        )

        # Tier
        if "tier" in item:
            level.tier = item.get("tier")

        # Creator
        if "creator" in item:
            level.creator = item.get("creator")

        # Tags
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

        # Enjoyment
        if "enjoyment" in item:
            level.enjoyment = item.get("enjoyment")

        # Video
        if "video" in item:
            level.video = item.get("video")

        # -----------------------------------------------------
        # 有 ID：匹配 TPL / Pemon
        # -----------------------------------------------------

        if level_id is not None:
            level_id = str(level_id)

            # TPL
            tpl_item = tpl_by_id.get(level_id)

            if tpl_item is not None:
                if tpl_item.get("position") is not None:
                    level.tpl = str(tpl_item["position"])

                tpl_matched += 1

            # Pemon List
            pemon_item = pemon_by_id.get(level_id)

            if pemon_item is not None:
                if pemon_item.get("position") is not None:
                    level.pemonlist = str(
                        pemon_item["position"]
                    )

                pemon_matched += 1

            merged_by_id[level_id] = level

        # -----------------------------------------------------
        # 没有 ID：仍然保留
        #
        # 典型情况：
        #     Null (Deathless)
        #
        # 后续 process_derived_levels() 会从主词条
        # 继承 ID。
        # -----------------------------------------------------

        else:
            no_id_count += 1

            # ID 缺失时不能使用 None 作为唯一 key，
            # 否则多个无 ID 词条会互相覆盖。
            #
            # 使用 name 构造一个内部临时 key。
            temporary_key = f"__no_id__:{name}"

            # 极端情况下同名无 ID 条目仍然避免覆盖
            if temporary_key in merged_by_id:
                suffix = 2

                while f"{temporary_key}:{suffix}" in merged_by_id:
                    suffix += 1

                temporary_key = f"{temporary_key}:{suffix}"

            merged_by_id[temporary_key] = level

    logger.info(
        f"[PLATBATCH] platdiff levels: {len(merged_by_id)}"
    )

    logger.info(
        f"[PLATBATCH] TPL matched: {tpl_matched}, "
        f"Pemon matched: {pemon_matched}, "
        f"no-id entries: {no_id_count}"
    )

    # =========================================================
    # 7. 最终转换为 name -> PlatLevel
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
