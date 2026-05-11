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
import datetime,time
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

class berry_manager:
    def is_botgroup(event: GroupMessageEvent) -> bool:
        return event.group_id == messages.inner_group_id
    rule_bot = Rule(is_botgroup)
    def is_berrybot(event: GroupMessageEvent) -> bool:
        return event.get_user_id() == "3948837959" or event.get_user_id() == "3928744142" or event.get_user_id() == "914578128"
    rule_isbot = Rule(is_berrybot)
    def is_berrygroup(event: GroupMessageEvent) -> bool:
        return event.group_id == messages.outer_group_id
    rule_group = Rule(is_berrygroup)

    def setCoins(id: int, num: int):
        return r.hset("berit_coins",f"{id}",str(num))

    def getCoins(id: int) -> int:
        if (not r.hexists("berit_coins", f"{id}")):
            return 0
        return int(r.hget("berit_coins",f"{id}")) 
    
    def addCoins(id: int, num: int):
        return r.hset("berit_coins",f"{id}",str(berry_manager.getCoins(id)+num))
    
    def ban(id: int, t: int):
        r.set(f"forbid_{id}","forbidden",ex = t)
    
    def recover(id: int):
        r.set(f"forbid_{id}","recovered",ex = 1)
    
    def isforbid(id: int) -> bool:
        return r.get(f"forbid_{id}") == "forbidden"
    
    async def check(id: int, amount : int):
        requests.post('http://localhost:3000/send_group_msg', json = json_check(id,amount))
    check_finish = on_command("berry_check_finish", rule = rule_bot)

    async def change(id: int, amount : int):
        requests.post('http://localhost:3000/send_group_msg', json = json_change(id,amount))
    change_finish = on_command("berry_change_finish", rule = rule_bot)

    forbid_guess = on_command("forbid_guess", rule = rule_bot)
    forbid_guess_recover = on_command("forbid_guess_recover", rule = rule_bot)

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
    await buy.finish("你带着一袋子草莓来到了蓝莓银行的门口，但是那块写着银行的牌子下面没有人也没有伯特。看样子你在这做不了任何事了。")
    return
    if event.group_id != outer_group_id:
        await buy.finish("这个功能依赖和其他伯特的通信，无法在伯特群外使用！")
    args = str(arg).lower().split()
    id = event.user_id
    
    if r.get(f"coins_status_{id}") == "buying":
        await buy.finish("你正在买蓝莓。请稍等。")
    if r.get(f"coins_status_{id}") == "selling":
        await buy.finish("你正在卖蓝莓。请稍等。")
    if len(args) != 1 or not args[0].isdecimal():
        await msg.emoji_like(event.message_id,"424")
        await buy.finish()
    elif int(args[0])<=0:
        await msg.emoji_like(event.message_id,"424")
        await buy.finish()
    else:
        r.set(f"coins_status_{id}", "buying", ex=600)
        await berry_manager.check(id,int(args[0]))

@sell.handle()
async def handle_function(event: GroupMessageEvent, arg: Message = CommandArg()):
    await sell.finish("那块写着银行的牌子下面没有人也没有伯特，你也明知道你的账户已经在那天被清空了。你想从这里得到什么？")
    return
    if event.group_id != outer_group_id:
        await sell.finish("这个功能依赖和其他伯特的通信，无法在伯特群外使用！")
    args = str(arg).lower().split()
    id = event.user_id
    
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
    r.set(f"coins_status_{id}", "selling", ex=600)
    await sell.finish(f"你正在尝试提现{num}蓝莓……",at_sender = True)

@check.handle()
async def handle_function(event: GroupMessageEvent):
    await check.finish("原本挂着账单的地方现在只有一张白纸。不需要任何人进行任何查询，你知道你的账户——如果它还存在的话——空空如也。")
    return
    if event.group_id != outer_group_id:
        await check.finish("在伯特群外ck有意义吗……")
    id = event.user_id
    await check.finish(f"你目前拥有{berry_manager.getCoins(id)}蓝莓",at_sender = True)

@berry_manager.check_finish.handle()
async def handle_function(arg: Message = CommandArg()):
    await msg.send_at("3251605531","broadcast群出现了不应该出现的消息")
    await berry_manager.check_finish.finish()
    return
    args = str(arg).lower().split()
    id = int(args[0])
    if args[2] == "404":
        await msg.send_at(id,"你没有这么多草莓！")
        r.set(f"coins_status_{id}", "nothing")
        await berry_manager.check_finish.finish()
    elif args[2] == "502":
        await msg.send_at(id,"请先解决你的债务！")
        r.set(f"coins_status_{id}", "nothing")
        await berry_manager.check_finish.finish()
    elif args[2] != "200":
        await msg.send_at(id,"someone told xiaozu there a problem with my 并非ai")
        r.set(f"coins_status_{id}", "nothing")
        await berry_manager.check_finish.finish()
    await berry_manager.change(id,-int(args[1]))

@berry_manager.change_finish.handle()
async def handle_function(arg: Message = CommandArg()):
    await msg.send_at("3251605531","broadcast群出现了不应该出现的消息")
    await berry_manager.change_finish.finish()
    return
    args = str(arg).lower().split()
    id = int(args[0])
    num = -int(args[1])
    if args[2] == "502":
        await msg.send_at(id,"请先解决你的债务！")
        r.set(f"coins_status_{id}", "nothing")
        await berry_manager.change_finish.finish()
    elif args[2] != "200":
        r.set(f"coins_status_{id}", "nothing")
        await berry_manager.change_finish.finish()
    berry_manager.addCoins(id,num)
    r.set(f"coins_status_{id}", "nothing")
    await msg.send_at(id,f"转化成功！一共转化了{abs(num)}草莓/蓝莓！")

@berry_manager.forbid_guess.handle()
async def handle_function(event: MessageEvent, arg: Message = CommandArg()):
    args = str(arg).lower().split()
    id = int(args[0])
    hrs = int(args[1])
    t = int(event.time + hrs*3600 - time.time())
    if t>0:
        berry_manager.ban(id,t)
        await msg.send_at(id,"已经没有蓝莓相关功能了……但至少此时此刻，小小卒仍然可以禁用你的guess。")
    await berry_manager.forbid_guess.finish()

@berry_manager.forbid_guess_recover.handle()
async def handle_function(arg: Message = CommandArg()):
    args = str(arg).lower().split()
    id = int(args[0])
    berry_manager.recover(id)
    await msg.send_at(id,"本群guess已解禁（想不出文案版）")
    await berry_manager.forbid_guess_recover.finish()