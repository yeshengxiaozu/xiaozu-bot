# https://api.aredl.net/v2/docs
# get /api/aredl/levels/{level_id}
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from nonebot import logger

from ..constants import HTTP_OK
from ..paths import DATA_DIR as WORK_FOLDER
from .http import request as http_request

# 外网请求超时。不设的话客户端会一直等，
# 而这个模块在 import 期就会去抓，等于能把 bot 卡死在启动阶段。
AREDL_TIMEOUT = 15

"""get /api/aredl/levels SCHEMA
[{
id*: uuid           # the code didn't use the internal id of aredl
name*: string
position*: integer
publisher_id*: uuid # the code didn't use the internal id of aredl
points*: integer
status*: enum       # Pending┃MainList┃Legacy┃Removed
level_id*: integer
two_player*: bool   # Whether this is the 2P version of a level or not.
tags*: [string or null]
description: string or null
song: integer or null
edel_enjoyment: number or null
is_edel_pending*: boolean
gddl_tier: number or null
nlw_tier: string or null
}]"""
class AREDLLevel:
    id: str
    name: str
    position: int
    points: int #100x of actual display point
    status: str
    level_id: int
    two_player: bool
    tags: list[str]
    description: str | None
    song: int | None
    edel_enjoyment: float | None
    is_edel_pending: bool
    gddl_tier: float | None
    nlw_tier: str | None

    def __init__(self, jsondict: dict[str,Any]) -> None:
        self.id = jsondict["id"]
        self.position = jsondict["position"]
        self.name = jsondict["name"]
        self.points = jsondict["points"]
        self.status = jsondict["status"]
        self.level_id = jsondict["level_id"]
        self.two_player = jsondict["two_player"]
        self.tags = jsondict["tags"]
        self.description = jsondict["description"]
        self.song = jsondict["song"]
        self.edel_enjoyment = jsondict["edel_enjoyment"]
        self.is_edel_pending = jsondict["is_edel_pending"]
        self.gddl_tier = jsondict["gddl_tier"]
        self.nlw_tier = jsondict["nlw_tier"]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

def _parse_rows(rows: object) -> tuple[list[AREDLLevel], list[int], int]:
    """Parse level rows one by one instead of all-or-nothing.

    A single renamed or null field used to make the whole list comprehension
    raise, which meant the daily refresh silently kept yesterday's chart. Now
    the bad row is skipped and its level id is reported back so the caller can
    reuse the previous snapshot entry for that specific level.

    Returns (levels, skipped_ids, malformed_rows).
    """
    levels: list[AREDLLevel] = []
    skipped_ids: list[int] = []
    malformed = 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            malformed += 1
            continue
        try:
            levels.append(AREDLLevel(row))
        except (KeyError, TypeError, ValueError) as exc:
            malformed += 1
            level_id = row.get("level_id")
            if isinstance(level_id, int) and level_id > 0:
                skipped_ids.append(level_id)
            logger.warning(
                f"[aredlapi] 跳过一条无法解析的榜单记录 level_id={level_id}: {exc}"
            )
    return levels, skipped_ids, malformed


def _fetch_levels(url: str) -> tuple[list[AREDLLevel], list[int]]:
    try:
        response = http_request(
            "GET", url, headers={"Content-Type": "application/json"}, timeout=AREDL_TIMEOUT
        )
    except httpx.HTTPError as e:
        logger.error(f"[aredlapi] 拉取失败 {url}: {e}")
        return [], []
    if response.status_code != HTTP_OK:
        logger.error(f"failed to fetch from {url}")
        return [], []
    try:
        rows = response.json()
    except (ValueError, TypeError):
        logger.error(f"[aredlapi] 响应不是合法 JSON: {url}")
        return [], []
    levels, skipped_ids, malformed = _parse_rows(rows)
    if malformed:
        logger.warning(
            f"[aredlapi] {url}：{malformed} 条记录解析失败，"
            f"其中 {len(skipped_ids)} 条有可用的 level_id"
        )
    logger.info(f"successfully fetch {len(levels)} levels from {url}")
    return levels, skipped_ids


def fetch_aredl_levels() -> list[AREDLLevel]:
    return _fetch_levels("https://api.aredl.net/v2/api/aredl/levels")[0]


def fetch_arepl_levels() -> list[AREDLLevel]:
    return _fetch_levels("https://api.aredl.net/v2/api/arepl/levels")[0]


def _fetch_aredl() -> tuple[list[AREDLLevel], list[int]]:
    return _fetch_levels("https://api.aredl.net/v2/api/aredl/levels")


