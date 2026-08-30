from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

from nonebot import logger

from ..paths import DATA_DIR


class PlatInfo:
    """Represents one level entry from plat_combined.json."""

    __slots__ = (
        "creator",
        "derived_from",
        "derived_levels",
        "enjoyment",
        "id",
        "is_main",
        "name",
        "pemonlist",
        "section",
        "tags",
        "tier",
        "tpl",
        "video",
        "weight",
    )

    def __init__(
        self,
        level_id: str,
        name: str,
        tier: str | None,
        tpl: str | None,
        pemonlist: str | None,
        creator: str | None,
        tags: list[str],
        enjoyment: float | None,
        video: str | None,
        weight: str | None,
        section: str | None,
        derived_from: str | None,
        derived_levels: list[str],
    ) -> None:
        self.id: str = level_id
        self.name: str = name
        self.tier: str | None = tier
        self.tpl: str | None = tpl
        self.pemonlist: str | None = pemonlist
        self.creator: str | None = creator
        self.tags: list[str] = tags
        self.enjoyment: float | None = enjoyment
        self.video: str | None = video
        self.weight: str | None = weight
        self.section: str | None = section
        self.derived_from: str | None = derived_from
        self.derived_levels: list[str] = derived_levels
        self.is_main: bool = derived_from is None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlatInfo":
        def to_str(value: Any) -> str | None:
            if value is None:
                return None
            return str(value).strip()

        tags = data.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        tags = [str(tag).strip() for tag in tags if tag is not None]
        if tags == ["---"]:
            tags = []

        derived_levels = data.get("derived_levels", [])
        if not isinstance(derived_levels, list):
            derived_levels = []
        derived_levels = [
            str(level).strip() for level in derived_levels if level is not None
        ]

        enjoyment = data.get("enjoyment")
        if enjoyment is not None:
            try:
                enjoyment = float(enjoyment)
            except (TypeError, ValueError):
                enjoyment = None

        tpl = to_str(data.get("tpl"))
        if tpl == "-":
            tpl = None

        pemonlist = to_str(data.get("pemonlist"))
        if pemonlist == "-":
            pemonlist = None

        return cls(
            level_id=to_str(data.get("id", "")) or "",
            name=to_str(data.get("name", "")) or "",
            tier=to_str(data.get("tier")),
            tpl=tpl,
            pemonlist=pemonlist,
            creator=to_str(data.get("creator")),
            tags=tags,
            enjoyment=enjoyment,
            video=to_str(data.get("video")),
            weight=to_str(data.get("weight")),
            section=to_str(data.get("section")),
            derived_from=to_str(data.get("derived_from")),
            derived_levels=derived_levels,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tier": self.tier,
            "tpl": self.tpl,
            "pemonlist": self.pemonlist,
            "creator": self.creator,
            "tags": self.tags,
            "enjoyment": self.enjoyment,
            "video": self.video,
            "weight": self.weight,
            "section": self.section,
            "derived_from": self.derived_from,
            "derived_levels": self.derived_levels,
        }


