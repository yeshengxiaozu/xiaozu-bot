import asyncio
from dataclasses import dataclass
from typing import Optional

import requests
from nonebot import logger

from .aredlapi import Aredl, aredllevels
from .gdapi import GDLevel, get_level_by_id
from .gddlapi import Gddl
from .imageinfo import send_ttp
from .nlwapi import Nlw, hdslevels, idslevels, lwlevels, nlwlevels
from .platapi import getderivedlevels, platdata_main_entries
from .platapi import getlevelbyid as get_plat_level_by_id

# i have no idea is this amount of exposal is bad but looked it worked


# fallback function since I should already get it using gdapi if this get called we f*cked up
def get_creator(level_id: int) -> Optional[str]:
    logger.warning("get_creator got called: " + str(level_id))
    try:
        data = requests.get(
            f"https://history.geometrydash.eu/api/v1/level/{level_id}", timeout=10
        )
        return data.json()["cache_username"]
    except Exception:  # noqa: BLE001
        return None


# nice little function that extract exactly what i need
def get_difficulty(level_id: int) -> Optional[str]:
    logger.info("get_difficulty got called: " + str(level_id))
    try:
        data = get_level_by_id(level_id)
    except Exception:  # noqa: BLE001
        return None
    return data.difficulty_label() if data else None

@dataclass
class SearchResult:
    id: int
    name: str
    creator: Optional[str] = None
    gddl_tier: Optional[str] = None
    difficulty: Optional[str] = None


def _add_search_result(  # noqa: PLR0913
    results: dict[int, SearchResult],
    level_id: int,
    name: str,
    creator: Optional[str] = None,
    gddl_tier: Optional[str] = None,
    difficulty: Optional[str] = None,
):
    if level_id is None:
        return
    if level_id in results:
        item = results[level_id]
        if not item.creator and creator:
            item.creator = creator
        if not item.gddl_tier and gddl_tier:
            item.gddl_tier = gddl_tier
        return
    results[level_id] = SearchResult(level_id, name, creator, gddl_tier, difficulty)


def search_by_name(name: str) -> list[SearchResult]:  # noqa: C901, PLR0912
    normalized = name.strip().lower()
    results: dict[int, SearchResult] = {}

    # 1) GDDL exact match
    gddl_candidates = Gddl.getlevelsbyname(name) or []
    for level in gddl_candidates:
        if not level or not getattr(level, "Meta", None):
            continue
        if getattr(level.Meta, "Name", "").strip().lower() == normalized:
            _add_search_result(
                results,
                int(level.ID),
                level.Meta.Name,
                None,
                str(round(level.Rating, 2)) if level.Rating else None,
                level.Meta.Difficulty + (" Pemon" if level.is_pemon() else " Demon"),
            )
            logger.info(
                f"Find a result in GDDL: tier {getattr(level, 'Rating', 'Unknown')}"
            )

    # 2) AREDL exact match
    for level in aredllevels:
        if not level or not getattr(level, "name", None):
            continue
        if level.name.strip().lower() == normalized:
            _add_search_result(
                results,
                int(level.level_id),
                level.name,
                None,
                getattr(level, "gddl_tier", None),
                "Extreme Demon",
            )
            logger.info("Find a result in AREDL: #" + str(level.position) or "Unknown")

    # 3) NLW exact match
    for level in (*nlwlevels, *idslevels, *lwlevels, *hdslevels):
        if not level or not getattr(level, "name", None):
            continue
        if level.name.strip().lower() == normalized:
            _add_search_result(
                results,
                int(level.id or "0"),
                level.name,
                getattr(level, "creator", None),
                None,
            )
            logger.info(
                f"Find a result in {level.source}: " + str(level.tier)
                or "Unknown" + " Tier",
                None,
            )

    # 4) Platdata exact match
    for level in platdata_main_entries:
        if not level or not getattr(level, "name", None):
            continue
        if level.name.strip().lower() == normalized:
            _add_search_result(
                results,
                int(level.id),
                level.name,
                getattr(level, "creator", None),
                getattr(level, "tier", "").strip(),
                None,
            )

    if results:
        return list(results.values())

    # No point to do these fallback thing since theres already a gdsearch bot that is better than mine
    # Fallback to gdapi exact match
    """
    try:
        rated = search_levels_by_name(name, star=True)
    except Exception:
        rated = []
    exact_rated = [lvl for lvl in (rated or []) if getattr(lvl, "level_name", "").strip().lower() == normalized]
    if exact_rated:
        return [SearchResult(int(lvl.level_id), lvl.level_name, None, None, "GDAPI") for lvl in exact_rated]

    try:
        unrated = search_levels_by_name(name, star=False)
    except Exception:
        unrated = []
    exact_unrated = [lvl for lvl in (unrated or []) if getattr(lvl, "level_name", "").strip().lower() == normalized]
    return [SearchResult(int(lvl.level_id), lvl.level_name, None, None, "GDAPI") for lvl in exact_unrated[:5]]
    """
    return []


