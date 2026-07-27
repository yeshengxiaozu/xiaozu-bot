import json
from pathlib import Path
from typing import Any

try:
    from ..paths import DATA_DIR
except ImportError:
    from updater.paths import DATA_DIR


class PlatInfo:
    """Represents one level entry from plat_combined.json."""

    def __init__(
        self,
        id: str,
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
        self.id: str = id
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
    def from_dict(cls, data: dict[str, Any]) -> 'PlatInfo':
        def to_str(value: Any) -> str | None:
            if value is None:
                return None
            return str(value).strip()

        tags = data.get('tags', [])
        if not isinstance(tags, list):
            tags = []
        tags = [str(tag).strip() for tag in tags if tag is not None]

        derived_levels = data.get('derived_levels', [])
        if not isinstance(derived_levels, list):
            derived_levels = []
        derived_levels = [str(level).strip() for level in derived_levels if level is not None]

        enjoyment = data.get('enjoyment')
        if enjoyment is not None:
            try:
                enjoyment = float(enjoyment)
            except (TypeError, ValueError):
                enjoyment = None

        return cls(
            id=to_str(data.get('id', '')) or '',
            name=to_str(data.get('name', '')) or '',
            tier=to_str(data.get('tier')),
            tpl=to_str(data.get('tpl')),
            pemonlist=to_str(data.get('pemonlist')),
            creator=to_str(data.get('creator')),
            tags=tags,
            enjoyment=enjoyment,
            video=to_str(data.get('video')),
            weight=to_str(data.get('weight')),
            section=to_str(data.get('section')),
            derived_from=to_str(data.get('derived_from')),
            derived_levels=derived_levels,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'tier': self.tier,
            'tpl': self.tpl,
            'pemonlist': self.pemonlist,
            'creator': self.creator,
            'tags': self.tags,
            'enjoyment': self.enjoyment,
            'video': self.video,
            'weight': self.weight,
            'section': self.section,
            'derived_from': self.derived_from,
            'derived_levels': self.derived_levels,
        }


class PlatData:
    """Loads plat_combined.json and exposes lookup helpers."""

    def __init__(self, cache_file: str | None = None) -> None:
        self.cache_file = cache_file or str(DATA_DIR / 'plat_combined.json')
        self.entries: list[PlatInfo] = []
        self.main_entries: list[PlatInfo] = []
        self.derived_entries: list[PlatInfo] = []
        self.by_id: dict[str, PlatInfo] = {}
        self.load()

    def load(self) -> list[PlatInfo]:
        self.entries = self._fetch()
        self.main_entries = [entry for entry in self.entries if entry.is_main]
        self.derived_entries = [entry for entry in self.entries if not entry.is_main]

        self.by_id = {}
        for entry in self.main_entries:
            if entry.id and entry.id not in self.by_id:
                self.by_id[entry.id] = entry

        return self.entries

    def _fetch(self) -> list[PlatInfo]:
        payload = self._load_json(self.cache_file)
        if not payload:
            return []

        raw_entries = payload.get('levels', [])
        entries: list[PlatInfo] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            entry = PlatInfo.from_dict(item)
            if entry.id:
                entries.append(entry)
        return entries

    def _load_json(self, filepath: str) -> dict[str, Any] | None:
        path = Path(filepath)
        try:
            with path.open('r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            return None

    def getlevelbyid(self, level_id: str) -> PlatInfo | None:
        return self.by_id.get(str(level_id).strip())


platdata = PlatData()
platdata_entries: list[PlatInfo] = platdata.entries
platdata_main_entries: list[PlatInfo] = platdata.main_entries
platdata_derived_entries: list[PlatInfo] = platdata.derived_entries
platdata_by_id: dict[str, PlatInfo] = platdata.by_id


def fetch(cache_file: str | None = None) -> list[PlatInfo]:
    """Reload plat data from JSON and return PlatInfo entries."""
    global platdata, platdata_entries, platdata_main_entries, platdata_derived_entries, platdata_by_id
    platdata = PlatData(cache_file=cache_file) if cache_file else PlatData()
    platdata_entries = platdata.entries
    platdata_main_entries = platdata.main_entries
    platdata_derived_entries = platdata.derived_entries
    platdata_by_id = platdata.by_id
    return platdata_entries


def getlevelbyid(level_id: str) -> PlatInfo | None:
    """Get a main PlatInfo entry by its id."""
    return platdata.getlevelbyid(level_id)
