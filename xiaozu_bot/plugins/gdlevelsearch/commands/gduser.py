"""*gduser 玩家信息。"""

import asyncio

from nonebot import on_command
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg

from ..api.gdapi import get_user_by_name

gduser = on_command("gduser")


@gduser.handle()
async def handle_gduser(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    name = arg.extract_plain_text().strip()
    if not name:
        await gduser.finish("请输入想要搜索的用户名")
    user = await asyncio.to_thread(get_user_by_name, name)
    if not user:
        await gduser.finish("没有找到对应的用户")
    user_basic_info = f"{user.user_name}\n{user.stars}⭐ {user.moons}🌙 {user.demons_count}👿 {str(user.creator_points) + '🔧' if user.creator_points else ''}"
    user_classic_nondemon = (
        f"\nClassic: {user.classic_levels[0]}🤖 {user.classic_levels[1]}💙 {user.classic_levels[2]}💚 {user.classic_levels[3]}💛 {user.classic_levels[4]}🧡 {user.classic_levels[5]}💜;\n{user.classic_levels[6]} Daily; {user.classic_levels[7]} Gauntlet"
        if user.classic_levels
        else ""
    )
    user_plat_nondemon = (
        f"\nPlatformer: {user.platformer_levels[0]}🤖 {user.platformer_levels[1]}💙 {user.platformer_levels[2]}💚 {user.platformer_levels[3]}💛 {user.platformer_levels[4]}🧡 {user.platformer_levels[5]}💜"
        if user.platformer_levels
        else ""
    )
    user_demon = (
        f"\nClassic Demons: {user.demons_breakdown[0]} / {user.demons_breakdown[1]} / {user.demons_breakdown[2]} / {user.demons_breakdown[3]} / {user.demons_breakdown[4]};\n{user.demons_breakdown[10]} Weekly; {user.demons_breakdown[11]} Gauntlet\n"
        + f"Platformer Demons: {user.demons_breakdown[5]} / {user.demons_breakdown[6]} / {user.demons_breakdown[7]} / {user.demons_breakdown[8]} / {user.demons_breakdown[9]}"
        if user.demons_breakdown
        else ""
    )
    user_info = user_basic_info + user_classic_nondemon + user_plat_nondemon + user_demon
    await gduser.finish(user_info)
