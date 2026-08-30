"""*plat随机推关。"""

import asyncio

from nonebot import on_command
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg

from ..api.platapi import Platapi
from ..services.search import send_result

PLAT_TIER_MIN = 1
PLAT_TIER_MAX = 13

platrandom = on_command("plat随机推关")


@platrandom.handle()
async def handle_platrandom(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    """按 plat tier 随机挑一关；不传参数时从所有 plat 关卡中挑选。"""
    args = arg.extract_plain_text().strip().split()
    if len(args) > 1:
        await platrandom.finish("用法：*plat随机推关 [tier]，tier 范围是 1-13")

    tier: int | None = None
    if args:
        if not args[0].isdigit():
            await platrandom.finish("tier 要是 1-13 之间的整数")
        tier = int(args[0])
        if not PLAT_TIER_MIN <= tier <= PLAT_TIER_MAX:
            await platrandom.finish("tier 要在 1-13 之间")

    result = await asyncio.to_thread(Platapi.getrandomlevelbytier, tier)
    if result is None:
        if tier is None:
            await platrandom.finish("没有找到可用的 plat 推关")
        await platrandom.finish(f"没有找到 tier {tier} 的 plat 推关")

    await send_result(bot, event, int(result.id))
    await platrandom.finish()
