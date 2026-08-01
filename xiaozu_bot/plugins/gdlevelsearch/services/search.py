"""?????????????gdsearch / gdrandom / dailydemon ????"""

from dataclasses import dataclass
from io import BytesIO

import requests
from nonebot import logger
from nonebot.internal.adapter import Bot, Event

from xiaozu_bot.utils.adapter_compat import send_image

from ..api.gdapi import GDLevel, get_level_by_id
from ..api.gddlapi import Gddl
from ..api.nlwapi import Nlw
from ..api.platapi import Platapi
from ..render.draw import create_image_from_gdlevel


# fallback function since I should already get it using gdapi if this get called we f*cked up
def get_creator(level_id: int) -> str | None:
    """??GDhistory API?????????????api????????????"""
    logger.warning("get_creator got called: " + str(level_id))
    try:
        data = requests.get(
            f"https://history.geometrydash.eu/api/v1/level/{level_id}", timeout=10
        )
        return data.json()["cache_username"]
    except Exception:
        return None


# nice little function that extract exactly what i need
def get_difficulty(level_id: int) -> str | None:
    """??GD API??????????????api???????????"""
    logger.info("get_difficulty got called: " + str(level_id))
    try:
        data = get_level_by_id(level_id)
    except Exception:
        return None
    return data.difficulty_label() if data else None


@dataclass
class SearchResult:
    """??????????????????????"""

    id: int
    name: str
    creator: str | None = None
    tier: str | None = None
    difficulty: str | None = None


def _add_search_result(
    results: dict[int, SearchResult],
    level_id: int,
    name: str,
    creator: str | None = None,
    tier: str | None = None,
    difficulty: str | None = None,
):
    """?results???????????"""
    if level_id is None:
        return
    if level_id in results:
        item = results[level_id]
        if not item.creator and creator:
            item.creator = creator
        if not item.tier and tier:
            item.tier = tier
        # difficulty ?????????????????GDDL????????
        # ???????????????? difficulty ????? *gdsearch
        # ????? gdapi?
        if not item.difficulty and difficulty:
            item.difficulty = difficulty
        return
    results[level_id] = SearchResult(level_id, name, creator, tier, difficulty)


def search_by_name(name: str) -> list[SearchResult]:
    """???????????????????????"""
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
    # ???? AREDL????? rated demon ??????? GDDL ??

    # 3) NLW exact match
    for level in Nlw.getlevelbyname(name):
        _add_search_result(
            results,
            int(level.id or 0),
            level.name,
            getattr(level, "creator", None),
            None,
        )
        # ??? "..." + str(tier) or "Unknown" + " Tier"?+ ? or ?????
        # ???????or ????????tier ????????? "None"
        logger.info(f"Find a result in {level.source}: {level.tier or 'Unknown'} Tier")

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

    # ?? gdapi ????????????????? gdsearch bot ?
    return list(results.values())


def getlevelinfo(level_id: int) -> GDLevel | None:
    """??gdapi???????????"""
    gdlevel = get_level_by_id(level_id)
    if not gdlevel:
        return None
    return gdlevel


# ????????pemon / demon / non-demon ???????????????????????
async def send_result(bot: Bot, event: Event, level_info: GDLevel) -> None:
    image = await create_image_from_gdlevel(level_info)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    await send_image(bot, event, buffer)


def _clear_all_sessions(event: Event) -> None:
    """??????????????? on_message ??????

    ?????? import ?????????????????????????
    """
    from ..commands.fullsearch import _drop_fullsearch
    from ..commands.ratings import _drop_ratings
    from ..commands.search import search_cache, timeout_tasks

    _drop_fullsearch(event.get_session_id())
    _drop_ratings(event.get_session_id())
    user_id = str(event.get_user_id())
    search_cache.pop(user_id, None)
    task = timeout_tasks.pop(user_id, None)
    if task:
        task.cancel()
