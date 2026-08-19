#!/usr/bin/env python3
"""检查 metadata.json 的重复条目，以及每条被哪些数据源引用。

用法（在项目根目录下运行，不要从其他目录调用）：

    python scripts/find_metadata_duplicates.py
    python scripts/find_metadata_duplicates.py --limit 30
    python scripts/find_metadata_duplicates.py --csv 待清理清单.csv
    python scripts/find_metadata_duplicates.py --prune   # 一键删除全部无引用条目
    python scripts/find_metadata_duplicates.py --path 其它metadata路径
    python scripts/find_metadata_duplicates.py --data-dir 其它数据目录

数据源和 getmetadata.py 一致：nlw_levels.json / ids_levels.json /
lw_levels.json / hds_levels.json / idl.json（.staging 优先，其次 data/）。
引用判定和 enrich_levels_with_ids 一致：数据源关卡里的
(name, creator 归一化) 精确命中 metadata 条目，就算被该数据源引用。

输出里的方括号数字是条目在 metadata.json 数组里的下标（从 0 开始），
方便对照原文件定位。「无引用」条目 = 当前没有任何数据源在用的缓存，
是最优先的人工删除候选；空 name 条目（id 0 占位符等）是预期行为，不列出。

--prune 会删除全部「无引用」条目（不影响空 name 条目），删除前先在
同目录生成 metadata.json.bak 备份；写回格式与原文件一致（indent=4、
非 ASCII 原样、CRLF、原子替换），和 getmetadata 的写盘方式相同。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

DATA_SOURCES: tuple[str, ...] = (
    "nlw_levels.json",
    "ids_levels.json",
    "lw_levels.json",
    "hds_levels.json",
    "idl.json",
)
SOURCE_SHORT: dict[str, str] = {
    "nlw_levels.json": "nlw",
    "ids_levels.json": "ids",
    "lw_levels.json": "lw",
    "hds_levels.json": "hds",
    "idl.json": "idl",
}
DEFAULT_CANDIDATES: tuple[Path, ...] = (
    Path("xiaozu_bot/plugins/gdlevelsearch/data/metadata.json"),
    Path("xiaozu-bot/xiaozu_bot/plugins/gdlevelsearch/data/metadata.json"),
)
DEFAULT_DATA_DIRS: tuple[Path, ...] = (
    Path("xiaozu_bot/plugins/gdlevelsearch/data"),
    Path("xiaozu-bot/xiaozu_bot/plugins/gdlevelsearch/data"),
)

Entry = dict[str, object]
Group = list[tuple[int, Entry]]


def resolve_path(explicit: str | None) -> Path:
    """确定要检查的 metadata.json 路径。"""
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            sys.exit(f"找不到文件: {path}")
        return path
    for candidate in DEFAULT_CANDIDATES:
        if candidate.is_file():
            return candidate
    sys.exit(
        "在当前目录下找不到 metadata.json，请到项目根目录运行，"
        "或用 --path 指定文件路径"
    )


def resolve_data_dir(explicit: str | None) -> Path:
    """确定数据源目录。"""
    if explicit is not None:
        path = Path(explicit)
        if not path.is_dir():
            sys.exit(f"找不到数据目录: {path}")
        return path
    for candidate in DEFAULT_DATA_DIRS:
        if candidate.is_dir():
            return candidate
    sys.exit(
        "在当前目录下找不到数据目录，请到项目根目录运行，"
        "或用 --data-dir 指定数据目录"
    )


def load_entries(path: Path) -> list[Entry]:
    """读取 JSON 数组，返回原始条目列表。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"读取 {path} 失败: {exc}")
    if not isinstance(raw, list):
        sys.exit(f"{path} 不是 JSON 数组")
    return raw


def is_empty_name(entry: Entry) -> bool:
    """name.strip() 为空视为预期行为（id 0 占位符等），不参与分析。"""
    return not str(entry.get("name", "")).strip()


def name_key(entry: Entry) -> str:
    """按 name.strip().lower() 归一化名称。"""
    return str(entry.get("name", "")).strip().lower()


def build_groups(items: list[tuple[int, Entry]]) -> dict[str, Group]:
    """把条目按下标归组，同组内按数组下标升序。"""
    groups: dict[str, Group] = defaultdict(list)
    for index, entry in items:
        groups[name_key(entry)].append((index, entry))
    return groups


