#!/usr/bin/env python3
"""Check list entries against the difficulty recorded in the local GDDL cache.

Run this file from the project root::

    python scripts/check_gddl_difficulties.py

The checker only reads JSON files.  It does not update, repair, or otherwise
modify any cache.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIRS = (
    Path("xiaozu_bot/plugins/gdlevelsearch/data"),
    Path("xiaozu-bot/xiaozu_bot/plugins/gdlevelsearch/data"),
)
DATA_FILES = (
    "gddl_levels.json",
    "lw_levels.json",
    "nlw_levels.json",
    "ids_levels.json",
    "idl.json",
    "hds_levels.json",
)


@dataclass(frozen=True)
class Rule:
    filename: str
    expected: str
    excluded: list[str]|None = None


RULES = (
    Rule("lw_levels.json", "Extreme"),
    Rule("nlw_levels.json", "Extreme"),
    Rule("ids_levels.json", "Insane", excluded=["legacy"]),
    Rule("idl.json", "Insane"),
    Rule("hds_levels.json", "Hard", excluded=["legacy","remorseless"]),
)


@dataclass(frozen=True)
class Mismatch:
    filename: str
    level_id: str
    name: str
    creator: str
    expected: str
    actual: str


def resolve_data_dir(explicit: str | None) -> Path:
    """Find the data directory when called from either repository root."""
    if explicit is not None:
        path = Path(explicit)
        if not path.is_dir():
            raise FileNotFoundError(f"data directory does not exist: {path}")
        return path

    script_dir = Path(__file__).resolve().parent
    project_dir = script_dir.parent
    candidates = (
        project_dir / "xiaozu_bot/plugins/gdlevelsearch/data",
        *(script_dir / path for path in DEFAULT_DATA_DIRS),
        *DEFAULT_DATA_DIRS,
    )
    for path in candidates:
        if path.is_dir():
            return path
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        f"could not find the gdlevelsearch data directory ({searched})"
    )


def load_levels(path: Path) -> list[dict[str, Any]]:
    """Load either the usual {"levels": [...]} cache or a bare list."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to read {path}: {exc}") from exc

    levels = payload.get("levels") if isinstance(payload, dict) else payload
    if not isinstance(levels, list):
        raise TypeError(f"{path} does not contain a levels list")
    return [level for level in levels if isinstance(level, dict)]


def as_level_id(value: Any) -> int | None:
    """Match the integer ID coercion used by the local cache indexes."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_gddl_difficulties(levels: list[dict[str, Any]]) -> dict[int, str | None]:
    """Build the same ID -> record lookup used by gddl_store.by_id."""
    difficulties: dict[int, str | None] = {}
    for level in levels:
        level_id = as_level_id(level.get("ID"))
        if level_id is None:
            continue
        meta = level.get("Meta")
        difficulty = meta.get("Difficulty") if isinstance(meta, dict) else None
        difficulties[level_id] = difficulty if isinstance(difficulty, str) else None
    return difficulties


def normalized(value: Any) -> str:
    return str(value or "").strip().casefold()


def check_rule(
    rule: Rule,
    levels: list[dict[str, Any]],
    gddl_difficulties: dict[int, str | None],
) -> list[Mismatch]:
    mismatches: list[Mismatch] = []
    for level in levels:
        if rule.excluded and normalized(level.get("tier")) in rule.excluded:
            continue

        level_id = as_level_id(level.get("id"))
        if level_id == 0:
            continue
        actual = gddl_difficulties.get(level_id) if level_id is not None else None
        if normalized(actual) == normalized(rule.expected):
            continue

        mismatches.append(
            Mismatch(
                filename=rule.filename,
                level_id=str(level.get("id", "<missing>")),
                name=str(level.get("name", "<missing name>")),
                creator=str(level.get("creator", "<missing creator>")),
                expected=rule.expected,
                actual=actual or "<not found>",
            )
        )
    return mismatches


def check_data(data_dir: Path) -> list[Mismatch]:
    gddl = build_gddl_difficulties(load_levels(data_dir / "gddl_levels.json"))
    mismatches: list[Mismatch] = []
    for rule in RULES:
        mismatches.extend(check_rule(rule, load_levels(data_dir / rule.filename), gddl))
    return mismatches


def print_report(mismatches: list[Mismatch]) -> None:
    if not mismatches:
        print("No difficulty mismatches found.")
        return

    print(f"Found {len(mismatches)} difficulty mismatch(es):")
    for item in mismatches:
        print(
            f"- {item.filename}: {item.name} by {item.creator} "
            f"(id={item.level_id}, expected={item.expected}, actual={item.actual})"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report list entries whose GDDL difficulty does not match."
    )
    parser.add_argument(
        "--data-dir",
        help="directory containing gddl_levels.json and the list JSON files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        data_dir = resolve_data_dir(args.data_dir)
        mismatches = check_data(data_dir)
    except (FileNotFoundError, RuntimeError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Data directory: {data_dir}")
    print_report(mismatches)
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
