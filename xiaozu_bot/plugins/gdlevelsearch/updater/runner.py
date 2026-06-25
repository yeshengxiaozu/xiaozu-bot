# updater/runner.py

import asyncio
from typing import Callable

from nonebot import logger

from .jobs import fetchsfh, getmetadata, hds, ids, lw, nlw, platbatch, platdata, platdiff, platrank  # 你的具体任务模块

_lock = asyncio.Lock()

# 每个任务： (任务名, 执行函数)
JOBS: list[tuple[str, Callable[[], None]]] = [
    ("nlw", nlw.fetch),
    ("ids", ids.fetch),
    ("lw", lw.fetch),
    ("hds", hds.fetch),
    ("platdiff", platdiff.fetch),
    ("platrank", platrank.fetch),
    ("platdata", platdata.fetch),
    ("platbatch", platbatch.batch),
    ("sfh", fetchsfh.main),
    ("getmetadata", getmetadata.main),
]


def run_all(stop_on_error: bool = True) -> dict:
    """
    严格顺序执行版本
    """

    results = {
        "success": [],
        "failed": []
    }

    logger.info("[RUNNER] sequential execution start")

    for name, job in JOBS:
        logger.info(f"[RUNNER] ▶ start job: {name}")

        try:
            job()  # ⭐关键：严格同步顺序执行

            results["success"].append(name)
            logger.info(f"[RUNNER] ✔ success: {name}")

        except Exception as e:
            logger.exception(f"[RUNNER] ✖ failed: {name}")

            results["failed"].append({
                "job": name,
                "error": str(e),
                "type": type(e).__name__
            })

            # ⭐关键控制点：是否中断
            if stop_on_error:
                logger.warning("[RUNNER] stop_on_error enabled, aborting pipeline")
                break

    logger.info(f"[RUNNER] finished: {results}")

    # ❗只要失败就抛（避免虚空更新）
    if results["failed"]:
        raise RuntimeError(f"Updater failed: {results['failed']}")

    return results