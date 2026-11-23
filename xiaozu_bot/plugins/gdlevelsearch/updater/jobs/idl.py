"""Fetch the Insane Demon List (IDL) top-150 snapshot."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from nonebot import logger

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

_REQUIRED_FIELDS = ("name", "position", "publisher")
_PROBE_FIELDS = ("name", "position", "publisher", "creator", "formerRank")

# Oldest known layout: a Pages Router response with one escaped level object
# per record and a fixed field order. Used only as the last fallback, so it
# deliberately does not require a closing brace or fully valid JSON.
_LEGACY_PATTERN = re.compile(
    r'\\"level\\":\{'
    r'[^{}]*?\\"name\\":\\"(?P<name>.*?)\\"'
    r'[^{}]*?\\"position\\":(?P<position>\d+)'
    r'[^{}]*?\\"publisher\\":\\"(?P<creator>.*?)\\"',
    re.DOTALL,
)


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


def _balanced(text: str, start: int, opening: str, closing: str) -> str | None:
    """Return the balanced JSON value beginning at ``text[start]``."""
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
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _probe(text: str, decoded: str) -> dict[str, int]:
    """Count layout markers in raw and once-unescaped RSC text."""
    stats: dict[str, int] = {
        "bytes": len(text),
        "raw_level": len(re.findall(r'\\"level\\":\{', text)),
        "raw_levels": len(re.findall(r'\\"levels\\":\[', text)),
        "decoded_level": len(re.findall(r'"level":\{', decoded)),
        "decoded_levels": len(re.findall(r'"levels":\[', decoded)),
    }
    for field in _PROBE_FIELDS:
        stats[f"field_{field}"] = len(re.findall(f'"{field}"', decoded))
    return stats


def _valid_items(values: list[Any]) -> list[dict[str, Any]] | None:
    """Accept a layout only when every entry carries the required fields."""
    if not values:
        return None
    if not all(
        isinstance(value, dict)
        and all(field in value for field in _REQUIRED_FIELDS)
        for value in values
    ):
        return None
    return values


def _decode_values(
    decoded: str, marker: str, opening: str, closing: str
) -> list[Any]:
    """Decode every balanced JSON value following ``marker``."""
    values: list[Any] = []
    for hit in re.finditer(re.escape(marker), decoded):
        raw = _balanced(decoded, hit.end() - 1, opening, closing)
        if raw is None:
            continue
        try:
            values.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return values


def _parse_level_objects(decoded: str) -> list[dict[str, Any]] | None:
    """Current layout: one ``"level":{...}`` component prop per record."""
    return _valid_items(_decode_values(decoded, '"level":{', "{", "}"))


def _parse_levels_array(decoded: str) -> list[dict[str, Any]] | None:
    """2026-11 layout: a single ``"levels":[...]`` array."""
    for hit in re.finditer(re.escape('"levels":['), decoded):
        raw = _balanced(decoded, hit.end() - 1, "[", "]")
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            items = _valid_items(data)
            if items is not None:
                return items
    return None


def _parse_legacy(text: str) -> list[dict[str, Any]] | None:
    """Oldest layout: escaped objects read with a field-order regex."""
    return _valid_items(
        [
            {
                "name": match.group("name"),
                "position": match.group("position"),
                "publisher": match.group("creator"),
            }
            for match in _LEGACY_PATTERN.finditer(text)
        ]
    )


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


def _dedup(levels: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the first record for each position (RSC can render a prop twice)."""
    by_position: dict[str, dict[str, str]] = {}
    for item in levels:
        by_position.setdefault(item["position"], item)
    return list(by_position.values())


def _sorted(levels: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(levels, key=lambda item: int(item["position"]))


def _range(levels: list[dict[str, str]]) -> str:
    if not levels:
        return "empty"
    positions = [int(item["position"]) for item in levels]
    return f"{min(positions)}..{max(positions)}"


def _is_complete(levels: list[dict[str, str]]) -> bool:
    return len(levels) == EXPECTED_COUNT and [
        int(item["position"]) for item in levels
    ] == list(range(1, EXPECTED_COUNT + 1))


def _parse_levels(text: str) -> tuple[list[dict[str, str]], str, dict[str, int]]:
    """Try every known layout in priority order and return parsed levels."""
    decoded = _undo_rsc_escaping(text)
    stats = _probe(text, decoded)

    # Newest layout first; a layout that matched but lacks fields is rejected
    # instead of silently producing truncated records.
    for name, parser in (
        ("level-objects", _parse_level_objects),
        ("levels-array", _parse_levels_array),
    ):
        items = parser(decoded)
        if items is not None:
            levels = _sorted(_dedup([_normalize(item) for item in items]))
            return levels, name, stats

    items = _parse_legacy(text)
    if items is not None:
        levels = _sorted(_dedup([_normalize(item) for item in items]))
        return levels, "legacy-regex", stats

    raise RuntimeError(
        "IDL 页面里没有任何已知格式能解析出关卡："
        f"原始 level={stats['raw_level']} levels={stats['raw_levels']}，"
        f"去转义后 level={stats['decoded_level']} levels={stats['decoded_levels']}，"
        f"字段计数={{name:{stats['field_name']}, position:{stats['field_position']}, "
        f"publisher:{stats['field_publisher']}, creator:{stats['field_creator']}}}，"
        f"共 {stats['bytes']} 字节。上游页面结构或字段名可能又改了"
    )


def _download(url: str, headers: dict[str, str]) -> str:
    response = http_request("GET", url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.text


def fetch() -> None:
    """Fetch the IDL top 150 and write it to staging.

    The current upstream splits the ranking (1-75 on /main, 76-150 on
    /extended). /extended is requested only when /main is incomplete, which
    also covers the older "all 150 on /main" layout and the period when
    /extended 307-redirected back to /main. Merging is keyed by position, so
    a redirect or a duplicated RSC render cannot inflate the snapshot.
    """
    headers = {"User-Agent": USER_AGENT}

    levels, fmt, stats = _parse_levels(_download(MAIN_URL, headers))
    logger.info(
        f"[IDL] /main format={fmt} entries={len(levels)} "
        f"range={_range(levels)} markers={stats}"
    )

    if not _is_complete(levels):
        extra, extra_fmt, extra_stats = _parse_levels(
            _download(EXTENDED_URL, headers)
        )
        logger.info(
            f"[IDL] /extended format={extra_fmt} entries={len(extra)} "
            f"range={_range(extra)} markers={extra_stats}"
        )
        merged = {item["position"]: item for item in levels}
        for item in extra:
            merged.setdefault(item["position"], item)
        levels = _sorted(list(merged.values()))

    # Upstream layout changes must fail loudly instead of publishing a
    # truncated or duplicated ranking.
    if not _is_complete(levels):
        raise RuntimeError(
            f"解析失败：预期连续的 {EXPECTED_COUNT} 个排名，"
            f"实际获取 {len(levels)} 个条目（{_range(levels)}）。"
        )

    write_json_atomic(
        OUTPUT_PATH,
        {"timestamp": time.time(), "levels": levels},
        ensure_ascii=False,
        indent=4,
    )


if __name__ == "__main__":
    fetch()
