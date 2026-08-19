"""IDL / HDL / MDL / EDL 榜单 id 的内存缓存与查询。

updater 每次跑完会把四个榜单的 JSON 写进 data/，这里负责解析成
``{榜单名: [id, ...]}`` 后常驻内存；``draw`` 时不再重复读文件。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from nonebot import logger

from ..paths import DATA_DIR

DATA_PATHS = {
    "IDL": DATA_DIR / "idl.json",
    "HDL": DATA_DIR / "hdl.json",
    "MDL": DATA_DIR / "mdl.json",
    "EDL": DATA_DIR / "edl.json",
}

#: 榜单名 -> 按排名排列的 level id 列表（列表索引 + 1 即排名）。
#: 首次查询时惰性加载，之后常驻内存；updater 跑完后由 :func:`reload` 刷新。
_cache: dict[str, list[int]] | None = None
_cache_lock = threading.Lock()


def _load_level_ids(name: str, path: str | Path) -> list[int]:
    """解析单个榜单 JSON，返回按排名排列的 level id 列表。

    - 兼容 IDL 的 ``{"timestamp": ..., "levels": [...]}`` 包装结构，也兼容纯列表；
    - 用 ``utf-8-sig`` 读取，容忍带 BOM 的文件；
    - 单个条目缺少有效 ``id`` 时跳过该条目，不让整张榜单崩掉。
    """
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"读取 {name} 失败: {exc}") from exc

    levels = data.get("levels") if isinstance(data, dict) else data
    if not isinstance(levels, list):
        raise TypeError(f"{name} 结构异常：找不到 levels 列表")

    ids: list[int] = []
    skipped = 0
    for level in levels:
        if not isinstance(level, dict):
            skipped += 1
            continue
        try:
            ids.append(int(level["id"]))
        except (KeyError, TypeError, ValueError):
            skipped += 1

    if skipped:
        logger.warning(f"[lists] {name} 有 {skipped} 条记录缺少有效 id，已跳过")

    return ids


def _load_all() -> dict[str, list[int]]:
    """从磁盘加载四个榜单；单个文件失败只影响它自己，不影响其余榜单。"""
    loaded: dict[str, list[int]] = {}
    for name, path in DATA_PATHS.items():
        try:
            loaded[name] = _load_level_ids(name, path)
        except Exception as exc:
            logger.warning(f"[lists] 加载 {name} 失败，该榜单暂时为空: {exc}")
            loaded[name] = []
    return loaded


def reload() -> None:
    """重新从磁盘加载四个榜单；单个文件失败时保留该榜单的旧缓存。"""
    global _cache
    with _cache_lock:
        loaded: dict[str, list[int]] = {}
        for name, path in DATA_PATHS.items():
            try:
                loaded[name] = _load_level_ids(name, path)
            except Exception as exc:
                logger.warning(f"[lists] 重新加载 {name} 失败，保留旧数据: {exc}")
                loaded[name] = _cache.get(name, []) if _cache else []
        _cache = loaded


def _get_lists() -> dict[str, list[int]]:
    """惰性加载并返回缓存；首次调用后解析结果常驻内存。"""
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _load_all()
        return _cache


class Lists:
    @classmethod
    def search_level(cls, id_value: int | str) -> str | None:
        """在 IDL/HDL/MDL/EDL 中按 level id 搜排名。

        匹配成功返回 ``"IDL #20"`` 这样的字符串，未找到或 id 非法返回 None。
        """
        try:
            target = int(id_value)
        except (TypeError, ValueError):
            return None

        if target == 0:
            return None

        for name, level_list in _get_lists().items():
            try:
                idx = level_list.index(target)
            except ValueError:
                continue
            return f"{name} #{idx + 1}"

        return None
