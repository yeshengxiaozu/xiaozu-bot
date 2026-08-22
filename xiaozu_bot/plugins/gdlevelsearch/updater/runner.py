# updater/runner.py

import asyncio
from collections.abc import Callable

from nonebot import logger

from .jobs import (
    fetchsfh,
    getmetadata,
    hds,
    idl,
    ids,
    lists,
    lw,
    nlw,
    pemonlist,
    platbatch,
    platdiff,
    tpl,
)
from .paths import clear_staging, ensure_dirs, publish

_lock = asyncio.Lock()

# Map each job name to its synchronous worker function.
JOBS: dict[str, Callable[[], None]] = {
    "nlw": nlw.fetch,
    "ids": ids.fetch,
    "lw": lw.fetch,
    "hds": hds.fetch,
    "idl": idl.fetch,
    "lists": lists.fetch,
    "platdiff": platdiff.fetch,
    "tpl": tpl.fetch,
    "pemonlist": pemonlist.fetch,
    "platbatch": platbatch.batch,
    "sfh": fetchsfh.main,
    "getmetadata": getmetadata.main,
}

# Jobs are grouped by dependency. Jobs in one stage can run concurrently.
# Stage 1 fetches independent sources; stage 2 consumes stage 1 outputs.
STAGES: tuple[tuple[str, ...], ...] = (
    (
        "nlw", "ids", "lw", "hds", "idl", "lists", "tpl", "pemonlist",
        "platdiff", "sfh",
    ),
    ("platbatch", "getmetadata"),
)


async def _run_job(name: str) -> tuple[str, Exception | None]:
    """Run one blocking fetcher in a worker thread.

    The jobs use synchronous requests. Moving them off the event loop keeps
    NoneBot responsive while allowing the stage to run concurrently.
    """
    logger.info(f"[RUNNER] ▶ start job: {name}")
    try:
        await asyncio.to_thread(JOBS[name])
    except Exception as e:
        logger.exception(f"[RUNNER] ✖ failed: {name}")
        return name, e
    logger.info(f"[RUNNER] ✔ success: {name}")
    return name, None


async def run_all_async(stop_on_error: bool = True) -> dict:
    """Run the staged update pipeline and publish successful output files.

    Every stage waits for all jobs in the previous stage. A failure leaves
    staging intact for diagnosis and prevents partial publication.
    """
    if _lock.locked():
        raise RuntimeError("已经有一个更新任务在跑了，等它跑完再来")

    async with _lock:
        ensure_dirs()
        clear_staging()  # Remove files left by a previous failed run.

        results: dict = {"success": [], "failed": [], "published": []}
        logger.info(f"[RUNNER] 开始，共 {len(STAGES)} 层")

        for depth, stage in enumerate(STAGES, start=1):
            logger.info(f"[RUNNER] 第 {depth} 层：{', '.join(stage)}（并发）")
            outcomes = await asyncio.gather(*(_run_job(name) for name in stage))

            for name, error in outcomes:
                if error is None:
                    results["success"].append(name)
                else:
                    results["failed"].append(
                        {"job": name, "error": str(error), "type": type(error).__name__}
                    )

            fatal_failures = list(results["failed"])
            if fatal_failures and stop_on_error:
                logger.warning(
                    "[RUNNER] 这一层有失败的，停在这里不再往下跑，也不发布 —— "
                    "线上数据保持上一次的样子"
                )
                break

        fatal_failures = list(results["failed"])
        if fatal_failures:
            # Keep staging intact so operators can inspect the failed output.
            raise RuntimeError(f"Updater failed: {fatal_failures}")

        results["published"] = publish()
        logger.info(f"[RUNNER] finished: {results}")
        return results


def run_all(stop_on_error: bool = True) -> dict:
    """Run the asynchronous pipeline from a synchronous script."""
    return asyncio.run(run_all_async(stop_on_error))
