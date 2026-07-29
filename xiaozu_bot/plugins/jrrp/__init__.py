import datetime
import random

from nonebot import get_plugin_config, on_command
from nonebot.internal.adapter import Event
from nonebot.plugin import PluginMetadata

from xiaozu_bot.utils.adapter_compat import get_user_id
from xiaozu_bot.utils.json_storage import JsonRedis, plugin_storage

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="jrrp",
    description="",
    usage="",
    config=Config,
)

random.seed()
r = JsonRedis(plugin_storage(__file__))

config = get_plugin_config(Config)

jrrp = on_command("jrrp")


@jrrp.handle()
async def handle_function(event: Event):
    user_id = get_user_id(event)
    if r.get(f"jrrp_{user_id}") is not None and r.get(f"jrrp_{user_id}") != "True":
        rp = int(r.get(f"jrrp_{user_id}"))
        await jrrp.finish(f"今天已经抽过啦：{rp}/100，明天再来刷新。", at_sender=True)
    rp = random.randint(1, 100)
    if rp <= 1:
        append = "今日关键词：谨慎。看到确认按钮先停半秒。"
    elif rp <= 20:
        append = "运气暂时在排队，稳住就能少踩坑。"
    elif rp <= 40:
        append = "有点小颠簸，按计划来问题不大。"
    elif rp <= 60:
        append = "中规中矩的一天，适合稳稳推进。"
    elif rp <= 80:
        append = "状态不错，搁置的事可以捡起来了。"
    elif rp <= 99:
        append = "今天手气很旺，适合挑战一点新东西。"
    else:
        append = "满分！今天的好运额度看起来很充足。"
    d = datetime.datetime.now()
    delta = (23 - d.hour) * 3600 + (59 - d.minute) * 60 + (59 - d.second)
    r.set(f"jrrp_{user_id}", f"{rp}", ex=delta)
    await jrrp.finish(f"今日人品：{rp}/100。{append}")
