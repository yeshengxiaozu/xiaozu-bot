from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot import on_command

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="help",
    description="",
    usage="",
    config=Config,
)

config = get_plugin_config(Config)

help = on_command("help")
helpb = on_command("helpb")

@help.handle()
async def handle_function():
    await help.finish(
'''欢迎使用小小卒！命令以*开头
用户群：1035708051可以在里面对bot功能提建议什么的
help：本命令！
helpb：蓝莓相关功能说明
小小卒对你回应按按钮：非法输入
map：随机抽一张图（不影响蓝莓数）
random：在你输入的选项中随机抽取一个
ultra：不要再！
nsdd：你说得对，但是……
insult：这破壁游戏……
say：让机器人说一句话？（不定时开放）
jwz x：我能在患有健忘症的情况下患有健忘症吗？
guess(_start(_hard)/giveup)：猜酱图 hard题目更小
按钮是答错了，问号是目前没有题（一般是题目被答完了）''')
    
@helpb.handle()
async def handle_function():
    await helpb.finish(
'''这里是蓝莓相关说明！
---注意：以下功能在伯特群外不可用---
buy x：将x个草莓转化为蓝莓
sell x/"all"：将x个/全部蓝莓提现
ck：查看你目前的蓝莓数
roulette：消耗10蓝莓随机抽一张图，抽到特定的图可以赢得奖池里一部分蓝莓
roulette_pool：查询目前奖池蓝莓数XD
使用蓝莓的原因：每次草莓变动都需要和Fhloy/Foxeline通信
大量通信会大量增加desom那边的负担和风控概率
所以在Fhloy半夜boom了之后就有了这一套蓝莓系统
使用各种命令时用蓝莓的变动替代草莓的变动
在使用小卒bot的蓝莓相关功能前要先使用*buy
将草莓1:1转化成蓝莓之后才能用于小卒bot相关功能
之后可以用*sell将蓝莓重新转化成草莓实现提现
此外：guess功能在且仅在bot群奖励蓝莓''')