def split_groups(
    duplicates: dict[str, Group],
) -> tuple[list[tuple[str, Group]], list[tuple[str, Group]]]:
    """拆成同 id 组和不同 id 组。"""
    same_id: list[tuple[str, Group]] = []
    diff_id: list[tuple[str, Group]] = []
    for key, items in duplicates.items():
        ids = {entry.get("id") for _, entry in items}
        if len(ids) == 1:
            same_id.append((key, items))
        else:
            diff_id.append((key, items))
    same_id.sort(key=lambda pair: pair[0])
    diff_id.sort(key=lambda pair: (-len(pair[1]), pair[0]))
    return same_id, diff_id


def normalize_creator_name(name: str) -> str:
    """与 metadata.py 保持一致：& 换成 and，再 strip + lower。"""
    return name.replace("&", "and").strip().lower()


def load_source(data_dir: Path, name: str) -> list[Entry] | None:
    """读一个数据源：.staging 优先，其次 data/；返回 levels 列表或 None。"""
    path = data_dir / ".staging" / name
    if not path.is_file():
        path = data_dir / name
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  跳过 {name}（读取失败: {exc}）")
        return None
    if isinstance(raw, dict):
        levels = raw.get("levels")
        return levels if isinstance(levels, list) else None
    return raw if isinstance(raw, list) else None


def source_level_keys(levels: list[Entry]) -> set[tuple[str, str]]:
    """数据源关卡键，和 enrich_levels_with_ids 的查找键一致。"""
    keys: set[tuple[str, str]] = set()
    for level in levels:
        name = str(level.get("name", ""))
        creator = normalize_creator_name(str(level.get("creator", "")))
        keys.add((name, creator))
    return keys


def load_all_sources(data_dir: Path) -> dict[str, set[tuple[str, str]]]:
    """按 getmetadata.py 的顺序加载数据源，返回文件名 -> 关卡键集合。"""
    loaded: dict[str, set[tuple[str, str]]] = {}
    for name in DATA_SOURCES:
        levels = load_source(data_dir, name)
        if levels is None:
            print(f"  数据源缺失，跳过: {name}")
            continue
        loaded[name] = source_level_keys(levels)
        print(f"  已加载数据源: {name}（{len(levels)} 条）")
    if not loaded:
        sys.exit(f"在 {data_dir} 下没有读到任何数据源，无法做引用分析")
    return loaded


def compute_refs(
    entries: list[Entry],
    source_keys: dict[str, set[tuple[str, str]]],
) -> dict[int, list[str]]:
    """每个 metadata 下标 -> 引用它的数据源简称列表（按数据源顺序）。"""
    refs: dict[int, list[str]] = {}
    for index, entry in enumerate(entries):
        key = (str(entry.get("name", "")), str(entry.get("creator", "")))
        used = [SOURCE_SHORT[name] for name in DATA_SOURCES if key in source_keys[name]]
        if used:
            refs[index] = used
    return refs


def format_refs(refs: dict[int, list[str]], index: int) -> str:
    """条目的引用标注文本。"""
    used = refs.get(index)
    return " ".join(used) if used else "无引用"


def print_entries(items: Group, refs: dict[int, list[str]]) -> None:
    """逐条打印：数组下标、id、name、creator、引用数据源（无引用在前）。"""
    for index, entry in sorted(
        items,
        key=lambda pair: (pair[0] not in refs, int(pair[1].get("id") or 0)),
    ):
        name = str(entry.get("name", "")).replace("\n", " ")
        creator = str(entry.get("creator", "")).replace("\n", " ")
        print(
            f"  [{index}]  id={entry.get('id')}  \"{name}\"  (by {creator})"
            f"  引用: {format_refs(refs, index)}"
        )


def write_csv(
    path: Path,
    duplicates: dict[str, Group],
    refs: dict[int, list[str]],
) -> None:
    """把全部同名条目导出为 CSV，引用数据源列和 note 列留给人工判断。"""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["组名", "条数", "是否同id", "数组下标", "id", "name", "creator", "引用数据源", "note"]
        )
        for key in sorted(duplicates):
            items = duplicates[key]
            ids = {entry.get("id") for _, entry in items}
            for index, entry in items:
                writer.writerow(
                    [
                        key,
                        len(items),
                        "是" if len(ids) == 1 else "否",
                        index,
                        entry.get("id"),
                        entry.get("name"),
                        entry.get("creator"),
                        format_refs(refs, index),
                        "",
                    ]
                )


