import json
import time
from typing import Any

from .constants import PLAT_DATA_ID, PLAT_DATA_SHEET_NAME
from .googlesheetapi import SheetAPI, persistently

try:
    from ..paths import staged
except ImportError:
    from updater.paths import staged


@persistently
def fetch_platdata_cells(service, sheet_id: str, sheet_name: str) -> dict[str, list]:
    """
    从 Google Sheet 中读取指定列的数据
    A列: ID
    B列: Name
    C列: Tier
    D列: TPL
    E列: Pemonlist
    """
    ids = SheetAPI.get_column_values(service, sheet_id, sheet_name, 'A')
    names = SheetAPI.get_column_values(service, sheet_id, sheet_name, 'B')
    tiers = SheetAPI.get_column_values(service, sheet_id, sheet_name, 'C')
    tpls = SheetAPI.get_column_values(service, sheet_id, sheet_name, 'D')
    pemonlists = SheetAPI.get_column_values(service, sheet_id, sheet_name, 'E')

    return {
        'id': ids,
        'name': names,
        'tier': tiers,
        'tpl': tpls,
        'pemonlist': pemonlists,
    }


def build_data_objects(columns: dict[str, list]) -> list[dict[str, Any]]:
    """
    将列数据转换为对象列表
    第一行为表头，后续行为数据
    当ID列为空时停止
    """
    ids = columns['id']
    names = columns['name']
    tiers = columns['tier']
    tpls = columns['tpl']
    pemonlists = columns['pemonlist']

    data_objects = []

    # 从第二行开始（跳过表头）
    for i in range(1, len(ids)):
        id_val = ids[i] if i < len(ids) else ''
        name_val = names[i] if i < len(names) else ''
        tier_val = tiers[i] if i < len(tiers) else ''
        tpl_val = tpls[i] if i < len(tpls) else ''
        pemonlist_val = pemonlists[i] if i < len(pemonlists) else ''

        # 当ID为空时停止
        if not id_val:
            break

        data_objects.append({
            'id': id_val,
            'name': name_val,
            'tier': tier_val,
            'tpl': tpl_val,
            'pemonlist': pemonlist_val,
        })

    return data_objects


@persistently
def fetch():
    """
    主函数：获取数据并保存到 JSON 文件
    """
    service = SheetAPI.get_service()

    # 读取指定列的数据
    columns = fetch_platdata_cells(service, PLAT_DATA_ID, PLAT_DATA_SHEET_NAME)

    # 构建数据对象列表
    data = build_data_objects(columns)

    # 保存到 JSON 文件
    output_data = {
        "timestamp": time.time(),
        "data": data,
        "row_count": len(data)
    }

    output_path = staged("platdata.json")
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=4)


if __name__ == "__main__":
    fetch()