def _fetch_arepl() -> tuple[list[AREDLLevel], list[int]]:
    return _fetch_levels("https://api.aredl.net/v2/api/arepl/levels")


def _load_cached_levels(path: Path) -> tuple[list[AREDLLevel], bool]:
    """Load a cache and report whether its timestamp is still fresh."""
    if not path.exists():
        return [], False
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception(f"[aredlapi] 缓存读不了，当过期处理: {path}")
        return [], False
    if not isinstance(data, dict):
        logger.warning(f"[aredlapi] 缓存顶层不是对象，当过期处理: {path}")
        return [], False
    levels, _skipped, malformed = _parse_rows(data.get("levels", []))
    timestamp = data.get("timestamp")
    # A cache that lost rows to parsing is not "fresh": refetch rather than
    # serving a silently shrunken chart from disk.
    fresh = bool(timestamp and time.time() - timestamp < 24 * 3600) and not malformed
    return levels, fresh


def _retain_previous(
    levels: list[AREDLLevel], skipped_ids: list[int], path: Path
) -> tuple[list[AREDLLevel], int]:
    """Reuse the previous snapshot entry for ids whose new row failed to parse.

    Only the affected levels are carried over; everything else comes from this
    run, so a format change in one field degrades to "that level is one day
    stale" instead of "the whole chart is one day stale".
    """
    if not skipped_ids:
        return levels, 0
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("levels") if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return levels, 0
    if not isinstance(rows, list):
        return levels, 0

    wanted = set(skipped_ids)
    merged = list(levels)
    retained = 0
    for row in rows:
        if not isinstance(row, dict) or row.get("level_id") not in wanted:
            continue
        try:
            merged.append(AREDLLevel(row))
        except (KeyError, TypeError, ValueError):
            logger.warning(
                f"[aredlapi] 旧快照里这条也解析不了 level_id={row.get('level_id')}"
            )
            continue
        retained += 1
    return merged, retained


def _load_levels(
    filename: str, label: str, fetcher: Callable[[], tuple[list[AREDLLevel], list[int]]]
) -> list[AREDLLevel]:
    path = WORK_FOLDER / filename
    cached_levels, fresh = _load_cached_levels(path)
    if fresh:
        logger.info(f"successly load {len(cached_levels)} levels from {filename}")
        return cached_levels
    logger.info("cache expired, trying to re-fetch levels...")
    fetched_levels, skipped_ids = fetcher()
    if skipped_ids:
        fetched_levels, retained = _retain_previous(fetched_levels, skipped_ids, path)
        logger.warning(
            f"[aredlapi] {label}：{len(skipped_ids)} 条记录本次解析失败，"
            f"沿用旧快照中的 {retained} 条"
        )
    if not fetched_levels and cached_levels:
        logger.warning(f"[aredlapi] 刷新失败，继续使用过期的 {label} 缓存")
        return cached_levels
    if fetched_levels:
        levels_data = [level.to_dict() for level in fetched_levels]
        with path.open("w", encoding="utf-8") as f:
            json.dump({"timestamp": time.time(), "levels": levels_data}, f, indent=4)
    else:
        logger.error(f"failed to save 0 {label} level datas")
    return fetched_levels


def get_aredl_levels() -> list[AREDLLevel]:
    return _load_levels("aredl_levels.json", "AREDL", _fetch_aredl)


def get_arepl_levels() -> list[AREDLLevel]:
    return _load_levels("arepl_levels.json", "AREPL", _fetch_arepl)


aredllevels: list[AREDLLevel] = []
arepllevels: list[AREDLLevel] = []
aredl_dict: dict[int, AREDLLevel] = {}
# level_id -> the AREDL entry of the solo version / the 2P version.
# aredl_dict keeps only one entry per level id (the better placement), so it
# cannot answer "what are both ranks" for a level listed on the solo and the
# 2P charts at once. Both indexes come from aredllevels only, same reason as
# the position index below: AREPL positions are a separate ranking.
aredl_solo_dict: dict[int, AREDLLevel] = {}
aredl_two_player_dict: dict[int, AREDLLevel] = {}
# position -> level, built from aredllevels only. AREPL has its own numbering,
# so platformer placements must not leak into the AREDL rank lookup.
aredl_position_dict: dict[int, AREDLLevel] = {}
# Highest rank currently on the AREDL list (0 when nothing is loaded).
# Commands use it to reject out-of-range input without touching the dict.
aredl_max_position: int = 0


