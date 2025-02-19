from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot import on_command,on_endswith
from nonebot.adapters import Message
from nonebot.rule import Rule
from nonebot.params import CommandArg
from nonebot import require
import random

from .config import Config
msg = require("zhua_api").msg_api()
bot = require("zhua_api").berry_manager.rule_bot

__plugin_meta__ = PluginMetadata(
    name="ultra",
    description="",
    usage="",
    config=Config,
)

config = get_plugin_config(Config)

ultra = on_command("ultra")
nsdd = on_command("nsdd")
insult = on_command("insult")
jwz = on_command("jwz")
bet1 = on_endswith("，你的猜测失败了！",rule = bot)
test = on_command("1145141919810")

@ultra.handle()
async def handle_function(event: GroupMessageEvent, arg: Message = CommandArg()):
    msg.group_id = event.group_id
    if len(str(arg)) > 100:
        await msg.emoji_like(event.message_id,"424")
        await ultra.finish()
    args = str(arg).lower().split()
    if len(args) == 0:
        await msg.send_emoji("不要再Ultra了！Ultra是加拿大人研发的新型鸦片！加拿大人往你的电脑里安装大炮，当你Ultra时大炮就会被引燃，真是细思极恐！加拿大研发的机器人会自动生成Ultra图，不费任何人力就能让你的孩子上瘾！现在的孩子竟然打蔚蓝的Ultra图，可见加拿大人已经嘟害了青少年的心灵，你的孩子已经失去了炼金能力！Ultra图多有暴力元素，引导蔚批走向暴力，残害家人和朋友，让你的孩子有自残倾向！其实这些都是加拿大人的诡计！坚决抵制Ultra图，坚决抵制文化入侵！如果你认同我的看法，请转发出去，转告你的亲友，不要再Ultra了。抵制Ultra！！！",'128560')
        await ultra.finish()
    elif len(args)<2:
        await msg.emoji_like(event.message_id,"424")
        await ultra.finish()
    else:
        await msg.send_emoji(f'不要再{args[0]}了！{args[0]}是{args[1]}研发的新型压片！{args[1]}往你的电脑里安装大炮，当你{args[0]}时大炮就会被引燃，真是细思极恐！{args[1]}研发的机器人会自动生成{args[0]}，不费任何人力就能让你的孩子上瘾！现在的孩子竟然打{args[0]}，可见{args[1]}已经嘟害了群友的心灵，你的孩子已经失去了炼金能力！{args[0]}多有暴力元素，引导群友走向暴力，残害家人和朋友，让你的孩子有自c倾向！其实这些都是{args[1]}的诡计！抵制{args[0]}！！！','128560')
        await ultra.finish()

@nsdd.handle()
async def handle_function(event: GroupMessageEvent):
    msg.group_id = event.group_id
    await msg.send_emoji(f'你说的对，但《zhuamadeline》是一款由Desom-fu自主研发的一款伯特风格的开放世界爆炸模拟器。你将进入一个名叫『Madeline猎场』的世界，被神选中的人将被授予『提取器』，导引『爆炸』之力。玩家将扮演一位拥有『抓玛的力量』的神必角色，探索各个猎场，结识各种提取不到、还抓不到的Madeline，并与他们一起面对强大的迷题，寻找失散的草莓——同时，揭开『古大小姐发电』的真相。','424')
    await nsdd.finish()

@insult.handle()
async def handle_function(event: GroupMessageEvent):
    msg.group_id = event.group_id
    await msg.send_emoji(f'这破壁游戏也配叫神作?一群手残孝子搁那吹"第九艺术"，实际就一像素自虐模拟器!爬山十分钟摔死三十次，血压直飙180，制作组怕不是抖S成精?剧情喂的鸡汤够开养鸡场了，真当抑郁症玩个4399就能治好?建议改名叫《痛苦攀岩模拟器》，附赠速效救心丸销量肯定翻倍!',"128560")
    await insult.finish()

@bet1.handle()
async def handle_function(event: GroupMessageEvent):
    msg.group_id = event.group_id
    await msg.emoji_like(event.message_id,"128074")
    await insult.finish()

@jwz.handle()
async def handle_function(event: GroupMessageEvent, arg: Message = CommandArg()):
    msg.group_id = event.group_id
    if len(str(arg)) > 100:
        await msg.emoji_like(event.message_id,"424")
        await jwz.finish()
    args = str(arg).lower().split()
    if len(args) == 0:
        await jwz.finish("请输入一个参数！")
    t=str(random.randint(1,20))+random.choice(["年","个月","天","小时","分钟","秒"])
    if len(args) >= 2:
        t = args[1]
    await msg.send_emoji(f"我能在患有健忘症的情况下通关{args[0]}吗？今天，我决定要进行一项之前从未有人达成过的挑战。{args[0]}推出已经有{t}了，是时候有人来在受健忘症影响的情况下通关{args[0]}了。这能被完成吗？这真的可能吗？","10068")
    await jwz.finish()

@test.handle()
async def handle_function(event: GroupMessageEvent, arg: Message = CommandArg()):
    ret = await jwz.send("这是一个测试消息！")
    await jwz.finish(str(ret))