from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot import on_command, require
import datetime
import random
import redis

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="jrrp",
    description="",
    usage="",
    config=Config,
)

random.seed()
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
berry_manager = require("zhua_api").berry_manager

config = get_plugin_config(Config)

jrrp = on_command("jrrp")

@jrrp.handle()
async def handle_function(event: GroupMessageEvent):
    id = event.user_id
    if (r.get(f"jrrp_{id}")!=None and r.get(f"jrrp_{id}")!="True"):
        rp = int(r.get(f"jrrp_{id}"))
        await jrrp.finish(f"你的今日人品是{rp}！（你不是已经知道了吗）",at_sender = True)
    rp = random.randint(1,100)
    append = "后面我还没想好"
    if (rp <= 1):
        append = "要不要挑战一下祈愿出1级？"
    elif (rp <= 20):
        append = "也许今天不适合玩会boom的道具……"
    elif (rp <= 40):
        append = "可能在其他bot那里的rp会高一些……"
    elif (rp <= 60):
        append = "平平淡淡才是真。"
    elif (rp <= 80):
        append = "打什么蔚蓝，快来玩zhuamadeline"
    elif (rp <= 99):
        append = "似乎浪费了一次五级不boom的机会(bushi)"
    else:
        append = "wow！你裸抓五级/藏品的运气用在这了！"
    d = datetime.datetime.now()
    delta = (23-d.hour)*3600 + (59-d.minute)*60 + (59-d.second)
    r.set(f"jrrp_{id}",f"{rp}",ex=delta)
    if isinstance(event,GroupMessageEvent) and berry_manager.is_berrygroup(event):
        berry_manager.addCoins(event.user_id,rp)
        await jrrp.finish(f"你的今日人品是……{rp}！" + append + f"\n你获得了{rp}蓝莓",at_sender = True)
    else:
        await jrrp.finish(f"你的今日人品是……{rp}！")