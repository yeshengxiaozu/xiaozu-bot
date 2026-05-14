import random
from typing import Union

from nonebot import get_plugin_config, on_command, on_endswith, on_type, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    PokeNotifyEvent,
    PrivateMessageEvent,
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule, to_me

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="joy",
    description="神秘娱乐指令合集",
    usage="",
    config=Config,
)


def banned(event: Union[GroupMessageEvent, PrivateMessageEvent]):
    return isinstance(event, GroupMessageEvent) and event.group_id == 569801410


def notbanned(event: Union[GroupMessageEvent, PrivateMessageEvent]):
    return not banned(event)


rule_ban = Rule(banned)
rule_notban = Rule(notbanned)

config = get_plugin_config(Config)

ultra = on_command("ultra", rule=rule_notban)
nsdd = on_command("nsdd", rule=rule_notban)
insult = on_command("insult", rule=rule_notban)
jwz = on_command("jwz", rule=rule_notban)
game = on_command("game")
today = on_command("today")
news = on_command("news", aliases={"公告", "新闻"})
test = on_command("1145141919810", permission=SUPERUSER)
group_poke = on_type(
    (PokeNotifyEvent,),
    rule=to_me(),
    priority=10,
    block=True,
)


@ultra.handle()
async def handle_function(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
):
    if len(str(arg)) > 100:
        await bot.call_api(
            "set_msg_emoji_like", message_id=event.message_id, emoji_id="424"
        )
        await ultra.finish()
    args = str(arg).lower().split()
    if len(args) == 0:
        tmsg = await ultra.send(
            "不要再Ultra了！Ultra是加拿大人研发的新型压片！加拿大人往你的电脑里安装大炮，当你Ultra时大炮就会被引燃，真是细思极恐！加拿大研发的机器人会自动生成Ultra图，不费任何人力就能让你的孩子上隐！现在的孩子竟然打蔚蓝的Ultra图，可见加拿大人已经嘟害了青少年的心灵，你的孩子已经失去了炼金能力！Ultra图多有暴力元素，引导蔚批走向暴力，残害家人和朋友，让你的孩子有自c倾向！其实这些都是加拿大人的诡计！如果你认同我的看法，请转发出去，转告你的亲友，不要再Ultra了。抵制Ultra！！！"
        )
        await bot.call_api(
            "set_msg_emoji_like", message_id=tmsg["message_id"], emoji_id="128560"
        )
        await ultra.finish()
    elif len(args) < 2:
        await bot.call_api(
            "set_msg_emoji_like", message_id=event.message_id, emoji_id="424"
        )
        await ultra.finish()
    else:
        tmsg = await ultra.send(
            f"不要再{args[0]}了！{args[0]}是{args[1]}研发的新型压片！{args[1]}往你的电脑里安装大炮，当你{args[0]}时大炮就会被引燃，真是细思极恐！{args[1]}研发的机器人会自动生成{args[0]}，不费任何人力就能让你的孩子上隐！现在的孩子竟然打{args[0]}，可见{args[1]}已经嘟害了群友的心灵，你的孩子已经失去了炼金能力！{args[0]}多有暴力元素，引导群友走向暴力，残害家人和朋友，让你的孩子有自c倾向！其实这些都是{args[1]}的诡计！抵制{args[0]}！！！"
        )
        await bot.call_api(
            "set_msg_emoji_like", message_id=tmsg["message_id"], emoji_id="128560"
        )
        await ultra.finish()


@nsdd.handle()
async def handle_function(bot: Bot, event: GroupMessageEvent):
    id = random.randint(1, 3)
    if id == 1:
        tmsg = await nsdd.send(
            "你说的对，但是《几何冲刺》是由RobTop Games自主研发的一款全新节奏式平台跳跃游戏。游戏发生在一个被称作「几何世界」的幻变空间，在这里，被节奏选中的人将被授予「跳跃之力」，导引机关与障碍的律动。你将扮演一位名为「几何方块」的神秘角色，在无序与秩序交织的关卡中，邂逅形态各异、节奏独特的障碍物们，和他们一起冲破陷阱，抵达未知的终点。"
        )
        await bot.call_api(
            "set_msg_emoji_like", message_id=tmsg["message_id"], emoji_id="424"
        )
    elif id == 2:
        tmsg = await nsdd.send(
            "你说的对，但《game2》是一款由Desom-fu不太自主研发的一款回合超时风格的恶魔投降模拟器。你将进入一个名叫『神必轮盘』的世界，被神权选中的人将被授予『烈弓』，导引『万军取首』之力。玩家将扮演一位试图『开枪自己』的神必角色，使用各个道具，发现各种10/9、9/8的弹夹，并与他们一起面对强大的休养生息，寻找失散的金苹果——同时，揭开『卧槽什么b道具对面什么狗运这把什么b弹夹禁止卡卡我启动了这玩集贸欸我怎么似了又开始带烈弓节奏了休养生息能不能死啊你怎么还有金苹果tnt真强吗错了别炸我黑洞偷了坨shi回来求求你不要减血上限了我刚回的血太久未参与对局回合超时喜欢我天秤直伤吗你玩具枪怎么还不空枪手套叠救我骰子眉目了别偷我刷新票啊』的真相。"
        )
        await bot.call_api(
            "set_msg_emoji_like", message_id=tmsg["message_id"], emoji_id="424"
        )
    elif id == 3:
        tmsg = await nsdd.send(
            "你说的对，但《蔚蓝》是一款由EXOK Games Inc.制作并发行的一款像素风格的动作冒险类游戏，玩家将进入一个神秘的幻想地区——塞斯来特山。在这个世界中，玩家将扮演一位神秘的小女孩，拥有冲刺的力量，探索各个地区，结识各种性格各异、能力独特的伙伴，并与他们一起面对强大的水晶之心，寻找失散的草莓，同时揭开塞斯来特山的真相。"
        )
        await bot.call_api(
            "set_msg_emoji_like", message_id=tmsg["message_id"], emoji_id="424"
        )
    await nsdd.finish()


