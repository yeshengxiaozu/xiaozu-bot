# updater/__init__.py

import asyncio
import functools

from nonebot import logger

from .runner import run_all_async


def setup_updater() -> None:
    from .paths import ensure_dirs
    ensure_dirs()

    import ssl

    import certifi

    # 标准库把这个名字当**工厂**用（http.client.HTTPSConnection.__init__ 里是
    # `context = ssl._create_default_https_context()`），所以只能挂一个可调用对象。
    # 挂成 create_default_context(...) 的返回值（一个 SSLContext 实例）的话，
    # 整个进程里任何不显式传 context 的 HTTPS 连接都会 TypeError。
    # 而且标准库拿到 context 之后会就地改它（set_alpn_protocols /
    # post_handshake_auth），本来也必须每条连接给一个新的。
    ssl._create_default_https_context = functools.partial(
        ssl.create_default_context, cafile=certifi.where()
    )
    logger.info("[UPDATER] initialized")

setup_updater()


async def daily_update_job() -> None:
    """
    每日自动更新入口
    """
    logger.info("[UPDATER] 开始执行每日数据更新")

    try:
        result = await run_all_async()

        logger.info(f"[UPDATER] 更新完成: {result}")

        # 抓完必须重载，否则新数据要等下次重启才生效
        from .. import reload_all  # 延迟 import，避开和父包的循环依赖

        # reload_all 里 aredl 那步是要走网络的，别堵在事件循环上
        await asyncio.to_thread(reload_all)

    except Exception as e:
        logger.exception("[UPDATER] 更新失败")

        from .notify import report_error

        await report_error(
            title="数据更新失败",
            err=e,
            context={
                "job": "daily_update",
                "stage": "run_all"
            }
        )


# 定时任务的注册放在最后，而且要能失败。
# 这样 scripts/run_updater.py 那种不在 bot 进程里的场景也能 import 这个包
# ——jobs/ 里那一堆 `except ImportError: from updater.paths import ...` 本来就是奔着这个去的。
#
# 只 catch ImportError 是不够的：nonebot_plugin_apscheduler 装着的时候 import
# 本身能成，但它的模块体里会调 get_driver()，没 nonebot.init() 就抛
# ValueError("NoneBot has not been initialized.")。也就是说单独跑脚本的场景
# 走的根本不是 ImportError 那一支，scripts/run_updater.py 会在 import 期直接炸。
try:
    from nonebot_plugin_apscheduler import scheduler
except (ImportError, ValueError):  # pragma: no cover - 只有脱离 bot 进程单独跑时才会走到
    logger.warning("[UPDATER] 没有 apscheduler 或 nonebot 未初始化，跳过定时任务注册（单独跑脚本时这是正常的）")
else:
    scheduler.scheduled_job("cron", hour=3, minute=0)(daily_update_job)
