"""*gdsong 歌曲信息。"""

import asyncio

from nonebot import on_command
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg

from ..api.gdapi import get_song_info

gdsong = on_command("gdsong")


def parse_song_id(text: str) -> int | None:
    """Parse a user-supplied song id; None means it is not a number.

    Official songs are addressed by a negative id, so this keeps int()
    semantics instead of restricting the input to isdigit().
    """
    try:
        return int(text)
    except ValueError:
        return None


@gdsong.handle()
async def handle_gdsong(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    song_id = arg.extract_plain_text().strip()
    song_number = parse_song_id(song_id)
    if song_number is None:
        await gdsong.finish("请输入想要搜索的歌曲id")
    song = await asyncio.to_thread(get_song_info, song_number)
    if song is None or song.name is None:
        await gdsong.finish("没有找到对应的歌曲")
    song_basic_info = f"{song.name} By {song.artist_name} [{song_id}]\n"
    # await gdsong.send(str(song.to_dict()))
    if song.level_count:
        song_data = f"⬆️{song.level_count if song.level_count <= 200000 else '200K+'} ⭐{song.rated_count} ⬇️{song.downloads}\n"
    else:
        song_data = ""
    if song.usage_detail:
        song_usage_info = "Usage detail:\n"
        difficulty = ["Auto", "Easy", "Normal", "Hard", "Harder", "Insane", "Demon"]
        rarity = ["star-rate", "Featured", "Epic", "Legendary", "Mythic"]
        for i in range(5):
            if sum(song.usage_detail[i]) > 0:
                song_usage_info += f"{rarity[i]}: "
                for j in range(7):
                    if song.usage_detail[i][j]:
                        song_usage_info +=f"{difficulty[j]}*{song.usage_detail[i][j]} "
                song_usage_info += "\n"
    else:
        song_usage_info = "Usage detail info not found"

    user_info = song_basic_info + song_data + song_usage_info
    await gdsong.finish(user_info)
