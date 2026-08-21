"""?????????????gdsearch / gdrandom / dailydemon ????"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from io import BytesIO

from nonebot import logger
from nonebot.internal.adapter import Bot, Event

from xiaozu_bot.utils.adapter_compat import send_image

from ..api.gdapi import GDLevel, get_level_by_id
from ..api.gddlapi import Gddl, GDDLSearchEntry
from ..api.http import request as http_request
from ..api.nlwapi import Nlw
from ..api.platapi import Platapi
from ..constants import SEARCH_MAX_WORKERS, SOURCE_LOOKUP_TIMEOUT
from ..render.draw import create_image_from_gdlevel


# fallback function since I should already get it using gdapi if this get called we f*cked up
def get_creator(level_id: int) -> str | None:
    """try to ge the creator name from a backup source"""
    logger.warning("get_creator got called: " + str(level_id))
    try:
        data = http_request(
            "GET",
            f"https://history.geometrydash.eu/api/v1/level/{level_id}",
            timeout=10,
        )
        return data.json()["cache_username"]
    except Exception:
        return None


# nice little function that extract exactly what i need
def get_difficulty(level_id: int) -> str | None:
    """directly fetch level difficulty from gdapi"""
    logger.info("get_difficulty got called: " + str(level_id))
    try:
        data = get_level_by_id(level_id)
    except Exception:
        return None
    return data.difficulty_label() if data else None


@dataclass
class SearchResult:
    id: int
    name: str
    creator: str | None = None
    tier: str | None = None
    difficulty: str | None = None


def _coerce_level_id(value: object) -> int | None:
    """Return a usable source ID without letting malformed rows stop search."""
    try:
        level_id = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return None
    return level_id if level_id > 0 else None


def _add_search_result(
    results: dict[int, SearchResult],
    level_id: int,
    name: str,
    creator: str | None = None,
    tier: str | None = None,
    difficulty: str | None = None,
):
    """add results to the lists"""
    level_id = _coerce_level_id(level_id)
    if level_id is None:
        return
    if level_id in results:
        item = results[level_id]
        if not item.creator and creator:
            item.creator = creator
        if not item.tier and tier:
            item.tier = tier
        if not item.difficulty and difficulty:
            item.difficulty = difficulty
        return
    results[level_id] = SearchResult(level_id, name, creator, tier, difficulty)


_SEARCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=SEARCH_MAX_WORKERS,
    thread_name_prefix="gdlevelsearch",
)


def _collect_result(future, source: str, name: str):
    try:
        return future.result()
    except Exception as exc:
        logger.warning(f"{source.upper()} name lookup failed for {name!r}: {exc}")
        return None


def _lookup_sources(name: str) -> dict[str, object | None]:
    """Query all optional indexes in parallel with one shared deadline.

    A provider that raises, returns nothing, or exceeds the deadline only
    contributes an empty result; the other providers still answer.
    """
    futures = {
        "gddl": _SEARCH_EXECUTOR.submit(Gddl.getlevelsbyname, name),
        "nlw": _SEARCH_EXECUTOR.submit(Nlw.getlevelbyname, name),
        "plat": _SEARCH_EXECUTOR.submit(Platapi.getlevelbyname, name),
    }
    source_by_future = {future: source for source, future in futures.items()}
    values: dict[str, object | None] = {}
    try:
        completed = as_completed(futures.values(), timeout=SOURCE_LOOKUP_TIMEOUT)
        for future in completed:
            source = source_by_future[future]
            values[source] = _collect_result(future, source, name)
    except FuturesTimeoutError:
        for source, future in futures.items():
            if not future.done():
                logger.warning(
                    f"{source.upper()} name lookup timed out after "
                    f"{SOURCE_LOOKUP_TIMEOUT}s for {name!r}"
                )
                # Queued work can be dropped immediately; a task already
                # running keeps unwinding in the background.
                future.cancel()
    # Futures that finished just after the deadline still get consumed so a
    # late but healthy provider is not silently discarded.
    for source, future in futures.items():
        if source not in values and future.done():
            values[source] = _collect_result(future, source, name)
    return values


def search_by_name(name: str) -> list[SearchResult]:
    normalized = name.strip().lower()
    results: dict[int, SearchResult] = {}

    source_values = _lookup_sources(name)

    # 1) GDDL exact match. Each source is best-effort: a transient failure in
    # one provider must not hide matches available in the other providers.
    gddl_candidates = source_values.get("gddl") or []
    for level in gddl_candidates:
        if not level:
            continue
        if not isinstance(level, GDDLSearchEntry):
            logger.error(f"level isnt search entry:{getattr(level,'id','None')}")
            continue
        source_id = _coerce_level_id(getattr(level, "ID", None))
        if source_id is None:
            continue
        if getattr(level, "Name", "").strip().lower() == normalized:
            _add_search_result(
                results,
                source_id,
                level.Name,
                level.PublisherName,
                str(round(level.Rating, 2)) if level.Rating else None,
                level.Difficulty + (" Pemon" if level.is_pemon() else " Demon"),
            )
            logger.info(
                f"Find a result in GDDL: tier {getattr(level, 'Rating', None) or 'Na'}"
            )
    # 2) everything in AREDL is included in GDDL so nah

    # 3) NLW exact match
    nlw_candidates = source_values.get("nlw") or []
    for level in nlw_candidates:
        source_id = _coerce_level_id(getattr(level, "id", None))
        if source_id is None:
            continue
        _add_search_result(
            results,
            source_id,
            level.name,
            getattr(level, "creator", None),
            None,
        )
        logger.info(f"Find a result in {level.source}: {level.tier or 'Unknown'} Tier")

    # 4) Platdata exact match
    plat_info = source_values.get("plat")
    plat_id = _coerce_level_id(getattr(plat_info, "id", None)) if plat_info else None
    if plat_info and plat_id is not None:
        _add_search_result(
            results,
            plat_id,
            plat_info.name,
            plat_info.creator,
            plat_info.tier,
            None,
        )

    return list(results.values())


def getlevelinfo(level_id: int) -> GDLevel | None:
    """query gdapi"""
    gdlevel = get_level_by_id(level_id)
    if not gdlevel:
        return None
    return gdlevel


async def send_result(bot: Bot, event: Event, level_id: int) -> None:
    image = await create_image_from_gdlevel(level_id)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    await send_image(bot, event, buffer)


def _clear_all_sessions(event: Event) -> None:
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
