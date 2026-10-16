"""*gduser 玩家信息。"""

import asyncio

from nonebot import on_command
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg

from ..api.gdapi import get_song_info

gdsong = on_command("gdsong")


@gdsong.handle()
async def handle_gduser(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    song_id = arg.extract_plain_text().strip()
    if not song_id:
        await gdsong.finish("请输入想要搜索的歌曲id")
    song = await asyncio.to_thread(get_song_info, int(song_id))
    if song is None:
        await gdsong.finish("没有找到对应的歌曲")
    song_basic_info = f"{song.name} By {song.artist_name} [{song_id}]"
    song_data = ""
    if song.level_count:
        song_data += f"⬆️{song.level_count if song.level_count <= 200000 else '200K+'}"
    if song.rated_count:
        song_data += f"⭐{song.rated_count}"
    if song.downloads:
        song_data += f"↓{song.downloads}"
    if song_data:
        song_data += '\n'
    if song.usage_detail:
        song_usage_info = "Usage detail:"
        DIFFICULTY = ["Auto", "Easy", "Normal", "Hard", "Harder", "Insane", "Demon"]
        RARITY = ["", "Featured ", "Epic ", "Legendary ", "Mythic "]
        for i in range(0,5):
            for j in range(0,7):
                if song.usage_detail[i][j]:
                    song_usage_info +=f"\n{RARITY[i]}{DIFFICULTY[j]} * {song.usage_detail[i][j]}"
    else:
        song_usage_info = "Usage detail info not found"

    user_info = song_basic_info + "\n" + song_usage_info
    await gdsong.finish(user_info)