def _rebuild_indexes() -> None:
    """Rebuild the rank indexes in place from aredllevels."""
    global aredl_max_position

    aredl_position_dict.clear()
    aredl_solo_dict.clear()
    aredl_two_player_dict.clear()
    for level in aredllevels:
        # Keep the first entry per position, same tie-break as aredl_dict.
        aredl_position_dict.setdefault(level.position, level)
        target = aredl_two_player_dict if level.two_player else aredl_solo_dict
        target.setdefault(level.level_id, level)
    aredl_max_position = max(aredl_position_dict, default=0)


def reload() -> None:
    """重新拉取 AREDL / AREPL 数据并重建查询字典。

    注意这里必须原地改 list/dict（clear + extend/update）而不是重新赋值：
    别的模块是 `from .aredlapi import aredllevels` 拿走引用的，重新赋值它们看不到。
    """
    new_aredl = get_aredl_levels()
    new_arepl = get_arepl_levels()

    aredllevels.clear()
    aredllevels.extend(new_aredl)
    arepllevels.clear()
    arepllevels.extend(new_arepl)

    aredl_dict.clear()
    for level in aredllevels:
        if level.level_id not in aredl_dict:
            aredl_dict[level.level_id] = level
        # 同关卡保留第一个也就是排位高的，用于兼容2p

    for level in arepllevels:
        if level.level_id not in aredl_dict:
            aredl_dict[level.level_id] = level

    _rebuild_indexes()

    logger.info(
        f"[aredlapi] 已加载 {len(aredl_dict)} 条关卡，"
        f"AREDL 最大排名 {aredl_max_position}"
    )


def load_from_cache_only() -> None:
    """只读本地缓存，绝不联网。

    启动阶段用这个：缓存新鲜就直接有数据，不新鲜就先空着，
    等 startup 钩子在后台把 reload() 跑完再补上。
    """
    try:
        with Path.open(WORK_FOLDER / "aredl_levels.json", encoding="utf-8") as f:
            aredl_data = json.load(f).get("levels", [])
        with Path.open(WORK_FOLDER / "arepl_levels.json", encoding="utf-8") as f:
            arepl_data = json.load(f).get("levels", [])
    except (OSError, json.JSONDecodeError):
        logger.warning("[aredlapi] 启动时没读到可用缓存，等后台刷新")
        return

    aredllevels.clear()
    aredllevels.extend(AREDLLevel(d) for d in aredl_data)
    arepllevels.clear()
    arepllevels.extend(AREDLLevel(d) for d in arepl_data)

    aredl_dict.clear()
    for level in (*aredllevels, *arepllevels):
        if level.level_id not in aredl_dict:
            aredl_dict[level.level_id] = level

    _rebuild_indexes()
    logger.info(f"[aredlapi] 启动时从缓存载入 {len(aredl_dict)} 条关卡")


# import 期只读本地缓存。
# 以前这里直接 reload()，缓存超过 24 小时就会在插件加载阶段同步打两次外网 ——
# api.aredl.net 一慢，nonebot 的 load_from_toml 就卡死，bot 起不来也不报错。
# 真正的刷新挪到 gdlevelsearch/__init__.py 的 startup 钩子里后台做。
load_from_cache_only()


class Aredl:
    @staticmethod
    def getlevelbyid(level_id: int) -> AREDLLevel | None:
        if level_id in aredl_dict:
            logger.trace(
                f"Level ID {level_id} found in aredl_dict as #{aredl_dict[level_id].position}"
            )
            return aredl_dict[level_id]
        return None

    @staticmethod
    def getlevelbyposition(position: int) -> AREDLLevel | None:
        """Look a level up by its AREDL rank (1-based)."""
        return aredl_position_dict.get(position)

    @staticmethod
    def getmaxposition() -> int:
        """Highest AREDL rank currently loaded; 0 when the list is empty."""
        return aredl_max_position

    @staticmethod
    def getrankpair(level_id: int) -> tuple[AREDLLevel | None, AREDLLevel | None]:
        """Return (solo, 2P) AREDL entries for one GD level id.

        Either side is None when that version is not on the list. Only the
        AREDL chart feeds these indexes; AREPL entries are unreachable here.
        """
        return aredl_solo_dict.get(level_id), aredl_two_player_dict.get(level_id)

# 这里原本自己挂了一个 3 点的 scheduled_job，但它是坏的：赋值的是局部变量，
# 没有 global，抓完数据就丢了，模块全局一直是启动时那份。
# 现在统一由 gdlevelsearch 的 reload_all() 在每日更新跑完后调用上面的 reload()。
