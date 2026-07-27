import json
import time
from typing import Any

from .googlesheetapi import SheetAPI, persistently

try:
    from ..paths import staged
except ImportError:
    from updater.paths import staged

from .constants import PLAT_RANK_ID


@persistently
def fetch_plat_rank_cells(service, sheet_name: str) -> dict[str, list]:
    names = SheetAPI.get_column_values(service, PLAT_RANK_ID, sheet_name, 'A')
    weights = SheetAPI.get_column_values(service, PLAT_RANK_ID, sheet_name, 'B')

    return {
        "names": names,
        "weights": weights
    }

def build_level_list(columns: dict[str, list]) -> list[dict[str, Any]]:
    names = columns['names']
    weights = columns['weights']

    level_objs = []
    current_section = None
    for i in range(len(names)):
        name=names[i]
        weight=weights[i]
        if not weight:
            current_section=name.removesuffix("Placements").strip()
            continue
        if not current_section:
            continue

        level_objs.append({
            'sheetIndex': i,
            'name': name,
            'weight': weight,
            'section': current_section
        })

    return level_objs

@persistently
def fetch_levels(service, sheet_name: str) -> list[dict[str, Any]]:
    cols = fetch_plat_rank_cells(service, sheet_name)
    return build_level_list(cols)

def fetch():
    data = fetch_levels(service=SheetAPI.get_service(),sheet_name="Weight")
    output_path = staged("platrank_weights.json")
    with output_path.open("w", encoding="utf-8") as f:
       json.dump({"timestamp": time.time(), "levels": data}, f, indent=4)

if __name__ == "__main__":
    fetch()
