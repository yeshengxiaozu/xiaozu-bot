from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11 import GroupMessageEvent,MessageEvent
from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent
from typing import Union
from nonebot import on_command, require
from pathlib import Path
from nonebot.permission import SUPERUSER
import random, redis

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="zhua",
    description="",
    usage="",
    config=Config,
)

r = redis.Redis(host='localhost', port=6379, decode_responses=True)  

descriptions = {
    "mc卒.png" : "小卒穿越进了mc，这是她的样子",
    "UwU卒.png" : "UwU",
    "xiaozu-boom.gif" : "被.bet2和充能炸弹气晕的小卒按下了致死量的按钮",
    "仙女棒卒.gif" : "OwO",
    "偷窥卒.png" : "暗中窥视",
    "加载卒.gif" : "不是你这动画怎么加载不出来啊？",
    "可怜卒.png" : "可怜",
    "叼花卒.gif" : "霸道小卒爱上我",
    "吃吃卒.gif" : "好饿好饿好饿（串戏）",
    "哭哭卒.gif" : "被泪水淹没",
    "圆锥卒.jpg" : "小卒头顶怎么尖尖的",
    "圣诞卒.jpg" : "和圣诞帽对比突出一个小卒画了个啥（）",
    "圣诞帽卒.png" : "圣诞群头像的废案",
    "巧克力卒.png" : "情人节可以吃小卒巧克力吗？",
    "幻想时间卒.gif" : "现在是幻想时间",
    "彩虹卒.gif" : "awzdsfl",
    "抱心卒.png" : "可爱捏*3",
    "拧巴卒.png" : "*",
    "按钮卒.gif" : "/续标识",
    "捏捏卒.PNG" : "这捏头我可太喜欢了",
    "撒娇卒.gif" : "求求你了求求你了",
    "擦汗卒.gif" : "（尴尬而不失礼貌的笑）",
    "无眼卒.png" : "面无表情（但只是没有眼睛）",
    "星星眼卒.png" : "(✧ʌ✧)",
    "智慧卒.jpg" : "这小卒看上去怎么这么智慧",
    "比心卒.gif" : "比心心",
    "泪眼卒.png" : "/e 55",
    "火山卒.png" : "我有点编不下去了（）",
    "烧杯卒.png" : "这也是卒，对吗",
    "爆发卒.png" : "卒要爆了",
    "生气卒.gif" : "啥比",
    "看烟花卒.png" : "小卒把桌面背景都爆出来了（）",
    "简约线条卒.png" : "按按钮",
    "极致色彩卒.png" : "无语地按按钮",
    "粉卒.png" : "粉粉的",
    "红包卒.gif" : "要是看到这条能给我发个专属红包就更好了（",
    "红药卒.png" : "for the worthy",
    "色块卒.png" : "经典出装",
    "草地卒.png" : "最普通的小卒，没什么好说的。",
    "野生大卒.jpg" : "铸币大卒",
    "闪红卒.gif" : "红温了",
    "问号卒.gif" : "？？？",
    "面无表情卒.png" : "无面人，但是小卒",
    "飞吻卒.gif" : "mua",
    "wow卒.png" : "owo",
    "卒币大头.png" : "铸",
    "神金比心卒.png" : "一看就是在某个主播那边进修过",
    "卒瞪.png" : "我会一直……一直……看着你……",
    "火柴卒.png" : "可以脑子旋转的火柴人",
    "棒棒糖卒.png" : "好吃",
    "chud卒.jpg" : "蔚蓝已经沦陷，亿万人必须奥欻",
    "路障卒.jpg" : "有点防护能力，但是不防boom",
    "擒史皇卒.jpg" : "玩一张叫五个括号的图导致的",
    "野生克星.jpg" : "最基本的刺型。没什么好说的。",
    "思考卒.jpg" : "别吵，卒在烧烤",
    "bet2卒.jpg" : "。开枪自己",
    "X卒.png" : "> <",
    "按钮卒.png" : "xiaozu-notboom",
    "流泪卒.png" : "[委屈]",
    "黑脸卒.png" : "生气了",
    "替身使者卒.jpg" : "竞技场神权，举办了",
    "指指点点卒.jpg" : "you！",
    "替身使者卒.jpg" : "可以魅惑敌人为你炼金",
    "GD像素卒.png" : "小卒来到GD世界直接硬刚这里的Plat Demon, 这是她发生的变化",
    "刺头卒.jpg" : "事实证明刺头人身还是太反人类了",
}

config = get_plugin_config(Config)

zhua = on_command("zhua")
zhua_test = on_command("zhua_test",permission=SUPERUSER)
berry_manager = require("zhua_api").berry_manager

@zhua.handle()
async def handle_function(event: Union[GroupMessageEvent,PrivateMessageEvent]):
    id = event.user_id
    if r.get(f"zhua_cd_{id}") == "waiting":
        t = r.ttl(f"zhua_cd_{id}")
        await zhua.finish(f"别抓啦，{t}秒后再来吧",at_sender = True)
    folder_path = Path("xiaozu_bot/plugins/zhua/data/")
    file_name = random.choice(list(descriptions.keys()))
    name = file_name.split('.')[0]
    image_path = folder_path / file_name
    ra = random.random()
    b = 0
    if (ra < 0.01): b = 50
    elif (ra < 0.05): b = 35
    elif (ra < 0.2): b = 20
    elif (ra < 0.5): b = 10
    else: b = 5
    if isinstance(event,GroupMessageEvent) and berry_manager.is_berrygroup(event):
        berry_manager.addCoins(event.user_id,b)
        await zhua.send(MessageSegment.text(f"恭喜你抓到一个小卒！\n") + MessageSegment.image(image_path) + MessageSegment.text(f"\n{name}\n{descriptions[file_name]}\n\n你获得了{b}蓝莓"),at_sender = True)
        r.set(f"zhua_cd_{id}","waiting",ex=1800)
    else:
        await zhua.send(MessageSegment.text(f"恭喜你抓到一个小卒！\n") + MessageSegment.image(image_path) + MessageSegment.text(f"\n{name}\n{descriptions[file_name]}\n\n你并没有获得{b}蓝莓"),at_sender = True)
        r.set(f"zhua_cd_{id}","waiting",ex=600)
    await zhua.finish()

@zhua_test.handle()
async def handle_function(event: MessageEvent):
    file_names = []
    folder_path = Path("xiaozu_bot/plugins/zhua/data/")
    file_names = [f.name for f in folder_path.iterdir() if f.is_file()]
    await zhua_test.send(str([f"[\"{i}\"] = \"\"" for i in file_names]))
    await zhua_test.finish()
