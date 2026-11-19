"""*aredlrank: render the level sitting at a given AREDL rank.

The rank index and its upper bound live in api/aredlapi.py, so the handler
only has to parse the argument, reject out-of-range input, and hand the level
id to the shared renderer.
"""

import asyncio

from nonebot import logger, on_command
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg

from ..api.aredlapi import Aredl
from ..services.search import send_result

USAGE = "用法：*aredlrank <排名>，比如 *aredlrank 1"


aredlrank = on_command("aredlrank")


def resolve_rank(text: str) -> tuple[int | None, str]:
    """Turn the raw argument into a level id.

    Returns (level_id, error message); level_id is None when the lookup failed.
    """
    raw = text.strip()
    if not raw:
        return None, USAGE

    try:
        position = int(raw)
    except ValueError:
        return None, f"排名要是个整数，你写的是「{raw}」\n{USAGE}"

    max_position = Aredl.getmaxposition()
    if max_position <= 0:
        logger.error("[aredlrank] AREDL 数据为空，无法按排名查询")
        return None, "AREDL 数据还没准备好，等会再试"

    if not 1 <= position <= max_position:
        return None, f"排名要在 1-{max_position} 之间，你写的是 {position}"

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
