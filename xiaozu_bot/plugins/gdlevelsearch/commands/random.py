"""*gd随机推关。"""

import asyncio

from nonebot import on_command
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg

from ..api.gdapi import GDAPIUnavailable
from ..api.gddlapi import Gddl
from ..services.search import getlevelinfo, send_result

gdrandom = on_command("gd随机推关")


@gdrandom.handle()
async def handle_gdrandom(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    """*gd随机推关 tier低 [tier高] [enj低] [enj高]"""
    args = arg.extract_plain_text().strip().split()
    if len(args) < 1:
        await gdrandom.finish(
            "用法：*gd随机推关 tier低 [tier高] [enj低] [enj高]\n"
            "tier 是 1-39，enj 是 0-10，后面三个都可以不写\n"
            "例：*gd随机推关 15 20 7 —— 15-20 tier、enjoyment 7 以上"
        )

    def _num(text: str, name: str, low: float, high: float) -> float:
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{name} 要是个数字，你写的是「{text}」") from None
        if not low <= value <= high:
            raise ValueError(f"{name} 要在 {low:g}-{high:g} 之间，你写的是 {value:g}")
        return value

    try:
        tier_low = int(_num(args[0], "tier", 1, 39))
        tier_high = int(_num(args[1], "tier", 1, 39)) if len(args) > 1 else -1
        enj_min = _num(args[2], "enjoyment", 0, 10) if len(args) > 2 else None
        enj_max = _num(args[3], "enjoyment", 0, 10) if len(args) > 3 else None
    except ValueError as e:
        await gdrandom.finish(str(e))

    if tier_high != -1 and tier_high < tier_low:
        tier_low, tier_high = tier_high, tier_low
    if enj_min is not None and enj_max is not None and enj_max < enj_min:
        enj_min, enj_max = enj_max, enj_min

    result = await asyncio.to_thread(
        Gddl.getrandomlevelbytier, tier_low, tier_high, enj_min, enj_max
    )
    if not result:
        await gdrandom.finish("没有找到符合条件的关卡，把条件放宽点试试")

    try:
        level = await asyncio.to_thread(getlevelinfo, result.ID)
    except GDAPIUnavailable:
        await gdrandom.finish("GD 关卡服务器暂时无法访问，请稍后重试")
    if level:
        await send_result(bot, event, level.level_id)
    else:
        await gdrandom.finish("发生未知错误。相关id: " + str(result.ID))
    await gdrandom.finish()
