"""轮盘已经下线，这里只剩三条命令。

蓝莓经济那套（buy / sell / ck / 奖池 / 捐赠）连同 zhua_api 插件一起删掉了，
剩下的两条 *map 和 *random 本来就跟蓝莓无关，单纯是随机工具，所以留着。
*roulette 保留成一块墓碑，免得老玩家发了没反应还以为 bot 坏了。
"""

import random

from nonebot import on_command
from nonebot.internal.adapter import Event, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from . import const
from .config import Config

# 不要 random.seed()：那是重置**全局** Mersenne Twister，guess/zhua/joy 共用
# 同一个 random，而且无参 seed 本来就是默认行为，纯属无意义的副作用。
# 另外 `const` 必须显式导入：下面 const.sjmap 能跑通只是因为导入子模块时
# importlib 会把它 setattr 到父包上，属于隐式副作用。

__plugin_meta__ = PluginMetadata(
    name="roulette",
    description="轮盘已下线，仅保留 *map / *random",
    usage="",
    config=Config,
)

roulette = on_command("roulette")
get_map = on_command("map")
rand_one = on_command("random")

RETIRED_MSG = (
    "你来到了那个轮盘原本所在的位置，只看到一块写着打烊的招牌。"
    "这个轮盘不会再转动了。\n"
    "（蓝莓系统和轮盘都已经下线，想随机抽个东西可以用 *random，"
    "随机来张地图用 *map）"
)


@roulette.handle()
async def handle_roulette() -> None:
    await roulette.finish(RETIRED_MSG)


@get_map.handle()
async def handle_map() -> None:
    await get_map.finish("Your map is: " + random.choice(const.sjmap))


@rand_one.handle()
async def handle_random(event: Event, arg: Message = CommandArg()) -> None:
    args = str(arg).split()
    if len(args) == 0:
        await rand_one.finish("请输入至少一个参数！")
    result = random.choice(args)
    await rand_one.finish(f"Your result is: {result}.")
