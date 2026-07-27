#!/usr/bin/env python
"""跑一遍 gdsearch 的完整查询 + 出图流程，不需要连 QQ、不需要起 bot。

用法（必须在仓库根目录跑，因为几个模块用的是相对 CWD 的路径）：

    python scripts/try_search.py Tartarus           # 按名字搜
    python scripts/try_search.py 51657783           # 按 id 搜
    python scripts/try_search.py Tartarus -o a.png  # 指定输出文件
    python scripts/try_search.py --reload Tartarus  # 先重载一遍缓存再搜

走的是和 bot 里一模一样的 search_by_name / getlevelinfo / create_image_from_gdlevel，
只是最后一步 bot.send 换成了写文件，所以出来的图应该和群里收到的一样。

不需要装 onebot 适配器和 htmlkit —— 见 _bootstrap.py 的说明。
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import REPO_ROOT, import_submodule

MIN_ID_LEN = 4  # 和 __init__.py 里判断"这串数字是不是 id"的规则保持一致


def reload_all() -> None:
    """等价于插件里的 reload_all()，但不需要 import 整个插件包"""
    for name in ("nlwapi", "platapi", "aredlapi"):
        module = import_submodule(name)
        module.reload()


def search_by_name(name: str) -> list:
    """复刻 __init__.py 里的 search_by_name，数据源和顺序保持一致"""
    gddlapi = import_submodule("gddlapi")
    nlwapi = import_submodule("nlwapi")
    platapi = import_submodule("platapi")

    normalized = name.strip().lower()
    results: dict[int, dict] = {}

    def add(level_id, lname, creator=None, tier=None, difficulty=None) -> None:
        if level_id is None:
            return
        if level_id in results:
            item = results[level_id]
            item["creator"] = item["creator"] or creator
            item["tier"] = item["tier"] or tier
            return
        results[level_id] = {
            "id": level_id, "name": lname, "creator": creator,
            "tier": tier, "difficulty": difficulty, "src": [],
        }

    for level in gddlapi.Gddl.getlevelsbyname(name) or []:
        if not level or not getattr(level, "Meta", None):
            continue
        if getattr(level.Meta, "Name", "").strip().lower() == normalized:
            add(
                int(level.ID), level.Meta.Name, None,
                str(round(level.Rating, 2)) if level.Rating else None,
                level.Meta.Difficulty + (" Pemon" if level.is_pemon() else " Demon"),
            )
            results[int(level.ID)]["src"].append("GDDL")

    for level in nlwapi.Nlw.getlevelbyname(name):
        add(int(level.id or 0), level.name, getattr(level, "creator", None), None)
        results[int(level.id or 0)]["src"].append(level.source)

    plat_info = platapi.Platapi.getlevelbyname(name)
    if plat_info:
        add(int(plat_info.id), plat_info.name, plat_info.creator, plat_info.tier, None)
        results[int(plat_info.id)]["src"].append("Plat")

    return list(results.values())


def _get_level(gdapi, level_id: int):
    """包一层 get_level_by_id，把 GD 服务器的常见拒绝翻译成人话"""
    try:
        return gdapi.get_level_by_id(level_id)
    except ValueError as e:
        msg = str(e)
        if "1005" in msg:
            print(
                "! GD 服务器返回 error code 1005 —— 这台机器的 IP 被 Cloudflare 挡了，"
                "不是代码的问题。\n"
                "  换个能正常访问 GD 服务器的网络（比如 bot 所在的服务器）再跑。",
                file=sys.stderr,
            )
        else:
            print(f"! gdapi 返回了看不懂的东西: {msg}", file=sys.stderr)
        return None


async def run(query: str, out: Path, do_reload: bool) -> int:
    gdapi = import_submodule("gdapi")
    draw = import_submodule("draw")

    if do_reload:
        print("== 先重载一遍本地缓存")
        reload_all()

    if len(query) > MIN_ID_LEN and query.isdigit():
        print(f"== 按 id 查 {query}")
        level = _get_level(gdapi, int(query))
        if not level:
            print("! 没查到这个 id 对应的关卡")
            return 1
    else:
        print(f"== 按名字查 {query!r}")
        started = time.time()
        results = search_by_name(query)
        print(f"   命中 {len(results)} 条，用了 {time.time() - started:.1f}s")
        for i, r in enumerate(results, start=1):
            creator = f" by {r['creator']}" if r["creator"] else ""
            tier = f" t{r['tier']}" if r["tier"] else ""
            src = "/".join(r["src"]) or "?"
            print(f"   {i}. {r['name']}{creator}{tier} (ID: {r['id']}) [{src}]")
        if not results:
            print("! 一条都没查到。data/ 里没缓存的话这是正常的，先跑 run_updater.py")
            return 1
        level = _get_level(gdapi, results[0]["id"])
        if not level:
            print(f"! 拿不到关卡详情，id={results[0]['id']}")
            return 1

    print(f"== 出图：{level.level_name}")
    image = await draw.create_image_from_gdlevel(level)
    image.save(out, format="PNG")
    size = out.stat().st_size / 1024
    print(f"== 已写入 {out}  ({size:.1f} KB, {image.size[0]}x{image.size[1]})")
    return 0


async def run_full(raw_args: str, out: Path, pages: int) -> int:
    """*gdfullsearch 的命令行版：直连 GD 服务器 + 翻页选择器。

    走的是和 bot 里同一套 fullsearch.py，只是把「等用户回消息」换成了
    自动往后翻 N 页，最后把第一条出成图。
    """
    fullsearch = import_submodule("fullsearch")
    draw = import_submodule("draw")

    try:
        session, err = fullsearch.start_session(raw_args)
    except fullsearch.ArgError as e:
        print(f"! 参数有问题：\n{e}", file=sys.stderr)
        return 2

    if session is None:
        print(err)
        return 1

    print(session.render())

    for _ in range(pages):
        ok, msg = session.go_next()
        print()
        if not ok:
            print(f"[翻页到底] {msg}")
            break
        print(session.render())

    first = session.current_levels[0]
    print(f"\n== 拿本页第 1 条出图：{first.level_name}")
    image = await draw.create_image_from_gdlevel(first)
    image.save(out)
    print(f"== 已写入 {out}  ({out.stat().st_size / 1024:.1f} KB, {image.size[0]}x{image.size[1]})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="关卡名或关卡 id；--full 模式下是整串参数")
    parser.add_argument("-o", "--out", type=Path, help="输出的 png 路径")
    parser.add_argument(
        "--reload",
        dest="do_reload",
        action="store_true",
        help="查之前先跑一次 reload_all()，用来验证重载逻辑",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="走 *gdfullsearch 那套（直连 GD 服务器 + 翻页），query 当成整串参数解析",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=0,
        help="--full 模式下自动往后翻几页（默认 0，只看第一页）",
    )
    args = parser.parse_args()

    if Path.cwd() != REPO_ROOT:
        print(f"! 请在仓库根目录运行：cd {REPO_ROOT}", file=sys.stderr)
        return 2

    stem = args.query.replace(" ", "_").replace("-", "")[:40] or "result"
    prefix = "gdfull" if args.full else "gdsearch"
    out = args.out or REPO_ROOT / "temp" / f"{prefix}_{stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.full:
        return asyncio.run(run_full(args.query, out, args.pages))
    return asyncio.run(run(args.query, out, args.do_reload))


if __name__ == "__main__":
    raise SystemExit(main())
