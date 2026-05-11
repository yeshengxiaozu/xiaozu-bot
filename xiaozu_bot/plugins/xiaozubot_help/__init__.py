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

@help.handle()
async def handle_function():
    await help.finish(
'''欢迎使用小小卒！命令以*开头
用户群：1035708051可以在里面对bot功能提建议什么的
help：本命令！
小小卒对你回应按按钮：非法输入
gdsearch：搜一个gd图（因为技术原因限制，暂时只能搜demon）
random：在你输入的选项中随机抽取一个
say：让机器人开口说话？
ai：你见过的最弱智的ai聊天

ultra：不要再！
say：让机器人说一句话？（不定时开放）
jwz x：我能在患有健忘症的情况下患有健忘症吗？
guess(_start(_hard/_ultra)/giveup)：猜酱图 hard题目更小
按钮是答错了，问号是目前没有题（一般是题目被答过）
zhua：抓一个小卒？''')