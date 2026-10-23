"""pointercrate listed-demon cache (rated+) and exact-name lookups.

``updater/jobs/ratedplus.py`` publishes ``data/ratedplus.json``; this module
parses it once at import and keeps an exact-name index in memory, so a search
never touches the disk. ``reload()`` re-reads it after the updater publishes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from nonebot import logger

from ..paths import DATA_DIR

DATA_PATH = DATA_DIR / "ratedplus.json"

#: Warn about a snapshot older than a week, like the NLW tables do.
STALE_AFTER = 7 * 24 * 3600


class RatedplusLevel:
    """One listed demon, keyed by its in-game Geometry Dash level id."""

    __slots__ = (
        "creator",
        "id",
        "name",
        "position",
        "rated",
        "requirement",
        "video",
    )

    def __init__(self, data: dict[str, Any]) -> None:
        self.id: int = int(data["id"])
        self.name: str = str(data["name"]).strip()
        self.creator: str | None = data.get("creator") or None
        self.position: int | None = data.get("position")
        self.requirement: int | None = data.get("requirement")
        self.video: str | None = data.get("video")
        #: False for a listed demon whose placement is not awarded yet. Such
        #: entries are kept on purpose: a not yet rated level can share a name
        #: with an already rated one (e.g. two "Thinking Space II"), and both
        #: have to stay selectable.
        rated = data.get("rated")
        self.rated: bool = True if rated is None else bool(rated)

    def __repr__(self) -> str:
        return f"<RatedplusLevel #{self.position} {self.name!r} (ID:{self.id})>"


levels: list[RatedplusLevel] = []
by_name: dict[str, list[RatedplusLevel]] = {}
by_id: dict[int, RatedplusLevel] = {}


def _read_payload(path: Path) -> dict[str, Any] | None:
    """Return the snapshot payload, or None when it is missing or unreadable."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        logger.warning(f"[ratedplus] 缓存不存在，跳过: {path}")
        return None
    except (OSError, json.JSONDecodeError):
        logger.warning(f"[ratedplus] 缓存读不了，跳过: {path}")
        return None
    if not isinstance(payload, dict):
        logger.warning(f"[ratedplus] 缓存顶层不是对象，跳过: {path}")
        return None
    return payload


def _rebuild(payload: dict[str, Any]) -> int:
    """Reparse the payload into the module-level index and return its size."""
    parsed: list[RatedplusLevel] = []
    rows = payload.get("levels")
    for row in rows if isinstance(rows, list) else []:
        try:
            parsed.append(RatedplusLevel(row))
        except (AttributeError, KeyError, TypeError, ValueError):
            logger.warning(f"[ratedplus] 有一条数据格式不对，跳过: {row!r}")

    levels[:] = parsed
    by_name.clear()
    by_id.clear()
    for level in levels:
        by_name.setdefault(level.name.strip().lower(), []).append(level)
        by_id[level.id] = level
    return len(levels)


def _unrated_count() -> int:
    return sum(1 for level in levels if not level.rated)


def load() -> None:
    """Read the published snapshot into memory (called at import time)."""
    payload = _read_payload(DATA_PATH)
    if payload is None:
        return
    count = _rebuild(payload)
    logger.info(
        f"[ratedplus] 载入 {count} 条 listed demon"
        f"（其中 {_unrated_count()} 条未 rated，{DATA_PATH.name}）"
    )

    timestamp = payload.get("timestamp")
    if isinstance(timestamp, (int, float)) and time.time() - timestamp > STALE_AFTER:
        logger.warning("[ratedplus] 本地缓存已经使用超过一周，建议再次 fetch 获取关卡")


def reload() -> None:
    """Re-read the snapshot; a missing or broken file keeps the old index."""
    payload = _read_payload(DATA_PATH)
    if payload is None:
        logger.warning("[ratedplus] 重新加载失败，保留旧数据")
        return
    count = _rebuild(payload)
    logger.info(
        f"[ratedplus] 已重新载入 {count} 条 listed demon"
        f"（其中 {_unrated_count()} 条未 rated）"
    )


load()


class Ratedplus:
    """Facade used by ``services/search.py`` (mirrors Gddl / Nlw / Platapi)."""

    @staticmethod
    def getlevelbyname(name: str) -> list[RatedplusLevel]:
        """Return listed demons whose name matches exactly, ignoring case."""
        key = (name or "").strip().lower()
        if not key:
            return []
        return list(by_name.get(key, []))

    @staticmethod
    def get_position(level_id: int) -> int | None:
        """Return the demonlist position for a level id, or None."""
        try:
            level_id = int(level_id)
        except (TypeError, ValueError):
            return None
        level = by_id.get(level_id)
        return level.position if level is not None else None
