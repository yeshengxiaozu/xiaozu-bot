import json
import time
from pathlib import Path
from typing import Any, Optional, Union

import requests
from nonebot import logger

HTTP_OK = 200

class Level:
    source : str = "IDK"
    name : str
    creator : Optional[str] = None
    length : Optional[str] = None
    checkpoints : Optional[str] = None
    id : Optional[str] = None
    description : Optional[str] = None
    video : Optional[str] = None
    tier : Optional[str] = None #for elimate the red lines
    skillset : Optional[str] = None #for elimate the red lines
    def __init__(self, jsondict: dict[str, Any]) -> None:
        self.name = jsondict["name"]
        self.creator = jsondict["creator"]
        self.length = jsondict.get("length")
        self.checkpoints = jsondict.get("checkpoints")
        self.id = jsondict.get("id")
        self.description = jsondict.get("description")
        self.video = jsondict["video"]
    def __str__(self) -> str:
        return f"Name: {self.name}\nCreator: {self.creator}\nID: {self.id}\nDescription: {self.description}\nVideo: {self.video}"

class NLWlevel(Level):
    tier : Optional[str] = None
    skillset : Optional[str] = None
    enjoyment : Optional[float] = None
    def __init__(self, jsondict: dict[str, Any]) -> None:
        super().__init__(jsondict)
        self.source = "NLW"
        self.tier = jsondict["tier"]
        self.skillset = jsondict["skillset"]
        self.enjoyment = jsondict["enjoyment"]

class IDSlevel(Level):
    tier : Optional[str] = None
    skillset : Optional[str] = None
    def __init__(self, jsondict: dict[str, Any]) -> None:
        super().__init__(jsondict)
        self.source = "IDS"
        self.tier = jsondict["tier"]
        self.skillset = jsondict["skillset"]

class HDSlevel(Level):
    tier : Optional[str] = None
    skillset : Optional[str] = None
    def __init__(self, jsondict: dict[str, Any]) -> None:
        super().__init__(jsondict)
        self.source = "HDS"
        self.tier = jsondict["tier"]
        self.skillset = jsondict["skillset"]

class LWlevel(Level):
    tier : Optional[str] = None
    skillset : Optional[str] = None
    def __init__(self, jsondict: dict[str, Any]) -> None:
        super().__init__(jsondict)
        self.source = "LW"
        self.tier = jsondict["tier"]
        self.skillset = jsondict["skillset"]
        self.enjoyment = jsondict["enjoyment"]

nlwlevels = []
idslevels = []
lwlevels = []
hdslevels = []

#为了方便以及优化查询，直接创建一个可以通过id查询的字典
nlwlevel_dict = {}
idslevel_dict = {}
lwlevel_dict = {}
hdslevel_dict = {}

#把request到创建字典的过程封装成函数
def fetch_nlw_levels() -> None:
    #从https://nlw.oat.zone/中的/list获取信息
    nlwurl = "https://nlw.oat.zone/list?type=all"
    response = requests.get(nlwurl)
    if response.status_code == HTTP_OK:
        data = response.json()
        #data中的每个元素都是一个关卡的信息，遍历data并将信息储存在nlwlevels中
        for level_data in data:
            nlwlevel_instance = NLWlevel(level_data)
            nlwlevels.append(nlwlevel_instance)
    #从ids获取信息并如法炮制
    idsurl= "https://nlw.oat.zone/ids?type=all"
    response = requests.get(idsurl)
    if response.status_code == HTTP_OK:
        data = response.json()
        for level_data in data:
            idslevel_instance = IDSlevel(level_data)
            idslevels.append(idslevel_instance)

