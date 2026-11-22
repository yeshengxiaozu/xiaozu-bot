"""*aredlrank: render the level sitting at a given AREDL rank.

The rank index and its upper bound live in api/aredlapi.py, so the handler
only has to parse the argument, reject out-of-range input, and hand the level
id to the shared renderer. Two arguments are treated as inclusive bounds and
collapsed to a random rank before the usual lookup.
"""

import asyncio
import random

from nonebot import logger, on_command
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg

from ..api.aredlapi import Aredl
from ..services.search import send_result

USAGE = (
    "用法：*aredlrank <排名> 或 *aredlrank <边界1> <边界2>（顺序不限），"
    "比如 *aredlrank 1 / *aredlrank 1 10"
)


aredlrank = on_command("aredlrank")


def resolve_rank(text: str) -> tuple[int | None, str]:
    """Turn the raw argument into a level id.

    Returns (level_id, error message); level_id is None when the lookup failed.
    A single argument is used as-is; two arguments define inclusive bounds
    for a random rank, regardless of their order.
    """
    parts = text.split()
    if not parts or len(parts) > 2:
        return None, USAGE

    numbers: list[int] = []
    for part in parts:
        try:
            numbers.append(int(part))
        except ValueError:
            return None, f"排名要是个整数，你写的是「{part}」\n{USAGE}"

    lower, upper = numbers[0], numbers[-1]
    if lower > upper:
        lower, upper = upper, lower

    max_position = Aredl.getmaxposition()
    if max_position <= 0:
        logger.error("[aredlrank] AREDL 数据为空，无法按排名查询")
        return None, "AREDL 数据还没准备好，等会再试"

    if not 1 <= lower <= upper <= max_position:
        if lower == upper:
            return None, f"排名要在 1-{max_position} 之间，你写的是 {lower}"
        return None, f"排名范围要在 1-{max_position} 之间，你写的是 {lower}-{upper}"

    position = lower if len(numbers) == 1 else random.randint(lower, upper)
    level = Aredl.getlevelbyposition(position)
    if level is None:
        # In range but absent from the index: the cached list has a gap.
        logger.error(
            f"[aredlrank] 排名 {position} 在 AREDL 索引里不存在"
            f"（最大排名 {max_position}）"
        )
        return None, f"AREDL 上排名 {position} 查不到关卡，数据可能刚更新，等会再试"

    level_id = int(level.level_id)
    if level_id <= 0:
        logger.error(
            f"[aredlrank] 排名 {position}（{level.name}）没有可用的 level_id：{level_id}"
        )
        return None, f"AREDL 上排名 {position} 的关卡没有有效的 GD 关卡 id，出不了图"

    return level_id, ""


@aredlrank.handle()
async def handle_aredlrank(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    level_id, err = await asyncio.to_thread(
        resolve_rank, arg.extract_plain_text()
    )
    if level_id is None:
        await aredlrank.finish(err)
    await send_result(bot, event, level_id)
    await aredlrank.finish()
