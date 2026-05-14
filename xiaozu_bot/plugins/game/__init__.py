# 导入定时任务库
from nonebot import get_bots, on_command, on_fullmatch, require
from nonebot.adapters.onebot.v11 import (
    GROUP,
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.rule import Rule

require("nonebot_plugin_apscheduler")
import datetime
import json

# 加载数学算法相关
import random

# 加载读取系统时间相关
import time
from pathlib import Path

import redis
from nonebot_plugin_apscheduler import scheduler

from .config import *

r = redis.Redis(host="localhost", port=6379, decode_responses=True)


def whitelist(event: GroupMessageEvent):
    return event.group_id == 1035708051 or event.group_id == 870217476


whitelist_rule = Rule(whitelist)


class datas:
    demon_data = {}


# demon_default
def demon_default():
    return {
        "pl": [],
        "hp": [],
        "item_0": [],
        "item_1": [],
        "hcf": 0,
        "clip": [],
        "turn": 0,
        "atk": 0,
        "hp_max": 0,
        "item_max": 0,
        "game_turn": 1,
        "add_atk": False,
        "start": False,
        "identity": 0,
        "demon_coldtime": int(time.time()),
        "turn_start_time": int(time.time()),
    }


# --------------------bet游戏-------------------------

setmode = on_command(
    "setmode", permission=GROUP, priority=1, block=True, rule=whitelist_rule
)
# 地下酒馆 - “游戏”判定
bet = on_command(
    "betgame", permission=GROUP, priority=100, block=True, rule=whitelist_rule
)


@setmode.handle()
async def handle_function(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
):
    args = str(arg).lower().split(" ")
    if len(args) != 1 or not str.isdigit(args[0]):
        await setmode.finish("请输入一个整数！")
    id = int(args[0])
    if id < 0 or id > 2:
        await setmode.finish(
            "目前只接受0（普通模式），1（身份模式），2（膀胱模式）这几个值作为参数哦~"
        )
    r.hset("game_mode", str(event.user_id), str(id))
    await setmode.finish(
        "已将你的游戏模式设置为" + ["普通模式", "身份模式", "膀胱模式"][id]
    )


@bet.handle()
async def bet_handle(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
    user_id = str(event.get_user_id())  # 获取玩家ID
    group_id = str(event.group_id)
    nick_name = event.sender.nickname
    current_time = int(time.time())  # 当前时间戳
    if True:
        # 确保 'datas.demon_data' 和 'group_id' 存在
        # 初始化 group_id 中的游戏数据
        if group_id not in datas.demon_data:
            datas.demon_data[group_id] = demon_default()

        # 检查游戏是否已经开始，如果已经开始，禁止其他玩家加入
        if datas.demon_data[group_id]["start"]:
            await bet.finish("游戏已开始，无法加入！")
        # datas.bar_data[user_id]['status'] = 'demon'
        # 判断玩家是否为第一位或第二位加入
        if len(datas.demon_data[group_id]["pl"]) != 1:
            # 第一位玩家加入
            datas.demon_data[group_id]["pl"].append(user_id)
            datas.demon_data[group_id]["turn_start_time"] = current_time
            # 写入数据
            await bet.finish(
                f"玩家 {nick_name} 加入游戏，等待第二位玩家加入。", at_sender=True
            )

        elif len(datas.demon_data[group_id]["pl"]) == 1:
            # 第二位玩家加入前检查是否已经加入
            if user_id in datas.demon_data[group_id]["pl"]:
                await bet.finish("你已经加入了游戏，无需重复加入！", at_sender=True)
            # 第二位玩家加入，初始化游戏
            datas.demon_data[group_id]["pl"].append(user_id)
            # 游戏开始标志
            datas.demon_data[group_id]["start"] = True
            add_max = 0
            # 膀胱加成
            pangguang_add = 0
            # 获取两个玩家的身份状态

            player_ids = [str(datas.demon_data[group_id]["pl"][i]) for i in range(2)]
            for player_id in player_ids:
                if player_id not in r.hkeys("game_mode"):
                    r.hset("game_mode", player_id, "0")
            identity_status_list = [
                int(r.hget("game_mode", player_id)) for player_id in player_ids
            ]

            # 如果两个玩家的身份状态不同
            if identity_status_list[0] != identity_status_list[1]:
                identity_found = random.choice(
                    identity_status_list
                )  # 随机选择一个状态，50% 概率选择其中一个
            else:
                identity_found = identity_status_list[
                    0
                ]  # 如果两个状态相同，直接选择该状态

            datas.demon_data[group_id]["identity"] = identity_found
            idt_len = len(item_dic2)
            if identity_found == 1:
                add_max = 2
                idt_len = 0
            elif identity_found in [2, 999]:
                add_max = 2
                pangguang_add = 2
                idt_len = 0
            # 设置玩家血量，随机生成血量值(放在上面后面好改)
            hp = random.randint(
                3 + max(int(add_max * 2 - 1), 0) + max(int(pangguang_add * 2 - 1), 0),
                6 + add_max * 2 + pangguang_add * 2,
            )
            datas.demon_data[group_id]["hp"] = [hp, hp]
            # 设定轮数
            datas.demon_data[group_id]["game_turn"] = 1
            # 设定血量上限
            datas.demon_data[group_id]["hp_max"] = 6 + add_max * 2 + pangguang_add * 3
            # 设定道具上限
            datas.demon_data[group_id]["item_max"] = 6 + add_max + pangguang_add
            # 加载弹夹状态
            datas.demon_data[group_id]["clip"] = load(identity_found)
            # 设定无限叠加攻击默认值
            datas.demon_data[group_id]["add_atk"] = False
            # 随机决定先手玩家
            datas.demon_data[group_id]["turn_start_time"] = int(time.time())
            datas.demon_data[group_id]["turn"] = random.randint(0, 1)
            # 随机生成道具并分配给两位玩家
            item_msg = refersh_item(identity_found, group_id)
            # 发送初始化消息
            msg = "轮盘，开局!\n"
            msg += "- 本局模式："
            if identity_found == 1:
                msg += "身份模式"
            elif identity_found in [2, 999]:
                msg += "急速模式"
            else:
                msg += "正常模式"
            msg += "\n\n"
            msg += item_msg
            msg += f"\n- 总弹数{len(datas.demon_data[group_id]['clip'])!s}，实弹数{datas.demon_data[group_id]['clip'].count(1)!s}\n"
            pid = datas.demon_data[group_id]["pl"][datas.demon_data[group_id]["turn"]]
            msg += "- 当前是" + MessageSegment.at(pid) + "的回合"
            await bet.finish(msg)
        else:
            await bet.finish("游戏已开始，无法再次加入！")


# “游戏2”：恶魔赌局
# 本游戏原设计来自 作者：樱井咲夜 https://forum.olivos.run/d/611 的 Olivos恶魔赌局的插件
# 我将其移植并且进行了大量魔改和添加新道具，进行了消息发送的修改和排版，大量减少了消息量，防止风控
# demon道具列表及相关函数
item_dic1 = {
    1: "桃",
    2: "医疗箱",
    3: "放大镜",
    4: "眼镜",
    5: "手铐",
    6: "欲望之盒",
    7: "无中生有",
    8: "小刀",
    9: "酒",
    10: "啤酒",
    11: "刷新票",
    12: "手套",
    13: "骰子",
    14: "禁止卡",
    15: "墨镜",
}
# 身份模式道具列表
item_dic2 = {
    16: "双转团",
    17: "天秤",
    18: "休养生息",
    19: "玩具枪",
    20: "烈弓",
    21: "血刃",
    22: "黑洞",
    23: "金苹果",
    24: "铂金草莓",
    25: "肾上腺素",
    26: "烈性TNT",
}

item_dic = item_dic1 | item_dic2

# 定义身份模式死斗回合数方便更改
death_turn = 12
pangguang_turn = 5

# 定义不同状态对应的轮数限制
turn_limit = {
    1: death_turn,  # "死斗模式" 开启的轮数限制
    2: pangguang_turn,  # "膀胱模式" 开启的轮数限制
}

# 设定kp必定先手
kp_pl = 1234567890

# 定义道具效果的字典
item_effects = {
    "桃": "回复1点hp",
    "医疗箱": "回复2点hp，但跳过你的这一回合，并且对方的所有束缚解除！",
    "放大镜": "观察下一颗子弹的种类",
    "眼镜": "观察下两颗子弹的种类，但顺序未知",
    "手铐": "跳过对方下一回合（不可重复使用/与禁止卡一同使用）",
    "禁止卡": "跳过对方下1~2（随机）个回合，对方获得禁止卡（若对方道具已达6个上限，将不会获得禁止卡）",
    "欲望之盒": "50%抽取一个道具，30%恢复一点血量（若血量达到上限将赠与一个本轮无视道具上限的桃），20%对对方造成一点伤害",
    "无中生有": "抽取两个道具，然后跳过回合，对方若有束缚，束缚的回合-1！并且无中生有生成的道具直到本轮实弹耗尽前可以超出上限（本轮实弹耗尽后超出上限的道具会消失）！",
    "小刀": "伤害变为2（注：同时使用多个小刀或酒会导致浪费！）",
    "酒": "伤害变为2，同时若hp等于1时，回复1hp（注：同时使用多个小刀或酒会导致浪费！）",
    "啤酒": "退掉下一发子弹（若退掉的是最后一发子弹，进行道具的刷新）",
    "刷新票": "使用后，重新抽取和剩余道具数量相等的道具",
    "手套": "重新换弹，不进行道具刷新",
    "骰子": "你的hp变为1到4的随机值",
    "墨镜": "观察第一颗和最后一颗子弹的种类，但顺序未知",
    "双转团": "（该道具为“身份”模式专属道具）把这个道具转移到对方道具栏里，若对方道具已达上限则丢弃本道具；另外还有概率触发特殊效果？可能会掉血，可能会回血，可能会送给对方道具……但由于其富含identity，可能有其他的非bet2游戏内的效果？",
    "天秤": "（该道具为“身份”模式专属道具）如果你的道具数量≥对方道具数量，你对对方造成一点伤害；你的道具数量<对方道具数量，你回一点血",
    "休养生息": "（该道具为“身份”模式专属道具）自己的hp恢复2，对方的hp恢复1，不跳回合；若对面为满血，则只回一点体力。",
    "玩具枪": "（该道具为“身份”模式专属道具）1/2的概率无事发生，1/2的概率对对面造成1点伤害",
    "烈弓": "（该道具为“身份”模式专属道具）使用烈弓后，下一发子弹伤害+1，且伤害类道具（小刀、酒、烈弓）的加伤效果可以无限叠加！",
    "血刃": "（该道具为“身份”模式专属道具）可以扣除自己1点hp，获得两个道具！并且获得的道具直到本轮实弹耗尽前可以超出上限（本轮实弹耗尽后超出上限的道具会消失）",
    "黑洞": "（该道具为“身份”模式专属道具）召唤出黑洞，随机夺取对方的任意一个道具！\n如果对方没有道具，黑洞将在沉寂中回到你的身边。",
    "金苹果": "（该道具为“身份”模式专属道具）金苹果可以让你回复3点hp！但是作为代价你会跳过接下来的两个回合！不过对面的手铐和禁止卡也似乎不能使用了……",
    "铂金草莓": "（该道具为“身份”模式专属道具）因为是铂金草莓，所以能做到！自己回复1点hp，并且双方各加1点hp上限！",
    "肾上腺素": "（该道具为“身份”模式专属道具）双方的hp上限-1，道具上限+1，并且使用者获得一个新道具！如果你们的hp上限为1，无法使用该道具！",
    "烈性TNT": "（该道具为“身份”模式专属道具）双方的hp上限-1，hp各-1！注意，是先扣hp上限，然后再扣hp！另外，如果使用后会自杀，则无法使用该道具！",
}

help_msg = """
输入 .开枪 自己/对方 -|- 向自己/对方开枪
输入 .查看局势 -|- 查看当前局势
输入 .恶魔道具 道具名/all -|- 查看道具的使用说明
输入 .恶魔投降 -|- 进行投降
输入 .使用道具 道具名 -|- 使用道具"""

# 奖励设置
jiangli = 388
bet_tax = int(jiangli * 0.1)  # 向下取整计算 10%
final_jiangli = jiangli - bet_tax

# 全局变量——事件（单位s）
turn_time = 600


# 定义权重表
def get_random_item(identity_found, normal_mode_limit, user_id):
    """根据模式返回一个随机道具"""

    item_count = len(item_dic)  # 道具总数
    normal_mode_items = []  # 普通模式需要增加权重的道具（暂无）
    identity_mode_items = [3]  # 身份模式需要增加权重的道具（放大镜）

    # 动态生成权重表
    weights = dict.fromkeys(range(1, item_count + 1), 0)  # 初始化所有道具权重为0

    if identity_found == 0:
        # 普通模式：前 normal_mode_limit 个道具权重设为1，其他保持0
        for i in range(1, normal_mode_limit + 1):
            weights[i] = 1
    elif identity_found in [1, 2]:
        # 身份模式：所有道具启用，部分稀有道具权重设为2
        for i in range(1, item_count + 1):
            weights[i] = 1
        for i in identity_mode_items:
            weights[i] = 2  # 增加稀有道具的出现概率

    # 生成候选列表（按照权重扩展）
    valid_items = [i for i in weights if weights[i] > 0]
    item_choices = [i for i in valid_items for _ in range(weights[i])]

    return random.choice(item_choices)


# 特殊扣血逻辑
def death_mode_damage(action_type: int, group_id: str):
    """
    处理死斗模式特殊扣血逻辑
    :param action_type: 0=开枪自己, 1=开枪对方, 2=使用道具
    :param datas.demon_data: 游戏数据字典
    :param group_id: 群组ID
    :return: (消息内容, 更新后的datas.demon_data)
    """
    msg = ""

    return msg


# 上弹函数
def load(identity_found):
    """上弹，1代表实弹，0代表空弹"""
    # # 根据identity_found值决定弹夹容量和实弹数量
    # if identity_found in [2, 999]:
    #     clip_size = random.randint(3, 8)  # 弹夹容量3-8
    #     # 确保至少2个实弹，最多不超过弹夹容量-1（至少留一个空弹）
    #     bullets = random.randint(2, clip_size // 2 + 1)  # 随机生成实弹数量
    # else:
    #     clip_size = random.randint(2, 8)  # 默认弹夹容量2-8
    #     if clip_size == 2:
    #         # 如果总弹数为2，强制设置一个实弹
    #         clip = [0, 1]
    #         random.shuffle(clip)  # 随机打乱弹夹顺序
    #         return clip
    #     else:
    #         bullets = random.randint(1, clip_size // 2 + 1)  # 随机生成实弹数量

    # 定义实弹数量及其概率（1发30%，2发30%，3发20%，4发10%，5发10%）
    bullet_options = [1, 2, 3, 4, 5]
    bullet_weights = [0.3, 0.3, 0.2, 0.1, 0.1]

    # 使用加权随机选择实弹数量
    bullets = random.choices(bullet_options, weights=bullet_weights, k=1)[0]

    # 计算弹夹最小容量(实弹*2-1)，最大为8
    min_clip_size = bullets * 2 - 1
    clip_size = random.randint(min_clip_size, 8) if min_clip_size <= 8 else 8

    # 特殊情况处理：如果clip_size为1，固定为1实弹1空弹
    if clip_size == 1:
        clip = [0, 1]
        random.shuffle(clip)
        return clip

    # 生成弹夹
    clip = [0] * clip_size
    bullet_positions = random.sample(range(clip_size), bullets)  # 随机生成实弹位置
    for pos in bullet_positions:
        clip[pos] = 1
    return clip


# 游戏结束函数
def handle_game_end(
    group_id: str,
    winner: str,
    prefix_msg: str,
):

    # 构建基础消息
    msg = prefix_msg + "恭喜" + MessageSegment.at(str(winner)) + ("胜利！")

    # 重置游戏数据
    datas.demon_data[group_id] = demon_default()

    return msg


# 死斗函数
def death_mode(identity_found, group_id):
    """判断是否开启死斗模式：根据不同的状态和轮数进行血量上限扣减，保存状态后最后返回msg"""
    player0 = str(datas.demon_data[group_id]["pl"][0])
    player1 = str(datas.demon_data[group_id]["pl"][1])
    msg = ""

    if (
        identity_found in turn_limit
        and datas.demon_data[group_id]["game_turn"] > turn_limit[identity_found]
    ):
        msg += f"\n- 轮数大于{turn_limit[identity_found]}，死斗模式开启！\n"

        # HP 上限减少
        if identity_found in [1, 2] and datas.demon_data[group_id]["hp_max"] > 1:
            datas.demon_data[group_id]["hp_max"] -= 1
            new_hp_max = datas.demon_data[group_id]["hp_max"]
            msg += f"- {new_hp_max + 1}>1，扣1点hp上限，当前hp上限：{new_hp_max}\n"

            # 校准所有玩家血量不得超过 hp 上限
            for i in range(len(datas.demon_data[group_id]["hp"])):
                datas.demon_data[group_id]["hp"][i] = min(
                    datas.demon_data[group_id]["hp"][i],
                    datas.demon_data[group_id]["hp_max"],
                )

        # 额外扣除 1 点道具上限，并随机删除 1-2 个道具
        if identity_found in [1, 2]:
            if datas.demon_data[group_id]["item_max"] > 6:
                datas.demon_data[group_id]["item_max"] -= (
                    1  # 扣 1 点道具上限（最低仍为 6）
                )
                new_item_max = datas.demon_data[group_id]["item_max"]
                msg += f"- {new_item_max + 1}>6，扣1点道具上限，当前道具上限：{datas.demon_data[group_id]['item_max']}\n"

            remove_random = random.randint(1, 2)

            # 计算可删除的道具数量
            remove_count0 = (
                min(remove_random, len(datas.demon_data[group_id]["item_0"]))
                if datas.demon_data[group_id]["item_0"]
                else 0
            )
            remove_count1 = (
                min(remove_random, len(datas.demon_data[group_id]["item_1"]))
                if datas.demon_data[group_id]["item_1"]
                else 0
            )

            # 随机选择要删除的道具
            removed_items_0 = (
                random.sample(datas.demon_data[group_id]["item_0"], remove_count0)
                if remove_count0
                else []
            )
            removed_items_1 = (
                random.sample(datas.demon_data[group_id]["item_1"], remove_count1)
                if remove_count1
                else []
            )

            # 逐个删除选定的道具实例
            for item in removed_items_0:
                datas.demon_data[group_id]["item_0"].remove(item)

            for item in removed_items_1:
                datas.demon_data[group_id]["item_1"].remove(item)

            # 记录被删除的道具名称
            removed_names_0 = [item_dic.get(i, "未知道具") for i in removed_items_0]
            removed_names_1 = [item_dic.get(i, "未知道具") for i in removed_items_1]

            # 记录删除的信息
            if removed_names_0:
                msg += (
                    "- "
                    + MessageSegment.at(player0)
                    + f"失去了{remove_count0}个道具：{'、'.join(removed_names_0)}！\n"
                )
            if removed_names_1:
                msg += (
                    "- "
                    + MessageSegment.at(player1)
                    + f"失去了{remove_count1}个道具：{'、'.join(removed_names_1)}！\n"
                )

        # 跑团专用999模式，额外扣2点HP上限
        elif identity_found == 999 and datas.demon_data[group_id]["hp_max"] > 1:
            old_hp_max = datas.demon_data[group_id]["hp_max"]
            datas.demon_data[group_id]["hp_max"] -= 2
            datas.demon_data[group_id]["hp_max"] = max(
                1, datas.demon_data[group_id]["hp_max"]
            )
            new_hp_max = datas.demon_data[group_id]["hp_max"]
            msg += f"- {old_hp_max}>1，扣2点hp上限，当前hp上限：{new_hp_max}\n"

            # 校准所有玩家血量不得超过hp上限
            for i in range(len(datas.demon_data[group_id]["hp"])):
                datas.demon_data[group_id]["hp"][i] = min(
                    datas.demon_data[group_id]["hp"][i],
                    datas.demon_data[group_id]["hp_max"],
                )

    return msg


# 计算随机函数
def calculate_interval(game_turn_add, identity_found):
    # 设置默认值
    lower_bound = 1
    upper_bound = 3

    # 根据不同的模式计算道具刷新上下限
    # 普通模式
    if identity_found == 0:
        lower_bound = 1 + game_turn_add
        upper_bound = 3 + game_turn_add

    # 身份模式
    elif identity_found == 1:
        lower_bound = 1 + game_turn_add * 2
        upper_bound = 3 + game_turn_add * 2

    # 极速模式
    elif identity_found in [2, 999]:
        lower_bound = 3 + game_turn_add
        upper_bound = 5 + game_turn_add

    return lower_bound, upper_bound


# 刷新道具函数
def refersh_item(identity_found, group_id):
    idt_len = len(item_dic2)
    game_turn_add = 0
    msg = ""
    game_turn_cal = datas.demon_data[group_id]["game_turn"]

    if game_turn_cal == 1:
        game_turn_add = 1

    lower, upper = calculate_interval(game_turn_add, identity_found)
    player0 = str(datas.demon_data[group_id]["pl"][0])
    player1 = str(datas.demon_data[group_id]["pl"][1])
    hp0 = datas.demon_data[group_id]["hp"][0]
    hp1 = datas.demon_data[group_id]["hp"][1]
    # 重新获取hp_max
    hp_max = datas.demon_data.get(group_id, {}).get("hp_max")
    item_max = datas.demon_data.get(group_id, {}).get("item_max")
    for i in range(random.randint(lower, upper)):
        datas.demon_data[group_id]["item_0"].append(
            get_random_item(identity_found, len(item_dic) - idt_len, player0)
        )
        datas.demon_data[group_id]["item_1"].append(
            get_random_item(identity_found, len(item_dic) - idt_len, player1)
        )
    # 检查并限制道具数量上限为max
    datas.demon_data[group_id]["item_0"] = datas.demon_data[group_id]["item_0"][
        :item_max
    ]  # 截取前max个道具
    datas.demon_data[group_id]["item_1"] = datas.demon_data[group_id]["item_1"][
        :item_max
    ]  # 截取前max个道具
    # 生成道具信息
    item_0 = ", ".join(
        item_dic.get(i, "未知道具") for i in datas.demon_data[group_id]["item_0"]
    )
    item_1 = ", ".join(
        item_dic.get(i, "未知道具") for i in datas.demon_data[group_id]["item_1"]
    )
    # 获取玩家道具信息
    items_0 = datas.demon_data[group_id]["item_0"]  # 玩家0道具列表
    items_1 = datas.demon_data[group_id]["item_1"]  # 玩家1道具列表
    if len(items_0) == 0:
        item_0 = "你目前没有道具哦！"
    if len(items_1) == 0:
        item_1 = "你目前没有道具哦！"
    msg += (
        MessageSegment.at(player0)
        + f"\nhp：{hp0}/{hp_max}\n"
        + f"道具({len(items_0)}/{item_max})："
        + f"\n{item_0}\n\n"
    )
    msg += (
        MessageSegment.at(player1)
        + f"\nhp：{hp1}/{hp_max}\n"
        + f"道具({len(items_1)}/{item_max})："
        + f"\n{item_1}\n"
    )

    return msg


# 开枪函数
async def shoot(stp, group_id, message, args):
    hp_max = datas.demon_data.get(group_id, {}).get("hp_max")
    item_max = datas.demon_data.get(group_id, {}).get("item_max")
    clip = datas.demon_data.get(group_id, {}).get("clip")
    hp = datas.demon_data.get(group_id, {}).get("hp")
    pl = datas.demon_data.get(group_id, {}).get("turn")
    player0 = str(datas.demon_data[group_id]["pl"][0])
    player1 = str(datas.demon_data[group_id]["pl"][1])
    identity_found = datas.demon_data[group_id]["identity"]
    add_max = 0
    pangguang_add = 0
    # 身份模式开了就更新dlc
    idt_len = len(item_dic2)
    if identity_found == 1:
        idt_len = 0
        add_max += 1
    elif identity_found in [2, 999]:
        idt_len = 0
        add_max += 1
        pangguang_add += 2
    msg = ""
    if clip[-1] == 1:
        atk = datas.demon_data[group_id]["atk"]
        hp[pl - stp] -= 1 + atk
        datas.demon_data[group_id]["atk"] = 0
        datas.demon_data[group_id]["add_atk"] = False
        if atk != 0:
            msg += f"\n- 这颗子弹伤害为……{atk + 1}点！"
        if atk in [3, 4]:
            msg += "\n- 癫狂屠戮！"
        if atk >= 5:
            msg += "\n- 无双，万军取首！"
        msg += f"\n- 你开枪了，子弹 *【击中了】* {args}！{args}剩余hp：{hp[pl - stp]!s}/{hp_max}\n"
    else:
        datas.demon_data[group_id]["atk"] = 0
        datas.demon_data[group_id]["add_atk"] = False
        msg += (
            f"\n- 你开枪了，子弹未击中{args}！{args}剩余hp：{hp[pl - stp]!s}/{hp_max}\n"
        )
    del clip[-1]

    if len(clip) == 0 or clip.count(1) == 0:
        msg += "- 子弹用尽，重新换弹，道具更新！\n"
        # 游戏轮数+1
        datas.demon_data[group_id]["game_turn"] += 1
        turn = datas.demon_data[group_id]["game_turn"]
        msg += f"- 当前轮数：{turn}\n"
        # 调用死斗模式伤害计算 (stp=0是开枪自己，1是开枪对方)
        damage_msg = death_mode_damage(stp, group_id)
        msg += damage_msg
        # 获取死斗模式信息
        death_msg = death_mode(identity_found, group_id)
        msg += death_msg
        # 增加换行，优化排版
        msg += "\n"
        clip = load(identity_found)
        # 获取刷新道具
        item_msg = refersh_item(identity_found, group_id)
        msg += item_msg
        # 增加换行，优化排版
        msg += "\n"

    if datas.demon_data[group_id]["hcf"] < 0 and stp != 0:
        datas.demon_data[group_id]["hcf"] = 0
        out_pl = datas.demon_data[group_id]["pl"][
            datas.demon_data[group_id]["turn"] - 1
        ]
        msg += "- " + MessageSegment.at(str(out_pl)) + "已挣脱束缚！\n"
    if datas.demon_data[group_id]["hcf"] == 0 or stp == 0:
        pl += stp
        pl = pl % 2
    else:
        datas.demon_data[group_id]["hcf"] -= 2
    hcf = datas.demon_data.get(group_id, {}).get("hcf")
    if hcf != 0:
        msg += f"- 当前对方剩余束缚回合数：{(hcf + 1) // 2}\n"
    datas.demon_data[group_id]["turn"] = pl
    datas.demon_data[group_id]["clip"] = clip
    datas.demon_data[group_id]["hp"] = hp
    # 刷新时间
    datas.demon_data[group_id]["turn_start_time"] = int(time.time())
    if datas.demon_data[group_id]["hp"][0] <= 0:
        winner = datas.demon_data[group_id]["pl"][1]
        end_msg = handle_game_end(
            group_id=str(group_id),
            winner=winner,
            prefix_msg="- 游戏结束！",
        )
        msg += end_msg
    elif datas.demon_data[group_id]["hp"][1] <= 0:
        winner = datas.demon_data[group_id]["pl"][0]
        end_msg = handle_game_end(
            group_id=str(group_id),
            winner=winner,
            prefix_msg="- 游戏结束！",
        )
        msg += end_msg
    else:
        pid = datas.demon_data[group_id]["pl"][datas.demon_data[group_id]["turn"]]
        msg += (
            "- 本局总弹数为"
            + str(len(datas.demon_data[group_id]["clip"]))
            + "，实弹数为"
            + str(datas.demon_data[group_id]["clip"].count(1))
            + "\n"
            + "- 当前是"
            + MessageSegment.at(pid)
            + "的回合"
        )
    await message.finish(msg, at_sender=True)


# 开枪命令
fire = on_command(
    "开枪",
    aliases={"射击"},
    permission=GROUP,
    priority=1,
    block=True,
    rule=whitelist_rule,
)


@fire.handle()
async def fire_handle(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
    group_id = str(event.group_id)
    if await check_timeout(group_id):
        return
    user_id = str(event.user_id)
    args = str(arg).strip()
    player_turn = datas.demon_data[group_id]["turn"]

    if datas.demon_data[group_id]["start"] == False:
        await fire.finish("轮盘尚未开始！", at_sender=True)

    if user_id not in datas.demon_data[group_id]["pl"]:
        await fire.finish("只有当前局内玩家能行动哦！", at_sender=True)

    if datas.demon_data[group_id]["pl"][player_turn] != user_id:
        await fire.finish("现在不是你的回合，请等待对方操作！", at_sender=True)

    if args == "自己":
        stp = 0
        # 调用开枪函数
        await shoot(stp, group_id, fire, args)
    elif args == "对方":
        stp = 1
        # 调用开枪函数
        await shoot(stp, group_id, fire, args)
    else:
        await fire.finish(
            "指令错误！请输入 <.开枪 自己> 或者 <.开枪 对方> 来开枪哦！", at_sender=True
        )


# 使用道具
prop_demon = on_command(
    "使用",
    aliases={"使用道具"},
    permission=GROUP,
    priority=1,
    block=True,
    rule=whitelist_rule,
)


@prop_demon.handle()
async def prop_demon_handle(
    bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()
):
    group_id = str(event.group_id)
    if await check_timeout(group_id):
        return
    user_id = str(event.user_id)
    args = str(arg).strip()
    player_turn = datas.demon_data[group_id]["turn"]
    add_max = 0
    pangguang_add = 0
    if datas.demon_data[group_id]["start"] == False:
        await prop_demon.finish("轮盘尚未开始！", at_sender=True)

    if user_id not in datas.demon_data[group_id]["pl"]:
        await prop_demon.finish("只有当前局内玩家能行动哦！", at_sender=True)

    if datas.demon_data[group_id]["pl"][player_turn] != user_id:
        await prop_demon.finish("现在不是你的回合，请等待对方操作！", at_sender=True)
    identity_found = datas.demon_data[group_id]["identity"]
    # 身份模式开了就更新dlc
    idt_len = len(item_dic2)
    if identity_found == 1:
        idt_len = 0
        add_max += 1
    elif identity_found in [2, 999]:
        idt_len = 0
        add_max += 1
        pangguang_add += 2

    # 提取数据
    player_items = datas.demon_data[group_id][f"item_{player_turn}"]
    opponent_turn = (player_turn + 1) % len(datas.demon_data[group_id]["pl"])
    opponent_items = datas.demon_data[group_id][f"item_{opponent_turn}"]

    # 道具名称匹配（忽略大小写）
    args_lower = args.lower()
    item_dic_lower = {
        key: value.lower() for key, value in item_dic.items()
    }  # 生成一个忽略大小写的字典

    if (
        args_lower not in item_dic_lower.values()
    ):  # 检查输入的名称是否存在于 item_dic（忽略大小写）
        await prop_demon.finish("你输入的道具不存在，请确认后再使用！")

    # 查找玩家的道具中是否存在该道具
    try:
        # 遍历玩家的道具ID，找到第一个匹配的道具名称（忽略大小写）
        item_idx = next(
            i
            for i, item_id in enumerate(player_items)
            if item_dic[item_id].lower() == args_lower
        )
    except StopIteration:
        await prop_demon.finish("你并没有这个道具，请确认后再使用！", at_sender=True)

    # 提取数据
    item_id = player_items[item_idx]
    item_name = item_dic[item_id]
    hp_max = datas.demon_data.get(group_id, {}).get("hp_max")
    item_max = datas.demon_data.get(group_id, {}).get("item_max")
    msg = (
        MessageSegment.at(str(datas.demon_data[group_id]["pl"][player_turn]))
        + f"使用了道具：{item_name}\n\n"
    )
    player_items.pop(item_idx)
    datas.demon_data[group_id]["turn_start_time"] = int(time.time())  # 更新回合时间

    if item_name == "桃":
        datas.demon_data[group_id]["hp"][player_turn] += 1
        datas.demon_data[group_id]["hp"][player_turn] = min(
            hp_max, datas.demon_data[group_id]["hp"][player_turn]
        )
        msg += f"你的hp回复1点（最高恢复至体力上限）。\n当前hp：{datas.demon_data[group_id]['hp'][player_turn]}/{hp_max}\n"

    elif item_name == "医疗箱":
        datas.demon_data[group_id]["hp"][player_turn] += 2
        datas.demon_data[group_id]["hcf"] = 0
        datas.demon_data[group_id]["atk"] = 0
        datas.demon_data[group_id]["hp"][player_turn] = min(
            hp_max, datas.demon_data[group_id]["hp"][player_turn]
        )
        datas.demon_data[group_id]["turn"] = (player_turn + 1) % len(
            datas.demon_data[group_id]["pl"]
        )
        msg += f"你的hp回复2点（最高恢复至体力上限），但是代价是跳过本回合，而且对方的束缚将自动挣脱！\n当前hp：{datas.demon_data[group_id]['hp'][player_turn]}/{hp_max}\n"

    elif item_name == "放大镜":
        next_bullet = "实弹" if datas.demon_data[group_id]["clip"][-1] == 1 else "空弹"
        msg += f"下一颗子弹是：{next_bullet}！\n"

    elif item_name == "眼镜":
        if len(datas.demon_data[group_id]["clip"]) > 1:
            count_real_bullets = datas.demon_data[group_id]["clip"][-2:].count(1)
            msg += f"前两颗子弹中有 {count_real_bullets} 颗实弹。\n"
        else:
            msg += f"枪膛里只剩最后一颗子弹了，是{'实弹' if datas.demon_data[group_id]['clip'][-1] == 1 else '空弹'}！\n"

    elif item_name == "手铐":
        if datas.demon_data[group_id]["hcf"] == 0:
            datas.demon_data[group_id]["hcf"] = 1
            msg += "你成功拷住了对方！\n"
        else:
            player_items.append(item_id)
            msg += "不可使用！对方仍处于束缚状态！\n"

    elif item_name == "禁止卡":
        # 获取对方的回合编号
        if datas.demon_data[group_id]["hcf"] == 0:
            add_turn = random.randint(0, 1) * 2
            if add_turn == 0:
                skip_turn = 1
            else:
                skip_turn = 2
            datas.demon_data[group_id]["hcf"] = 1 + add_turn
            if len(opponent_items) < item_max:
                opponent_items.append(
                    item_id
                )  # 只有在对方道具少于 max_item 个时才增加禁止卡
                msg += f"你成功禁止住了对方！禁止了{skip_turn}个回合，但同时对方也获得了一张禁止卡！\n"
            else:
                msg += f"你成功禁止住了对方！禁止了{skip_turn}个回合，但对方道具已满，并未获得这张禁止卡！\n"
        else:
            player_items.append(item_id)
            msg += "不可使用！对方仍处于束缚状态！\n"

    elif item_name == "欲望之盒":
        randchoice = random.randint(1, 10)
        if randchoice <= 5:
            new_item = get_random_item(identity_found, len(item_dic) - idt_len, user_id)
            player_items.append(new_item)
            new_item_name = item_dic[new_item]
            msg += f"你打开了欲望之盒，获得了道具：{new_item_name}\n"
        elif randchoice <= 8:
            msg += "你打开了欲望之盒，恢复了1点体力\n"
            datas.demon_data[group_id]["hp"][player_turn] += 1
            if datas.demon_data[group_id]["hp"][player_turn] >= hp_max + 1:
                datas.demon_data[group_id]["hp"][player_turn] = hp_max
                player_items.append(1)
                msg += "但是由于你的体力已经到达上限，这点体力将转化为桃送给你。这个桃无视道具上限，但只有这轮有效。\n"
            msg += f"当前hp：{datas.demon_data[group_id]['hp'][player_turn]}/{hp_max}\n"
        elif randchoice <= 10:
            datas.demon_data[group_id]["hp"][opponent_turn] -= 1
            msg += f"你打开了欲望之盒，对对面造成了一点伤害！\n对方目前剩余hp为：{datas.demon_data[group_id]['hp'][opponent_turn]}\n"

    elif item_name == "无中生有":
        new_items = [
            get_random_item(identity_found, len(item_dic) - idt_len, user_id)
            for _ in range(2)
        ]
        player_items.extend(new_items)  # 添加新道具
        new_items_names = [item_dic[item] for item in new_items]
        if datas.demon_data[group_id]["hcf"] <= 0:
            datas.demon_data[group_id]["atk"] = 0
            datas.demon_data[group_id]["hcf"] = 0
            datas.demon_data[group_id]["turn"] = (player_turn + 1) % len(
                datas.demon_data[group_id]["pl"]
            )
            msg += f"你使用了无中生有，获得了道具：{', '.join(new_items_names)}\n代价是跳过了自己的回合!\n"
        elif datas.demon_data[group_id]["hcf"] >= 1:
            datas.demon_data[group_id]["hcf"] -= 2
            msg += f"你使用了无中生有，获得了道具：{', '.join(new_items_names)}\n代价是对方的束缚的回合将-1！\n"

    elif item_name == "小刀":
        if datas.demon_data[group_id]["add_atk"]:
            datas.demon_data[group_id]["atk"] += 1
            msg += f"你装备了小刀，由于受到烈弓的效果，这颗子弹的攻击力可以无限叠加！目前这颗子弹的攻击力为{datas.demon_data[group_id]['atk'] + 1}！\n"
        else:
            datas.demon_data[group_id]["atk"] = 1
            msg += "你装备了小刀，攻击力提升至两点！\n"

    elif item_name == "酒":
        if datas.demon_data[group_id]["add_atk"]:
            datas.demon_data[group_id]["atk"] += 1
            msg += f"你喝下了酒，由于受到烈弓的效果，这颗子弹的攻击力可以无限叠加！目前这颗子弹的攻击力为{datas.demon_data[group_id]['atk'] + 1}！\n"
        else:
            datas.demon_data[group_id]["atk"] = 1
            msg += "你喝下了酒，需不需要一把古锭刀？攻击力提升至两点！\n"

        if datas.demon_data[group_id]["hp"][player_turn] == 1:
            datas.demon_data[group_id]["hp"][player_turn] += 1
            msg += "酒精振奋了你，hp恢复到2点！\n"

    elif item_name == "啤酒":
        if datas.demon_data[group_id]["clip"]:
            removed_bullet = datas.demon_data[group_id]["clip"].pop()
            bullet_type = "实弹" if removed_bullet == 1 else "空弹"

            msg += f"- 你退掉了一颗子弹，这颗子弹是：{bullet_type}\n"

        if not datas.demon_data[group_id]["clip"] or all(
            b == 0 for b in datas.demon_data[group_id]["clip"]
        ):
            datas.demon_data[group_id]["clip"] = load(identity_found)
            msg += "- 子弹已耗尽，重新装填！\n"
            msg += "\n"
            # 游戏轮数+1
            datas.demon_data[group_id]["game_turn"] += 1
            # 调用死斗模式伤害计算 (action_type=2)
            damage_msg = death_mode_damage(2, group_id)
            msg += damage_msg
            # 获取死斗模式信息
            death_msg = death_mode(identity_found, group_id)
            msg += death_msg

            # 获取刷新道具
            item_msg = refersh_item(identity_found, group_id)
            msg += item_msg

            # 增加弹数消息，优化排版
            msg += (
                "\n- 本局总弹数为"
                + str(len(datas.demon_data[group_id]["clip"]))
                + "，实弹数为"
                + str(datas.demon_data[group_id]["clip"].count(1))
            )

    elif item_name == "刷新票":
        num_items = len(player_items)
        player_items.clear()
        player_items.extend(
            [
                get_random_item(identity_found, len(item_dic) - idt_len, user_id)
                for _ in range(num_items)
            ]
        )
        new_items_names = [item_dic[item] for item in player_items]
        if len(new_items_names) == 0:
            msg += "哎呀！你在只有刷新票的情况用了刷新票，现在一个新道具都没有！\n"
        else:
            msg += f"你刷新了你的所有道具，新道具为：{', '.join(new_items_names)}\n"

    elif item_name == "手套":
        datas.demon_data[group_id]["clip"] = load(identity_found)
        msg += f"你重新装填了子弹！新弹夹总数：{len(datas.demon_data[group_id]['clip'])} 实弹数：{datas.demon_data[group_id]['clip'].count(1)}\n"

    elif item_name == "骰子":
        random_hp = random.randint(1, 4)  # 生成一个随机的 hp 值
        random_hp = min(hp_max, random_hp)
        datas.demon_data[group_id]["hp"][player_turn] = random_hp
        msg += "恶魔掷出骰子……骰子掷出了新的hp值：\n"
        msg += f"你的的 hp 已变更成：{random_hp}！\n"

    elif item_name == "天秤":
        len_player_items = len(player_items)
        len_opponent_items = len(opponent_items)
        if len_player_items >= len_opponent_items:
            datas.demon_data[group_id]["hp"][opponent_turn] -= 1
            msg += f"天秤的指针开始转动…… 检测到你的道具数量为：{len_player_items}，对面的道具数量为：{len_opponent_items}；\n由于{len_player_items}≥{len_opponent_items}，你成功对对方造成一点伤害！\n对方目前剩余hp为：{datas.demon_data[group_id]['hp'][opponent_turn]}\n"
        else:
            datas.demon_data[group_id]["hp"][player_turn] += 1
            datas.demon_data[group_id]["hp"][player_turn] = min(
                hp_max, datas.demon_data[group_id]["hp"][player_turn]
            )
            msg += f"天秤的指针开始转动…… 检测到你的道具数量为：{len_player_items}，对面的道具数量为：{len_opponent_items}；\n由于{len_player_items}<{len_opponent_items}，你回复一点体力（最高恢复至上限！）！\n你目前的hp为：{datas.demon_data[group_id]['hp'][player_turn]}\n"

    elif item_name == "双转团":
        # 获取原始道具长度
        original_opponent_count = len(opponent_items)

        if len(opponent_items) < item_max:
            opponent_items.append(
                item_id
            )  # 只有在对方道具少于 max_item 个时才获得双转团
            msg += "这件物品太“IDENTITY”了，对方十分感兴趣，所以拿走了这件物品！\n"
        else:
            msg += "这件物品太“IDENTITY”了，对方十分感兴趣，但是由于道具已满，没办法拿走这件物品，所以把双转团丢了！\n"

        # 获取新的道具列表（双转团转移后的状态）
        now_player_items = datas.demon_data[group_id][f"item_{player_turn}"]
        now_opponent_items = datas.demon_data[group_id][f"item_{opponent_turn}"]
        # 首先 1/4 触发事件
        kou_first = random.randint(1, 4)
        kou_second = 0
        if kou_first == 1:
            kou_second = random.randint(1, 3)
        # 功能1：1/3概率转移随机道具
        if kou_second == 1 and len(now_player_items) > 0:  # 确保玩家还有道具
            random_idx = random.randint(0, len(now_player_items) - 1)
            random_item_id = player_items.pop(random_idx)
            random_item_name = item_dic[random_item_id]
            # 检查转移后的道具栏状态
            if len(now_opponent_items) < item_max:
                opponent_items.append(random_item_id)
                msg += f"- 对方还顺手拿走了你的【{random_item_name}】！\n"
                # 1/2扣对面一点血
                if random.randint(1, 2) == 1:
                    datas.demon_data[group_id]["hp"][opponent_turn] -= 1
                    oppo_hp = datas.demon_data[group_id]["hp"][opponent_turn]
                    datas.demon_data[group_id]["hp"][player_turn] = current_hp
                    msg += f"但是一不小心摔了一跤，hp-1！\n- 当前对方hp：{oppo_hp}/{hp_max}\n"
            else:
                msg += f"- 对方还顺手拿走了你的【{random_item_name}】，但是由于物品栏已满，他遗憾的把这件道具丢了！\n"

        # 功能2：1/3概率扣自己1点血，1/3加一点血
        elif kou_second == 2:
            datas.demon_data[group_id]["hp"][player_turn] -= 1
            hp = datas.demon_data[group_id]["hp"][player_turn]
            msg += f"你在丢双转团的时候太急了！人一旦急，就会更急，神就不会定，所以你一不小心把血条往左滑了一下，损失了1点hp！\n- 当前自己hp：{hp}/{hp_max}\n"

        elif kou_second == 3:
            datas.demon_data[group_id]["hp"][player_turn] += 1
            # 无法超过上限
            datas.demon_data[group_id]["hp"][player_turn] = min(
                hp_max, datas.demon_data[group_id]["hp"][player_turn]
            )
            hp = datas.demon_data[group_id]["hp"][player_turn]
            msg += f"你在丢双转团的时候太急了！人一旦急，就会更急，神就不会定，所以你一不小心把血条往右滑了一下，增加了1点hp！\n- 当前自己hp：{hp}/{hp_max}\n"

    elif item_name == "休养生息":
        if datas.demon_data[group_id]["hp"][opponent_turn] == hp_max:
            datas.demon_data[group_id]["hp"][player_turn] += 1  # 只回 1 点血
            msg += "休养生息，备战待敌；止兵止战，休养生息。\n对方hp已满，你仅恢复了1点hp。\n"
        else:
            datas.demon_data[group_id]["hp"][player_turn] += 2
            datas.demon_data[group_id]["hp"][opponent_turn] += 1
            msg += "休养生息，备战待敌；止兵止战，休养生息。\n你恢复了2点hp，对方恢复了1点hp（最高恢复至上限）。\n"

        # 校准所有玩家血量不得超过hp上限
        for i in range(len(datas.demon_data[group_id]["hp"])):
            datas.demon_data[group_id]["hp"][i] = min(
                datas.demon_data[group_id]["hp"][i],
                datas.demon_data[group_id]["hp_max"],
            )

        # 追加体力信息
        msg += (
            f"\n你的体力为 {datas.demon_data[group_id]['hp'][player_turn]}/{hp_max}\n"
        )
        msg += (
            f"对方的体力为 {datas.demon_data[group_id]['hp'][opponent_turn]}/{hp_max}\n"
        )

    elif item_name == "玩具枪":
        randchoice = random.randint(1, 2)
        if randchoice == 1:
            datas.demon_data[group_id]["hp"][opponent_turn] -= 1
            msg += f"你使用了玩具枪，可没想到里面居然是真弹！你对对面造成了一点伤害！\n对方目前剩余hp为：{datas.demon_data[group_id]['hp'][opponent_turn]}\n"
        else:
            msg += "你使用了玩具枪，这确实是一个可以滋水的玩具水枪，无事发生。\n"

    elif item_name == "血刃":
        if datas.demon_data[group_id]["hp"][player_turn] == 1:
            player_items.append(item_id)
            msg += "你的血量无法支持你使用血刃！\n"
        else:
            randchoice = random.randint(1, 5)
            datas.demon_data[group_id]["hp"][player_turn] -= 1
            new_items = [
                get_random_item(identity_found, len(item_dic) - idt_len, user_id)
                for _ in range(2)
            ]
            player_items.extend(new_items)  # 添加新道具
            new_items_names = [item_dic[item] for item in new_items]
            msg += f"你使用了血刃，献祭自己1盎司鲜血，祈祷，获得了道具：{', '.join(new_items_names)}\n你目前剩余hp为：{datas.demon_data[group_id]['hp'][player_turn]}/{hp_max}\n"
            if randchoice == 5:
                msg += (
                    "\n“血刃？你怎么会在这里？0猎的工资不够你用的吗，还跑过来再就业？”"
                )
                msg += "\n“唉，工作困难啊……抓玛德琳我太没存在感了，总是被人遗忘，必须要出来再就业了。”\n"

    elif item_name == "烈弓":
        datas.demon_data[group_id]["atk"] += 1
        datas.demon_data[group_id]["add_atk"] = True
        msg += f"你使用了烈弓，开始叠花色！烈弓解除了限制，并且伤害+1！现在酒和小刀的伤害可无限叠加！这颗子弹的攻击力可以无限叠加！目前这颗子弹的攻击力为{datas.demon_data[group_id]['atk'] + 1}！\n"

    elif item_name == "黑洞":
        if opponent_items:  # 对方有道具
            # 随机选择对方道具
            stolen_idx = random.randint(0, len(opponent_items) - 1)
            stolen_item_id = opponent_items.pop(stolen_idx)
            stolen_item_name = item_dic[stolen_item_id]

            player_items.append(stolen_item_id)  # 抢夺道具

            msg += (
                f"你召唤出黑洞！\n"
                f"空间开始剧烈扭曲……\n"
                f"对方的【{stolen_item_name}】被黑洞吞噬，送进你的背包！\n"
            )
        else:
            # 如果对方没有道具，黑洞会重新回到玩家背包
            player_items.append(item_id)
            msg += (
                "你召唤出黑洞！然而对方空无一物，黑洞在无尽的沉寂中回到了你的手中。\n"
            )

    elif item_name == "金苹果":
        datas.demon_data[group_id]["hp"][player_turn] += 3
        datas.demon_data[group_id]["hcf"] = 1
        datas.demon_data[group_id]["atk"] = 0
        datas.demon_data[group_id]["hp"][player_turn] = min(
            hp_max, datas.demon_data[group_id]["hp"][player_turn]
        )
        datas.demon_data[group_id]["turn"] = (player_turn + 1) % len(
            datas.demon_data[group_id]["pl"]
        )
        msg += f"你吃下了金苹果，因为太美味了，hp回复3点！但是由于过于美味，接下来你要好好回味这种味道，将直接跳过两个回合！不过对方的手铐和禁止卡也不能用了……\n当前hp：{datas.demon_data[group_id]['hp'][player_turn]}/{hp_max}\n"

    elif item_name == "铂金草莓":
        datas.demon_data[group_id]["hp"][player_turn] += 1
        hp_max += 1
        datas.demon_data[group_id]["hp_max"] = hp_max
        datas.demon_data[group_id]["hp"][player_turn] = min(
            hp_max, datas.demon_data[group_id]["hp"][player_turn]
        )
        msg += f"因为是铂金草莓，所以能做到。吃下铂金草莓后，你的hp回复1点，并且双方的hp上限均+1！要不要尝试去拿一个9dp？\n当前hp：{datas.demon_data[group_id]['hp'][player_turn]}/{hp_max}\n当前hp上限：{hp_max}\n"

    elif item_name == "肾上腺素":
        # 检查血量上限是否为1
        if datas.demon_data[group_id]["hp_max"] <= 1:
            player_items.append(item_id)
            msg += "你想使用肾上腺素，但是血量上限已经过低，你无法承受这种后果！\n"
        else:
            # 增加使用者的道具
            new_item = get_random_item(identity_found, len(item_dic) - idt_len, user_id)
            player_items.append(new_item)
            new_item_name = item_dic[new_item]
            # 调整hp上限和道具上限
            hp_max -= 1
            item_max += 1
            datas.demon_data[group_id]["hp_max"] = max(
                1, datas.demon_data[group_id]["hp_max"]
            )  # 血量上限保护锁
            datas.demon_data[group_id]["item_max"] = item_max
            datas.demon_data[group_id]["hp_max"] = hp_max
            new_hp_max = datas.demon_data[group_id]["hp_max"]
            # 校准所有玩家血量不得超过hp上限
            for i in range(len(datas.demon_data[group_id]["hp"])):
                datas.demon_data[group_id]["hp"][i] = min(
                    datas.demon_data[group_id]["hp"][i],
                    datas.demon_data[group_id]["hp_max"],
                )

            msg += (
                f"你注射了肾上腺素！心跳如雷，时间仿佛放慢，力量在血管中沸腾！\n"
                f"- 双方道具上限 +1！\n"
                f"- 你获得了新道具：{new_item_name}\n"
                f"- 当前道具上限：{item_max}\n\n"
                f"然而，一丝生命力被悄然抽离……对手也感到一阵莫名的心悸。\n"
                f"- 双方HP上限 -1！\n"
                f"- 当前HP上限：{hp_max}\n"
            )
    elif item_name == "烈性TNT":
        # 获取当前 HP 和 HP 上限
        current_hp = datas.demon_data[group_id]["hp"][player_turn]
        current_hp_max = datas.demon_data[group_id]["hp_max"]
        # 判定是否禁止使用 TNT
        if (
            current_hp_max <= 1
            or current_hp <= 1
            or (current_hp_max == 2 and current_hp == 2)
        ):
            player_items.append(item_id)
            msg += "你想引爆烈性TNT，但你的血量/血量上限已经过低，这样做无异于自杀！\n"
        else:
            datas.demon_data[group_id]["hp_max"] -= 1
            datas.demon_data[group_id]["hp_max"] = max(
                1, datas.demon_data[group_id]["hp_max"]
            )  # 确保体力上限不会降到 0，虽然前面有判断了

            # 校准所有玩家血量不得超过hp上限
            for i in range(len(datas.demon_data[group_id]["hp"])):
                datas.demon_data[group_id]["hp"][i] = min(
                    datas.demon_data[group_id]["hp"][i],
                    datas.demon_data[group_id]["hp_max"],
                )

            # 扣完上限调整血量后再扣血
            datas.demon_data[group_id]["hp"][player_turn] -= 1
            datas.demon_data[group_id]["hp"][opponent_turn] -= 1

            msg += (
                "你点燃了烈性TNT，产生了巨大的爆炸！\n"
                f"- 双方HP上限 -1！\n- 当前HP上限：{datas.demon_data[group_id]['hp_max']}\n"
                f"- 双方HP -1！\n- 你的HP：{datas.demon_data[group_id]['hp'][player_turn]}/{datas.demon_data[group_id]['hp_max']}\n- 对方HP：{datas.demon_data[group_id]['hp'][opponent_turn]}/{datas.demon_data[group_id]['hp_max']}\n"
            )

    elif item_name == "墨镜":
        if len(datas.demon_data[group_id]["clip"]) > 1:
            first_bullet = datas.demon_data[group_id]["clip"][0]
            last_bullet = datas.demon_data[group_id]["clip"][-1]
            real_bullet_count = first_bullet + last_bullet  # 计算两个位置的实弹数量
            msg += f"你戴上了墨镜，观察枪膛……\n第一颗和最后一颗子弹加起来，有{real_bullet_count}颗实弹！\n"
        else:
            msg += f"枪膛里只剩最后一颗子弹了，是{'实弹' if datas.demon_data[group_id]['clip'][-1] == 1 else '空弹'}！\n"
    else:
        msg += "道具不存在或无法使用！\n"

    next_player_turn = datas.demon_data[group_id]["turn"]  # 获取下一位玩家的 turn
    next_player_id = str(
        datas.demon_data[group_id]["pl"][next_player_turn]
    )  # 下一位玩家的 ID
    msg += "\n- 现在轮到" + MessageSegment.at(str(next_player_id)) + "行动！"
    if datas.demon_data[group_id]["hp"][0] <= 0:
        winner = datas.demon_data[group_id]["pl"][1]
        end_msg = handle_game_end(
            group_id=str(group_id),
            winner=winner,
            prefix_msg="- 游戏结束！",
        )
        msg += end_msg
    elif datas.demon_data[group_id]["hp"][1] <= 0:
        winner = datas.demon_data[group_id]["pl"][0]
        end_msg = handle_game_end(
            group_id=str(group_id),
            winner=winner,
            prefix_msg="- 游戏结束！",
        )
        msg += end_msg
    await prop_demon.finish(msg)


# 查看局势
check = on_command(
    "查看局势", permission=GROUP, priority=1, block=True, rule=whitelist_rule
)


@check.handle()
async def check_handle(event: GroupMessageEvent):
    group_id = str(event.group_id)
    if await check_timeout(group_id):
        return
    user_id = str(event.user_id)
    if datas.demon_data[group_id]["start"] == False:
        await check.finish("当前并没有开始任何一句轮盘哦！", at_sender=True)
    if user_id not in datas.demon_data[group_id]["pl"]:
        await check.finish("只有当前局内玩家能查看局势哦！", at_sender=True)
    # 生成玩家信息
    player0 = str(datas.demon_data[group_id]["pl"][0])
    player1 = str(datas.demon_data[group_id]["pl"][1])
    game_turn = datas.demon_data.get(group_id, {}).get("game_turn")
    hp_max = datas.demon_data.get(group_id, {}).get("hp_max")
    item_max = datas.demon_data.get(group_id, {}).get("item_max")
    hcf = datas.demon_data.get(group_id, {}).get("hcf")
    identity_found = datas.demon_data[group_id]["identity"]
    # 生成道具信息
    item_0 = ", ".join(
        item_dic.get(i, "未知道具") for i in datas.demon_data[group_id]["item_0"]
    )
    item_1 = ", ".join(
        item_dic.get(i, "未知道具") for i in datas.demon_data[group_id]["item_1"]
    )
    # 获取玩家道具信息
    items_0 = datas.demon_data[group_id]["item_0"]  # 玩家0道具列表
    items_1 = datas.demon_data[group_id]["item_1"]  # 玩家1道具列表
    if len(items_0) == 0:
        item_0 = "你目前没有道具哦！"
    if len(items_1) == 0:
        item_1 = "你目前没有道具哦！"
    # 生成血量信息
    hp0 = datas.demon_data[group_id]["hp"][0]
    hp1 = datas.demon_data[group_id]["hp"][1]
    atk = datas.demon_data[group_id]["atk"]
    identity_found = datas.demon_data[group_id]["identity"]
    # 步时信息
    elapsed = int(time.time()) - datas.demon_data[group_id]["turn_start_time"]
    remaining_seconds = (
        turn_time - elapsed
    )  # 计算剩余冷却时间, 全局变量，设定时间（秒）
    remaining_minutes = remaining_seconds // 60  # 剩余分钟数
    remaining_seconds = remaining_seconds % 60  # 剩余秒数
    msg = "- 本局模式："
    if identity_found == 1:
        # death_turn轮以后死斗模式显示
        if (
            identity_found in turn_limit
            and datas.demon_data[group_id]["game_turn"] > turn_limit[identity_found]
        ):
            msg += "（死斗）"
        msg += "身份模式\n"
    elif identity_found in [2, 999]:
        # 1轮以后死斗模式显示
        if (
            identity_found in turn_limit
            and datas.demon_data[group_id]["game_turn"] > turn_limit[identity_found]
        ):
            msg += "（死斗）"
        msg += "急速模式\n"
    else:
        msg += "正常模式\n"
    msg += f"- 本步剩余时间：{remaining_minutes}分{remaining_seconds}秒\n"
    msg += f"- 当前轮数：{game_turn}\n"
    if hcf != 0:
        msg += f"- 当前对方剩余束缚回合数：{(hcf + 1) // 2}\n"
    if atk > 0:
        msg += f"- 本颗子弹伤害为：{atk + 1}点\n"
    msg += (
        "\n"
        + MessageSegment.at(player0)
        + f"\nhp：{hp0}/{hp_max}\n"
        + f"道具({len(items_0)}/{item_max})："
        + f"\n{item_0}\n\n"
    )
    msg += (
        MessageSegment.at(player1)
        + f"\nhp：{hp1}/{hp_max}\n"
        + f"道具({len(items_1)}/{item_max})："
        + f"\n{item_1}\n\n"
    )
    msg += f"- 总弹数{len(datas.demon_data[group_id]['clip'])!s}，实弹数{datas.demon_data[group_id]['clip'].count(1)!s}\n"
    pid = datas.demon_data[group_id]["pl"][datas.demon_data[group_id]["turn"]]
    msg += "- 当前是" + MessageSegment.at(pid) + "的回合"
    await check.finish(msg)


# 恶魔投降指令：随时投降
demon_surrender = on_command("恶魔投降", permission=GROUP, priority=1, block=True)


@demon_surrender.handle()
async def demon_surrender_handle(event: Event):
    group_id = str(event.group_id)  # 获取群组ID
    player_id = str(event.user_id)  # 获取发出投降指令的玩家ID

    # 判断玩家是否在游戏中
    if datas.demon_data[group_id]["start"] == False:
        await demon_surrender.finish("当前没有进行中的游戏！", at_sender=True)
    # 获取当前游戏的玩家信息
    players = datas.demon_data[group_id]["pl"]  # 当前游戏中的两位玩家ID
    if player_id not in players:
        await demon_surrender.finish("你当前不在游戏中，无法投降！", at_sender=True)

    # 确定投降的玩家和获胜的玩家
    loser = player_id
    winner = str(players[1] if loser == players[0] else players[0])
    end_msg = handle_game_end(
        group_id=str(group_id),
        winner=winner,
        prefix_msg="玩家" + MessageSegment.at(loser) + "已投降。\n游戏结束，",
    )

    # 发送投降结果消息
    await demon_surrender.finish(end_msg)


# 恶魔道具查询功能：展示指定道具的效果
prop_demon_query = on_command(
    "恶魔道具", permission=GROUP, priority=1, block=True, rule=whitelist_rule
)


@prop_demon_query.handle()
async def prop_demon_query_handle(bot: Bot, event: Event, arg: Message = CommandArg()):
    # 去除前后空格，处理用户输入
    prop_name = str(arg).strip().lower()

    if prop_name == "":  # 没有输入默认all
        prop_name = "all"
    if prop_name == "all":  # 如果是查询所有道具
        # 构建所有道具的效果信息
        all_effects = "\n".join(
            [f"-【{prop}】：{effect}" for prop, effect in item_effects.items()]
        )

        # 构建转发的消息内容
        msg_list = [
            {
                "type": "node",
                "data": {
                    "name": "全部恶魔道具",
                    "uin": event.self_id,
                    "content": all_effects,
                },
            }
        ]
        # 转发全部道具效果消息
        await bot.call_api(
            "send_group_forward_msg", group_id=event.group_id, messages=msg_list
        )
        await prop_demon_query.finish()
    else:  # 查询指定道具
        # 创建一个忽略大小写的映射字典
        lower_to_original = {key.lower(): key for key in item_effects}

        # 查找原始名称
        original_name = lower_to_original.get(prop_name)

        if original_name:
            # 使用原始名称查询效果
            effect = item_effects[original_name]
            await prop_demon_query.finish(
                f"\n道具【{original_name}】的效果是：\n{effect}", at_sender=True
            )
        else:
            # 没找到匹配项
            await prop_demon_query.finish(
                f"未找到名为【{prop_name}】的道具，请检查道具名称是否正确！",
                at_sender=True,
            )


# 恶魔帮助
prop_demon_help = on_fullmatch(
    [".恶魔帮助", "。恶魔帮助"],
    permission=GROUP,
    priority=1,
    block=True,
    rule=whitelist_rule,
)


@prop_demon_help.handle()
async def prop_demon_help_handle():
    await prop_demon_help.finish(help_msg, at_sender=True)


# 超时检查
async def check_timeout(group_id):
    bots = get_bots()
    if not bots:
        logger.error("没有可用的Bot实例，无法检测bet2！")
        return None
    bot = list(bots.values())[0]  # 获取第一个 Bot 实例
    # 确保 'datas.demon_data' 和 'group_id' 存在
    # 初始化 group_id 中的游戏数据
    if group_id not in datas.demon_data:
        datas.demon_data[group_id] = demon_default()
    elapsed = int(time.time()) - datas.demon_data[group_id]["turn_start_time"]
    if elapsed > turn_time:  # 全局变量，设定时间（秒）
        # 判断游戏是否开始
        if datas.demon_data[group_id]["start"]:
            # 获取双方玩家
            player_turn = datas.demon_data[group_id]["turn"]
            opponent_turn = (player_turn + 1) % len(datas.demon_data[group_id]["pl"])
            player = datas.demon_data[group_id]["pl"][player_turn]
            non_current_player = datas.demon_data[group_id]["pl"][opponent_turn]

            end_msg = handle_game_end(
                group_id=str(group_id),
                winner=non_current_player,
                prefix_msg="回合超时！当前回合玩家"
                + MessageSegment.at(player)
                + "自动判负！",
            )
            msg = end_msg
            # 发送通知
            await bot.send_group_msg(group_id=group_id, message=msg)
            return True
        # 判断是否有人
        if len(datas.demon_data[group_id]["pl"]) == 1:
            player = datas.demon_data[group_id]["pl"][0]
            # 重置游戏
            datas.demon_data[group_id] = demon_default()
            # 发送通知
            await bot.send_group_msg(
                group_id=group_id,
                message="由于长时间无第二人进入轮盘，现已重置游戏。",
            )
            return True
    return False


# 30s检测是不是回合超时
@scheduler.scheduled_job("interval", seconds=30)
async def check_all_games():
    for group_id in list(datas.demon_data.keys()):
        if isinstance(group_id, str) and group_id.isdigit():
            await check_timeout(group_id)
