"""Local GDDL level snapshot and offline query helpers."""

from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nonebot import logger

PLUGIN_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = PLUGIN_DIR / "data" / "gddl_levels.json"

PAGE_SIZE = 25
FETCH_INTERVAL = 0.7
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
    meta = level.get("Meta") or {}
    name = filters.get("name")
    if name is not None and str(meta.get("Name") or "").strip().lower() != str(name).strip().lower():
        return False

    rating = _number(level.get("Rating"))
    enjoyment = _number(level.get("Enjoyment"))
    submission_count = _number(level.get("SubmissionCount"))
    bounds = (
        (rating, "minRating", "maxRating"),
        (enjoyment, "minEnjoyment", "maxEnjoyment"),
        (submission_count, "minSubmissionCount", None),
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
    """Run the subset of GDDL search supported by the local snapshot."""
    supported = {
        "name",
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
        result.sort(key=lambda level: int(level.get("ID", 0)), reverse=direction == "desc")
    else:
        return None

    page = max(0, int(page))
    limit = max(1, int(limit))
    start = page * limit
    return {
        "total": len(result),
        "limit": limit,
        "page": page,
        "levels": result[start : start + limit],
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
        rating = _number(level.get("Rating"))
        if rating is None or not low_exact <= rating <= high_exact:
            continue
        enjoyment = _number(level.get("Enjoyment"))
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
    global fetched_at
    levels.clear()
    by_id.clear()
    _by_name.clear()
    for level in new_levels:
        try:
            level_id = int(level["ID"])
        except (KeyError, TypeError, ValueError):
            continue
        level["ID"] = level_id
        levels.append(level)
        by_id[level_id] = level
        name = str((level.get("Meta") or {}).get("Name") or "").strip().lower()
        if name:
            _by_name.setdefault(name, []).append(level)
    fetched_at = stamp


def reload(path: Path | None = None) -> None:
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
            int(level["ID"])
        except (KeyError, TypeError, ValueError):
            continue
        usable_levels.append(level)
    if not usable_levels:
        return
    _rebuild_indexes(usable_levels, payload.get("fetchedAt"))
    logger.info(f"[gddl_store] loaded {len(levels)} levels ({fetched_at})")


def _save(new_levels: list[dict[str, Any]], path: Path | None = None) -> str:
    if path is None:
        path = DATA_FILE
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {"fetchedAt": stamp, "total": len(new_levels), "levels": new_levels}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temporary, path)
    return stamp


def fetch_all_levels() -> list[dict[str, Any]] | None:
    """Fetch a complete remote snapshot without consulting the local fallback."""
    try:
        from .gddlapi import Gddl
    except ImportError:  # standalone updater script mode
        from gddlapi import Gddl

    def page(number: int) -> dict[str, Any] | None:
        return Gddl._searchlevels_online(
            page=number, limit=PAGE_SIZE, sort="ID", sort_direction="asc"
        )

    first = page(0)
    if not first or not first.get("levels"):
        logger.warning("[gddl_store] first page was empty; abandoning scan")
        return None
    total = int(first.get("total") or 0)
    pages = max(1, -(-total // PAGE_SIZE))
    seen: dict[int, dict[str, Any]] = {}
    for level in first.get("levels") or []:
        if isinstance(level, dict) and "ID" in level:
            seen[int(level["ID"])] = level

    for number in range(1, pages):
        time.sleep(FETCH_INTERVAL)
        payload = page(number)
        if payload is None:
            logger.warning(f"[gddl_store] page {number} failed; retrying once")
            time.sleep(RETRY_WAIT)
            payload = page(number)
        if payload is None:
            logger.error(f"[gddl_store] page {number} failed twice; abandoning scan")
            return None
        for level in payload.get("levels") or []:
            if isinstance(level, dict) and "ID" in level:
                seen[int(level["ID"])] = level
        if number % 50 == 0:
            logger.info(f"[gddl_store] page {number} fetched")

    if total and len(seen) < total * MIN_COMPLETE_RATIO:
        logger.error(f"[gddl_store] only fetched {len(seen)}/{total} levels")
        return None
    return list(seen.values())


def refresh(path: Path | None = None, reload_after: bool = True) -> bool:
    if path is None:
        path = DATA_FILE
    new_levels = fetch_all_levels()
    if new_levels is None:
        return False
    try:
        stamp = _save(new_levels, path)
    except OSError:
        logger.exception("[gddl_store] snapshot write failed")
        return False
    if reload_after:
        _rebuild_indexes(new_levels, stamp)
    return True


reload()
