"""*gd随机推关。"""

import asyncio
from dataclasses import dataclass

from nonebot import on_command
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg

from xiaozu_bot.utils.args import Flag
from xiaozu_bot.utils.gd_args import (
    ArgError,
    parse_difficulty,
    parse_flags,
    parse_number,
)

from ..api.gddlapi import Gddl
from ..services.search import getlevelinfo, send_result  # noqa: F401

gdrandom = on_command("gd随机推关")

USAGE = """用法：*gd随机推关 tier低 [tier高] [enj低] [enj高] [-d 难度]
  tier 是 1-39，enj 是 0-10，tier高 和两个 enj 都可以不写
  -d 难度　只推这个难度：1-5 或 easy/medium/hard/insane/extreme
  例：*gd随机推关 15 20 7 —— 15-20 tier、enjoyment 7 以上
  例：*gd随机推关 15 20 -d extreme —— 再加一道难度限制"""

RANDOM_FLAGS = (Flag(("-d",), True, parse_difficulty),)



@dataclass
class RandomQuery:
    """一次 gd随机推关 的查询条件"""

    tier_low: int
    tier_high: int = -1          # -1 means "same tier as tier_low"
    enjoyment_min: float | None = None
    enjoyment_max: float | None = None
    difficulty: str | None = None  # GDDL search enum value; None means no filter


def parse_args(text: str) -> RandomQuery:
    """解析 *gd随机推关 后面那串东西。

    `-d` 吃掉它后面那个词当难度，位置随意；其余的词都是位置参数：tier 下限、
    tier 上限、enjoyment 下限、enjoyment 上限。写反了会自动交换，不报错。
    """
    flags, positional = parse_flags(text.split(), RANDOM_FLAGS, USAGE)

    if not positional:
        raise ArgError(USAGE)

    tier_low = int(parse_number(positional[0], "tier", 1, 39))
    tier_high = (
        int(parse_number(positional[1], "tier", 1, 39)) if len(positional) > 1 else -1
    )
    enjoyment_min = (
        parse_number(positional[2], "enjoyment", 0, 10) if len(positional) > 2 else None
    )
    enjoyment_max = (
        parse_number(positional[3], "enjoyment", 0, 10) if len(positional) > 3 else None
    )

    if tier_high != -1 and tier_high < tier_low:
        tier_low, tier_high = tier_high, tier_low
    if (
        enjoyment_min is not None
        and enjoyment_max is not None
        and enjoyment_max < enjoyment_min
    ):
        enjoyment_min, enjoyment_max = enjoyment_max, enjoyment_min

    return RandomQuery(
        tier_low=tier_low,
        tier_high=tier_high,
        enjoyment_min=enjoyment_min,
        enjoyment_max=enjoyment_max,
        difficulty=flags.get("-d"),
    )


@gdrandom.handle()
async def handle_gdrandom(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    """*gd随机推关 tier低 [tier高] [enj低] [enj高] [-d 难度]"""
    try:
        query = parse_args(arg.extract_plain_text())
    except ArgError as e:
        await gdrandom.finish(str(e))

    result = await asyncio.to_thread(
        Gddl.getrandomlevelbytier,
        query.tier_low,
        query.tier_high,
        query.enjoyment_min,
        query.enjoyment_max,
        difficulty=query.difficulty,
    )
    if not result:
        await gdrandom.finish("没有找到符合条件的关卡，把条件放宽点试试")

    await send_result(bot, event, int(result.ID))
    await gdrandom.finish()
