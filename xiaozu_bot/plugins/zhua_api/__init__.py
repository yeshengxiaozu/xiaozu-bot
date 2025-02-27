from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot import on_command,on_fullmatch
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent,MessageEvent
from nonebot.params import CommandArg
from nonebot.adapters import Event
from nonebot.rule import Rule
from nonebot.permission import SUPERUSER
from .messages import *
import datetime
import requests
import redis
import json

from .config import Config
r = redis.Redis(host='localhost', port=6379, decode_responses=True)  

__plugin_meta__ = PluginMetadata(
    name="zhua_api",
    description="",
    usage="",
    config=Config,
)

config = get_plugin_config(Config)
buy = on_command("buy")
sell = on_command("sell")
check = on_command("ck")
ckzhua = on_fullmatch({".ck","。ck"})
test = on_command("test",permission=SUPERUSER)
give = on_command("give",permission=SUPERUSER)
extract = on_command("extract",permission=SUPERUSER)
coins_fix = on_command("coins_fix",permission=SUPERUSER)

class berry_manager:
    def is_berrybot(event: Event) -> bool:
        return event.get_user_id() == "3948837959" or event.get_user_id() == "3928744142"
    rule_bot = Rule(is_berrybot)
    def is_berrygroup(event: GroupMessageEvent) -> bool:
        return event.group_id == messages.outer_group_id
    rule_group = Rule(is_berrygroup)

    def setCoins(id: int, num: int):
        return r.hset("berit_coins",f"{id}",str(num))

    def getCoins(id: int) -> int:
        if (not r.hexists("berit_coins", f"{id}")):
            return 0
        return int(r.hget("berit_coins",f"{id}")) 
    
    async def check(id: int, amount : int):
        requests.post('http://localhost:3000/send_group_msg', json = json_check(id,amount))
    check_finish = on_command("berry_check_finish", rule = rule_bot)

    async def change(id: int, amount : int):
        requests.post('http://localhost:3000/send_group_msg', json = json_change(id,amount))
    change_finish = on_command("berry_change_finish", rule = rule_bot)

class msg_api:
    group_id = outer_group_id
    async def send(self, text: str) -> dict:
        return json.loads(requests.post('http://localhost:3000/send_group_msg', json = json_group(self.group_id,text)).text)['data']
    
    async def send_private(self, id: int, text: str) -> dict:
        return json.loads(requests.post('http://localhost:3000/send_private_msg', json = json_private(id,text)).text)['data']
    
    async def send_at(self, id: int, text: str) -> dict:
        return json.loads(requests.post('http://localhost:3000/send_group_msg', json = json_group_at(self.group_id,id,text)).text)['data']
    
    async def emoji_like(self, id: int, eid: str) -> dict:
        return json.loads(requests.post('http://localhost:3000/set_msg_emoji_like', json = json_emoji_like(id,eid)).text)['data']
    
    async def send_emoji(self, text: str, eid: str) -> dict:
        mes = await self.send(text)
        await self.emoji_like(mes["message_id"],eid)
        return mes
    
    async def send_at_emoji(self, id, text: str, eid: str) -> dict:
        mes = await self.send_at(id, text)
        await self.emoji_like(mes["message_id"],eid)
        return mes

msg = msg_api()
    
@buy.handle()
async def handle_function(event: GroupMessageEvent, arg: Message = CommandArg()):
    if event.group_id != outer_group_id:
        await buy.finish("这个功能依赖和其他伯特的通信，无法在伯特群外使用！")
    args = str(arg).lower().split()
    id = event.user_id
    d = datetime.datetime.now()
    if d.hour == 0 and d.minute < 30:
        await buy.finish("Fhloy/Foxelne正在冬眠[可怜]",at_sender = True)
    
    if r.get(f"coins_status_{id}") == "buying":
        await buy.finish("你正在买蓝莓。请稍等。")
    if r.get(f"coins_status_{id}") == "selling":
        await buy.finish("你正在卖蓝莓。请稍等。")
    if len(args) != 1 or not args[0].isdecimal():
        await msg.emoji_like(event.message_id,"424")
        await sell.finish()
    elif int(args[0])<=0:
        await msg.emoji_like(event.message_id,"424")
        await buy.finish()
    else:
        r.set(f"coins_status_{id}", "buying")
        await berry_manager.check(id,int(args[0]))

