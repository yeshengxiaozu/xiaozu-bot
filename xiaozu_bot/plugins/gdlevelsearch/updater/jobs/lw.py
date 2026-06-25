import os,json
import time
from pathlib import Path
from functools import wraps
from typing import Optional, Dict, List, Any

from nonebot import logger
from .googlesheetapi import persistently,SheetAPI

try:
    from ..paths import DATA_DIR
except ImportError:
    from updater.paths import DATA_DIR

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from .constants import LW_ID, LW_LEVELS_NAME, LW_PENDING_LEVELS_NAME

# ------------------------------
# 数据抓取函数（现在接收 sheet_name 而非 sheet_id）
# ------------------------------
@persistently
def fetch_regular_cells(service, sheet_name: str) -> Dict[str, list]:
    videos = SheetAPI.get_hyperlink_column(service, LW_ID, sheet_name, 'A')
    levels = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'B')
    creators = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'C')
    lengths = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'D')
    skillsets = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'E')
    enjoyments = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'F')
    descriptions = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'G')

    return {
        'levels': levels,
        'creators': creators,
        'lengths': lengths,
        'skillsets': skillsets,
        'enjoyments': enjoyments,
        'descriptions': descriptions,
        'videos': videos,
    }

@persistently
def fetch_pending_cells(service, sheet_name: str) -> Dict[str, list]:
    levels = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'A')
    creators = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'B')
    lengths = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'C')
    skillsets = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'D')
    enjoyments = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'E')
    descriptions = SheetAPI.get_column_values(service, LW_ID, sheet_name, 'F')

    return {
        'levels': levels,
        'creators': creators,
        'lengths': lengths,
        'skillsets': skillsets,
        'enjoyments': enjoyments,
        'descriptions': descriptions,
        'videos': [None] * len(levels),
    }

def build_level_list(columns: Dict[str, list]) -> List[Dict[str, Any]]:
    levels = columns['levels']
    creators = columns['creators']
    lengths = columns['lengths']
    skillsets = columns['skillsets']
    enjoyments = columns['enjoyments']
    descriptions = columns['descriptions']
    videos = columns['videos']

    level_objs = []
    last_tier = None

    for i in range(len(levels)):
        lvl = levels[i] if i < len(levels) else ''
        creator = creators[i] if i < len(creators) else ''
        length = lengths[i] if i < len(lengths) else ''
        skillset = skillsets[i] if i < len(skillsets) else ''
        enjoyment = enjoyments[i] if i < len(enjoyments) else ''
        desc = descriptions[i] if i < len(descriptions) else ''
        video = videos[i] if i < len(videos) else None

        if i > 50 and not lvl and last_tier != "Shortcuts":
            break

        if lvl.startswith('| '):
            last_tier = lvl[2:].replace(' Tier', '').strip()
            continue

        if lvl in ["Low End","Low-Mid Range","Mid Range","Mid-High Range","High End","Ouchie","Unknown"]:
            last_tier = lvl
            continue

        if not last_tier or last_tier == "Shortcuts":
            continue
        if last_tier == 'Not enough levels for you?':
            break

        enjoyment_value = None
        if enjoyment:
            try:
                enjoyment_value = float(enjoyment)
            except ValueError:
                pass

        name = lvl.strip()
        if name == 'None Yet!':
            continue
        if name.startswith("Can't find an extreme") or name.startswith("If you STILL can't find an extreme") or name.startswith("More info on pending levels here."):
            break

        creator_clean = creator

        level_objs.append({
            'sheetIndex': i,
            'tier': last_tier,
            'name': name,
            'creator': creator_clean,
            'length': length,
            'skillset': skillset.strip(),
            'enjoyment': enjoyment_value,
            'description': desc.strip(),
            'video': video,
        })

    return level_objs

@persistently
def fetch_levels(service, sheet_name: str, pending) -> List[Dict[str, Any]]:
    if pending:
        cols = fetch_pending_cells(service, sheet_name)
    else:
        cols = fetch_regular_cells(service, sheet_name)
    return build_level_list(cols)

# ------------------------------
# 公开接口
# ------------------------------
def fetch_all_levels() -> Dict[str, List[Dict[str, Any]]]:
    """
    返回全部已整理关卡数据。
    返回值:
      {
        'regular': [...],
        'pending': [...],
        'platformer': [...]
      }
    """
    service = SheetAPI.get_service()
    logger.info('[LW] Loading spreadsheet info...')

    # 获取各工作表名称
    lvl_name = LW_LEVELS_NAME
    pending_lvl_name = LW_PENDING_LEVELS_NAME

    logger.info('[LW] Fetching LW levels...')
    regular = fetch_levels(service, lvl_name, pending=False)
    pending = fetch_levels(service, pending_lvl_name, pending=True)

    return {
        'regular': regular,
        'pending': pending,
    }

def fetch():
    logger.info('[LW] 开始生成 LW 数据文件')
    data = fetch_all_levels()
    output_path = DATA_DIR / "lw_levels.json"
    with output_path.open("w", encoding="utf-8") as f:
       json.dump({"timestamp": time.time(), "levels": data['regular']+data['pending']}, f, indent=4)
    logger.info(f"[LW] 已保存到 {output_path}")

if __name__ == '__main__':
    fetch()