#创建一个函数来调用fetch_nlw_levels以获取信息，并且将获取的信息存储到本地json中，优先从本地json获取信息
#与此同时，在本地json中创建一个时间戳，以便在下一次调用函数时判断是否需要重新获取信息，暂定每天重新获取一次
def get_nlw_levels() -> None:  # noqa: C901, PLR0912, PLR0915
    work_folder = "xiaozu_bot/plugins/gdlevelsearch/data"
    nlwfilename = "nlw_levels.json"
    nlwfilepath = Path(work_folder) / nlwfilename
    idsfilename = "ids_levels.json"
    idsfilepath = Path(work_folder) / idsfilename
    lwfilename = "lw_levels.json"
    lwfilepath = Path(work_folder) / lwfilename
    hdsfilename = "hds_levels.json"
    hdsfilepath = Path(work_folder) / hdsfilename
    if Path.exists(nlwfilepath):
        with Path.open(nlwfilepath, "r") as f:
            data = json.load(f)
            timestamp = data.get("timestamp")
            if True:
                levels_data = data.get("levels", [])
                for level_data in levels_data:
                    nlwlevel_instance = NLWlevel(level_data)
                    nlwlevels.append(nlwlevel_instance)
            logger.info(f"Sussefully load {nlwlevels.__len__()} levels from nlw_levels.json")
            if timestamp and time.time() - timestamp > 7 * 24 * 3600:
                logger.warning("NLW本地缓存已经使用超过一周，建议再次fetch获取关卡")
        with Path.open(idsfilepath, "r") as f:
            data = json.load(f)
            timestamp = data.get("timestamp")
            if True:
                levels_data = data.get("levels", [])
                for level_data in levels_data:
                    idslevel_instance = IDSlevel(level_data)
                    idslevels.append(idslevel_instance)
            logger.info(f"Sussefully load {idslevels.__len__()} levels from ids_levels.json")
            if timestamp and time.time() - timestamp > 7 * 24 * 3600:
                logger.warning("IDS本地缓存已经使用超过一周，建议再次fetch获取关卡")
        with Path.open(lwfilepath, "r") as f:
            data = json.load(f)
            timestamp = data.get("timestamp")
            if True:
                levels_data = data.get("levels", [])
                for level_data in levels_data:
                    lwlevel_instance = LWlevel(level_data)
                    lwlevels.append(lwlevel_instance)
            logger.info(f"Sussefully load {lwlevels.__len__()} levels from lw_levels.json")
            if timestamp and time.time() - timestamp > 7 * 24 * 3600:
                logger.warning("LW本地缓存已经使用超过一周，建议再次fetch获取关卡")
        with Path.open(hdsfilepath, "r") as f:
            data = json.load(f)
            timestamp = data.get("timestamp")
            if True:
                levels_data = data.get("levels", [])
                for level_data in levels_data:
                    hdslevel_instance = HDSlevel(level_data)
                    hdslevels.append(hdslevel_instance)
            logger.info(f"Sussefully load {hdslevels.__len__()} levels from hds_levels.json")
            if timestamp and time.time() - timestamp > 7 * 24 * 3600:
                logger.warning("HDS本地缓存已经使用超过一周，建议再次fetch获取关卡")
        return
    logger.error("本地缓存不存在")
    return #因为无代理环境难以自动fetch，改为在另一设备手动执行fetch
    """
    fetch_nlw_levels()
    levels_data = []
    for level in nlwlevels:
        level_data = {
            "name": level.name,
            "creator": level.creator,
            "id": level.id,
            "description": level.description,
            "video": level.video,
            "tier": level.tier,
            "skillset": level.skillset,
            "enjoyment": level.enjoyment
        }
        levels_data.append(level_data)
    with open(nlwfilepath, "w") as f:
        json.dump({"timestamp": time.time(), "levels": levels_data}, f)
    levels_data = []
    for level in idslevels:
        level_data = {
            "name": level.name,
            "creator": level.creator,
            "id": level.id,
            "description": level.description,
            "video": level.video,
            "tier": level.tier,
            "skillset": level.skillset
        }
        levels_data.append(level_data)
    with open(idsfilepath, "w") as f:
        json.dump({"timestamp": time.time(), "levels": levels_data}, f)
    """


get_nlw_levels()
for level in nlwlevels:
    nlwlevel_dict[level.id] = level
for level in idslevels:
    idslevel_dict[level.id] = level
for level in lwlevels:
    lwlevel_dict[level.id] = level
for level in hdslevels:
    hdslevel_dict[level.id] = level

class Nlw:
    @staticmethod
    def nlw_query_level(level_id: Union[str,int]) -> Optional[NLWlevel]:
        return nlwlevel_dict.get(level_id)

    @staticmethod
    def ids_query_level(level_id: Union[str,int]) -> Optional[IDSlevel]:
        return idslevel_dict.get(level_id)

    @staticmethod
    def lw_query_level(level_id: Union[str,int]) -> Optional[LWlevel]:
        return lwlevel_dict.get(level_id)

    @staticmethod
    def hds_query_level(level_id: Union[str,int]) -> Optional[HDSlevel]:
        return hdslevel_dict.get(level_id)

    @staticmethod
    def getlevelbyid(level_id: Union[str,int]) -> Optional[Level]:
        level = Nlw.nlw_query_level(level_id)
        if level:
            logger.info(f"find the level as NLW {level.tier} Tier")
            return level
        level = Nlw.lw_query_level(level_id)
        if level:
            logger.info(f"find the level as LW {level.tier} Tier")
            return level
        leveli = Nlw.ids_query_level(level_id)
        levelh = Nlw.hds_query_level(level_id)
        level = None
        if leveli and levelh:
            if leveli.tier != "Legacy":
                logger.info(f"find the level as IDS {leveli.tier} Tier")
                level = leveli
            elif levelh.description and levelh.tier != "Legacy":
                logger.info(f"find the level as HDS {levelh.tier} Tier")
                level = levelh
            else:
                logger.info(f"find the level as IDS {leveli.tier} Tier")
                level = leveli

        elif leveli:
            logger.info(f"find the level as IDS {leveli.tier} Tier")
            level = leveli
        elif levelh:
            logger.info(f"find the level as HDS {levelh.tier} Tier")
            level = levelh
        return level