def getlevelinfo(level_id: int) -> Optional[GDLevel]:
    gdlevel = get_level_by_id(level_id)
    if not gdlevel:
        return None
    return gdlevel

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.rule import Rule


def _format_demon_message(level_info: GDLevel) -> str:  # noqa: C901
    level_id = level_info.level_id
    gddl_info = Gddl.getlevelbyid(level_id)
    aredl_info = Aredl.getlevelbyid(level_id)
    nlw_info = Nlw.getlevelbyid(level_id)
    creator = level_info.creator_name
    msgstr = f"{level_info.level_name} {'by ' + creator if creator else ''}"

    if nlw_info and nlw_info.creator and (
        not creator
        or nlw_info.creator.strip().lower() != creator.strip().lower()
    ):
        msgstr += f" (by {nlw_info.creator})"

    msgstr += f" ({level_info.difficulty_label()})"

    length_info = (f"Length: {nlw_info.length}" if nlw_info and nlw_info.length else "")
    msgstr += f"\nID: {level_info.level_id} {length_info}"

    if gddl_info:
        if gddl_info.Rating:
            rating_2p = ""
            if gddl_info.Meta.IsTwoPlayer and gddl_info.TwoPlayerRating:
                rating_2p = f" / {round(gddl_info.TwoPlayerRating, 2)}(2p)"
            msgstr += (
                f"\nGDDL Rating: {round(gddl_info.Rating, 2)}{rating_2p}"
                f" ({gddl_info.RatingCount})"
            )

        if gddl_info.Enjoyment:
            enjoy_2p = ""
            if gddl_info.Meta.IsTwoPlayer and gddl_info.TwoPlayerEnjoyment:
                enjoy_2p = f" / {round(gddl_info.TwoPlayerEnjoyment, 2)}(2p)"
            msgstr += (
                f"\nGDDL Enjoyment: {round(gddl_info.Enjoyment, 2)}{enjoy_2p}"
                f" ({gddl_info.EnjoymentCount})"
            )

        if gddl_info.Tags:
            tags_str = ", ".join(
                f"{tag['Name']}({tag['Count']})" for tag in gddl_info.Tags[:3]
            )
            msgstr += f"\nGDDL Tags: {tags_str}"

    if aredl_info:
        tags_str = (
            ", ".join(aredl_info.tags) if aredl_info.tags else "Unknown"
        )
        if aredl_info.legacy:
            msgstr += f"\nAREDL #Legacy ({tags_str})"
        else:
            msgstr += f"\nAREDL #{aredl_info.position} ({tags_str})"

    if nlw_info:
        skillset = f"({nlw_info.skillset})" if nlw_info.skillset else ""
        msgstr += (
            f"\n{nlw_info.source} Tier: {nlw_info.tier}{skillset}"
        )

    if aredl_info and aredl_info.edel_enjoyment:
        msgstr += f"\nEDEL Enjoyment: {aredl_info.edel_enjoyment}"

    return msgstr


