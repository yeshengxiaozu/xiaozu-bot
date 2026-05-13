#https://api.aredl.net/v2/docs
#get /api/aredl/levels/{level_id}
from nonebot import logger
import requests
import json
import os,time
from typing import Optional

class AREDLLevel:
    id : str
    position : Optional[int] = None
    name : str
    points : Optional[float] = None
    legacy : bool
    level_id : int
    two_player : bool
    tags : list[str]
    description : Optional[str] = None
    song : Optional[int] = None
    edel_enjoyment : Optional[float] = None
    is_edel_pending : Optional[bool] = None
    gddl_tier : Optional[float] = None
    nlw_tier : Optional[str] = None
    verificationurl : Optional[str] = None
    def __init__(self, jsondict):
        self.id = jsondict['id']
        self.position = jsondict['position']
        self.name = jsondict['name']
        self.points = jsondict['points']
        self.legacy = jsondict['legacy']
        self.level_id = jsondict['level_id']
        self.two_player = jsondict['two_player']
        self.tags = jsondict['tags']
        self.description = jsondict['description']
        self.song = jsondict['song']
        self.edel_enjoyment = jsondict['edel_enjoyment']
        self.is_edel_pending = jsondict['is_edel_pending']
        self.gddl_tier = jsondict['gddl_tier']
        self.nlw_tier = jsondict['nlw_tier']
        self.verificationurl = None
    def __str__(self):
        return f"ID: {self.id}\nPosition: {self.position}\nName: {self.name}\nPoints: {self.points}\nLegacy: {self.legacy}\nLevel ID: {self.level_id}\nTwo Player: {self.two_player}\nTags: {', '.join(self.tags if self.tags else [])}\nDescription: {self.description}\nSong: {self.song}\nEdel Enjoyment: {self.edel_enjoyment}\nIs Edel Pending: {self.is_edel_pending}\nGDDL Tier: {self.gddl_tier}\nNLW Tier: {self.nlw_tier}\nVerification URL: {self.verificationurl}"

aredllevels = []
arepllevels = []

def fetch_aredl_levels():
    url = "https://api.aredl.net/v2/api/aredl/levels"
    headers = {
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        levels = response.json()
        aredllevels = [AREDLLevel(level) for level in levels]
        logger.info(f"successfully fetch {levels.__len__()} levels from {url}")
        return aredllevels
    else:
        logger.error(f"failed to fetch from {url}")
        return []

def get_aredl_levels():
    work_folder = "xiaozu_bot/plugins/gdlevelsearch/data"
    aredlfilename = "aredl_levels.json"
    aredlfilepath = os.path.join(work_folder, aredlfilename)
    if os.path.exists(aredlfilepath):
        with open(aredlfilepath, "r") as f:
            data = json.load(f)
            timestamp = data.get("timestamp")
            if timestamp and time.time() - timestamp < 24 * 3600:
                levels_data = data.get("levels", [])
                aredllevels = [AREDLLevel(level_data) for level_data in levels_data]
                logger.info(f"successly load {levels_data.__len__()} levels from aredl_levels.json")
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
        with open(aredlfilepath, "w") as f:
            json.dump({"timestamp": time.time(), "levels": levels_data}, f, indent=4)
    else:
        logger.error(f"failed to save {aredllevels.__len__()} level datas")
    return aredllevels

def fetch_arepl_levels():
    url = "https://api.aredl.net/v2/api/arepl/levels"
    headers = {
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        levels = response.json()
        arepllevels = [AREDLLevel(level) for level in levels]
        logger.info(f"successfully fetch {levels.__len__()} levels from {url}")
        return arepllevels
    else:
        logger.error(f"failed to fetch from {url}")
        return []

def get_arepl_levels():
    work_folder = "xiaozu_bot/plugins/gdlevelsearch/data"
    areplfilename = "arepl_levels.json"
    areplfilepath = os.path.join(work_folder, areplfilename)
    if os.path.exists(areplfilepath):
        with open(areplfilepath, "r") as f:
            data = json.load(f)
            timestamp = data.get("timestamp")
            if timestamp and time.time() - timestamp < 24 * 3600:
                arepllevels = []
                levels_data = data.get("levels", [])
                for level_data in levels_data:
                    aredllevel_instance = AREDLLevel(level_data)
                    arepllevels.append(aredllevel_instance)
                logger.info(f"successly load {levels_data.__len__()} levels from arepl_levels.json")
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
        with open(areplfilepath, "w") as f:
            json.dump({"timestamp": time.time(), "levels": levels_data}, f, indent=4)
    else:
        logger.error(f"failed to save {arepllevels.__len__()} level datas")
    return arepllevels

aredllevels = get_aredl_levels()
arepllevels = get_arepl_levels()
aredl_dict = {}

for level in aredllevels:
    if level.level_id not in aredl_dict:
        aredl_dict[level.level_id] = level
    #同关卡保留第一个也就是排位高的，用于兼容2p

for level in arepllevels:
    if level.level_id not in aredl_dict:
        aredl_dict[level.level_id] = level

class aredl:
    def getlevelbyid(id):
        if id in aredl_dict:
            logger.info(f"Level ID {id} found in aredl_dict as #{aredl_dict[id].position}")
            return aredl_dict[id]
        else:
            for level in aredllevels:
                if level.level_id == id:
                    logger.warning(f"Level ID {id} found in aredllevels_backup as #{level.position}")
                    return level
            return None