import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

from nonebot import logger

try:
    from ..paths import DATA_DIR, staged, staged_or_published
except ImportError:
    from updater.paths import DATA_DIR, staged, staged_or_published

ID_FIX = {
    #标记错误的id手动修复
    "112363390": "112603907",
    #无效关卡id统一处理为0
    "104683046": "0",
    "127566338": "0",
    "Pending Removal": "0",
}

@dataclass
class PlatLevel:
    """平台难度关卡数据类"""
    name: str
    id: Optional[str] = None
    tier: Optional[str] = None
    tpl: Optional[str] = None
    pemonlist: Optional[str] = None
    creator: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    enjoyment: Optional[float] = None
    video: Optional[str] = None
    weight: Optional[str] = None
    section: Optional[str] = None
    # platdata_tier: Optional[str] = None 重复词条无须保存
    derived_from: Optional[str] = None  # 如果是附属词条，指向主词条的名称
    derived_levels: List[str] = field(default_factory=list)  # 这个词条的所有附属词条
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'id': self.id,
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


def extract_base_name(name: str) -> tuple:
    """
    提取基础名称和附属词条
    例如：
    - "Null (Deathless)" -> ("Null", "Deathless")
    - "Moongrinder (Coin)" -> ("Moongrinder", "Coin")
    - "Normal Level" -> ("Normal Level", None)
    返回：(base_name, suffix)
    """
    if '(' in name and ')' in name:
        parts = name.rsplit('(', 1)
        if len(parts) == 2:
            base = parts[0].strip()
            suffix = parts[1].rstrip(')').strip()
            if base and suffix:  # 两部分都不能为空
                return base, suffix
    return name, None


def merge_plat_data() -> Dict[str, PlatLevel]:
    """
    合并来自三个模块的数据，以 name 为键值
    """
    logger.info("[PLATBATCH] loading platdata...")
    try:
        with staged_or_published("platdata.json").open("r", encoding="utf-8") as f:
            platdata_raw = json.load(f).get('data', [])
    except FileNotFoundError:
        logger.warning("[PLATBATCH] platdata.json not found, skip")
        platdata_raw = []
    
    logger.info("[PLATBATCH] loading platdiff...")
    try:
        with staged_or_published("platdiff.json").open("r", encoding="utf-8") as f:
            platdiff_raw = json.load(f).get('entries', [])
    except FileNotFoundError:
        logger.warning("[PLATBATCH] platdiff.json not found, skip")
        platdiff_raw = []
    
    logger.info("[PLATBATCH] loading platrank...")
    try:
        with staged_or_published("platrank_weights.json").open("r", encoding="utf-8") as f:
            platrank_raw = json.load(f).get('levels', [])
    except FileNotFoundError:
        logger.warning("[PLATBATCH] platrank_weights.json not found, skip")
        platrank_raw = []
    
    # 创建以 name 为键的字典
    merged: Dict[str, PlatLevel] = {}
    
    # 1. 加载 platdata
    for item in platdata_raw:
        name = item.get('name', '').strip()
        if not name:
            continue
        
        if name not in merged:
            merged[name] = PlatLevel(name=name)
        
        # 只在源数据中存在字段时才赋值，避免 None 覆盖已有值
        if 'id' in item:
            merged[name].id = item.get('id')
        if 'tpl' in item:
            merged[name].tpl = item.get('tpl')
        if 'pemonlist' in item:
            merged[name].pemonlist = item.get('pemonlist')
    
    # 2. 加载 platdiff
    for item in platdiff_raw:
        name = item.get('name', '').strip()
        if not name:
            continue
        
        if name not in merged:
            merged[name] = PlatLevel(name=name)
        
        # 优先使用 platdata 的 id，如果没有则使用 platdiff 的
        if not merged[name].id and 'id' in item:
            merged[name].id = item.get('id')
        
        # 只在源数据中存在字段时才赋值
        if 'tier' in item:
            merged[name].tier = item.get('tier')
        if 'creator' in item:
            merged[name].creator = item.get('creator')
        
        # 解析 tags（以逗号分隔为 List[str]）
        if 'tags' in item:
            tags_str = item.get('tags', '').strip()
            if tags_str:
                merged[name].tags = [t.strip() for t in tags_str.split(',') if t.strip()]
        
        if 'enjoyment' in item:
            merged[name].enjoyment = item.get('enjoyment')
        if 'video' in item:
            merged[name].video = item.get('video')
    
    # 3. 加载 platrank
    for item in platrank_raw:
        name = item.get('name', '').strip()
        if not name:
            continue
        
        if name not in merged:
            merged[name] = PlatLevel(name=name)
        
        # 只在源数据中存在字段时才赋值
        if 'weight' in item:
            merged[name].weight = item.get('weight')
        if 'section' in item:
            merged[name].section = item.get('section')
    
    return merged


