import json
import time
from pathlib import Path
from typing import Any, Optional, Union

import requests
from nonebot import logger

HTTP_OK = 200


class Level:
    source: str = "IDK"
    name: str
    creator: Optional[str] = None
    length: Optional[str] = None
    checkpoints: Optional[str] = None
    id: Optional[str] = None
    description: Optional[str] = None
    video: Optional[str] = None
    tier: Optional[str] = None  # for elimate the red lines
    skillset: Optional[str] = None  # for elimate the red lines

    def __init__(self, jsondict: dict[str, Any]) -> None:
        self.name = jsondict["name"]
        self.creator = jsondict["creator"]
        self.length = jsondict.get("length")
        self.checkpoints = jsondict.get("checkpoints")
        self.id = jsondict.get("id")
        self.description = jsondict.get("description")
        self.video = jsondict["video"]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

class NLWlevel(Level):
    tier: Optional[str] = None
    skillset: Optional[str] = None
    enjoyment: Optional[float] = None

    def __init__(self, jsondict: dict[str, Any]) -> None:
        super().__init__(jsondict)
        self.source = "NLW"
        self.tier = jsondict["tier"]
        self.skillset = jsondict["skillset"]
        self.enjoyment = jsondict["enjoyment"]


class IDSlevel(Level):
    tier: Optional[str] = None
    skillset: Optional[str] = None

    def __init__(self, jsondict: dict[str, Any]) -> None:
        super().__init__(jsondict)
        self.source = "IDS"
        self.tier = jsondict["tier"]
        self.skillset = jsondict["skillset"]


class HDSlevel(Level):
    tier: Optional[str] = None
    skillset: Optional[str] = None

    def __init__(self, jsondict: dict[str, Any]) -> None:
        super().__init__(jsondict)
        self.source = "HDS"
        self.tier = jsondict["tier"]
        self.skillset = jsondict["skillset"]


class LWlevel(Level):
    tier: Optional[str] = None
    skillset: Optional[str] = None

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

# 为了方便以及优化查询，直接创建一个可以通过id查询的字典
nlwlevel_dict = {}
idslevel_dict = {}
lwlevel_dict = {}
hdslevel_dict = {}


# 数据目录。以前写的是相对当前工作目录的 "xiaozu_bot/plugins/..."，
# 换个目录启动就读不到；改成相对本文件。
WORK_FOLDER = Path(__file__).resolve().parent / "data"

# 四个数据源长得一模一样，列出来循环处理就行
_SOURCES = (
    ("nlw_levels.json", "NLW", NLWlevel, nlwlevels),
    ("ids_levels.json", "IDS", IDSlevel, idslevels),
    ("lw_levels.json", "LW", LWlevel, lwlevels),
    ("hds_levels.json", "HDS", HDSlevel, hdslevels),
)

STALE_AFTER = 7 * 24 * 3600


# 抓取逻辑现在统一由 updater/jobs/{nlw,ids,lw,hds}.py 负责，这里只负责读缓存。
def get_nlw_levels() -> None:
    """从各路 json 里读数据进内存。

    每个源单独判断存在、单独 try —— 以前是只判断 nlw 一个文件存在就
    无条件打开另外三个，而且四个 json.load 都没有保护：
    任何一个文件损坏或缺失都会让异常冒到插件加载器，
    整个 gdlevelsearch（包括跟这些文件无关的命令）一起下线。
    """
    for filename, label, cls, target in _SOURCES:
        path = WORK_FOLDER / filename
        if not path.exists():
            logger.warning(f"[nlwapi] {label} 缓存不存在，跳过: {path}")
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.exception(f"[nlwapi] {label} 缓存读不了，跳过: {path}")
            continue

        for level_data in data.get("levels", []):
            try:
                target.append(cls(level_data))
            except (KeyError, TypeError):
                logger.exception(f"[nlwapi] {label} 有一条数据格式不对，跳过这条")

        logger.info(f"[nlwapi] {label} 载入 {len(target)} 条（{filename}）")

        timestamp = data.get("timestamp")
        if timestamp and time.time() - timestamp > STALE_AFTER:
            logger.warning(f"{label}本地缓存已经使用超过一周，建议再次fetch获取关卡")


def _rebuild_dicts() -> None:
    """按 id 重建四张查询表"""
    for dct, levels in (
        (nlwlevel_dict, nlwlevels),
        (idslevel_dict, idslevels),
        (lwlevel_dict, lwlevels),
        (hdslevel_dict, hdslevels),
    ):
        dct.clear()
        for level in levels:
            dct[level.id] = level


def reload() -> None:
    """重新从本地缓存加载 NLW/IDS/LW/HDS 数据。

    get_nlw_levels() 是往 list 里 append 的，所以必须先 clear，
    否则每更新一次条目就翻一倍。
    也正因为要保持 `from .nlwapi import nlwlevels` 那种引用有效，
    这里全部原地清空而不是重新赋值。
    """
    for levels in (nlwlevels, idslevels, lwlevels, hdslevels):
        levels.clear()
    get_nlw_levels()
    _rebuild_dicts()


reload()


class Nlw:
    """nlwapi的接口类"""
    @staticmethod
    def nlw_query_level(level_id: Union[str, int]) -> Optional[NLWlevel]:
        """从NLW表格中查询指定id的关卡"""
        return nlwlevel_dict.get(level_id)

    @staticmethod
    def ids_query_level(level_id: Union[str, int]) -> Optional[IDSlevel]:
        """从IDS表格中查询指定id的关卡"""
        return idslevel_dict.get(level_id)

    @staticmethod
    def lw_query_level(level_id: Union[str, int]) -> Optional[LWlevel]:
        """从LW表格中查询指定id的关卡"""
        return lwlevel_dict.get(level_id)

    @staticmethod
    def hds_query_level(level_id: Union[str, int]) -> Optional[HDSlevel]:
        """从HDS表格中查询指定id的关卡"""
        return hdslevel_dict.get(level_id)

    @staticmethod
    def getlevelbyname(name: str) -> list[Level]:
        """从所有表格中查询指定名称的关卡"""
        levels = []
        normalized = name.strip().lower()
        for level in (*nlwlevels, *idslevels, *lwlevels, *hdslevels):
            if not level or not getattr(level, "name", None):
                continue
            if level.name.strip().lower() == normalized:
                levels.append(level)
        return levels

    @staticmethod
    def getlevelbyid(level_id: Union[str, int]) -> Optional[Level]:
        """从所有表格中查询指定id的关卡，总是尝试获取最佳匹配"""
        level = Nlw.nlw_query_level(level_id)
        if level:
            logger.trace(f"find the level as NLW {level.tier} Tier")
            return level
        level = Nlw.lw_query_level(level_id)
        if level:
            logger.trace(f"find the level as LW {level.tier} Tier")
            return level
        leveli = Nlw.ids_query_level(level_id)
        levelh = Nlw.hds_query_level(level_id)
        level = None
        if leveli and levelh:
            if leveli.tier != "Legacy":
                logger.trace(f"find the level as IDS {leveli.tier} Tier")
                level = leveli
            elif levelh.description and levelh.tier != "Legacy":
                logger.trace(f"find the level as HDS {levelh.tier} Tier")
                level = levelh
            else:
                logger.trace(f"find the level as IDS {leveli.tier} Tier")
                level = leveli

        elif leveli:
            logger.trace(f"find the level as IDS {leveli.tier} Tier")
            level = leveli
        elif levelh:
            logger.trace(f"find the level as HDS {levelh.tier} Tier")
            level = levelh
        return level