@game.handle()
async def handle_function(event: GroupMessageEvent, arg: Message = CommandArg()):
    args = str(arg).lower().split()
    if len(args) < 1:
        await game.send(
            "请在使用小小卒的时候带上想要让小小卒帮忙的游戏编号(1~4)哦~免责声明：仅供参考"
        )
    if args[0] == "1":
        t = random.randint(1, 3)
        if t == 1:
            await game.send("小小卒的猜测建议是：" + random.choice(["大于7", "小于7"]))
        elif t == 2:
            await game.send(
                "小小卒的猜测建议是：" + random.choice(["方片", "梅花", "黑桃", "红桃"])
            )
        else:
            a = random.choice(
                ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
            )
            b = random.choice(
                ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
            )
            while b == a:
                b = random.choice(
                    ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
                )
            await game.send("小小卒的猜测建议是：" + a + "/" + b)
    elif args[0] == "2":
        await game.send("小小卒觉得下一发是：" + random.choice(["实弹", "虚弹"]))
    elif args[0] == "3":
        await game.send("小小卒觉得最有潜力的擂台是：" + str(random.randint(1, 10)))
    elif args[0] == "4":
        await game.send(
            "小小卒觉得今天的宝藏埋藏在："
            + str(random.randint(1, 10))
            + "/"
            + str(random.randint(1, 10))
            + "/"
            + str(random.randint(1, 10))
        )
    else:
        await game.send("小小卒不知道你想让我建议什么哦~")
    await game.finish()


@jwz.handle()
async def handle_function(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
):
    if len(str(arg)) > 100:
        await bot.call_api(
            "set_msg_emoji_like", message_id=event.message_id, emoji_id="424"
        )
        await jwz.finish()
    args = str(arg).lower().split()
    if len(args) == 0:
        await jwz.finish("请输入一个参数！")
    t = str(random.randint(1, 20)) + random.choice(
        ["年", "个月", "天", "小时", "分钟", "秒"]
    )
    if len(args) >= 2:
        t = args[1]
    tmsg = await jwz.send(
        f"我能在患有健忘症的情况下通关{args[0]}吗？今天，我决定要进行一项之前从未有人达成过的挑战。{args[0]}推出已经有{t}了，是时候有人来在受健忘症影响的情况下通关{args[0]}了。这能被完成吗？这真的可能吗？"
    )
    await bot.call_api(
        "set_msg_emoji_like", message_id=tmsg["message_id"], emoji_id="10068"
    )
    await jwz.finish()


@today.handle()
async def handle_function(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
):
    if len(str(arg)) > 100:
        await bot.call_api(
            "set_msg_emoji_like", message_id=event.message_id, emoji_id="424"
        )
        await today.finish()
    args = str(arg).lower().split()
    if len(args) != 3:
        await today.finish("请输入3个参数！")
    tmsg = await today.send(
        f"今天是著名{args[0]}大神{args[1]}{args[2]}的日子，山高路远，岁月漫漫，有你，一切都变得有趣。所有的美好都将如期而至，所有的阴霾都将被阳光赶走。刹那间，天地失却颜色，三万里冰川消融，暖阳拂过大地，花开到荼蘼。温柔的一半是知识，没有涵养的温柔撑不起你要的风骨。心里眼里都是你，亿万星辰犹不及。我们终会上岸，无论去到哪里都是阳光灿烂，鲜花开放。光终究会撒在你身上，你也会灿烂一场。生活中的酸甜苦辣，记录着命运的轨迹，轨迹留下你的影子，{args[2]}到来之际，送给你的祝愿最诚挚，衷心祝你大吉大利，顺心如意。只有尊重自己的人，才能够更勇于缩小自己，通过退让来成全别人，非愚即智。梦自己想梦的，做自己想做的，因为生命只有一次，机会不会再来。人生苦短，咱们何必计较得失，有爱就有梦。每个人都有一番不一样的经历，每个人都是一部新鲜的故事。懂得珍惜，风雨兼程的日子，有你有我也有他。"
    )
    await bot.call_api(
        "set_msg_emoji_like", message_id=tmsg["message_id"], emoji_id="144"
    )


@news.handle
async def handle_function():
    await news.finish("""更新公告：移除了蓝莓系统因为我不想转移数据了；
翻修了help删除对应的内容
增加了*gdlevelsearch功能因为小卒变成gd吃了
借楼辱骂一下rubtap的api我看一次养胃一次""")


@test.handle()
async def handle_function(event: GroupMessageEvent, arg: Message = CommandArg()):
    ret = await test.send("这是一个测试消息！")
    await test.finish(str(ret))


@group_poke.handle()
async def handle_function(bot: Bot, event: PokeNotifyEvent):
    if event.user_id is not bot.self_id:
        await bot.call_api("group_poke", group_id=event.group_id, user_id=event.user_id)
