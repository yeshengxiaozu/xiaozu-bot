import time, os
from functools import wraps
from typing import Optional, List

from nonebot import logger
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ------------------------------
# 重试装饰器
# ------------------------------
def persistently(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        max_tries = 5
        for attempt in range(1, max_tries + 1):
            try:
                return func(*args, **kwargs)
            except HttpError as e:
                if e.resp.status in (429, 500, 503):
                    wait = 2 ** attempt
                    logger.warning(f'API error {e.resp.status}, retrying in {wait}s...')
                    time.sleep(wait)
                else:
                    raise
            except Exception as e:
                logger.warning(f'Unexpected error: {e}, retrying in 2s...')
                time.sleep(2)
        return func(*args, **kwargs)
    return wrapper

# ------------------------------
# Sheets 服务与工作表名称映射
# ------------------------------
class SheetAPI:
    
    @staticmethod
    def get_service():
        api_key = os.getenv('GOOGLE_SHEETS_API_KEY')
        if not api_key:
            api_key = 'AIzaSyALLzmrnlwXvymi4OdzysGS3rM77_0Qo2E'
            #raise ValueError('环境变量 API_KEY 未设置')
        return build('sheets', 'v4', developerKey=api_key)

    @staticmethod
    def get_column_values(service, sheet_ID: str, sheet_name: str, column: str) -> List[str]:
        """获取指定工作表中某一列的所有值（自动扩充到数据区域）"""
        try:
            range_name = f"'{sheet_name}'!{column}:{column}"
            result = service.spreadsheets().values().get(
                spreadsheetId=sheet_ID,
                range=range_name,
            ).execute()
            values = result.get('values', [])
            return [row[0] if row else '' for row in values]
        except Exception as e:
            logger.warning(f'获取数据失败：{e}')
            raise

    @staticmethod
    def get_column_values_with_note(service, sheet_ID: str, sheet_name: str, column: str) -> List[str]:
        """
        获取指定列的单元格内容，如果单元格存在备注，则格式化为 "内容[备注]"。
        参数与原 get_column_values 相同。
        """
        try:
            # 1. 先获取普通值列表（保持与原函数一致的行范围）
            range_name = f"'{sheet_name}'!{column}:{column}"
            result = service.spreadsheets().values().get(
                spreadsheetId=sheet_ID,
                range=range_name,
            ).execute()
            values = result.get('values', [])
            # 将原始值转换为一维字符串列表（空单元格转为 ''）
            plain_values = [row[0] if row else '' for row in values]

            # 2. 获取同一列的备注信息（需要 includeGridData）
            # 先获取工作表 ID
            sheet_metadata = service.spreadsheets().get(spreadsheetId=sheet_ID).execute()
            sheet_id = None
            for sheet in sheet_metadata.get('sheets', []):
                if sheet.get('properties', {}).get('title') == sheet_name:
                    sheet_id = sheet.get('properties', {}).get('sheetId')
                    break
            if sheet_id is None:
                raise ValueError(f"未找到工作表: {sheet_name}")

            # 调用 get 接口，带上 includeGridData 和精确的列范围
            get_result = service.spreadsheets().get(
                spreadsheetId=sheet_ID,
                ranges=[f"'{sheet_name}'!{column}:{column}"],
                includeGridData=True
            ).execute()

            # 解析备注：构建行号（从 1 开始）-> 备注的字典
            notes = {}
            sheets = get_result.get('sheets', [])
            if sheets:
                grid_data = sheets[0].get('data', [])
                if grid_data:
                    row_data = grid_data[0].get('rowData', [])
                    for row_idx, row in enumerate(row_data):
                        values_in_row = row.get('values', [])
                        if values_in_row:
                            note = values_in_row[0].get('note')
                            if note:
                                notes[row_idx + 1] = note  # 行号从1开始

            # 3. 合并：如有备注则格式化
            result_with_note = []
            for idx, cell_value in enumerate(plain_values):
                row_num = idx + 1
                note = notes.get(row_num)
                if note:
                    result_with_note.append(f"{cell_value}[{note}]")
                else:
                    result_with_note.append(cell_value)

            return result_with_note

        except Exception as e:
            logger.warning(f'获取带备注的列数据失败：{e}')
            raise
    
    @staticmethod
    def get_hyperlink_column(service, sheet_ID: str, sheet_name: str, column: str) -> List[Optional[str]]:
        """获取指定工作表中某一列的超链接列表，无超链接则为 None"""
        try:
            range_name = f"'{sheet_name}'!{column}:{column}"
            sheet_meta = service.spreadsheets().get(
                spreadsheetId=sheet_ID,
                ranges=[range_name],
                fields='sheets/data/rowData/values/hyperlink',
            ).execute()

            links = []
            rows = sheet_meta['sheets'][0]['data'][0].get('rowData', [])
            for row in rows:
                if 'values' in row and row['values']:
                    cell = row['values'][0]
                    links.append(cell.get('hyperlink'))
                else:
                    links.append(None)
            return links
        except Exception as e:
            logger.warning(f'获取超链接失败：{e}')
            raise
    
if __name__ == '__main__':
    service = SheetAPI.get_service()
    from constants import (
        NLW_ID,
        NLW_REGULAR_LEVELS_NAME,
        NLW_PENDING_LEVELS_NAME,
        NLW_REGULAR_PLATFORMER_LEVELS_NAME,
        NLW_PENDING_PLATFORMER_LEVELS_NAME,
        FRUITY_LEVELS_NLW as FRUITY_LEVELS,
        FRUITY_CREATORS_NLW as FRUITY_CREATORS,
    )

    sheet_ID = NLW_ID
    sheet_name = NLW_REGULAR_LEVELS_NAME
    values = SheetAPI.get_column_values(service, sheet_ID, sheet_name, 'A')
    print("successfully get values from sheet")