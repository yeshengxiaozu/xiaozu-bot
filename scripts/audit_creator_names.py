#!/usr/bin/env python3
"""把榜单 sheet 里的作者名和 GDDL 的发布者名对一遍，列出对不上的。

Run this file from the project root::

    python scripts/audit_creator_names.py
    python scripts/audit_creator_names.py --md temp/creator_name_audit.md
    python scripts/audit_creator_names.py --json temp/creator_name_audit.json

判定口径：

1. 先按 `updater/jobs/constants.py` 里的 FRUITY_CREATORS_* 把表里的作者名映射一遍，
   所以报告反映的是「映射之后还剩什么」，不是原始表。
2. 作者串按 `,` `&` `+` `and` 拆成多个名字，只要任意一个和 GDDL 的 publisherName
   一致就算对上——GDDL 只记上传账号，`X & Y` 对 `X` 不算错。
3. 末尾的 `& more` / `and more` / `+ more` 比较时忽略。
4. 剩下的分三类：
   - typo    同一关卡 ID 在 GDDL 里有极相近的写法
   - renamed GDDL 里这关的发布者是完全不同的名字（通常是上传账号，不是拼写错误）
   - unknown 这关不在 GDDL 快照里，无法核对

只有 typo 这一类值得补进 constants.py；renamed 改了会把作者名改错。

脚本只读 JSON，不写任何缓存。
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

DEFAULT_DATA_DIRS = (
    Path("xiaozu_bot/plugins/gdlevelsearch/data"),
    Path("xiaozu-bot/xiaozu_bot/plugins/gdlevelsearch/data"),
)
CONSTANTS = Path("xiaozu_bot/plugins/gdlevelsearch/updater/jobs/constants.py")

# sheet 名 -> (快照文件名, constants 里的映射表名)
SHEETS = (
    ("NLW", "nlw_levels.json", "FRUITY_CREATORS_NLW"),
    ("IDS", "ids_levels.json", "FRUITY_CREATORS_IDS"),
    ("HDS", "hds_levels.json", "FRUITY_CREATORS_HDS"),
    ("LW", "lw_levels.json", None),
)

MORE_SUFFIX = re.compile(r"(?:,?\s*(?:and|&|\+)\s*more)\s*$", re.IGNORECASE)
SPLIT = re.compile(r"\s*(?:,|&|\+|\band\b)\s*", re.IGNORECASE)

# 已知的、和 GDDL 不一致但确实正确的写法，跳过不报。三条都用 Boomlings 按关卡 ID
# 核对过：sheet（以及 Boomlings）对，GDDL 自己写错了。
#   blueskii  GDDL 多写一个 i
#   at424     GDDL 写成 At242
#   nikver    GDDL 写成 nikverr，Boomlings 是 nikver19
KNOWN_OK = {"blueskii", "at424", "nikver"}


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = MORE_SUFFIX.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def norm(text: str) -> str:
    return clean(text).casefold()


def components(text: str) -> list[str]:
    return [part for part in (norm(p) for p in SPLIT.split(clean(text))) if part]


def resolve_data_dir(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_dir():
            raise SystemExit(f"数据目录不存在：{path}")
        return path
    for candidate in DEFAULT_DATA_DIRS:
        if candidate.is_dir():
            return candidate
    raise SystemExit("找不到数据目录，用 --data-dir 指定")


def load_constants(path: Path) -> dict[str, dict[str, str]]:
    """直接按文件路径加载 constants.py，避开需要 nonebot 的包 __init__。"""
    if not path.is_file():
        return {}
    spec = importlib.util.spec_from_file_location("_gd_constants", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {
        name: getattr(module, name, {})
        for _, _, name in SHEETS
        if name
    }


def load_gddl(data_dir: Path):
    payload = json.loads((data_dir / "gddl_levels.json").read_text(encoding="utf-8"))
    by_id: dict[str, str] = {}
    names: Counter[str] = Counter()
    display: dict[str, str] = {}
    for row in payload["levels"]:
        publisher = (row.get("publisherName") or "").strip()
        if not publisher:
            continue
        key = norm(publisher)
        names[key] += 1
        display.setdefault(key, publisher)
        level_id = str(row.get("id") or "").strip()
        if level_id:
            by_id[level_id] = publisher
    return by_id, names, display


def load_levels(data_dir: Path, filename: str) -> list[dict]:
    payload = json.loads((data_dir / filename).read_text(encoding="utf-8"))
    return payload.get("levels", payload if isinstance(payload, list) else [])


def suggest(key: str, pool: list[str], display: dict[str, str]) -> str | None:
    hits = difflib.get_close_matches(key, pool, n=1, cutoff=0.82)
    if hits:
        return display.get(hits[0], hits[0])
    stripped = key.lstrip("i")
    if stripped != key and stripped in display:
        return display[stripped]
    for candidate in pool:
        if len(candidate) >= 5 and (key.startswith(candidate) or candidate.startswith(key)):
            return display.get(candidate, candidate)
    return None


def collect(data_dir: Path):
    gddl_by_id, gddl_names, gddl_display = load_gddl(data_dir)
    constants = load_constants(CONSTANTS)
    pool = list(gddl_names)

    findings = []
    sheet_names: dict[str, Counter] = {name: Counter() for name, _, _ in SHEETS}
    for sheet, filename, const_name in SHEETS:
        mapping = constants.get(const_name, {}) if const_name else {}
        for level in load_levels(data_dir, filename):
            raw = level.get("creator") or ""
            if not raw.strip():
                continue
            # 映射表的键必须和表里的原串完全一致（有些写法带前导空格），所以先查再 strip。
            creator = mapping.get(raw, raw).strip()
            if norm(creator) not in KNOWN_OK:
                sheet_names[sheet][norm(creator)] += 1
            if any(part in gddl_names for part in components(creator)):
                continue
            if norm(creator) in KNOWN_OK:
                continue
            level_id = str(level.get("id") or "").strip()
            publisher = gddl_by_id.get(level_id)

            if publisher is None:
                kind = "unknown"
                guess = suggest(norm(creator), pool, gddl_display)
            else:
                guess = publisher
                ratio = difflib.SequenceMatcher(None, norm(creator), norm(guess)).ratio()
                kind = "typo" if ratio >= 0.75 else "renamed"
            findings.append(
                {
                    "sheet": sheet,
                    "kind": kind,
                    "level": level.get("name"),
                    "id": level_id,
                    "creator": creator,
                    "guess": guess,
                }
            )
    return findings, sheet_names


def aggregate(findings: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for item in findings:
        row = grouped.setdefault(
            (item["sheet"], item["creator"]),
            {"sheet": item["sheet"], "creator": item["creator"], "count": 0,
             "levels": [], "guesses": Counter(), "kind": item["kind"]},
        )
        row["count"] += 1
        if len(row["levels"]) < 3:
            row["levels"].append(item["level"])
        row["guesses"][item["guess"]] += 1
    return [
        {**row, "guess": row["guesses"].most_common(1)[0][0]}
        for row in grouped.values()
    ]


def render_md(rows: list[dict], gddl_total: int) -> str:
    order = {name: index for index, (name, _, _) in enumerate(SHEETS)}
    rows = sorted(rows, key=lambda r: (order[r["sheet"]], -r["count"], r["creator"].casefold()))
    lines: list[str] = []
    add = lines.append

    add("# Sheet 作者名 vs GDDL 发布者名")
    add("")
    add(f"对照 {gddl_total} 条 GDDL 快照的 `publisherName`，"
        "作者名已先按 `constants.py` 的 `FRUITY_CREATORS_*` 映射过。")
    add("")
    add("| Sheet | 差异写法 | 涉及关卡 | 疑似拼写 | 换了名字 | 查不到 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for sheet, _, _ in SHEETS:
        block = [r for r in rows if r["sheet"] == sheet]
        kinds = Counter(r["kind"] for r in block)
        add(f"| {sheet} | {len(block)} | {sum(r['count'] for r in block)} | "
            f"{kinds['typo']} | {kinds['renamed']} | {kinds['unknown']} |")
    add(f"| **合计** | **{len(rows)}** | **{sum(r['count'] for r in rows)}** | "
        f"**{sum(1 for r in rows if r['kind'] == 'typo')}** | "
        f"**{sum(1 for r in rows if r['kind'] == 'renamed')}** | "
        f"**{sum(1 for r in rows if r['kind'] == 'unknown')}** |")
    add("")

    add("## 疑似拼写错误（值得补进 constants.py）")
    add("")
    add("「同表已有」= 这个写法在同一份 sheet 里已经出现过，基本可以断定是笔误。")
    add("")
    add("| Sheet | 表里的写法 | 次数 | GDDL 写法 | 同表已有 | 建议映射 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for row in rows:
        if row["kind"] != "typo":
            continue
        mapping = f"`{row['creator']!r}: {row['guess']!r}`" if row["guess"] else ""
        add(f"| {row['sheet']} | {row['creator']} | {row['count']} | "
            f"{row['guess'] or '—'} | {'是' if row['same_sheet'] else '—'} | {mapping} |")
    add("")

    add("## 名字不同（GDDL 记的是上传账号，一般不要改）")
    add("")
    add("| Sheet | 表里的写法 | 次数 | GDDL 发布者 |")
    add("| --- | --- | --- | --- |")
    for row in rows:
        if row["kind"] == "renamed":
            add(f"| {row['sheet']} | {row['creator']} | {row['count']} | {row['guess'] or '—'} |")
    add("")

    add("## GDDL 快照里查不到（无法核对）")
    add("")
    add("| Sheet | 表里的写法 | 次数 | 可能的写法 | 示例关卡 |")
    add("| --- | --- | --- | --- | --- |")
    for row in rows:
        if row["kind"] == "unknown":
            levels = " / ".join(str(x) for x in row["levels"][:2])
            add(f"| {row['sheet']} | {row['creator']} | {row['count']} | "
                f"{row['guess'] or '—'} | {levels} |")
    add("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", help="数据目录，默认用 gdlevelsearch/data")
    parser.add_argument("--md", help="把完整报告写到这个路径")
    parser.add_argument("--json", help="把原始明细写到这个路径")
    args = parser.parse_args()

    data_dir = resolve_data_dir(args.data_dir)
    findings, sheet_names = collect(data_dir)
    rows = aggregate(findings)

    for row in rows:
        row["same_sheet"] = bool(sheet_names[row["sheet"]].get(norm(row["guess"] or ""), 0))

    gddl_total = json.loads(
        (data_dir / "gddl_levels.json").read_text(encoding="utf-8")
    ).get("total", 0)

    if args.json:
        Path(args.json).write_text(
            json.dumps(findings, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(render_md(rows, gddl_total), encoding="utf-8")
        print(f"报告已写入 {args.md}")

    print(f"合计 {len(rows)} 个差异写法 / {sum(r['count'] for r in rows)} 关")
    for sheet, _, _ in SHEETS:
        block = [r for r in rows if r["sheet"] == sheet]
        kinds = Counter(r["kind"] for r in block)
        print(f"  {sheet}: {len(block)} 写法, typo={kinds['typo']} "
              f"renamed={kinds['renamed']} unknown={kinds['unknown']}")


if __name__ == "__main__":
    main()
