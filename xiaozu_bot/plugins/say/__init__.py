from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import PrivateMessageEvent
from nonebot.adapters.onebot.v11 import Message, GroupMessageEvent
from nonebot.params import CommandArg
from nonebot import on_command
from nonebot.rule import Rule
from nonebot.permission import SUPERUSER
from nonebot import require
import requests
from pathlib import Path
import os

from .config import Config
msg = require("zhua_api").msg_api()

__plugin_meta__ = PluginMetadata(
    name="say",
    description="",
    usage="",
    config=Config,
)

def json_group_audio(group_id: int, path: str) -> set:
    return {
        'group_id': group_id,
        'message': [{
            'type': 'record',
            'data': {
                'file': path
                    }
        }]}

def json_private_audio(user_id: int, path: str) -> set:
    return {
        'user_id': user_id,
        'message': [{
            'type': 'record',
            'data': {
                'file': path
                    }
        }]}

config = get_plugin_config(Config)

say = on_command("say")
say_on = on_command("say_on",permission=SUPERUSER)
say_off = on_command("say_off",permission=SUPERUSER)

class Cansay:
    sayenable = False

@say.handle()
async def handle_function(event: GroupMessageEvent, arg: Message = CommandArg()):
    if not Cansay.sayenable:
        await say.finish("say功能目前不开放哦QAQ")
    text = str(arg)
    if len(text) > 1000:
        await msg.emoji_like(event.message_id,"424")
        await say.finish("请善待小小卒！")
    res = requests.get("http://127.0.0.1:23456/voice/gpt-sovits?id=1&preset=default&text="+text)
    if res.status_code != 200:
        await say.finish()   
    path = f"xiaozu_bot/plugins/say/audios/{event.message_id}.wav"
    f = open(path,"wb+")
    f.write(res.content)
    f.close()
    requests.post('http://localhost:3000/send_group_msg', json = json_group_audio(event.group_id,path))
    os.remove(path)
    await say.finish()

@say.handle()
async def handle_function(event: PrivateMessageEvent, arg: Message = CommandArg()):
    if not Cansay.sayenable:
        await say.finish("say功能目前不开放哦QAQ")
    text = str(arg)
    if len(text) > 1000:
        await say.finish("请善待小小卒！")
    res = requests.get("http://127.0.0.1:23456/voice/gpt-sovits?id=1&preset=default&text="+text)
    if res.status_code != 200:
        await say.finish()   
    path = f"xiaozu_bot/plugins/say/audios/{event.message_id}.wav"
    f = open(path,"wb+")
    f.write(res.content)
    f.close()
    requests.post('http://localhost:3000/send_private_msg', json = json_private_audio(event.user_id,path))
    os.remove(path)
    await say.finish()

@say_on.handle()
async def handle_function():
    Cansay.sayenable = True
    await say_on.finish("say功能已启用！")

@say_off.handle()
async def handle_function():
    Cansay.sayenable = False
    await say_off.finish("say功能已关闭！")