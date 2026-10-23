"""updater/jobs/ratedplus.py

Fetch pointercrate's demon list into ``data/ratedplus.json``.

``/api/v2/demons/listed/`` feeds pointercrate's rated+ pages. Facts measured
against the real response, which the code below relies on:

- ``limit`` is capped at 100 (``limit=150`` answers HTTP 422), hence the 50
  fallback;
- ``position`` is dense (1..N), which makes "the snapshot is complete"
  checkable instead of hopeful;
- the default order is *almost* position-ascending, so records are sorted
  explicitly and never trusted as delivered;
- the meaning of the ``after`` cursor is undocumented (a position or a record
  id), so it is detected at runtime rather than guessed.

The whole table is kept, not only the top of it: the bot uses this file as a
name index, so a level at position 400 is as findable as one at position 4.
Mid-2025 that is ~716 demons, i.e. eight requests.

Output goes to staging and is published by the runner; any failure below keeps
the previous snapshot in place.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any

from nonebot import logger

from xiaozu_bot.utils.json_storage import write_json_atomic

from ...api.http import request as http_request
from ...constants import USER_AGENT

try:
    from ..paths import staged
except ImportError:
    from updater.paths import staged

API = "https://pointercrate.com/api/v2/demons/listed/"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
FETCH_TIMEOUT = 30
#: Pointercrate rate limits; keep a gap between pages.
PAGE_DELAY = 0.4
LIMIT_CANDIDATES = (100, 50)
MAX_PAGES = 60
OUTPUT_NAME = "ratedplus.json"


class RatedplusFetchError(RuntimeError):
    """The endpoint answered with something that cannot be used."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _as_int(value: Any) -> int | None:
    """Best-effort int for a JSON field, None when it is not a number."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def position_of(item: dict[str, Any]) -> int | None:
    return _as_int(item.get("position"))


def record_id_of(item: dict[str, Any]) -> int | None:
    """Pointercrate's own record id, *not* the in-game level id."""
    return _as_int(item.get("id"))


def build_url(limit: int, after: int | None = None) -> str:
    query: dict[str, Any] = {"limit": limit}
    if after is not None:
        query["after"] = after
    return f"{API}?{urllib.parse.urlencode(query)}"


def fetch_page(
    limit: int,
    after: int | None = None,
    timeout: float = FETCH_TIMEOUT,
) -> list[dict[str, Any]]:
    """Return one page of records, raising RatedplusFetchError otherwise."""
    url = build_url(limit, after)
    try:
        response = http_request("GET", url, headers=HEADERS, timeout=timeout)
    except Exception as exc:
        raise RatedplusFetchError(f"request failed: {exc}") from exc
    if response.status_code != 200:
        raise RatedplusFetchError(f"HTTP {response.status_code}", response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RatedplusFetchError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise RatedplusFetchError(f"unexpected payload: {type(payload).__name__}")
    return [item for item in payload if isinstance(item, dict)]


def pick_limit(timeout: float = FETCH_TIMEOUT) -> tuple[int, list[dict[str, Any]]]:
    """Return the largest accepted page size together with the first page."""
    for limit in LIMIT_CANDIDATES:
        try:
            return limit, fetch_page(limit, None, timeout)
        except RatedplusFetchError as exc:
            if exc.status in (400, 422):
                logger.warning(
                    f"[RATEDPLUS] limit={limit} rejected (HTTP {exc.status}), "
                    "trying a smaller page"
                )
                continue
            raise
    raise RatedplusFetchError("every page size was rejected")


def sort_by_position(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            position_of(item) if position_of(item) is not None else 10**6,
            record_id_of(item) or 0,
        ),
    )


def missing_positions(items: list[dict[str, Any]], target: int) -> list[int]:
    """Positions in ``1..target`` that ``items`` does not cover."""
    have = {position_of(item) for item in items if position_of(item) is not None}
    return sorted(set(range(1, target + 1)) - have)


def _absorb(
    items: list[dict[str, Any]],
    seen: set[int],
    batch: list[dict[str, Any]],
) -> int:
    """Append records whose record id was not seen yet; return how many were new.

    Record ids are unique upstream, so this only collapses pages that the
    cursor probe repeats -- never two different levels that share a name
    (pointercrate really does list two "Thinking Space II").
    """
    added = 0
    for item in batch:
        record_id = record_id_of(item)
        if record_id is not None:
            if record_id in seen:
                continue
            seen.add(record_id)
        items.append(item)
        added += 1
    return added


