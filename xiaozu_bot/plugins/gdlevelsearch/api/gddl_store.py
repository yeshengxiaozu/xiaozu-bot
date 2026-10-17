"""Local GDDL level snapshot and offline query helpers."""

from __future__ import annotations

import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nonebot import logger

from ..paths import DATA_DIR

DATA_FILE = DATA_DIR / "gddl_levels.json"

PAGE_SIZE = 25
FETCH_INTERVAL = 5
FETCH_WORKERS = 5
RETRY_WAIT = 60
MIN_COMPLETE_RATIO = 0.95
TIER_MIN = 1.0
TIER_MAX = 39.0
GDDL_TIER_MIN = TIER_MIN
GDDL_TIER_MAX = TIER_MAX

levels: list[dict[str, Any]] = []
by_id: dict[int, dict[str, Any]] = {}
_by_name: dict[str, list[dict[str, Any]]] = {}
fetched_at: str | None = None


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one best-effort snapshot scan."""

    levels: list[dict[str, Any]]
    failed_pages: list[int]
    fetched_at: str


def loaded() -> bool:
    return bool(by_id)


def get_by_id(level_id: str | int) -> dict[str, Any] | None:
    try:
        return by_id.get(int(level_id))
    except (TypeError, ValueError):
        return None


def get_by_name(name: str) -> list[dict[str, Any]]:
    return list(_by_name.get(name.strip().lower(), ()))


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _matches(level: dict[str, Any], filters: dict[str, Any]) -> bool:
    name = filters.get("Name")
    if name is not None and str(level.get("name") or "").strip().lower() != str(name).strip().lower():
        return False

    difficulty = filters.get("difficulty")
    if difficulty is not None and (
        str(level.get("difficulty") or "").strip().lower()
        != str(difficulty).strip().lower()
    ):
        return False

    rating = _number(level.get("rating"))
    enjoyment = _number(level.get("enjoyment"))
    bounds = (
        (rating, "minRating", "maxRating"),
        (enjoyment, "minEnjoyment", "maxEnjoyment"),
    )
    for value, minimum, maximum in bounds:
        if filters.get(minimum) is not None:
            lower = _number(filters[minimum])
            if lower is None or value is None or value < lower:
                return False
        if maximum is not None and filters.get(maximum) is not None:
            upper = _number(filters[maximum])
            if upper is None or value is None or value > upper:
                return False
    return True


def search_levels(
    page: int = 0,
    limit: int = 1,
    sort: str = "ID",
    sort_direction: str | None = None,
    **filters: Any,
) -> dict[str, Any] | None:
    """Run the subset of GDDL search supported by the local snapshot.

    The payload uses the same keys as the remote search endpoint (notably the
    rows under "data"), because Gddl.searchlevels hands either source to the
    same callers.
    """
    supported = {
        "name",
        "difficulty",
        "minRating",
        "maxRating",
        "minEnjoyment",
        "maxEnjoyment",
        "minSubmissionCount",
    }
    if any(key not in supported and value is not None for key, value in filters.items()):
        return None

    result = [level for level in levels if _matches(level, filters)]
    direction = (sort_direction or "asc").lower()
    if len(result) == 0:
        return None
    if sort.lower() == "random":
        random.shuffle(result)
    elif sort.lower() == "id":
        result.sort(key=lambda level: int(level.get("id", 0)), reverse=direction == "desc")
    else:
        return None

    page = max(0, int(page))
    limit = max(1, int(limit))
    start = page * limit
    return {
        "total": len(result),
        "limit": limit,
        "page": page,
        "data": result[start : start + limit],
    }


def pick_random(
    low: int,
    high: int,
    enjoyment_min: float | None = None,
    enjoyment_max: float | None = None,
) -> dict[str, Any] | None:
    low_exact = max(low - 0.5, TIER_MIN)
    high_exact = min(high + 0.5, TIER_MAX)
    pool: list[dict[str, Any]] = []
    for level in levels:
        rating = _number(level.get("rating"))
        if rating is None or not low_exact <= rating <= high_exact:
            continue
        enjoyment = _number(level.get("enjoyment"))
        if enjoyment_min is not None and (
            enjoyment is None or enjoyment < enjoyment_min
        ):
            continue
        if enjoyment_max is not None and (
            enjoyment is None or enjoyment > enjoyment_max
        ):
            continue
        pool.append(level)
    return random.choice(pool) if pool else None


def _rebuild_indexes(new_levels: list[dict[str, Any]], stamp: str | None) -> None:
    """Replace all lookup indexes from one validated snapshot."""
    global fetched_at
    levels.clear()
    by_id.clear()
    _by_name.clear()
    for level in new_levels:
        try:
            level_id = int(level["id"])
        except (KeyError, TypeError, ValueError):
            continue
        level["id"] = level_id
        levels.append(level)
        by_id[level_id] = level
        name = str(level.get("name") or "").strip().lower()
        if name:
            _by_name.setdefault(name, []).append(level)
    fetched_at = stamp


def reload(path: Path | None = None) -> None:
    """Load a usable local snapshot and rebuild its in-memory indexes."""
    if path is None:
        path = DATA_FILE
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        logger.info(f"[gddl_store] no usable snapshot at {path}")
        return
    new_levels = payload.get("levels") if isinstance(payload, dict) else None
    if not isinstance(new_levels, list) or not new_levels:
        return
    valid_levels = [level for level in new_levels if isinstance(level, dict)]
    usable_levels = []
    for level in valid_levels:
        try:
            int(level["id"])
        except (KeyError, TypeError, ValueError):
            continue
        usable_levels.append(level)
    if not usable_levels:
        return
    _rebuild_indexes(usable_levels, payload.get("fetchedAt"))
    logger.info(f"[gddl_store] loaded {len(levels)} levels ({fetched_at})")


def _merge_old_data_for_failed_pages(
    new_levels: list[dict[str, Any]],
    failed_pages: list[int],
    path: Path,
) -> tuple[list[dict[str, Any]], int]:
    """Merge old records only for IDs not returned by the successful scan.

    Failed page numbers are hints for which old IDs may be retained. Remote
    data always wins for an ID, so page-boundary shifts cannot overwrite a
    newer snapshot record with stale data.
    """
    if not failed_pages:
        return new_levels, 0

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = None
    old_levels = payload.get("levels") if isinstance(payload, dict) else None
    if not isinstance(old_levels, list):
        old_levels = []

    merged = {int(level["id"]): level for level in new_levels if _valid_id(level)}
    retained = 0
    for level in old_levels:
        if not _valid_id(level):
            continue
        level_id = int(level["id"])
        if level_id in merged or level_id // PAGE_SIZE not in failed_pages:
            continue
        merged[level_id] = level
        retained += 1

    return list(merged.values()), retained


def _valid_id(level: dict[str, Any]) -> bool:
    try:
        int(level["id"])
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _save(
    new_levels: list[dict[str, Any]],
    failed_pages: list[int],
    path: Path | None = None,
) -> str:
    if path is None:
        path = DATA_FILE
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stored_levels = []
    for level in new_levels:
        stored = level.copy()
        stored.setdefault("fetchedAt", stamp)
        stored_levels.append(stored)
    payload = {
        "fetchedAt": stamp,
        "total": len(stored_levels),
        "partial": bool(failed_pages),
        "failedPages": failed_pages,
        "levels": stored_levels,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temporary, path)
    return stamp


def fetch_all_levels() -> FetchResult | None:
    """Fetch a remote snapshot while tolerating failures on non-initial pages.

    Page zero is required because it establishes the total page count. Data
    for failed later pages is merged back by ID during publication.
    """
    try:
        from .gddlapi import Gddl
    except ImportError:  # standalone updater script mode
        from gddlapi import Gddl

    def page(number: int) -> dict[str, Any] | None:
        return Gddl._searchlevels_online(
            page=number, limit=PAGE_SIZE, sort="ID", sort_direction="asc"
        )

    def fetch_with_retry(number: int) -> dict[str, Any] | None:
        payload = page(number)
        if payload is None:
            logger.warning(f"[gddl_store] page {number} failed; retrying once")
            time.sleep(RETRY_WAIT)
            payload = page(number)
        return payload

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
        # Fetch page zero first because it provides the total page count.
        first = executor.submit(page, 0).result()
        if not first or not first.get("data"):
            logger.warning("[gddl_store] first page was empty; abandoning scan")
            return None
        total = int(first.get("total") or 0)
        pages = max(1, -(-total // PAGE_SIZE))
        seen: dict[int, dict[str, Any]] = {}
        failed_pages: list[int] = []
        for level in first.get("data") or []:
            if isinstance(level, dict) and "id" in level:
                if "isInPack" in level:
                    del level['isInPack']
                if "isComplete" in level:
                    del level['isComplete']
                seen[int(level["id"])] = level

        for batch_start in range(1, pages, FETCH_WORKERS):
            # Keep a short pause between batches to avoid overwhelming the API.
            time.sleep(FETCH_INTERVAL)
            batch = range(batch_start, min(batch_start + FETCH_WORKERS, pages))
            futures = {
                executor.submit(fetch_with_retry, number): number for number in batch
            }
            for future in as_completed(futures):
                number = futures[future]
                payload = future.result()
                if payload is None:
                    logger.error(
                        f"[gddl_store] page {number} failed twice; will retain old records"
                    )
                    failed_pages.append(number)
                    continue
                for level in payload.get("data") or []:
                    if isinstance(level, dict) and "id" in level:
                        seen[int(level["id"])] = level
                if number % 50 == 0:
                    logger.info(f"[gddl_store] page {number} fetched")

    return FetchResult(
        levels=list(seen.values()),
        failed_pages=failed_pages,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def refresh(path: Path | None = None, reload_after: bool = True) -> bool:
    if path is None:
        path = DATA_FILE
    result = fetch_all_levels()
    if result is None:
        return False
    levels_to_save, retained_count = _merge_old_data_for_failed_pages(
        result.levels, result.failed_pages, path
    )
    try:
        stamp = _save(levels_to_save, result.failed_pages, path)
    except OSError:
        logger.exception("[gddl_store] snapshot write failed")
        return False
    if result.failed_pages:
        logger.warning(
            f"[gddl_store] partial snapshot published: failed_pages={result.failed_pages}, "
            f"reused_old_levels={retained_count}"
        )
    if reload_after:
        _rebuild_indexes(levels_to_save, stamp)
    return True


reload()