def process_derived_levels(merged: Dict[str, PlatLevel]) -> Dict[str, PlatLevel]:
    """
    处理附属词条：
    1. 识别形如 "Null (Deathless)" 的词条
    2. 找到对应的主词条 "Null"
    3. 从主词条复制 id 到附属词条
    4. 在主词条中添加对附属词条的引用
    """
    # 先识别所有的附属词条
    derived_records: List[tuple] = []  # (derived_name, base_name)
    
    for name in list(merged.keys()):
        base_name, suffix = extract_base_name(name)
        
        if suffix and base_name in merged:
            # 这是一个附属词条，且找到了对应的主词条
            derived_records.append((name, base_name))
            merged[name].derived_from = base_name
            
            # 从主词条复制 id
            if merged[base_name].id:
                merged[name].id = merged[base_name].id
            
            # 在主词条中添加对附属词条的引用
            if name not in merged[base_name].derived_levels:
                merged[base_name].derived_levels.append(name)
    
    return merged


def clean_level_data(level: PlatLevel) -> None:
    """
    清理数据中的无效值：
    1. TPL 和 Pemonlist 中的 "-" 转为 None
    2. 从 Tags 中移除占位符 "---"，但保持为列表（即使为空）
    3. 确认关卡内容是否在ID_FIX中，若在其中则替换
    """
    # 清理 TPL
    if level.tpl == "-":
        level.tpl = None
    
    # 清理 Pemonlist
    if level.pemonlist == "-":
        level.pemonlist = None
    
    # 清理 Tags：移除占位符 "---"，但保持为列表类型（空列表也保留）
    if level.tags:
        level.tags = [tag for tag in level.tags if tag != "---"]

    if level.id in ID_FIX:
        level.id = ID_FIX[level.id]


def batch_process():
    """
    主函数：合并数据并处理附属词条
    """
    
    # 合并数据
    merged = merge_plat_data()
    logger.info(f"[PLATBATCH] merged: {len(merged)} levels")
    
    # 处理附属词条
    merged = process_derived_levels(merged)
    
    # 统计附属词条
    derived_count = sum(1 for level in merged.values() if level.derived_from)
    base_count = sum(1 for level in merged.values() if level.derived_levels)
    
    logger.info(f"[PLATBATCH] linked {derived_count} levels to {base_count} base levels")
    
    # 清理数据
    for level in merged.values():
        clean_level_data(level)
    
    # 移除清理之后id为0的条目
    merged = {name: level for name, level in merged.items() if level.id != "0"}
    
    # 保存结果
    output_data = {
        "timestamp": time.time(),
        "levels": [level.to_dict() for level in sorted(merged.values(), key=lambda x: x.name)]
    }
    
    output_path = staged("plat_combined.json")
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=4)
    
    logger.info(f"[PLATBATCH] saved to {output_path}")
    
    return merged


def batch():
    """
    在 batch.py 中调用的主函数
    """
    batch_process()


if __name__ == "__main__":
    batch_process()
