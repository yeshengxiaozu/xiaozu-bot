
import json
import time
from typing import Dict, List, Any

from nonebot import logger
from .googlesheetapi import persistently,SheetAPI

try:
    from ..paths import staged
except ImportError:
    from updater.paths import staged

from .constants import (
    IDS_ID,
    IDS_LEVELS_NAME,
    IDS_PLATFORMER_LEVELS_NAME,
    FRUITY_LEVELS_IDS as FRUITY_LEVELS,
    FRUITY_CREATORS_IDS as FRUITY_CREATORS,
)

# ------------------------------
# 数据抓取函数（现在接收 sheet_name 而非 sheet_id）
# ------------------------------
@persistently
def fetch_regular_cells(service, sheet_name: str) -> Dict[str, list]:
    levels = SheetAPI.get_column_values(service, IDS_ID, sheet_name, 'A')
    videos = SheetAPI.get_hyperlink_column(service, IDS_ID, sheet_name, 'B')
    creators = SheetAPI.get_column_values(service, IDS_ID, sheet_name, 'C')
    lengths = SheetAPI.get_column_values(service, IDS_ID, sheet_name, 'D')
    skillsets = SheetAPI.get_column_values(service, IDS_ID, sheet_name, 'E')
    descriptions = SheetAPI.get_column_values(service, IDS_ID, sheet_name, 'F')

    return {
        'levels': levels,
        'creators': creators,
        'lengths': lengths,
        'checkpoints': [None] * len(levels),
        'skillsets': skillsets,
        'descriptions': descriptions,
        'videos': videos,
    }

@persistently
def fetch_platformer_cells(service, sheet_name: str) -> Dict[str, list]:
    levels = SheetAPI.get_column_values(service, IDS_ID, sheet_name, 'A')
    videos = SheetAPI.get_hyperlink_column(service, IDS_ID, sheet_name, 'B')
    creators = SheetAPI.get_column_values(service, IDS_ID, sheet_name, 'C')
    checkpoints = SheetAPI.get_column_values(service, IDS_ID, sheet_name, 'D')
    skillsets = SheetAPI.get_column_values(service, IDS_ID, sheet_name, 'E')
    descriptions = SheetAPI.get_column_values(service, IDS_ID, sheet_name, 'F')

    return {
        'levels': levels,
        'creators': creators,
        'lengths': [None] * len(levels),
        'checkpoints': checkpoints,
        'skillsets': skillsets,
        'descriptions': descriptions,
        'videos': videos,
    }

def build_level_list(columns: Dict[str, list]) -> List[Dict[str, Any]]:
    levels = columns['levels']
    creators = columns['creators']
    lengths = columns['lengths']
    checkpoints = columns['checkpoints']
    skillsets = columns['skillsets']
    descriptions = columns['descriptions']
    videos = columns['videos']

    level_objs = []
    last_tier = None

    for i in range(len(levels)):
        lvl = levels[i] if i < len(levels) else ''
        creator = creators[i] if i < len(creators) else ''
        length = lengths[i] if i < len(lengths) else ''
        checkpoint = checkpoints[i] if i < len(checkpoints) else ''
        skillset = skillsets[i] if i < len(skillsets) else ''
        desc = descriptions[i] if i < len(descriptions) else ''
        video = videos[i] if i < len(videos) else None

        if not lvl:
            break
        if lvl.startswith('↓'):
            last_tier = lvl[2:-2].strip()
            continue

        if not last_tier or last_tier == "Other" or last_tier == "Spreadsheet Fakes (Legacy)":
            continue # 干什么。。。
        if last_tier == 'Hard Demon/Extreme Demon Rerates':
            last_tier = 'Legacy' #demoted or promoted

        name = FRUITY_LEVELS.get(lvl, lvl).strip()

        creator_clean = FRUITY_CREATORS.get(creator, creator)
        checkpoint_clean = checkpoint.strip() if checkpoint else None

        level_objs.append({
            'sheetIndex': i,
            'tier': last_tier,
            'name': name,
            'creator': creator_clean,
            'length': length,
            'skillset': skillset.strip(),
            'description': desc.strip(),
            'checkpoints': checkpoint_clean,
            'video': video,
        })

    return level_objs

@persistently
def fetch_levels(service, sheet_name: str, platformer: bool, pending: bool) -> List[Dict[str, Any]]:
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
    logger.info('[IDS] Loading spreadsheet info...')

    # 获取各工作表名称
    reg_name = IDS_LEVELS_NAME
    plat_name = IDS_PLATFORMER_LEVELS_NAME

    logger.info('[IDS] Fetching IDS levels...')
    regular = fetch_levels(service, reg_name, platformer=False, pending=False)
    platformer = fetch_levels(service, plat_name, platformer=True, pending=False)

    return {
        'regular': regular,
        'platformer': platformer,
    }

def fetch():
    logger.info('[IDS] 开始生成 IDS 数据文件')
    data = fetch_all_levels()
    output_path = staged("ids_levels.json")
    with output_path.open("w", encoding="utf-8") as f:
       json.dump({"timestamp": time.time(), "levels": data['regular']+data['platformer']}, f, indent=4)
    logger.info(f"[IDS] 已保存到 {output_path}")

if __name__ == '__main__':
    fetch()