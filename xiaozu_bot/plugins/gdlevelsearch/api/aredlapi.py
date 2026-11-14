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

def _fetch_levels(url: str) -> list[AREDLLevel]:
    try:
        response = http_request(
            "GET", url, headers={"Content-Type": "application/json"}, timeout=AREDL_TIMEOUT
        )
    except httpx.HTTPError as e:
        logger.error(f"[aredlapi] 拉取失败 {url}: {e}")
        return []
    if response.status_code == HTTP_OK:
        levels = response.json()
        logger.info(f"successfully fetch {len(levels)} levels from {url}")
        return [AREDLLevel(level) for level in levels]
    logger.error(f"failed to fetch from {url}")
    return []


def fetch_aredl_levels() -> list[AREDLLevel]:
    return _fetch_levels("https://api.aredl.net/v2/api/aredl/levels")


def fetch_arepl_levels() -> list[AREDLLevel]:
    return _fetch_levels("https://api.aredl.net/v2/api/arepl/levels")


def _load_cached_levels(path: Path) -> tuple[list[AREDLLevel], bool]:
    """Load a cache and report whether its timestamp is still fresh."""
    if not path.exists():
        return [], False
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        levels = [AREDLLevel(level_data) for level_data in data.get("levels", [])]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        logger.exception(f"[aredlapi] 缓存读不了，当过期处理: {path}")
        return [], False
    timestamp = data.get("timestamp")
    fresh = bool(timestamp and time.time() - timestamp < 24 * 3600)
    return levels, fresh


def _load_levels(
    filename: str, label: str, fetcher: Callable[[], list[AREDLLevel]]
) -> list[AREDLLevel]:
    path = WORK_FOLDER / filename
    cached_levels, fresh = _load_cached_levels(path)
    if fresh:
        logger.info(f"successly load {len(cached_levels)} levels from {filename}")
        return cached_levels
    logger.info("cache expired, trying to re-fetch levels...")
    fetched_levels = fetcher()
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
    return _load_levels("aredl_levels.json", "AREDL", fetch_aredl_levels)


def get_arepl_levels() -> list[AREDLLevel]:
    return _load_levels("arepl_levels.json", "AREPL", fetch_arepl_levels)


aredllevels: list[AREDLLevel] = []
arepllevels: list[AREDLLevel] = []
aredl_dict: dict[int, AREDLLevel] = {}


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

    logger.info(f"[aredlapi] 已加载 {len(aredl_dict)} 条关卡")


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

# 这里原本自己挂了一个 3 点的 scheduled_job，但它是坏的：赋值的是局部变量，
# 没有 global，抓完数据就丢了，模块全局一直是启动时那份。
# 现在统一由 gdlevelsearch 的 reload_all() 在每日更新跑完后调用上面的 reload()。
