from .googlesheetapi import SheetAPI,persistently
from typing import Optional, Dict, List, Any
import json,time
from pathlib import Path

try:
    from ..paths import DATA_DIR
except ImportError:
    from updater.paths import DATA_DIR

from .constants import PLAT_RANK_ID

@persistently
def fetch_plat_rank_cells(service, sheet_name: str) -> Dict[str, list]:
    names = SheetAPI.get_column_values(service, PLAT_RANK_ID, sheet_name, 'A')
    weights = SheetAPI.get_column_values(service, PLAT_RANK_ID, sheet_name, 'B')

    return {
        "names": names,
        "weights": weights
    }

def build_level_list(columns: Dict[str, list]) -> List[Dict[str, Any]]:
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
def fetch_levels(service, sheet_name: str) -> List[Dict[str, Any]]:
    cols = fetch_plat_rank_cells(service, sheet_name)
    return build_level_list(cols)

def fetch():
    data = fetch_levels(service=SheetAPI.get_service(),sheet_name="Weight")
    output_path = DATA_DIR / "platrank_weights.json"
    with output_path.open("w", encoding="utf-8") as f:
       json.dump({"timestamp": time.time(), "levels": data}, f, indent=4)

if __name__ == "__main__":
    fetch()