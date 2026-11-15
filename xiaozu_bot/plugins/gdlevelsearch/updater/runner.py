# updater/runner.py

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path

from nonebot import logger

from xiaozu_bot.utils.json_storage import write_json_atomic

from . import paths as updater_paths
from .jobs import (
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
    ratedplus,
    sfh,
    tpl,
)
from .paths import clear_staging, ensure_dirs, publish, staged

_lock = asyncio.Lock()

# Map each job name to its synchronous worker function. Most jobs take no
# arguments; getmetadata accepts the sources fetched successfully this run.
JOBS: dict[str, Callable[..., object]] = {
    "nlw": nlw.fetch,
    "ids": ids.fetch,
    "lw": lw.fetch,
    "hds": hds.fetch,
    "idl": idl.fetch,
    "lists": lists.fetch,
    "platdiff": platdiff.fetch,
    "tpl": tpl.fetch,
    "pemonlist": pemonlist.fetch,
    "platbatch": platbatch.batch_process,
    "sfh": sfh.fetch,
    "ratedplus": ratedplus.fetch,
    "getmetadata": getmetadata.main,
}

# Only direct upstream edges live here. A failure may travel along these edges
# and no further: unrelated jobs keep running, publishing and reloading.
DEPS: dict[str, tuple[str, ...]] = {
    "getmetadata": ("nlw", "ids", "lw", "hds", "idl"),
    "platbatch": ("tpl", "pemonlist", "platdiff"),
}

# getmetadata is a soft consumer: it enriches whichever upstreams succeeded.
# platbatch is strict: one missing upstream would produce a chart with holes.
STRICT_JOBS = frozenset({"platbatch"})

# Output files owned by each job. getmetadata rewrites its upstream files in
# staging, so it owns no file of its own.
OUTPUTS: dict[str, tuple[str, ...]] = {
    "nlw": ("nlw_levels.json",),
    "ids": ("ids_levels.json",),
    "lw": ("lw_levels.json",),
    "hds": ("hds_levels.json",),
    "idl": ("idl.json",),
    "lists": ("hdl.json", "mdl.json", "edl.json"),
    "tpl": ("tpl.json",),
    "pemonlist": ("pemonlist.json",),
    "platdiff": ("platdiff.json",),
    "platbatch": ("plat_combined.json",),
    "sfh": ("nong_index.json",),
    "ratedplus": ("ratedplus.json",),
    "getmetadata": (),
}

# The GDDL background task stages this file outside the main pipeline. Keep
# publishing it whenever present so the partial-publication change cannot
# break the existing GDDL snapshot path.
UNOWNED_FILES = ("gddl_levels.json",)

LAST_RUN_FILENAME = ".updater_last_run.json"


def last_run_path() -> Path:
    """Resolve the last-run state file at call time for test redirection."""
    return updater_paths.DATA_DIR / LAST_RUN_FILENAME


def load_last_run() -> dict | None:
    """Read the last pipeline result; return None when no run is recorded."""
    try:
        with last_run_path().open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_last_run(result: dict) -> None:
    """Persist the last result so status commands survive a bot restart."""
    try:
        write_json_atomic(last_run_path(), result, indent=2)
    except (OSError, TypeError) as exc:
        logger.warning(f"[RUNNER] 写不上一次更新状态，忽略: {exc}")


def _failure(name: str, error: Exception) -> dict:
    return {"job": name, "error": str(error), "type": type(error).__name__}


def _skipped(name: str, reason: str, missing: list[str]) -> dict:
    return {"job": name, "reason": reason, "missing": missing}


async def _run_job(name: str, *args: object) -> tuple[str, object | None, Exception | None]:
    """Run one blocking fetcher in a worker thread without cancelling others."""
    logger.info(f"[RUNNER] ▶ start job: {name}")
    try:
        value = await asyncio.to_thread(JOBS[name], *args)
    except Exception as e:
        logger.exception(f"[RUNNER] ✖ failed: {name}")
        return name, None, e
    logger.info(f"[RUNNER] ✔ success: {name}")
    return name, value, None


def _metadata_processed(metadata_files: list[str], result: object) -> set[str]:
    """Return the upstream files that really received fresh metadata."""
    if isinstance(result, dict):
        processed = result.get("processed")
        if isinstance(processed, list):
            return {str(name) for name in processed}
    # A legacy or mocked job without a structured result: trust the selection.
    return set(metadata_files)


