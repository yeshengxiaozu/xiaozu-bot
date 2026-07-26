import json
from pathlib import Path
import time, os
from typing import Any, Dict, List, Optional

from .googlesheetapi import SheetAPI, persistently
from .constants import PLAT_DIFF_ID, PLAT_DIFF_NAME

try:
    from ..paths import DATA_DIR, staged, staged_or_published
except ImportError:
    from updater.paths import DATA_DIR, staged, staged_or_published

class PlatDiff:
    def __init__(
        self,
        name: str,
        id: str,
        creator: str,
        tags: str,
        enjoyment: Optional[float],
        video: Optional[str],
        tier: Optional[str],
        sheet_index: int,
    ):
        self.name = name
        self.id = id
        self.creator = creator
        self.tags = tags
        self.enjoyment = enjoyment
        self.video = video
        self.tier = tier
        self.sheet_index = sheet_index

    def to_dict(self) -> Dict[str, Any]:
        return {
            'sheetIndex': self.sheet_index,
            'tier': self.tier,
            'name': self.name,
            'id': self.id,
            'creator': self.creator,
            'tags': self.tags,
            'enjoyment': self.enjoyment,
            'video': self.video,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PlatDiff':
        return cls(
            name=data.get('name', ''),
            id=data.get('id', ''),
            creator=data.get('creator', ''),
            tags=data.get('tags', ''),
            enjoyment=data.get('enjoyment'),
            video=data.get('video'),
            tier=data.get('tier'),
            sheet_index=data.get('sheetIndex', -1),
        )

    @staticmethod
    def _parse_enjoyment(value: str) -> Optional[float]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None


@persistently
def fetch_plat_diff_cells(service, sheet_id: str, sheet_name: str) -> Dict[str, List[str]]:
    names = SheetAPI.get_column_values(service, sheet_id, sheet_name, 'A')
    ids = SheetAPI.get_column_values(service, sheet_id, sheet_name, 'C')
    creators = SheetAPI.get_column_values(service, sheet_id, sheet_name, 'D')
    tags = SheetAPI.get_column_values(service, sheet_id, sheet_name, 'E')
    enjoyments = SheetAPI.get_column_values(service, sheet_id, sheet_name, 'F')
    videos = SheetAPI.get_hyperlink_column(service, sheet_id, sheet_name, 'G')

    return {
        'names': names,
        'ids': ids,
        'creators': creators,
        'tags': tags,
        'enjoyments': enjoyments,
        'videos': videos, # pyright: ignore[reportReturnType]
    }


def build_plat_diff_list(columns: Dict[str, List[str]]) -> List[PlatDiff]:
    names = columns.get('names', [])
    ids = columns.get('ids', [])
    creators = columns.get('creators', [])
    tags = columns.get('tags', [])
    enjoyments = columns.get('enjoyments', [])
    videos = columns.get('videos', [])

    entries: List[PlatDiff] = []
    last_tier: Optional[str] = None

    for i in range(len(names)):
        name = names[i].strip() if i < len(names) else ''
        level_id = ids[i].strip() if i < len(ids) else ''
        creator = creators[i].strip() if i < len(creators) else ''
        tag_value = tags[i].strip() if i < len(tags) else ''
        enjoyment_value = enjoyments[i].strip() if i < len(enjoyments) else ''
        video = videos[i] if i < len(videos) else None

        if not name:
            continue

        #can't use upper name because there is literally a level called "tier 1" and it kinda fucks up the logic
        if name.startswith('TIER'):
            tier_label = name[4:].strip()
            last_tier = tier_label if tier_label else None
            continue

        if not last_tier:
            continue

        enjoyment_float = PlatDiff._parse_enjoyment(enjoyment_value)

        entries.append(PlatDiff(
            name=name,
            id=level_id,
            creator=creator,
            tags=tag_value,
            enjoyment=enjoyment_float,
            video=video,
            tier=last_tier,
            sheet_index=i,
        ))

    return entries


@persistently
def fetch_plat_diff_levels(service, sheet_id: str, sheet_name: str) -> List[PlatDiff]:
    columns = fetch_plat_diff_cells(service, sheet_id, sheet_name)
    return build_plat_diff_list(columns)


def save_plat_diff_cache(entries: List[PlatDiff]) -> None:
    cache_path = staged('platdiff.json')

    payload = {
        'timestamp': time.time(),
        'entries': [entry.to_dict() for entry in entries],
    }

    with cache_path.open('w', encoding='utf-8') as f:
        json.dump(payload, f, indent=4)


def load_plat_diff_cache() -> List[PlatDiff]:
    cache_path = staged_or_published('platdiff.json')
    if not cache_path.exists():
        return []

    with cache_path.open('r', encoding='utf-8') as f:
        payload = json.load(f)

    entries = payload.get('entries', [])
    return [PlatDiff.from_dict(item) for item in entries]

def fetch() -> None:
    service = SheetAPI.get_service()
    entries = fetch_plat_diff_levels(service, PLAT_DIFF_ID, PLAT_DIFF_NAME)
    save_plat_diff_cache(entries)
