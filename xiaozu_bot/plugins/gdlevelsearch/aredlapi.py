# https://api.aredl.net/v2/docs
# get /api/aredl/levels/{level_id}
import json
import time
from pathlib import Path
from typing import Any

import requests
from nonebot import logger

HTTP_OK = 200
# 外网请求超时。不设的话 requests 会一直等，
# 而这个模块在 import 期就会去抓，等于能把 bot 卡死在启动阶段。
AREDL_TIMEOUT = 15

# 数据目录。以前写的是相对当前工作目录的 "xiaozu_bot/plugins/..."，
# 换个目录启动就读不到；改成相对本文件。
WORK_FOLDER = Path(__file__).resolve().parent / "data"

"""get /api/aredl/levels SCHEMA
[{
id*: uuid           # the code didn't use the internal id of aredl
name*: string
position*: integer
publisher_id*: uuid # the code didn't use the internal id of aredl
points*: integer
legacy*: boolean
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
    legacy: bool
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
        self.legacy = jsondict["legacy"]
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

aredllevels = []
arepllevels = []


def fetch_aredl_levels() -> list[AREDLLevel]:
    url = "https://api.aredl.net/v2/api/aredl/levels"
    headers = {
        "Content-Type": "application/json",
    }
    try:
        response = requests.get(url, headers=headers, timeout=AREDL_TIMEOUT)
    except requests.RequestException as e:
        logger.error(f"[aredlapi] 拉取失败 {url}: {e}")
        return []
    if response.status_code == HTTP_OK:
        levels = response.json()
        aredllevels = [AREDLLevel(level) for level in levels]
        logger.info(f"successfully fetch {levels.__len__()} levels from {url}")
        return aredllevels
    logger.error(f"failed to fetch from {url}")
    return []


def get_aredl_levels() -> list[AREDLLevel]:
    aredlfilename = "aredl_levels.json"
    aredlfilepath = WORK_FOLDER / aredlfilename
    if Path.exists(aredlfilepath):
        try:
            with Path.open(aredlfilepath, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.exception(f"[aredlapi] 缓存读不了，当过期处理: {aredlfilepath}")
            data = {}
        if True:
            timestamp = data.get("timestamp")
            if timestamp and time.time() - timestamp < 24 * 3600:
                levels_data = data.get("levels", [])
                aredllevels = [AREDLLevel(level_data) for level_data in levels_data]
                logger.info(
                    f"successly load {levels_data.__len__()} levels from aredl_levels.json"
                )
                return aredllevels
    logger.info("cache expired, trying to re-fetch levels...")
    aredllevels = fetch_aredl_levels()
    levels_data = []
    for level in aredllevels:
        level_data = {
            "id": level.id,
            "position": level.position,
            "name": level.name,
            "points": level.points,
            "legacy": level.legacy,
            "level_id": level.level_id,
            "two_player": level.two_player,
            "tags": level.tags,
            "description": level.description,
            "song": level.song,
            "edel_enjoyment": level.edel_enjoyment,
            "is_edel_pending": level.is_edel_pending,
            "gddl_tier": level.gddl_tier,
            "nlw_tier": level.nlw_tier,
        }
        levels_data.append(level_data)
    if levels_data.__len__() > 0:
        with Path.open(aredlfilepath, "w", encoding="utf-8") as f:
            json.dump({"timestamp": time.time(), "levels": levels_data}, f, indent=4)
    else:
        logger.error(f"failed to save {aredllevels.__len__()} level datas")
    return aredllevels


def fetch_arepl_levels() -> list[AREDLLevel]:
    url = "https://api.aredl.net/v2/api/arepl/levels"
    headers = {
        "Content-Type": "application/json",
    }
    try:
        response = requests.get(url, headers=headers, timeout=AREDL_TIMEOUT)
    except requests.RequestException as e:
        logger.error(f"[aredlapi] 拉取失败 {url}: {e}")
        return []
    if response.status_code == HTTP_OK:
        levels = response.json()
        arepllevels = [AREDLLevel(level) for level in levels]
        logger.info(f"successfully fetch {levels.__len__()} levels from {url}")
        return arepllevels
    logger.error(f"failed to fetch from {url}")
    return []


def get_arepl_levels() -> list[AREDLLevel]:
    areplfilename = "arepl_levels.json"
    areplfilepath = WORK_FOLDER / areplfilename
    if Path.exists(areplfilepath):
        try:
            with Path.open(areplfilepath, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.exception(f"[aredlapi] 缓存读不了，当过期处理: {areplfilepath}")
            data = {}
        if True:
            timestamp = data.get("timestamp")
            if timestamp and time.time() - timestamp < 24 * 3600:
                arepllevels = []
                levels_data = data.get("levels", [])
                for level_data in levels_data:
                    aredllevel_instance = AREDLLevel(level_data)
                    arepllevels.append(aredllevel_instance)
                logger.info(
                    f"successly load {levels_data.__len__()} levels from arepl_levels.json"
                )
                return arepllevels
    arepllevels = fetch_arepl_levels()
    levels_data = []
    for level in arepllevels:
        level_data = {
            "id": level.id,
            "position": level.position,
            "name": level.name,
            "points": level.points,
            "legacy": level.legacy,
            "level_id": level.level_id,
            "two_player": level.two_player,
            "tags": level.tags,
            "description": level.description,
            "song": level.song,
            "edel_enjoyment": level.edel_enjoyment,
            "is_edel_pending": level.is_edel_pending,
            "gddl_tier": level.gddl_tier,
            "nlw_tier": level.nlw_tier,
        }
        levels_data.append(level_data)
    if levels_data.__len__() > 0:
        with Path.open(areplfilepath, "w", encoding="utf-8") as f:
            json.dump({"timestamp": time.time(), "levels": levels_data}, f, indent=4)
    else:
        logger.error(f"failed to save {arepllevels.__len__()} level datas")
    return arepllevels


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
        for level in aredllevels:
            if level.level_id == level_id:
                logger.warning(
                    f"Level ID {level_id} found in aredllevels_backup as #{level.position}"
                )
                return level
        return None

# 这里原本自己挂了一个 3 点的 scheduled_job，但它是坏的：赋值的是局部变量，
# 没有 global，抓完数据就丢了，模块全局一直是启动时那份。
# 现在统一由 gdlevelsearch 的 reload_all() 在每日更新跑完后调用上面的 reload()。