@sell.handle()
async def handle_function(event: GroupMessageEvent, arg: Message = CommandArg()):
    if event.group_id != outer_group_id:
        await sell.finish("这个功能依赖和其他伯特的通信，无法在伯特群外使用！")
    args = str(arg).lower().split()
    id = event.user_id
    d = datetime.datetime.now()
    if d.hour == 0 and d.minute < 30:
        await sell.finish("Fhloy/Foxeline正在冬眠[可怜]",at_sender = True)
    
    if r.get(f"coins_status_{id}") == "buying":
        await sell.finish("你正在买蓝莓。请稍等。")
    if r.get(f"coins_status_{id}") == "selling":
        await sell.finish("你正在卖蓝莓。请稍等。")
    if len(args) == 1 and args[0] == 'all':
        if (berry_manager.getCoins(id) == 0):
            await sell.finish("你想干什么，展示你空空如也的蓝莓钱包吗？",at_sender = True)
        else:
            num = berry_manager.getCoins(id)
    elif len(args) != 1 or not args[0].isdecimal():
        await msg.emoji_like(event.message_id,"424")
        await sell.finish()
    elif int(args[0])<=0:
        await msg.emoji_like(event.message_id,"424")
        await sell.finish()
    elif int(args[0])>berry_manager.getCoins(id):
        await sell.finish("你没有这么多蓝莓！",at_sender = True)
    else:
        num = int(args[0])
    await berry_manager.change(id,num)
    r.set(f"coins_status_{id}", "selling")
    await sell.finish(f"你正在尝试提现{num}蓝莓……",at_sender = True)

@check.handle()
async def handle_function(event: GroupMessageEvent):
    if event.group_id != outer_group_id:
        await check.finish("在伯特群外ck有意义吗……")
    id = event.user_id
    await check.finish(f"你目前拥有{berry_manager.getCoins(id)}蓝莓",at_sender = True)

@check.handle()
async def handle_function(event: PrivateMessageEvent):
    id = event.user_id
    await check.finish(f"你目前拥有{berry_manager.getCoins(id)}蓝莓",at_sender = True)

@ckzhua.handle()
async def handle_function(event: GroupMessageEvent):
    if event.group_id != outer_group_id:
        await ckzhua.finish()
    id = event.user_id
    num = berry_manager.getCoins(id)
    if num != 0:
        await ckzhua.finish(f"你目前同时拥有{num}蓝莓")
    await ckzhua.finish()

@berry_manager.check_finish.handle()
async def handle_function(arg: Message = CommandArg()):
    args = str(arg).lower().split()
    id = int(args[0])
    if args[2] != "true":
        await msg.send_at(id,"你没有这么多草莓！")
        r.set(f"coins_status_{id}", "nothing")
        await berry_manager.check_finish.finish()
    await berry_manager.change(id,-int(args[1]))

@berry_manager.change_finish.handle()
async def handle_function(arg: Message = CommandArg()):
    args = str(arg).lower().split()
    id = int(args[0])
    num = -int(args[1])
    if args[2] != "true":
        await berry_manager.change_finish.finish()
    berry_manager.setCoins(id,berry_manager.getCoins(id)+num)
    r.set(f"coins_status_{id}", "nothing")
    await msg.send_at(id,f"转化成功！一共转化了{abs(num)}草莓/蓝莓！")

@give.handle()
async def handle_function(arg: Message = CommandArg()):
    args = str(arg).lower().split()
    if len(args) != 2:
        give.finish("INVALID SYNTAX")
    id = int(args[0])
    num = int(args[1])
    berry_manager.setCoins(id,berry_manager.getCoins(id)+num)
    await give.finish(f"Success: {num} coins were given to id:{id}")

@extract.handle()
async def handle_function(event: MessageEvent, arg: Message = CommandArg()):
    args = str(arg).lower().split()
    if len(args) != 1:
        extract.finish("INVALID SYNTAX")
    num = int(args[1])
    if berry_manager.getCoins(event.self_id) < num:
        await extract.finish(f"bot don't have enough strawberries!")
    berry_manager.setCoins(event.self_id,berry_manager.getCoins(event.self_id)-num)
    berry_manager.setCoins(event.user_id,berry_manager.getCoins(event.user_id)+num)
    await extract.finish(f"Success: {num} coins were extracted to admin")

@coins_fix.handle()
async def handle_function():
    for i in r.keys(pattern='coins_status_*'):
        r.set(i,"fixed",ex=1)
    await coins_fix.finish("fixed.")