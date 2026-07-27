# updater/runner.py

import asyncio
from collections.abc import Callable

from nonebot import logger

from .jobs import (
    fetchsfh,
    getmetadata,
    hds,
    ids,
    lw,
    nlw,
    platbatch,
    platdata,
    platdiff,
    platrank,
)
from .paths import clear_staging, ensure_dirs, publish

_lock = asyncio.Lock()

# 每个任务： 任务名 -> 执行函数
JOBS: dict[str, Callable[[], None]] = {
    "nlw": nlw.fetch,
    "ids": ids.fetch,
    "lw": lw.fetch,
    "hds": hds.fetch,
    "platdiff": platdiff.fetch,
    "platrank": platrank.fetch,
    "platdata": platdata.fetch,
    "platbatch": platbatch.batch,
    "sfh": fetchsfh.main,
    "getmetadata": getmetadata.main,
}

# 按依赖分层，同一层之间没有先后关系，可以一起跑。
#   第 1 层：纯抓取，各写各的
#   第 2 层：platbatch 要 platdata/platdiff/platrank_weights，
#            getmetadata 要 nlw/ids/lw/hds
STAGES: tuple[tuple[str, ...], ...] = (
    ("nlw", "ids", "lw", "hds", "platdiff", "platrank", "platdata", "sfh"),
    ("platbatch", "getmetadata"),
)


async def _run_job(name: str) -> tuple[str, Exception | None]:
    """把同步的抓取函数丢到线程里跑。

    这些 job 用的是 requests，是同步阻塞的，直接 await 会把事件循环卡死。
    它们全是网络 IO，丢线程池里并发就够了，没必要为此重写成 httpx。
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
    """跑完整条流水线。

    同一层的任务并发跑，层与层之间等前一层全部结束。
    **全部成功才会把 staging 里的东西发布到 data/**，中途挂了线上数据一动不动。
    """
    if _lock.locked():
        raise RuntimeError("已经有一个更新任务在跑了，等它跑完再来")

    async with _lock:
        ensure_dirs()
        clear_staging()  # 清掉上次失败留下的残渣

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

            if results["failed"] and stop_on_error:
                logger.warning(
                    "[RUNNER] 这一层有失败的，停在这里不再往下跑，也不发布 —— "
                    "线上数据保持上一次的样子"
                )
                break

        if results["failed"]:
            # 不发布，staging 留着方便查问题
            raise RuntimeError(f"Updater failed: {results['failed']}")

        results["published"] = publish()
        logger.info(f"[RUNNER] finished: {results}")
        return results


def run_all(stop_on_error: bool = True) -> dict:
    """同步版，给脚本用。bot 里请用 run_all_async。"""
    return asyncio.run(run_all_async(stop_on_error))
