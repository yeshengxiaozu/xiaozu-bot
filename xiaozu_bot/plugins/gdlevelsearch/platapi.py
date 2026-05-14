import json
from pathlib import Path
from typing import Any, Optional, Union


class PlatInfo:
    """Represents one level entry from plat_combined.json."""

    def __init__(  # noqa: PLR0913
        self,
        level_id: str,
        name: str,
        tier: Optional[str],
        tpl: Optional[str],
        pemonlist: Optional[str],
        creator: Optional[str],
        tags: list[str],
        enjoyment: Optional[float],
        video: Optional[str],
        weight: Optional[str],
        section: Optional[str],
        derived_from: Optional[str],
        derived_levels: list[str],
    ) -> None:
        self.id: str = level_id
        self.name: str = name
        self.tier: Optional[str] = tier
        self.tpl: Optional[str] = tpl
        self.pemonlist: Optional[str] = pemonlist
        self.creator: Optional[str] = creator
        self.tags: list[str] = tags
        self.enjoyment: Optional[float] = enjoyment
        self.video: Optional[str] = video
        self.weight: Optional[str] = weight
        self.section: Optional[str] = section
        self.derived_from: Optional[str] = derived_from
        self.derived_levels: list[str] = derived_levels
        self.is_main: bool = derived_from is None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlatInfo":
        def to_str(value: Any) -> Optional[str]:
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
        derived_levels = [str(level).strip() for level in derived_levels if level is not None]

        enjoyment = data.get("enjoyment")
        if enjoyment is not None:
            try:
                enjoyment = float(enjoyment)
            except (TypeError, ValueError):
                enjoyment = None
        if data.get("tpl") == "-":
            data["tpl"] = None
        if data.get("pemonlist") == "-":
            data["pemonlist"] = None

        return cls(
            level_id=to_str(data.get("id", "")) or "",
            name=to_str(data.get("name", "")) or "",
            tier=to_str(data.get("tier")),
            tpl=to_str(data.get("tpl")),
            pemonlist=to_str(data.get("pemonlist")),
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

    def __init__(self, cache_file: Optional[str] = None) -> None:
        self.cache_file = cache_file or (Path(__file__).parent / "data" / "plat_combined.json")
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
        for entry in self.main_entries:
            if entry.id and entry.id not in self.by_id:
                self.by_id[entry.id] = entry

        self.by_name = {}
        for entry in self.derived_entries:
            if entry.name and entry.name not in self.by_name:
                self.by_name[entry.name] = entry

        return self.entries

    def _fetch(self) -> list[PlatInfo]:
        payload = self._load_json(Path(self.cache_file))
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

    def _load_json(self, filepath: Path) -> Optional[dict[str, Any]]:
        try:
            with Path.open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            return None

    def getlevelbyid(self, level_id: str) -> Optional[PlatInfo]:
        return self.by_id.get(str(level_id).strip())


platdata = PlatData()
platdata_entries: list[PlatInfo] = platdata.entries
platdata_main_entries: list[PlatInfo] = platdata.main_entries
platdata_derived_entries: list[PlatInfo] = platdata.derived_entries
platdata_by_id: dict[str, PlatInfo] = platdata.by_id
platdata_by_name: dict[str, PlatInfo] = platdata.by_name

def fetch(cache_file: Optional[str] = None) -> list[PlatInfo]:
    """Reload plat data from JSON and return PlatInfo entries."""
    global platdata, platdata_entries, platdata_main_entries, platdata_derived_entries, platdata_by_id  # noqa: PLW0603
    platdata = PlatData(cache_file=cache_file) if cache_file else PlatData()
    platdata_entries = platdata.entries
    platdata_main_entries = platdata.main_entries
    platdata_derived_entries = platdata.derived_entries
    platdata_by_id = platdata.by_id
    return platdata_entries

def getlevelbyid(level_id: Optional[Union[str,int]]) -> Optional[PlatInfo]:
    """Get a main PlatInfo entry by its id."""
    if not level_id:
        return None
    return platdata.getlevelbyid(str(level_id))

def getderivedlevels(level: PlatInfo) -> list[PlatInfo]:
    """Get all derived entry from one PlatInfo entry"""
    return [platdata_by_name[derived_name] for derived_name in level.derived_levels]
