import os
import time
from collections.abc import Callable
from functools import wraps
from threading import Semaphore
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from nonebot import logger

_SHEETS_EXECUTE_SEMAPHORE = Semaphore(1)
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _status_code(error: HttpError) -> int | None:
    return getattr(getattr(error, "resp", None), "status", None)


def _execute(request: Callable[[], Any]) -> Any:
    """Execute one Sheets request with global serialization and bounded retry."""
    max_tries = 5
    for attempt in range(1, max_tries + 1):
        try:
            with _SHEETS_EXECUTE_SEMAPHORE:
                return request()
        except HttpError as error:
            status = _status_code(error)
            if status not in _RETRYABLE_STATUS_CODES:
                raise
            wait = 60 if status == 429 else 2**attempt
            logger.warning(
                f"API error {status}, retrying in {wait}s "
                f"({attempt}/{max_tries})..."
            )
            if attempt == max_tries:
                raise
            time.sleep(wait)
        except (TimeoutError, ConnectionError) as error:
            logger.warning(
                f"Transient Sheets error: {error}, "
                f"retrying ({attempt}/{max_tries})..."
            )
            if attempt == max_tries:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


def persistently(func: Callable[..., Any]) -> Callable[..., Any]:
    """Compatibility decorator backed by the shared Sheets execute policy."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return _execute(lambda: func(*args, **kwargs))

    return wrapper


class SheetAPI:
    @staticmethod
    def get_service():
        api_key = os.getenv("GOOGLE_SHEETS_API_KEY")
        if not api_key:
            api_key = "AIzaSyALLzmrnlwXvymi4OdzysGS3rM77_0Qo2E"
        return build("sheets", "v4", developerKey=api_key)

    @staticmethod
    def get_column_values(service, sheet_ID: str, sheet_name: str, column: str) -> list[str]:
        range_name = f"'{sheet_name}'!{column}:{column}"
        request = service.spreadsheets().values().get(
            spreadsheetId=sheet_ID,
            range=range_name,
        )
        result = _execute(request.execute)
        values = result.get("values", [])
        return [row[0] if row else "" for row in values]

    @staticmethod
    def get_column_values_with_note(
        service, sheet_ID: str, sheet_name: str, column: str
    ) -> list[str]:
        range_name = f"'{sheet_name}'!{column}:{column}"
        result = _execute(
            service.spreadsheets().values().get(
                spreadsheetId=sheet_ID,
                range=range_name,
            ).execute
        )
        values = result.get("values", [])
        plain_values = [row[0] if row else "" for row in values]

        sheet_metadata = _execute(
            service.spreadsheets().get(spreadsheetId=sheet_ID).execute
        )
        sheet_id = next(
            (
                sheet.get("properties", {}).get("sheetId")
                for sheet in sheet_metadata.get("sheets", [])
                if sheet.get("properties", {}).get("title") == sheet_name
            ),
            None,
        )
        if sheet_id is None:
            raise ValueError(f"未找到工作表: {sheet_name}")

        get_result = _execute(
            service.spreadsheets()
            .get(
                spreadsheetId=sheet_ID,
                ranges=[range_name],
                includeGridData=True,
            )
            .execute
        )
        notes: dict[int, str] = {}
        sheets = get_result.get("sheets", [])
        if sheets:
            grid_data = sheets[0].get("data", [])
            if grid_data:
                for row_idx, row in enumerate(grid_data[0].get("rowData", [])):
                    values_in_row = row.get("values", [])
                    if values_in_row and values_in_row[0].get("note"):
                        notes[row_idx + 1] = values_in_row[0]["note"]

        return [
            f"{value}[{notes[row_num]}]" if row_num in notes else value
            for row_num, value in enumerate(plain_values, start=1)
        ]

    @staticmethod
    def get_hyperlink_column(
        service, sheet_ID: str, sheet_name: str, column: str
    ) -> list[str | None]:
        range_name = f"'{sheet_name}'!{column}:{column}"
        sheet_meta = _execute(
            service.spreadsheets()
            .get(
                spreadsheetId=sheet_ID,
                ranges=[range_name],
                fields="sheets/data/rowData/values/hyperlink",
            )
            .execute
        )
        rows = sheet_meta["sheets"][0]["data"][0].get("rowData", [])
        links: list[str | None] = []
        for row in rows:
            if row.get("values"):
                links.append(row["values"][0].get("hyperlink"))
            else:
                links.append(None)
        return links
