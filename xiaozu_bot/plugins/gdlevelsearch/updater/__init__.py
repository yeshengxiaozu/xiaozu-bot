# updater/__init__.py

import asyncio
import functools

from nonebot import logger

from .runner import run_all_async

# GDDL 是独立于主 updater 的后台更新任务。
#
# 主 updater 通常约 5 分钟完成，而 GDDL 通常需要约 20 分钟。
# 因此两者在每天 03:00 同时启动，互不等待。
#
# 保存 Task 引用，避免后台任务成为没有强引用的悬空任务。
_background_tasks: set[asyncio.Task] = set()

# 防止某一次 GDDL 更新异常延长后，与下一次定时任务同时运行。
_gddl_lock = asyncio.Lock()


def _create_background_task(coro) -> asyncio.Task:
    """
    创建一个后台 Task，并保存引用直到任务结束。
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def setup_updater() -> None:
    from .paths import ensure_dirs

    ensure_dirs()

    import ssl

    import certifi

    # 标准库把这个名字当工厂使用：
    #
    #     context = ssl._create_default_https_context()
    #
    # 因此这里必须挂一个可调用对象。
    #
    # 不能直接挂 create_default_context(...) 返回的 SSLContext，
    # 否则标准库后续调用时会变成：
    #
    #     SSLContext(...)
    #
    # 从而产生 TypeError。
    #
    # 使用 partial 后，每次调用都会创建一个新的 SSLContext。
    ssl._create_default_https_context = functools.partial(
        ssl.create_default_context,
        cafile=certifi.where(),
    )

    logger.info("[UPDATER] initialized")


async def gddl_update_job() -> None:
    """
    独立的 GDDL 更新任务。

    GDDL 不属于主 updater 流水线。
    即使 GDDL 更新失败，也不会影响主 updater。

    同一时间只允许存在一个 GDDL 更新任务。
    """
    if _gddl_lock.locked():
        logger.warning(
            "[GDDL] 上一次 GDDL 更新仍在运行，"
            "跳过本次更新"
        )
        return

    async with _gddl_lock:
        logger.info("[GDDL] 开始更新")

        try:
            # gddl.fetch() 是同步函数，并且内部使用 requests。
            # 不能直接在事件循环中执行。
            from .jobs import gddl

            await asyncio.to_thread(gddl.fetch)

        except Exception as e:
            logger.exception("[GDDL] 更新失败")

            from .notify import report_error

            try:
                await report_error(
                    title="GDDL 更新失败",
                    err=e,
                    context={
                        "job": "gddl",
                        "stage": "gddl",
                    },
                )
            except Exception:
                # 通知本身失败不能继续向外抛，
                # 否则会产生一个未处理的后台 Task 异常。
                logger.exception(
                    "[GDDL] 发送错误通知失败"
                )

            return

        logger.info("[GDDL] 更新完成")


async def daily_update_job() -> None:
    """
    每日自动更新入口。

    03:00 时同时启动：

        1. 主 updater
        2. GDDL updater

    主 updater 通常约 5 分钟。
    GDDL 通常约 20 分钟。

    两者完全独立：
        - GDDL 失败不会导致主 updater 失败
        - 主 updater 失败不会取消 GDDL
        - 主 updater 完成后立即执行 reload_all()
        - 不需要等待 GDDL 完成
    """
    logger.info("[UPDATER] 开始执行每日数据更新")

    # GDDL 独立后台运行。
    #
    # 不 await。
    # 主 updater 不需要等待 GDDL。
    _create_background_task(
        gddl_update_job()
    )

    try:
        # 主 updater 正常执行。
        result = await run_all_async()

        logger.info(
            f"[UPDATER] 主数据更新完成: {result}"
        )

        # 抓完必须重载，否则新数据要等下次重启才生效。
        from .. import reload_all

        # reload_all 里 aredl 那步需要网络请求，
        # 不能直接堵塞事件循环。
        await asyncio.to_thread(reload_all)

        logger.info("[UPDATER] 数据重载完成")

    except Exception as e:
        logger.exception(
            "[UPDATER] 主 updater 更新失败"
        )

        from .notify import report_error

        try:
            await report_error(
                title="数据更新失败",
                err=e,
                context={
                    "job": "daily_update",
                    "stage": "run_all",
                },
            )
        except Exception:
            logger.exception(
                "[UPDATER] 发送错误通知失败"
            )


setup_updater()


# 定时任务的注册放在最后，而且允许失败。
#
# 这样 scripts/run_updater.py 之类的独立脚本仍然可以：
#
#     import updater
#
# 而不会因为没有初始化 NoneBot 就在 import 阶段炸掉。
try:
    from nonebot_plugin_apscheduler import scheduler
except (ImportError, ValueError):
    logger.warning(
        "[UPDATER] 没有 apscheduler 或 nonebot 未初始化，"
        "跳过定时任务注册"
        "（单独跑脚本时这是正常的）"
    )
else:
    scheduler.scheduled_job(
        "cron",
        hour=3,
        minute=0,
    )(daily_update_job)
