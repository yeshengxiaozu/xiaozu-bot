# https://api.aredl.net/v2/docs
# get /api/aredl/levels/{level_id}
import json
import time
from pathlib import Path
from typing import Any, Optional

import requests
from nonebot import logger

HTTP_OK = 200

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
    description: Optional[str]
    song: Optional[int]
    edel_enjoyment: Optional[float]
    is_edel_pending: bool
    gddl_tier: Optional[float]
    nlw_tier: Optional[str]

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
    response = requests.get(url, headers=headers)
    if response.status_code == HTTP_OK:
        levels = response.json()
        aredllevels = [AREDLLevel(level) for level in levels]
        logger.info(f"successfully fetch {levels.__len__()} levels from {url}")
        return aredllevels
    logger.error(f"failed to fetch from {url}")
    return []


def get_aredl_levels() -> list[AREDLLevel]:
    work_folder = "xiaozu_bot/plugins/gdlevelsearch/data"
    aredlfilename = "aredl_levels.json"
    aredlfilepath = Path(work_folder) / aredlfilename
    if Path.exists(aredlfilepath):
        with Path.open(aredlfilepath, encoding="utf-8") as f:
            data = json.load(f)
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
    response = requests.get(url, headers=headers)
    if response.status_code == HTTP_OK:
        levels = response.json()
        arepllevels = [AREDLLevel(level) for level in levels]
        logger.info(f"successfully fetch {levels.__len__()} levels from {url}")
        return arepllevels
    logger.error(f"failed to fetch from {url}")
    return []


def get_arepl_levels() -> list[AREDLLevel]:
    work_folder = "xiaozu_bot/plugins/gdlevelsearch/data"
    areplfilename = "arepl_levels.json"
    areplfilepath = Path(work_folder) / areplfilename
    if Path.exists(areplfilepath):
        with Path.open(areplfilepath, encoding="utf-8") as f:
            data = json.load(f)
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


reload()


class Aredl:
    @staticmethod
    def getlevelbyid(level_id: int) -> Optional[AREDLLevel]:
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