def _format_pemon_message(level_info: GDLevel) -> str:  # noqa: C901, PLR0912
    level_id = level_info.level_id
    plat_info = get_plat_level_by_id(level_info.level_id)
    gddl_info = Gddl.getlevelbyid(level_id)
    aredl_info = Aredl.getlevelbyid(level_id)
    nlw_info = Nlw.getlevelbyid(level_id)
    creator = level_info.creator_name

    _firstline = f"{level_info.level_name} {'by ' + creator if creator else ''}"

    if nlw_info and nlw_info.creator and (
        not creator
        or nlw_info.creator.strip().lower() != creator.strip().lower()
    ):
        _firstline += f" (by {nlw_info.creator})"
    _firstline += f" ({level_info.difficulty_label()})"

    lines = [_firstline]

    check_info = (f": {nlw_info.checkpoints}" if nlw_info and nlw_info.checkpoints else "")
    lines.append(f"ID: {level_info.level_id} {check_info}")

    if plat_info and plat_info.tier:
        lines.append(f"Difficulty：{plat_info.tier}")

    rank_parts = []
    if plat_info and plat_info.tpl:
        rank_parts.append(f"{plat_info.tpl}(TPL)")
    if plat_info and plat_info.pemonlist:
        rank_parts.append(f"{plat_info.pemonlist}(Pemonlist)")
    if aredl_info:
        rank_parts.append(f"{aredl_info.position}(AREDL)")
    if rank_parts:
        lines.append(f"Rank：{'/'.join(rank_parts)}")

    if plat_info and plat_info.tags:
        lines.append(f"Tags：{', '.join(plat_info.tags)}")

    enjoyment_parts = []
    if plat_info and plat_info.enjoyment is not None:
        enjoyment_parts.append(f"{plat_info.enjoyment}(PEL)")
    if aredl_info and aredl_info.edel_enjoyment is not None:
        enjoyment_parts.append(f"{round(aredl_info.edel_enjoyment, 0)}(EDEL)")
    if gddl_info and gddl_info.Enjoyment:
        enjoyment_parts.append(f"{round(gddl_info.Enjoyment,2)}(GDDL)")
    if enjoyment_parts:
        lines.append(f"Enjoyment: {'/'.join(enjoyment_parts)}")

    if aredl_info:
        tags_str = ", ".join(aredl_info.tags) if aredl_info.tags else "Unknown"
        if aredl_info.legacy:
            lines.append(f"\nAREDL #Legacy ({tags_str})")
        else:
            lines.append(f"\nAREDL #{aredl_info.position} ({tags_str})")

    if nlw_info:
        skillset = f"({nlw_info.skillset})" if nlw_info.skillset else ""
        lines.append(f"\n{nlw_info.source} Tier: {nlw_info.tier}{skillset}")

    if plat_info and plat_info.derived_levels:
        for child in getderivedlevels(plat_info):
            lines.append(f"-- {child.name}")
            if child.tier:
                lines.append(f"Difficulty: {child.tier}")
            if child.enjoyment is not None:
                lines.append(f"PEL Enjoyment: {child.enjoyment}")

    return "\n".join(lines)


def _format_non_demon_message(level_info: GDLevel) -> str:
    level_id = level_info.level_id
    creator = level_info.creator_name
    lines = [f"{level_info.level_name} {'by ' + creator if creator else ''}"]

    difficulty_label = level_info.difficulty_label()
    lines.append(f"Difficulty: {difficulty_label}")

    plat_info = get_plat_level_by_id(level_id)
    if plat_info:
        plat_line = f"Plat: {plat_info.tier or 'Unknown'}"
        if plat_info.tpl:
            plat_line += f", TPL {plat_info.tpl}"
        if plat_info.pemonlist:
            plat_line += f", Pemonlist {plat_info.pemonlist}"
        lines.append(plat_line)
        if plat_info.tags:
            lines.append(f"Tags: {', '.join(plat_info.tags)}")
        if plat_info.enjoyment is not None:
            lines.append(f"Enjoyment: {plat_info.enjoyment}(PEL)")

    if plat_info and plat_info.derived_levels:
        for child in getderivedlevels(plat_info):
            lines.append(f"-- {child.name}")
            if child.tier:
                lines.append(f"Difficulty: {child.tier}")
            if child.enjoyment is not None:
                lines.append(f"PEL Enjoyment: {child.enjoyment}")
    return "\n".join(lines)


def _format_demon_image_text(level_info: GDLevel) -> str:
    level_id = level_info.level_id
    nlw_info = Nlw.getlevelbyid(level_id)
    aredl_info = Aredl.getlevelbyid(level_id)
    parts = []
    parts.append(f"Song: [{level_info.song_id}] {level_info.song_name} ({level_info.song_author})<br>" + f"Description: {level_info.description}")
    if nlw_info and nlw_info.description:
        parts.append(
            f"{nlw_info.source} Description: {nlw_info.description}"
        )
    if aredl_info and aredl_info.description:
        parts.append(f"AREDL Description: {aredl_info.description}")
    return "<p>".join(parts)


def _format_pemon_image_text(level_info: GDLevel) -> str:
    level_id = level_info.level_id
    nlw_info = Nlw.getlevelbyid(level_id)
    aredl_info = Aredl.getlevelbyid(level_id)
    parts = []
    parts.append(f"Song: [{level_info.song_id}] {level_info.song_name} ({level_info.song_author})<br>" + f"Description: {level_info.description}")
    if nlw_info and nlw_info.description:
        parts.append(f"{nlw_info.source} Description: {nlw_info.description}")
    if aredl_info and aredl_info.description:
        parts.append(f"AREDL Description: {aredl_info.description}")
    return "<p>".join(parts)



