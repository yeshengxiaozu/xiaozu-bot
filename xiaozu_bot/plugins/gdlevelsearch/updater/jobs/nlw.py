import os,json
import time
from nonebot import logger
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from .googlesheetapi import persistently,SheetAPI
from typing import Dict, List, Any

try:
    from ..paths import DATA_DIR, staged
except ImportError:
    from updater.paths import DATA_DIR, staged

from .constants import (
    NLW_ID,
    NLW_REGULAR_LEVELS_NAME,
    NLW_PENDING_LEVELS_NAME,
    NLW_REGULAR_PLATFORMER_LEVELS_NAME,
    NLW_PENDING_PLATFORMER_LEVELS_NAME,
    FRUITY_LEVELS_NLW as FRUITY_LEVELS,
    FRUITY_CREATORS_NLW as FRUITY_CREATORS,
    MOONTHLIES_PREFIX
)

# ------------------------------
# 数据抓取函数（现在接收 sheet_name 而非 sheet_id）
# ------------------------------
@persistently
def fetch_regular_cells(service, sheet_name: str) -> Dict[str, list]:
    videos = SheetAPI.get_hyperlink_column(service, NLW_ID, sheet_name, 'A')
    levels = SheetAPI.get_column_values(service, NLW_ID, sheet_name, 'B')
    creators = SheetAPI.get_column_values(service, NLW_ID, sheet_name, 'C')
    lengths = SheetAPI.get_column_values(service, NLW_ID, sheet_name, 'D')
    skillsets = SheetAPI.get_column_values_with_note(service, NLW_ID, sheet_name, 'E')
    enjoyments = SheetAPI.get_column_values(service, NLW_ID, sheet_name, 'F')
    descriptions = SheetAPI.get_column_values(service, NLW_ID, sheet_name, 'G')

    return {
        'levels': levels,
        'creators': creators,
        'checkpoints': [None] * len(levels),
        'lengths': lengths,
        'skillsets': skillsets,
        'enjoyments': enjoyments,
        'descriptions': descriptions,
        'videos': videos,
    }

@persistently
def fetch_platformer_cells(service, sheet_name: str) -> Dict[str, list]:
    videos = SheetAPI.get_hyperlink_column(service, NLW_ID, sheet_name, 'A')
    levels = SheetAPI.get_column_values(service, NLW_ID, sheet_name, 'B')
    creators = SheetAPI.get_column_values(service, NLW_ID, sheet_name, 'C')
    checkpoints = SheetAPI.get_column_values(service, NLW_ID, sheet_name, 'D')
    skillsets = SheetAPI.get_column_values_with_note(service, NLW_ID, sheet_name, 'E')
    enjoyments = SheetAPI.get_column_values(service, NLW_ID, sheet_name, 'F')
    descriptions = SheetAPI.get_column_values_with_note(service, NLW_ID, sheet_name, 'G')

    return {
        'levels': levels,
        'creators': creators,
        'checkpoints': checkpoints,
        'lengths': [None] * len(levels),
        'skillsets': skillsets,
        'enjoyments': enjoyments,
        'descriptions': descriptions,
        'videos': videos,
    }

def build_level_list(columns: Dict[str, list]) -> List[Dict[str, Any]]:
    levels = columns['levels']
    creators = columns['creators']
    checkpoints = columns['checkpoints']
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
        checkpoint = checkpoints[i] if i < len(checkpoints) else ''
        length = lengths[i] if i < len(lengths) else ''
        skillset = skillsets[i] if i < len(skillsets) else ''
        enjoyment = enjoyments[i] if i < len(enjoyments) else ''
        desc = descriptions[i] if i < len(descriptions) else ''
        video = videos[i] if i < len(videos) else None

        if not lvl:
            break

        if lvl.startswith('| '):
            last_tier = lvl[2:].replace(' Tier', '').strip()
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

        name = FRUITY_LEVELS.get(lvl, lvl).strip()
        if name == 'None Yet!':
            continue
        if name.startswith("Can't find an extreme") or name.startswith("If you STILL can't find an extreme"):
            break

        for moon in MOONTHLIES_PREFIX:
            if name.startswith(moon):
                name = name.removeprefix(moon)

        creator_clean = FRUITY_CREATORS.get(creator, creator)
        checkpoint_clean = checkpoint.strip() if checkpoint else None

        level_objs.append({
            'sheetIndex': i,
            'tier': last_tier,
            'name': name,
            'creator': creator_clean,
            'length': length,
            'skillset': skillset.strip(),
            'enjoyment': enjoyment_value,
            'description': desc.strip(),
            'checkpoints': checkpoint_clean,
            'video': video,
        })

    return level_objs

@persistently
def fetch_levels(service, sheet_name: str, platformer: bool, pending: bool) -> List[Dict[str, Any]]:
    if pending:
        if platformer:
            cols = fetch_platformer_cells(service, sheet_name)
        else:
            cols = fetch_regular_cells(service, sheet_name)
    else:
        if platformer:
            cols = fetch_platformer_cells(service, sheet_name)
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
    logger.info('[NLW] Loading spreadsheet info...')

    # 获取各工作表名称
    reg_name = NLW_REGULAR_LEVELS_NAME
    pend_name = NLW_PENDING_LEVELS_NAME
    plat_name = NLW_REGULAR_PLATFORMER_LEVELS_NAME
    pend_plat_name = NLW_PENDING_PLATFORMER_LEVELS_NAME

    logger.info('[NLW] Fetching NLW levels...')
    regular = fetch_levels(service, reg_name, platformer=False, pending=False)
    pending1 = fetch_levels(service, pend_name, platformer=False, pending=True)
    pending2 = fetch_levels(service, pend_plat_name, platformer=True, pending=True)
    platformer = fetch_levels(service, plat_name, platformer=True, pending=False)

    return {
        'regular': regular,
        'pending': pending1 + pending2,
        'platformer': platformer,
    }

def fetch():
    logger.info('[NLW] 开始生成 NLW 数据文件')
    data = fetch_all_levels()
    output_path = staged("nlw_levels.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
       json.dump({"timestamp": time.time(), "levels": data['regular']+data['platformer']+data['pending']}, f, indent=4)
    logger.info(f"[NLW] 已保存到 {output_path}")

if __name__ == '__main__':
    fetch()