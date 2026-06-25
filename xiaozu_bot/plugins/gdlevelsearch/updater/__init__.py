# updater/__init__.py

from nonebot import logger
from nonebot_plugin_apscheduler import scheduler

from .notify import report_error
from .runner import run_all


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


@scheduler.scheduled_job("cron", hour=3, minute=0)
async def daily_update_job():
    """
    每日自动更新入口
    """
    logger.info("[UPDATER] 开始执行每日数据更新")

    try:
        result = run_all()

        logger.info(f"[UPDATER] 更新完成: {result}")

    except Exception as e:
        logger.exception("[UPDATER] 更新失败")

        await report_error(
            title="数据更新失败",
            err=e,
            context={
                "job": "daily_update",
                "stage": "run_all"
            }
        )