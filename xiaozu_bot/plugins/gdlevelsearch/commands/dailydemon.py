"""*dailydemon：每天一关，从 GDDL 里按固定条件挑。

选出来之后要**落地存下来**，不能只靠「日期当种子算下标」：
GDDL 的数据是会变的（有人提交评分，关卡的 enjoyment / tier / 提交数就动了，
符合条件的总数跟着变），实测几分钟之内 total 就能从 2380 变成 2381。
总数一变，同一个下标指向的关卡就变了，「一天之内不变」的保证当场失效。

所以流程是：当天已经存过 -> 直接用存的那个；没存过 -> 挑一个并存下来。
存的是关卡 id，按日期做 key，过期时间给两天，旧的自己会清掉。
"""

import asyncio
import datetime
import random

from nonebot import logger, on_command
from nonebot.internal.adapter import Bot, Event

from xiaozu_bot.utils.json_storage import JsonRedis

from ..api.gddlapi import Gddl
from ..services.search import send_result

# 挑关卡的条件
TIER_MIN = 1
TIER_MAX = 9
ENJOYMENT_MIN = 7
SUBMISSION_MIN = 10

FILTERS = {
    "minRating": TIER_MIN,
    "maxRating": TIER_MAX,
    "minEnjoyment": ENJOYMENT_MIN,
    "minSubmissionCount": SUBMISSION_MIN,
}

# 和别的插件一样放自己的 data 目录，这个目录的 *.json 已经在 .gitignore 里了
from ..paths import storage

r = JsonRedis(storage())

KEY_PREFIX = "dailydemon_"
# 存两天，跨天之后旧的自动过期，不用手动清
KEEP_SECONDS = 2 * 24 * 3600

# 最近挑过的关卡 id，用来避免短期内重样。
# 池子里两千多关，但新 demon 评级之后会不断掉进这个区间，池子是一直在变的，
# 光靠日期种子并不能保证不撞车，所以显式记一份最近的。
RECENT_KEY = "dailydemon_recent"
RECENT_KEEP = 30      # 记最近 30 次
MAX_REROLL = 5        # 撞车最多再挑几次，每次都是一个 HTTP 请求，别太多


def _today(today: datetime.date | None = None) -> datetime.date:
    return today or datetime.datetime.now().astimezone().date()


def _key(day: datetime.date) -> str:
    return f"{KEY_PREFIX}{day.isoformat()}"


def today_seed(today: datetime.date | None = None) -> int:
    """拿日期当种子，比如 2026-07-26 -> 20260726"""
    day = _today(today)
    return day.year * 10000 + day.month * 100 + day.day


def pick_index(total: int, today: datetime.date | None = None) -> int:
    """按日期在 [0, total) 里定出一个下标"""
    return random.Random(today_seed(today)).randrange(total)


def get_recent() -> list[int]:
    """最近挑过的关卡 id"""
    value = r.get(RECENT_KEY)
    if not isinstance(value, list):
        return []
    return [int(x) for x in value if str(x).lstrip("-").isdigit()]


def remember(level_id: int) -> None:
    """把这次挑的记进最近列表，只留最后 RECENT_KEEP 条"""
    recent = [x for x in get_recent() if x != level_id]
    recent.append(level_id)
    r.set(RECENT_KEY, recent[-RECENT_KEEP:])


def get_cached_id(today: datetime.date | None = None) -> int | None:
    """看今天是不是已经挑过了"""
    cached = r.get(_key(_today(today)))
    if cached is None:
        return None
    try:
        return int(cached)
    except (TypeError, ValueError):
        logger.warning(f"[dailydemon] 存的 id 不是数字：{cached!r}，当没存过处理")
        return None


def get_daily_demon(
    today: datetime.date | None = None,
) -> tuple[int | None, int, str]:
    """取今天这一关。

    返回 (关卡, 符合条件的总数, 出错时的提示)。
    总数只在当天第一次挑的时候才有意义，之后是 0。
    """
    day = _today(today)

    # 今天已经挑过了就直接用，别再受 GDDL 数据变动的影响
    cached_id = get_cached_id(day)
    if cached_id is not None:
        return cached_id, 0, ""

    head = Gddl.searchlevels(page=0, limit=1, sort="ID", **FILTERS)
    if head is None:
        return None, 0, "GDDL 那边没响应，等会再试试"

    total = head.get("total", 0)
    if not total:
        return None, 0, "错误：GDDL返回的信息显示存在0个符合条件的关卡（显然不应该发生）"

    recent = get_recent()
    rng = random.Random(today_seed(day))
    level = None
    # 撞到最近推过的就再挑一个。池子有两千多关，正常一次就过，
    # 实在挑不出不重样的就用最后那个，别死循环。
    for attempt in range(MAX_REROLL):
        index = rng.randrange(total)
        candidate = Gddl.getlevelbyindex(index, **FILTERS)
        if candidate is None:
            continue
        level = candidate
        if candidate.ID not in recent:
            break
        logger.info(
            f"[dailydemon] 第 {index} 个是最近推过的 {candidate.ID}，"
            f"再挑一次（第 {attempt + 1} 次）"
        )

    if level is None:
        return None, total, "取今日关卡失败，等会再试试"

    r.set(_key(day), str(level.ID), ex=KEEP_SECONDS)
    remember(level.ID)
    logger.info(f"[dailydemon] {day} 定为 {level.Name}（ID {level.ID}），已存下")
    return level.ID, total, ""


def describe_conditions() -> str:
    return (
        f"tier {TIER_MIN}-{TIER_MAX}、enjoyment {ENJOYMENT_MIN}+、"
        f"提交数 {SUBMISSION_MIN}+"
    )


# ----------------------------------------------------------------- dailydemon
# 每天一关，条件写死在上面的逻辑里，按日期定死所以一天之内不会变。

dailydemon = on_command("dailydemon")


@dailydemon.handle()
async def handle_dailydemon(bot: Bot, event: Event) -> None:
    level_id, _total, err = await asyncio.to_thread(get_daily_demon)
    if level_id is None:
        await dailydemon.finish(err)
    await send_result(bot, event, int(level_id))
    await dailydemon.finish()