def write_json_like(path: Path, data: list[Entry]) -> None:
    """按原文件风格写回：indent=4、非 ASCII 原样、CRLF、原子替换。"""
    temporary = path.with_name(f".{path.name}.prune.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\r\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=4)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as exc:
            print(f"  清理临时文件失败: {temporary}: {exc}", file=sys.stderr)


def prune_unreferenced(
    path: Path,
    all_entries: list[Entry],
    remove_indices: set[int],
) -> int:
    """先备份成 .bak，再把 remove_indices 对应的条目删掉，返回剩余条数。"""
    backup = Path(str(path) + ".bak")
    shutil.copy2(path, backup)
    kept = [
        entry for index, entry in enumerate(all_entries) if index not in remove_indices
    ]
    write_json_like(path, kept)
    return len(kept)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", help="metadata.json 路径（默认自动查找）")
    parser.add_argument("--data-dir", help="数据源目录（默认自动查找）")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="无引用条目最多列前 N 条（0=全部，默认 0）",
    )
    parser.add_argument("--csv", metavar="FILE", help="导出全部同名条目到 CSV，方便手动标注")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="一键删除全部无引用条目（删除前自动备份 metadata.json.bak）",
    )
    args = parser.parse_args()

    path = resolve_path(args.path)
    data_dir = resolve_data_dir(args.data_dir)
    all_entries = load_entries(path)
    print(f"文件: {path}")
    print(f"数据目录: {data_dir}")
    source_keys = load_all_sources(data_dir)
    refs = compute_refs(all_entries, source_keys)

    pairs = [
        (index, entry)
        for index, entry in enumerate(all_entries)
        if not is_empty_name(entry)
    ]
    groups = build_groups(pairs)
    duplicates = {key: items for key, items in groups.items() if len(items) > 1}
    same_id, diff_id = split_groups(duplicates)

    unreferenced = [pair for pair in pairs if pair[0] not in refs]
    unreferenced.sort(
        key=lambda pair: (
            str(pair[1].get("name", "")).strip().lower(),
            int(pair[1].get("id") or 0),
        )
    )
    in_dup = sum(
        1
        for index, _ in unreferenced
        if index
        in {item_index for items in duplicates.values() for item_index, _ in items}
    )

    print(f"共 {len(all_entries)} 条（空 name {len(all_entries) - len(pairs)} 条为预期行为，已忽略）")
    print(f"被数据源引用: {len(refs)} 条；无引用: {len(unreferenced)} 条（其中 {in_dup} 条在同名组里）")
    per_source = {
        short: sum(1 for used in refs.values() if short in used)
        for short in SOURCE_SHORT.values()
    }
    source_line = "各数据源引用条数: " + ", ".join(
        f"{short}={n}" for short, n in per_source.items()
    )
    print(source_line)
    multi = sum(1 for used in refs.values() if len(used) > 1)
    print(f"同时被多个数据源引用: {multi} 条")
    print()

    shown = len(unreferenced) if args.limit <= 0 else min(args.limit, len(unreferenced))
    print(f"== 无任何数据源引用的条目（最优先删除候选）: {len(unreferenced)} 条 ==")
    for index, entry in unreferenced[:shown]:
        key = name_key(entry)
        marker = f"  [同名{len(groups[key])}组]" if len(groups[key]) > 1 else ""
        name = str(entry.get("name", "")).replace("\n", " ")
        creator = str(entry.get("creator", "")).replace("\n", " ")
        print(f"  [{index}]  id={entry.get('id')}  \"{name}\"  (by {creator}){marker}")
    if shown < len(unreferenced):
        print(f"  ... 其余 {len(unreferenced) - shown} 条未列出（--limit）")
    print()

    print(f"== 同名同 id（完全重复，删任意一条即可）: {len(same_id)} 组 ==")
    for _, items in same_id:
        print_entries(items, refs)
    print()

    print(
        "== 同名不同 id（可能是撞名的不同关卡，"
        f"也可能是死项目/旧版本，需人工甄别）: {len(diff_id)} 组 =="
    )
    for key, items in diff_id:
        print(f"「{key}」{len(items)} 条:")
        print_entries(items, refs)
    print()

    if args.csv:
        write_csv(Path(args.csv), duplicates, refs)
        print(f"已导出: {args.csv}")

    if args.prune:
        remove_indices = {index for index, _ in unreferenced}
        print(f"== 将删除 {len(remove_indices)} 条无引用条目（删除前自动备份） ==")
        for index, entry in unreferenced:
            name = str(entry.get("name", "")).replace("\n", " ")
            creator = str(entry.get("creator", "")).replace("\n", " ")
            print(f"  [{index}]  id={entry.get('id')}  \"{name}\"  (by {creator})")
        kept = prune_unreferenced(path, all_entries, remove_indices)
        print()
        print(f"已备份: {path}.bak")
        print(f"已删除 {len(remove_indices)} 条，剩余 {kept} 条")


if __name__ == "__main__":
    main()