def collect(
    target: int | None = None,
    *,
    timeout: float = FETCH_TIMEOUT,
    delay: float = PAGE_DELAY,
) -> list[dict[str, Any]]:
    """Page through the list until ``1..target`` is covered or the table ends.

    ``target=None`` means "everything": stop when a page stops adding records.
    The result is always sorted by position.
    """
    limit, batch = pick_limit(timeout)
    items: list[dict[str, Any]] = []
    #: Record ids are unique upstream; this set only detects a repeated page.
    seen: set[int] = set()
    logger.info(f"[RATEDPLUS] page 1: {_absorb(items, seen, batch)} records (limit={limit})")

    cursor: str | None = None  # None (unknown) | "position" | "record-id"
    last_after: int | None = None
    pages = 1

    while pages < MAX_PAGES:
        if target is not None and not missing_positions(items, target):
            break
        if not batch:
            break

        last_record = record_id_of(batch[-1])
        max_position = max(
            (position_of(item) for item in items if position_of(item) is not None),
            default=None,
        )
        # Until the cursor semantics are known, walk it as a position: the
        # first probe page then either continues the list or repeats records.
        after = last_record if cursor == "record-id" else (max_position or last_record)
        if after is None or after == last_after:
            break
        last_after = after

        if delay > 0:
            time.sleep(delay)
        batch = fetch_page(limit, after, timeout)
        pages += 1
        if not batch:
            break

        detecting = cursor is None
        if detecting:
            overlap = sum(1 for item in batch if record_id_of(item) in seen)
            cursor = "record-id" if overlap else "position"
            logger.info(
                f"[RATEDPLUS] cursor semantics: {cursor} "
                f"(page {pages} repeated {overlap} records)"
            )

        added = _absorb(items, seen, batch)
        logger.info(f"[RATEDPLUS] page {pages}: {added} new, {len(items)} total")
        # A page that only repeats records proves the probe used the wrong
        # cursor semantics; it does not mean the table ended. Retry with the
        # detected cursor instead of returning a half-fetched list.
        if added == 0 and not detecting:
            break

    ordered = sort_by_position(items)
    if target is None:
        return ordered
    return ordered[:target]


def verify(items: list[dict[str, Any]]) -> None:
    """Raise unless the snapshot covers positions ``1..N`` without gaps.

    Dense positions are the only cheap proof that a page was not silently
    dropped and that ``position`` still exists upstream. A gap means the
    snapshot is unusable, not merely incomplete.
    """
    if not items:
        raise RatedplusFetchError("the list came back empty")
    without_position = sum(1 for item in items if position_of(item) is None)
    if without_position:
        raise RatedplusFetchError(f"{without_position} records have no position")
    missing = missing_positions(items, len(items))
    if missing:
        raise RatedplusFetchError(f"positions are not dense, missing {missing[:10]}")


def _account_name(value: Any) -> str | None:
    """Pointercrate embeds players as ``{"id": .., "name": ..}``."""
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    return name if isinstance(name, str) and name else None


def build_levels(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize pointercrate records into this project's level schema.

    ``level_id`` is the in-game Geometry Dash level id and is *not* the same as
    pointercrate's record ``id``; mixing them up would make the bot render the
    wrong level. A record without one cannot be searched, so it is dropped.
    """
    levels: list[dict[str, Any]] = []
    skipped = 0
    for item in raw:
        level_id = _as_int(item.get("level_id"))
        name = item.get("name")
        if level_id is None or not isinstance(name, str) or not name.strip():
            skipped += 1
            continue
        video = item.get("video")
        levels.append(
            {
                "id": level_id,
                "position": position_of(item),
                #: Position among rated demons; null while a listed demon is
                #: still unawarded a rating (its `position` already counts it).
                "rated_position": _as_int(item.get("rated_position")),
                "name": name.strip(),
                "creator": _account_name(item.get("publisher")),
                "verifier": _account_name(item.get("verifier")),
                "requirement": _as_int(item.get("requirement")),
                "rated": bool(item.get("rated")),
                "video": video if isinstance(video, str) else None,
            }
        )
    if skipped:
        logger.warning(f"[RATEDPLUS] dropped {skipped} records without a usable level_id")
    return levels


def fetch() -> None:
    """Fetch the list into staging, keeping the previous snapshot on failure."""
    logger.info("[RATEDPLUS] fetching the pointercrate demon list")
    try:
        raw = collect(delay=PAGE_DELAY)
        verify(raw)
        levels = build_levels(raw)
        if not levels:
            raise RatedplusFetchError("no record carried an in-game level id")
    except RatedplusFetchError as exc:
        logger.error(f"[RATEDPLUS] fetch failed, keeping the old snapshot: {exc}")
        return

    output = staged(OUTPUT_NAME)
    write_json_atomic(
        output,
        {"timestamp": time.time(), "levels": levels},
        indent=4,
    )
    logger.info(f"[RATEDPLUS] saved {len(levels)} listed demons to {output}")


if __name__ == "__main__":
    fetch()
