# updater/__init__.py

import asyncio

from nonebot import logger

from .runner import run_all_async


def setup_updater() -> None:
    from .paths import ensure_dirs
    ensure_dirs()

    import ssl

    import certifi

    ssl._create_default_https_context = ssl.create_default_context(
        cafile=certifi.where()
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
try:
    from nonebot_plugin_apscheduler import scheduler
except ImportError:  # pragma: no cover - 只有脱离 bot 进程单独跑时才会走到
    logger.warning("[UPDATER] 没有 apscheduler，跳过定时任务注册（单独跑脚本时这是正常的）")
else:
    scheduler.scheduled_job("cron", hour=3, minute=0)(daily_update_job)