async def send_result(bot: Bot, event: Event, level_info: GDLevel) -> None:
    if level_info.is_pemon():
        msgstr = _format_pemon_message(level_info)
        await bot.send(event=event, message=msgstr)
        await send_ttp(bot, event, _format_pemon_image_text(level_info))
    elif level_info.is_demon_detail():
        msgstr = _format_demon_message(level_info)
        await bot.send(event=event, message=msgstr)
        await send_ttp(bot, event, _format_demon_image_text(level_info))
    else:
        msgstr = _format_non_demon_message(level_info)
        await bot.send(event=event, message=msgstr)


gdsearch = on_command("gdsearch")

# 搜索缓存与超时
search_cache = {}
timeout_tasks = {}


def has_cache(event: MessageEvent) -> bool:
    return str(event.get_user_id()) in search_cache


rule_cache = Rule(has_cache)
gdsearchselect = on_message(rule_cache, priority=100, block=False)


async def clear_search_cache(bot: Bot, event: Event, user_id: str) -> None:
    """30秒后自动清除搜索缓存"""
    await asyncio.sleep(30)
    search_cache.pop(user_id, None)
    timeout_tasks.pop(user_id, None)
    await bot.send(event, "输入超时,请重新再试")


@gdsearch.handle()
async def handle_gdsearch(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    name = arg.extract_plain_text().strip()
    if name == "":
        await gdsearch.finish("请提供关卡的名字或id")

    user_id = str(event.get_user_id())
    # 清除旧缓存/任务
    search_cache.pop(user_id, None)
    if user_id in timeout_tasks:
        timeout_tasks[user_id].cancel()
        del timeout_tasks[user_id]

    # ID 搜索
    if len(name) > 2 and name.isdigit():  # noqa: PLR2004 yes its a magic number but it help user
        level = getlevelinfo(int(name))
        if level:
            await send_result(bot, event, level)
        else:
            await gdsearch.finish("不存在符合这个id的demon关卡")
        return

    # 名称搜索
    results = search_by_name(name)
    if not results:
        await gdsearch.finish(f"没有找到名为 '{name}' 的demon关卡")

    if len(results) == 1:
        level = getlevelinfo(results[0].id)
        if level:
            await send_result(bot, event, level)
        else:
            await gdsearch.finish("发生未知错误。相关id: " + str(results[0].id))
        await gdsearch.finish()

    # 多结果缓存
    search_cache[user_id] = results
    timeout_tasks[user_id] = asyncio.create_task(
        clear_search_cache(bot, event, user_id)
    )

    msgstr = f"找到 {len(results)} 个名为 '{name}' 的demon关卡："
    for i, result in enumerate(results, start=1):
        difficulty_str = f" ({result.difficulty or get_difficulty(result.id)})"
        creator_str = f" by {result.creator}" if result.creator else ""
        tier_str = f" t{result.gddl_tier}" if result.gddl_tier else ""
        msgstr += f"\n{i}. {result.name}{creator_str}{difficulty_str}{tier_str} (ID: {result.id})"
    msgstr += "\n输入序号以选中关卡,输入“结束”以中止搜索"
    await gdsearch.finish(msgstr)


@gdsearchselect.handle()
async def handle_choice(bot: Bot, event: Event) -> None:
    user_id = str(event.get_user_id())
    if user_id not in search_cache:
        await gdsearchselect.finish()

    choice = event.get_message().extract_plain_text().strip()

    # 手动取消
    if "结束" in choice or "取消" in choice:
        search_cache.pop(user_id, None)
        if user_id in timeout_tasks:
            timeout_tasks[user_id].cancel()
            del timeout_tasks[user_id]
        await gdsearchselect.finish("已取消搜索")

    if not choice.isdigit():
        await gdsearchselect.finish()

    index = int(choice)
    results = search_cache[user_id]
    if index < 1 or index > len(results):
        await gdsearchselect.finish("请输入正确的序号")

    result = results[index - 1]
    # 清理缓存
    search_cache.pop(user_id, None)
    if user_id in timeout_tasks:
        timeout_tasks[user_id].cancel()
        del timeout_tasks[user_id]

    level = getlevelinfo(result.id)
    if level:
        await send_result(bot, event, level)
    else:
        await gdsearchselect.finish("发生未知错误。相关id: " + str(results[0].id))
    await gdsearchselect.finish()