class PlatData:
    """Loads plat_combined.json and exposes lookup helpers."""

    def __init__(self, cache_file: str | Path | None = None) -> None:
        self.cache_file = Path(cache_file) if cache_file else (DATA_DIR / "plat_combined.json")
        self.entries: list[PlatInfo] = []
        self.main_entries: list[PlatInfo] = []
        self.derived_entries: list[PlatInfo] = []
        self.by_id: dict[str, PlatInfo] = {}
        self.by_name: dict[str, PlatInfo] = {}
        self.load()

    def load(self) -> list[PlatInfo]:
        self.entries = self._fetch()
        self.main_entries = [entry for entry in self.entries if entry.is_main]
        self.derived_entries = [entry for entry in self.entries if not entry.is_main]

        self.by_id = {}
        self.by_name = {}

        # 只需要遍历一次，同时构建两个索引
        for entry in self.entries:
            if entry.is_main and entry.id and entry.id not in self.by_id:
                self.by_id[entry.id] = entry

            if entry.name:
                key = entry.name.strip().lower()
                if key not in self.by_name:
                    self.by_name[key] = entry

        return self.entries

    def _fetch(self) -> list[PlatInfo]:
        payload = self._load_json(self.cache_file)
        if not payload:
            return []

        raw_entries = payload.get("levels", [])
        entries: list[PlatInfo] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            entry = PlatInfo.from_dict(item)
            if entry.id:
                entries.append(entry)
        return entries

    def _load_json(self, filepath: Path) -> dict[str, Any] | None:
        try:
            with filepath.open("r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            return None

    def getlevelbyid(self, level_id: str) -> PlatInfo | None:
        return self.by_id.get(str(level_id).strip())

    def getlevelbyname(self, name: str) -> PlatInfo | None:
        if not name:
            return None
        return self.by_name.get(name.strip().lower())

    def to_dict(self) -> dict[str, Any]:
        # 原来 self.__dict__.copy() 会把所有索引也复制进去。
        # 如果只是导出关卡数据，这样更轻量；如果不需要 to_dict，也可以直接删掉。
        return {"entries": [entry.to_dict() for entry in self.entries]}


platdata = PlatData()
platdata_entries: list[PlatInfo] = platdata.entries
platdata_main_entries: list[PlatInfo] = platdata.main_entries
platdata_derived_entries: list[PlatInfo] = platdata.derived_entries
platdata_by_id: dict[str, PlatInfo] = platdata.by_id
platdata_by_name: dict[str, PlatInfo] = platdata.by_name


def fetch(cache_file: str | Path | None = None) -> list[PlatInfo]:
    """Reload plat data from JSON and return PlatInfo entries."""
    global platdata, platdata_entries, platdata_main_entries, platdata_derived_entries
    global platdata_by_id, platdata_by_name

    platdata = PlatData(cache_file=cache_file) if cache_file else PlatData()
    platdata_entries = platdata.entries
    platdata_main_entries = platdata.main_entries
    platdata_derived_entries = platdata.derived_entries
    platdata_by_id = platdata.by_id
    platdata_by_name = platdata.by_name
    return platdata_entries


def reload() -> None:
    """重新从 plat_combined.json 加载 plat 数据"""
    fetch()
    logger.info(f"[platapi] 已加载 {len(platdata_entries)} 条 plat 关卡")


class Platapi:
    @staticmethod
    def getlevelbyid(level_id: str | int | None) -> PlatInfo | None:
        """Get a main PlatInfo entry by its id."""
        if not level_id:
            return None
        return platdata.getlevelbyid(str(level_id))

    @staticmethod
    def getlevelbyname(name: str) -> PlatInfo | None:
        """Get a main PlatInfo entry by its name."""
        if not name:
            return None
        return platdata.getlevelbyname(name)

    @staticmethod
    def getrandomlevelbytier(tier: int | str | None = None) -> PlatInfo | None:
        """Return a random usable main entry, optionally filtered by tier."""
        tier_text = None if tier is None else str(tier).strip()
        if tier_text is not None and not tier_text.isdigit():
            return None

        candidates = []
        for entry in platdata_main_entries:
            if not entry.id.isdigit() or int(entry.id) <= 0:
                continue
            match = re.match(r"^\s*(\d+)", entry.tier or "")
            if match is None:
                continue
            if tier_text is None or int(match.group(1)) == int(tier_text):
                candidates.append(entry)
        return random.choice(candidates) if candidates else None

    @staticmethod
    def getderivedlevels(level: PlatInfo) -> list[PlatInfo]:
        """Get all derived entry from one PlatInfo entry"""
        derived: list[PlatInfo] = []
        for derived_name in level.derived_levels:
            info = platdata_by_name.get(derived_name.strip().lower())
            if info is not None:
                derived.append(info)
        return derived