def _publishable_files(
    status: dict[str, bool], metadata_files: list[str], metadata_result: object
) -> set[str]:
    """Compute the files whose whole dependency closure succeeded."""
    metadata_jobs = set(DEPS.get("getmetadata", ()))
    strict_jobs = set(DEPS.get("platbatch", ()))
    downstream = set(DEPS)

    allowed: set[str] = set(UNOWNED_FILES)
    for name, outputs in OUTPUTS.items():
        if name in downstream or not status.get(name):
            continue
        # Metadata sources wait for getmetadata; platform sources are only
        # meaningful as part of a complete platbatch merge.
        if name in metadata_jobs or name in strict_jobs:
            continue
        allowed.update(outputs)

    for name in downstream:
        if name == "getmetadata" or not status.get(name):
            continue
        allowed.update(OUTPUTS.get(name, ()))
        for upstream in DEPS.get(name, ()):
            allowed.update(OUTPUTS.get(upstream, ()))

    if status.get("getmetadata"):
        allowed.update(_metadata_processed(metadata_files, metadata_result))
    return allowed


async def run_all_async() -> dict:
    """Run independent branches and publish only clean dependency closures.

    A job failure skips exactly its direct downstream jobs and their outputs.
    Unrelated jobs still run and publish. Partial failures are reported through
    the result dict instead of an exception, so callers do not treat them as a
    total outage and skip the cache reload.
    """
    if _lock.locked():
        raise RuntimeError("已经有一个更新任务在跑了，等它跑完再来")

    async with _lock:
        ensure_dirs()
        clear_staging()

        results: dict = {
            "success": [],
            "failed": [],
            "skipped": [],
            "published": [],
            "finished_at": None,
        }
        status: dict[str, bool] = {}

        upstream_jobs = [name for name in JOBS if name not in DEPS]
        logger.info(f"[RUNNER] 第一层：{len(upstream_jobs)} 个独立任务并发")
        outcomes = await asyncio.gather(*(_run_job(name) for name in upstream_jobs))

        for name, _, error in outcomes:
            if error is None:
                results["success"].append(name)
                status[name] = True
            else:
                results["failed"].append(_failure(name, error))
                status[name] = False

        # Some legacy jobs log an error and return without writing anything.
        # Treat that as a failure, otherwise a downstream job would silently
        # read an old snapshot and publish it as if it were fresh.
        for name in upstream_jobs:
            if not status.get(name):
                continue
            missing = [f for f in OUTPUTS.get(name, ()) if not staged(f).exists()]
            if not missing:
                continue
            status[name] = False
            results["success"].remove(name)
            results["failed"].append(
                {
                    "job": name,
                    "error": f"no output written: {', '.join(missing)}",
                    "type": "MissingOutputError",
                }
            )

        downstream_jobs = [name for name in DEPS if name in JOBS]
        second_layer = []
        second_names: list[str] = []
        metadata_files: list[str] = []
        for name in downstream_jobs:
            upstreams = DEPS[name]
            missing = [u for u in upstreams if not status.get(u)]
            if missing and name in STRICT_JOBS:
                logger.warning(f"[RUNNER] 跳过 {name}：上游未全部成功 {missing}")
                results["skipped"].append(_skipped(name, "上游未全部成功", missing))
                status[name] = False
                continue
            available = [u for u in upstreams if status.get(u)]
            if not available:
                logger.warning(f"[RUNNER] 跳过 {name}：没有可用上游")
                results["skipped"].append(_skipped(name, "没有可用上游", missing))
                status[name] = False
                continue
            second_names.append(name)
            if name == "getmetadata":
                metadata_files = [OUTPUTS[u][0] for u in available]
                second_layer.append(_run_job(name, metadata_files))
            else:
                second_layer.append(_run_job(name))

        metadata_result: object = None
        if second_layer:
            logger.info(f"[RUNNER] 第二层：{', '.join(second_names)}（并发）")
            for name, value, error in await asyncio.gather(*second_layer):
                if error is not None:
                    results["failed"].append(_failure(name, error))
                    status[name] = False
                else:
                    results["success"].append(name)
                    status[name] = True
                    if name == "getmetadata":
                        metadata_result = value

        if isinstance(metadata_result, dict):
            failed_sources = metadata_result.get("failed")
            if isinstance(failed_sources, dict):
                for source, reason in failed_sources.items():
                    results["failed"].append(
                        {
                            "job": f"getmetadata:{source}",
                            "error": str(reason),
                            "type": "MetadataSourceError",
                        }
                    )

        allowed = _publishable_files(status, metadata_files, metadata_result)
        results["published"] = publish(allowed)
        results["finished_at"] = time.time()
        _write_last_run(results)

        logger.info(
            f"[RUNNER] finished: 成功 {len(results['success'])}，"
            f"失败 {len(results['failed'])}，跳过 {len(results['skipped'])}，"
            f"发布 {len(results['published'])} 个文件"
        )
        return results


def run_all() -> dict:
    """Run the asynchronous pipeline from a synchronous script."""
    return asyncio.run(run_all_async())
