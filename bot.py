# import bot_config
from os import path
import nonebot
from nonebot.adapters.console import Adapter as ConsoleAdapter
from nonebot.adapters.onebot.v11 import Adapter as OnebotAdapter


nonebot.init()
# nonebot.init(bot_config)

driver = nonebot.get_driver()
#driver.register_adapter(ConsoleAdapter)
driver.register_adapter(OnebotAdapter)

nonebot.load_builtin_plugins("echo")
#nonebot.load_plugin("thirdparty_plugin")
nonebot.load_plugins("xiaozu_bot/plugins")

if __name__ == '__main__':
    nonebot.run()