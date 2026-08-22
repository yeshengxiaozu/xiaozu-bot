# updater/paths.py

import os
from pathlib import Path

from nonebot import logger

# Directory containing the gdlevelsearch plugin.
PLUGIN_DIR = Path(__file__).resolve().parent.parent

# The bot reads only complete, published snapshots from this directory.
DATA_DIR = PLUGIN_DIR / "data"

# Write the current run here first, then publish only complete files. This
# prevents a failed metadata stage from replacing the live snapshot.
STAGING_DIR = DATA_DIR / ".staging"

# Files consumed by the bot and therefore eligible for publication.
PUBLISHED_FILES = (
    "gddl_levels.json",
    "nlw_levels.json",
    "ids_levels.json",
    "lw_levels.json",
    "hds_levels.json",
    "idl.json",
    "hdl.json",
    "mdl.json",
    "edl.json",
    "plat_combined.json",
    "nong_index.json",
)

# Intermediate inputs for platbatch; they are intentionally not published.
INTERMEDIATE_FILES = (
    "tpl.json",
    "pemonlist.json",
    "platdiff.json",
)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)


def staged(name: str) -> Path:
    """Return the staging path for a job output."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    return STAGING_DIR / name


def staged_or_published(name: str) -> Path:
    """Prefer this run's output and fall back to the last published snapshot.

    The fallback lets an isolated job rerun while downstream jobs still see
    the previous snapshot for every source that was not refreshed.
    """
    candidate = STAGING_DIR / name
    return candidate if candidate.exists() else DATA_DIR / name


#: Reject a replacement when its record count falls below this ratio.
#: The threshold is intentionally loose and catches only near-empty snapshots.
MIN_KEEP_RATIO = 0.5


def _entry_count(path: Path) -> "int | None":
    """Count records in a snapshot, returning ``None`` when it is unreadable."""
    import json

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        levels = data.get("levels")
        return len(levels) if isinstance(levels, list) else None
    return len(data) if isinstance(data, list) else None


def publish() -> list[str]:
    """Atomically move valid staged outputs into the live data directory.

    Each file is replaced atomically and the returned list contains the files
    that were actually published.

    Atomicity applies per file, not to the whole set. A process exit between
    replacements can leave a mixed snapshot; rerunning the updater converges
    it to a consistent state.

    Before replacing an existing file, compare record counts and reject a
    near-empty result. Upstream format changes can otherwise produce a
    successful job with a truncated file and destroy the only live snapshot.
    """
    moved: list[str] = []
    for name in PUBLISHED_FILES:
        src = STAGING_DIR / name
        if not src.exists():
            continue
        dst = DATA_DIR / name

        new_n = _entry_count(src)
        old_n = _entry_count(dst) if dst.exists() else None
        if new_n is not None and old_n and new_n < old_n * MIN_KEEP_RATIO:
            logger.error(
                f"[UPDATER] 拒绝发布 {name}：新数据只有 {new_n} 条，"
                f"旧数据有 {old_n} 条（不到 {MIN_KEEP_RATIO:.0%}）。"
                f"多半是上游表格改了格式导致解析失败，"
                f"已保留旧数据，新文件留在 staging 里可以自己看一眼。"
            )
            continue

        os.replace(src, dst)
        moved.append(name)
    if moved:
        logger.info(f"[UPDATER] 已发布 {len(moved)} 个文件: {', '.join(moved)}")
    else:
        logger.warning("[UPDATER] staging 里没有可发布的文件")
    return moved


def clear_staging() -> None:
    """Remove staged files before a new update run."""
    if not STAGING_DIR.exists():
        return
    for path in STAGING_DIR.iterdir():
        if path.is_file():
            path.unlink()
