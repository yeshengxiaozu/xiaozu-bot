from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot import on_command
from nonebot import require
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters import Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from .const import *
from .data import *
import random
import redis

from .config import Config
manager = require("zhua_api").berry_manager
msg = require("zhua_api").msg_api()

random.seed()
r = redis.Redis(host='localhost', port=6379, decode_responses=True)  
data.pool = int(r.get('roulette_pool'))

__plugin_meta__ = PluginMetadata(
    name="roulette",
    description="",
    usage="",
    config=Config,
)

config = get_plugin_config(Config)

roulette = on_command("roulette")
get_map = on_command("map")
get_pool = on_command("roulette_pool")
donate = on_command("roulette_donate")
set_pool = on_command("roulette_set_pool",permission=SUPERUSER)
roulette_fix = on_command("roulette_fix",permission=SUPERUSER)
rand_one = on_command("random")

@roulette.handle()
async def handle_function(event: GroupMessageEvent):
    if event.group_id != msg.group_id:
        await roulette.finish("这个功能无法在伯特群外使用！考虑使用*map作为替代品。")
    id = event.user_id
    if manager.getCoins(id)<10:
        await msg.send_at_emoji(id,"You don't have enough blueberries. 10 needed.\nUse *buy to convert strawberries.","144")
        await roulette.finish()
    if r.get(f"roulette_status_{id}") == "pending":
        await roulette.finish("You have a pending roll. Please wait.",at_sender = True)
    if r.get(f"roulette_status_{id}") == "waiting":
        await roulette.finish("Roulette have 3s cd. P-l-e-a-s-e w-a-i-t.",at_sender = True)
    r.set(f"roulette_status_{id}","pending")
    manager.setCoins(id,manager.getCoins(id)-10)
    manager.setCoins(event.self_id,manager.getCoins(event.self_id)+1)
    data.pool+=10
    r.set('roulette_pool', str(data.pool))
    map_result = random.choice(const.sjmap)
    prize_available = map_result in prize.keys()
    if prize_available:
        if type(prize[map_result]) == type("str"):
            prizewined = int(prize[map_result])
        else:
            prizewined = int(data.pool * prize[map_result])
        manager.setCoins(id,manager.getCoins(id)+prizewined)
        data.pool -= prizewined
        data.pool = max(data.pool,202)
        r.set('roulette_pool', str(data.pool))
        r.set(f"roulette_status_{id}","waiting",ex=3)
        await msg.send_at_emoji(id,f"Your result is: {map_result}.\nCongradulated for winning {prizewined} blueberries!\n{data.pool} blueberries left in the pool.","144")
        await roulette.finish()
    else:
        r.set(f"roulette_status_{id}","waiting",ex=3)
        await roulette.finish(f"Your result is: {map_result}.\n{data.pool} blueberries left in the pool.",at_sender = True)

@get_pool.handle()
async def handle_function():
    await get_pool.finish("Pool: " + str(data.pool))

@set_pool.handle()
async def handle_function(args: Message = CommandArg()):
    data.pool = int(str(args))
    await set_pool.finish("Pool: " + str(data.pool))

@get_map.handle()
async def handle_function():
    await get_map.finish("Your map is: " + random.choice(const.sjmap))

@donate.handle()
async def handle_function(event: GroupMessageEvent, arg: Message = CommandArg()):
    if (event.group_id != msg.group_id):
        await donate.finish("这个功能依赖和其他伯特的通信，无法在伯特群外使用！")
    args = str(arg).lower().split()
    id = event.user_id
    if len(args) == 1 and args[0] == 'all':
        if manager.getCoins(id) == 0:
            await donate.finish("你想干什么，展示你空空如也的蓝莓钱包吗？",at_sender = True)
        else:
            num = manager.getCoins(id)
    elif len(args) != 1 or not args[0].isdecimal():
        await msg.emoji_like(event.message_id,"424")
        await donate.finish()
    elif int(args[0])<=0:
        await msg.emoji_like(event.message_id,"424")
        await donate.finish()
    elif int(args[0])>manager.getCoins(id):
        await donate.finish("你没有这么多蓝莓！",at_sender = True)
    else :
        num = int(args[0])
    data.pool += num
    r.set('roulette_pool', str(data.pool))
    manager.setCoins(id,manager.getCoins(id)-num)
    await msg.send_at_emoji(id,f"你往池子里捐赠{num}蓝莓！\nPool: {data.pool}","144")
    await donate.finish()

@roulette_fix.handle()
async def handle_function():
    for i in r.keys(pattern='roulette_status*'):
        r.set(i,"fixed",ex=1)
    await roulette_fix.finish("fixed.")

@rand_one.handle()
async def handle_function(event: GroupMessageEvent, arg: Message = CommandArg()):
    args = str(arg).split()
    if len(args) == 0:
        await rand_one.finish(f"请输入至少一个参数！")
    result = random.choice(args)
    await rand_one.finish(f"Your result is: {result}.")
