# updater/__init__.py

import asyncio
import functools

from nonebot import get_driver, logger

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

DAILY_UPDATE_JOB_ID = "gdlevelsearch.daily_update"
GDDL_SNAPSHOT_REFRESH_JOB_ID = "gdlevelsearch.gddl_snapshot_refresh"


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


async def gddl_store_update_job() -> bool:
    """Refresh the published GDDL store immediately and reload its indexes."""
    if _gddl_lock.locked():
        logger.warning("[GDDL] store update skipped because another update is running")
        return False

    async with _gddl_lock:
        try:
            from ..api import gddl_store

            if not await asyncio.to_thread(gddl_store.refresh):
                raise RuntimeError("GDDL snapshot refresh failed")
        except Exception as e:
            logger.exception("[GDDL] store update failed")

            from .notify import report_error

            try:
                await report_error(
                    title="GDDL store 更新失败",
                    err=e,
                    context={"job": "gddl_store", "stage": "gddl_store"},
                )
            except Exception:
                logger.exception("[GDDL] store update error notification failed")
            return False

        logger.info("[GDDL] store update completed")
        return True


async def gddl_snapshot_refresh_job() -> None:
    """Refresh the GDDL snapshot at 07:00 without blocking the event loop."""
    if _gddl_lock.locked():
        logger.warning("[GDDL] snapshot refresh skipped: another update is running")
        return

    async with _gddl_lock:
        from ..api import gddl_store

        try:
            success = await asyncio.to_thread(gddl_store.refresh)
        except Exception as exc:
            logger.exception("[GDDL] snapshot refresh failed")
            from .notify import report_error

            try:
                await report_error(
                    title="GDDL snapshot refresh failed",
                    err=exc,
                    context={"job": GDDL_SNAPSHOT_REFRESH_JOB_ID, "stage": "refresh"},
                )
            except Exception:
                logger.exception("[GDDL] snapshot refresh notification failed")
            return

        if not success:
            logger.error("[GDDL] snapshot refresh failed")
            return
        logger.info("[GDDL] snapshot refresh completed")


async def _report_pipeline_error(err: Exception) -> None:
    """Report a pipeline-level crash without letting the notification crash."""
    from .notify import report_error

    try:
        await report_error(
            title="数据更新失败",
            err=err,
            context={"job": "daily_update", "stage": "run_all"},
        )
    except Exception:
        logger.exception("[UPDATER] 发送错误通知失败")


async def _report_partial_failure(result: dict) -> None:
    """Report the failed/skipped jobs, but never treat them as a total outage."""
    from .notify import report_error

    entries = list(result.get("failed") or []) + list(result.get("skipped") or [])
    summary = "; ".join(
        f"{entry.get('job')}: {entry.get('error') or entry.get('reason')}"
        for entry in entries
    )
    stamp = result.get("finished_at")
    # The notification dedup key is the error text; include the run time so a
    # recurring failure is reported once per run instead of once forever.
    err = RuntimeError(f"[{stamp}] {summary or '部分任务未完成'}")
    try:
        await report_error(
            title="数据更新部分失败",
            err=err,
            context={
                "job": "daily_update",
                "stage": "partial",
                "published": result.get("published") or [],
            },
        )
    except Exception:
        logger.exception("[UPDATER] 发送部分失败通知失败")


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
        - 无论主 updater 是全部成功还是部分失败，都要执行 reload_all()
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

    result: dict | None = None
    try:
        result = await run_all_async()
        logger.info(f"[UPDATER] 主数据更新完成: {result}")
    except Exception as e:
        logger.exception("[UPDATER] 主 updater 更新失败")
        await _report_pipeline_error(e)
    finally:
        # reload_all 里 aredl 那步需要网络请求，不能直接堵塞事件循环。
        # 部分失败也必须重载：成功的分支已经发布了新文件，而 aredl 的刷新
        # 就挂在这一步，跳过它会让 aredl 一直停在过期的缓存上。
        try:
            from .. import reload_all

            await asyncio.to_thread(reload_all)
            logger.info("[UPDATER] 数据重载完成")
        except Exception:
            logger.exception("[UPDATER] 数据重载失败")

    if result is not None and (result.get("failed") or result.get("skipped")):
        await _report_partial_failure(result)


def _register_cron(job_id: str, hour: int, func, label: str) -> bool:
    """Register one daily cron job; return False when APScheduler is unavailable."""
    try:
        from nonebot_plugin_apscheduler import scheduler
    except (ImportError, ValueError):
        logger.error(f"[UPDATER] apscheduler is unavailable; {label} disabled")
        return False

    try:
        job = scheduler.add_job(
            func,
            "cron",
            hour=hour,
            minute=0,
            id=job_id,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
    except Exception:
        logger.exception(f"[UPDATER] failed to register the {label} job")
        return False

    next_run = getattr(job, "next_run_time", "unknown")
    logger.info(
        f"[UPDATER] {label} job registered: "
        f"id={job_id}, schedule={hour:02d}:00, next_run={next_run!s}"
    )
    return True


def register_daily_update_job() -> bool:
    """Register the daily job after all NoneBot plugins have loaded."""
    return _register_cron(DAILY_UPDATE_JOB_ID, 3, daily_update_job, "daily update")


def register_gddl_snapshot_refresh_job() -> bool:
    """Register the 07:00 GDDL snapshot refresh job."""
    return _register_cron(
        GDDL_SNAPSHOT_REFRESH_JOB_ID, 7, gddl_snapshot_refresh_job, "GDDL snapshot refresh"
    )


def _register_daily_update_job_on_startup() -> None:
    """Register the jobs and fail startup if automatic updates are unavailable."""
    if not register_daily_update_job():
        raise RuntimeError("Automatic gdlevelsearch updates could not be registered")
    if not register_gddl_snapshot_refresh_job():
        raise RuntimeError("Automatic GDDL snapshot refresh could not be registered")
setup_updater()


# Register during startup rather than during module import.
#
# NoneBot plugin loading order is not guaranteed. Registering here means the
# APScheduler plugin has had a chance to load before we add the job, while
# standalone updater scripts can still import this package safely.
try:
    get_driver().on_startup(_register_daily_update_job_on_startup)
except ValueError:
    logger.warning(
        "[UPDATER] NoneBot is not initialized; skipping scheduler setup "
        "(expected in standalone updater mode)"
    )
