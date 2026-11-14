"""Fetch the Insane Demon List (IDL) top-150 snapshot."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from xiaozu_bot.utils.json_storage import write_json_atomic

from ...api.http import request as http_request
from ...constants import USER_AGENT

try:
    from ..paths import staged
except ImportError:
    from updater.paths import staged


MAIN_URL = "https://insanedemonlist.com/main"
EXTENDED_URL = "https://insanedemonlist.com/extended"

EXPECTED_COUNT = 150
OUTPUT_PATH = staged("idl.json")

# The App Router page ships its data as one RSC flight chunk. The chunk is a
# JS string, so the JSON inside it is escaped a second time (quotes as \\").
_LEVELS_KEY = '"levels":['
_REQUIRED_FIELDS = ("name", "position", "publisher")


def _undo_rsc_escaping(text: str) -> str:
    r"""Remove one JS-string escaping layer from Next.js RSC flight data.

    Other escapes (\\n, \\uXXXX, ...) are kept verbatim: json.loads still
    understands them, while decoding them here would put bare control
    characters inside JSON strings.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            following = text[index + 1]
            out.append(
                following if following in ('"', "\\", "/") else char + following
            )
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _balanced_array(text: str, start: int) -> str | None:
    """Return the JSON array beginning at ``text[start] == '['``."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _raw_levels(text: str) -> list[dict[str, Any]]:
    """Locate and decode the RSC levels array."""
    decoded = _undo_rsc_escaping(text)
    for marker in re.finditer(re.escape(_LEVELS_KEY), decoded):
        raw = _balanced_array(decoded, marker.end() - 1)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list) and data and all(
            isinstance(item, dict)
            and all(field in item for field in _REQUIRED_FIELDS)
            for item in data
        ):
            return data
    raise RuntimeError("IDL 页面里找不到 levels 数组，上游页面结构可能又改了")


def _normalize(item: dict[str, Any]) -> dict[str, str]:
    """Keep the output shape consumed by listsapi / getmetadata."""
    try:
        position = int(item["position"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("IDL 条目的 position 不是整数") from exc
    return {
        "position": str(position),
        "name": str(item["name"]).strip(" ⠀"),
        "creator": str(item["publisher"]).strip().lower(),
    }


def _parse_levels(text: str) -> list[dict[str, str]]:
    return [_normalize(item) for item in _raw_levels(text)]


def _download(url: str, headers: dict[str, str]) -> str:
    response = http_request("GET", url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.text


def _is_complete(levels: list[dict[str, str]]) -> bool:
    return len(levels) == EXPECTED_COUNT and [int(item["position"]) for item in levels] == list(
        range(1, EXPECTED_COUNT + 1)
    )


def _sorted(levels: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(levels, key=lambda item: int(item["position"]))


def fetch() -> None:
    """Fetch the IDL top 150 and write it to staging.

    /main now carries the whole list; /extended 307-redirects back to /main.
    The second request is kept only as a fallback for the older split layout
    (1-75 on /main, 76-150 on /extended), and its entries are deduped by
    position so a redirect cannot double the snapshot.
    """
    headers = {"User-Agent": USER_AGENT}

    levels = _sorted(_parse_levels(_download(MAIN_URL, headers)))

    if not _is_complete(levels):
        extra = _parse_levels(_download(EXTENDED_URL, headers))
        merged = {item["position"]: item for item in levels}
        for item in extra:
            merged.setdefault(item["position"], item)
        levels = _sorted(list(merged.values()))

    # Upstream layout changes must fail loudly instead of publishing a
    # truncated or duplicated ranking.
    if not _is_complete(levels):
        raise RuntimeError(
            f"解析失败：预期连续的 {EXPECTED_COUNT} 个排名，实际获取 {len(levels)} 个条目。"
        )

    write_json_atomic(
        OUTPUT_PATH,
        {"timestamp": time.time(), "levels": levels},
        ensure_ascii=False,
        indent=4,
    )


if __name__ == "__main__":
    fetch()
