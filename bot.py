import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter
from nonebot.adapters.qq import Adapter as QQAdapter

from xiaozu_bot.utils.adapter_compat import install_qq_rich_media_compat

nonebot.init(driver="~fastapi+~httpx+~websockets")

driver = nonebot.get_driver()
install_qq_rich_media_compat()
driver.register_adapter(ONEBOT_V11Adapter)
driver.register_adapter(QQAdapter)


nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()
