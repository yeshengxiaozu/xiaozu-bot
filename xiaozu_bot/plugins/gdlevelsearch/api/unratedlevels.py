"""
读取手写的白名单
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nonebot import logger

from ..paths import DATA_DIR

DATA_PATH = DATA_DIR / "unrated_levels.json"

class UnratedLevel:

    __slots__ = (
        "creator",
        "id",
        "name",
        "note"
    )

    def __init__(self, data: dict[str, Any]) -> None:
        self.id: int = int(data["id"])
        self.name: str = str(data["name"]).strip()
        self.creator: str | None = data.get("creator") or None
        self.note: str | None = data.get("note") or None

    def __repr__(self) -> str:
        return f"<UnratedLevel {self.name!r} (ID:{self.id})>"


levels: list[UnratedLevel] = []
by_name: dict[str, UnratedLevel] = {}
by_id: dict[int, UnratedLevel] = {}

def _read_payload(path: Path) -> dict[str, Any] | None:
    """Return the snapshot payload, or None when it is missing or unreadable."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        logger.warning(f"[UnratedLevel] 文件不存在，跳过: {path}")
        return None
    except (OSError, json.JSONDecodeError):
        logger.warning(f"[UnratedLevel] 文件读不了，跳过: {path}")
        return None
    if not isinstance(payload, dict):
        logger.warning(f"[UnratedLevel] 文件顶层不是对象，跳过: {path}")
        return None
    return payload


def _rebuild(payload: dict[str, Any]) -> int:
    """Reparse the payload into the module-level index and return its size."""
    parsed: list[UnratedLevel] = []
    rows = payload.get("levels")
    for row in rows if isinstance(rows, list) else []:
        try:
            if not row.get("is_comment") :
                parsed.append(UnratedLevel(row))
        except (AttributeError, KeyError, TypeError, ValueError):
            logger.warning(f"[UnratedLevel] 有一条数据格式不对，跳过: {row!r}")

    levels[:] = parsed
    by_name.clear()
    by_id.clear()
    for level in levels:
        by_name[level.name.strip().lower()] = level
        previous = by_id.get(level.id)
        if previous is not None:
            # 这个文件是手写的，复制粘贴漏改 id 是很正常的操作失误。
            # 不叫出来的话，搜到前一个关卡会拿到后一个的卡片，而且看不出为什么。
            logger.warning(
                f"[UnratedLevel] 重复的关卡 id {level.id}："
                f"{previous.name!r} 被 {level.name!r} 覆盖，"
                f"查前者会拿到后者的 note"
            )
        by_id[level.id] = level
    return len(levels)


def load() -> None:
    """Read the published snapshot into memory (called at import time)."""
    payload = _read_payload(DATA_PATH)
    if payload is None:
        return
    count = _rebuild(payload)
    logger.info(
        f"[UnratedLevel] 载入 {count} 条 unrated demon 白名单"
    )


def reload() -> None:
    """Re-read the snapshot; a missing or broken file keeps the old index."""
    payload = _read_payload(DATA_PATH)
    if payload is None:
        logger.warning("[UnratedLevel] 重新加载失败，保留旧数据")
        return
    count = _rebuild(payload)
    logger.info(
        f"[UnratedLevel] 已重新载入 {count} 条 unrated demon 白名单"
    )

load()

class UnratedLevels:
    """Facade used by ``services/search.py`` (mirrors Gddl / Nlw / Platapi)."""

    @staticmethod
    def getlevelbyname(name: str) -> UnratedLevel | None:
        """Return the whitelisted level whose name matches exactly, ignoring case."""
        key = (name or "").strip().lower()
        if not key:
            return None
        return by_name.get(key)

    @staticmethod
    def get_note(level_id: int) -> str | None:
        """Return the hand-written note for a level id, or None."""
        try:
            level_id = int(level_id)
        except (TypeError, ValueError):
            return None
        level = by_id.get(level_id)
        return level.note if level is not None else None
