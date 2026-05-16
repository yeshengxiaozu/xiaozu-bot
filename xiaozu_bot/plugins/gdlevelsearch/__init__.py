import asyncio
from dataclasses import dataclass
from typing import Optional

import requests
from nonebot import logger

from .aredlapi import Aredl  # noqa: F401
from .gdapi import GDLevel, get_level_by_id
from .gddlapi import Gddl
from .imageinfo import send_ttp
from .nlwapi import Nlw
from .platapi import Platapi

# i have no idea is this amount of exposal is bad but looked it worked


# fallback function since I should already get it using gdapi if this get called we f*cked up
def get_creator(level_id: int) -> Optional[str]:
    """通过GDhistory API获取关卡作者信息，没有官方api靠谱且原则上不应该被调用"""
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
    """通过GD API获取关卡难度标签，由于会造成api调用最好尽可能少被调用"""
    logger.info("get_difficulty got called: " + str(level_id))
    try:
        data = get_level_by_id(level_id)
    except Exception:  # noqa: BLE001
        return None
    return data.difficulty_label() if data else None

@dataclass
class SearchResult:
    """存储有关搜索结果的基本信息，方便用户进行筛选"""
    id: int
    name: str
    creator: Optional[str] = None
    tier: Optional[str] = None
    difficulty: Optional[str] = None


def _add_search_result(  # noqa: PLR0913
    results: dict[int, SearchResult],
    level_id: int,
    name: str,
    creator: Optional[str] = None,
    tier: Optional[str] = None,
    difficulty: Optional[str] = None,
):
    """向results中加入一条新的搜索结果"""
    if level_id is None:
        return
    if level_id in results:
        item = results[level_id]
        if not item.creator and creator:
            item.creator = creator
        if not item.tier and tier:
            item.tier = tier
        return
    results[level_id] = SearchResult(level_id, name, creator, tier, difficulty)


def search_by_name(name: str) -> list[SearchResult]:
    """使用名称从多个来源中搜索特定关卡，返回所有结果"""
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
                f"Find a result in GDDL: tier {getattr(level, 'Rating', None) or 'Na'}"
            )
    """
    # 2) AREDL exact match
    # logically useless because all rated demon should already be in GDDL
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
    """

    # 3) NLW exact match
    for level in Nlw.getlevelbyname(name):
        _add_search_result(
            results,
            int(level.id or 0),
            level.name,
            getattr(level, "creator", None),
            None,
        )
        logger.info(f"Find a result in {level.source}: " +
                    str(level.tier) or "Unknown" + " Tier")

    # 4) Platdata exact match
    plat_info = Platapi.getlevelbyname(name)
    if plat_info:
        _add_search_result(
            results,
            int(plat_info.id),
            plat_info.name,
            plat_info.creator,
            plat_info.tier,
            None,
        )

    if results:
        return list(results.values())

    # Fallback to gdapi exact match
    """
    # No point to do these fallback thing since theres already a gdsearch bot that is better than mine
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
    """调用gdapi获取一个关卡的基本信息"""
    gdlevel = get_level_by_id(level_id)
    if not gdlevel:
        return None
    return gdlevel

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.rule import Rule

from .format_message import _format_demon_image_text, _format_demon_message, _format_non_demon_message, _format_pemon_image_text, _format_pemon_message


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
    """处理用户对gdsearch的调用"""
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
        tier_str = f" t{result.tier}" if result.tier else ""
        msgstr += f"\n{i}. {result.name}{creator_str}{difficulty_str}{tier_str} (ID: {result.id})"
    msgstr += "\n输入序号以选中关卡,输入“结束”以中止搜索"
    await gdsearch.finish(msgstr)


@gdsearchselect.handle()
async def handle_choice(bot: Bot, event: Event) -> None:
    """处理用户对gdsearch返回多结果的回复"""
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
