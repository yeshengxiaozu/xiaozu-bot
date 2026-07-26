#!/usr/bin/env python
"""单独跑一遍 gdlevelsearch 的数据更新，不需要连 QQ、不需要起 bot。

用法（必须在仓库根目录跑，因为有几个模块用的是相对 CWD 的路径）：

    python scripts/run_updater.py            # 跑全部任务，遇错即停
    python scripts/run_updater.py --continue  # 某个任务挂了也接着跑后面的
    python scripts/run_updater.py nlw ids     # 只跑指定的任务

跑完会打印每个任务的成败，以及 data/ 下各个 json 的大小和更新时间。
"""

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "xiaozu_bot" / "plugins" / "gdlevelsearch"

# 把 gdlevelsearch/ 放进 sys.path，让 updater 变成一个顶层包。
# 这样就不会去 import gdlevelsearch/__init__.py（那玩意要拉 onebot 适配器和
# 一堆插件依赖），jobs/ 里那些 `except ImportError: from updater.paths import ...`
# 的兜底分支也正好是为这个场景写的。
sys.path.insert(0, str(PLUGIN_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "jobs",
        nargs="*",
        help="只跑这些任务（不填就是全部）。可选：见 updater/runner.py 的 JOBS",
    )
    parser.add_argument(
        "--continue",
        dest="keep_going",
        action="store_true",
        help="某个任务失败也继续跑后面的",
    )
    args = parser.parse_args()

    if Path.cwd() != REPO_ROOT:
        print(f"! 请在仓库根目录运行：cd {REPO_ROOT}", file=sys.stderr)
        return 2

    import asyncio

    from updater import runner
    from updater.paths import DATA_DIR, STAGING_DIR, ensure_dirs, publish

    ensure_dirs()
    started = time.time()

    if args.jobs:
        unknown = [j for j in args.jobs if j not in runner.JOBS]
        if unknown:
            print(f"! 没有这些任务：{', '.join(unknown)}", file=sys.stderr)
            print(f"  可选：{', '.join(runner.JOBS)}", file=sys.stderr)
            return 2

        # 只跑指定的几个：写进 staging，跑完手动发布。
        # 注意这样可能只发布一部分文件，其余的还是上一次的。
        print(f"== 只跑 {len(args.jobs)} 个任务：{', '.join(args.jobs)}")
        print(f"   中间产物写到 {STAGING_DIR}")
        ok, failed = [], []
        for name in args.jobs:
            job_started = time.time()
            print(f"-- {name} ... ", end="", flush=True)
            try:
                runner.JOBS[name]()
            except Exception as e:  # noqa: BLE001
                failed.append((name, e))
                print(f"失败 ({time.time() - job_started:.1f}s): {type(e).__name__}: {e}")
                if not args.keep_going:
                    print("   (加 --continue 可以让它接着跑后面的任务)")
                    break
            else:
                ok.append(name)
                print(f"OK ({time.time() - job_started:.1f}s)")
        if failed and not args.keep_going:
            print("\n有任务失败，不发布，data/ 保持原样")
        else:
            moved = publish()
            print(f"\n已发布 {len(moved)} 个文件：{', '.join(moved) or '（无）'}")
    else:
        # 跑完整流水线：分层并发，全绿才发布
        print(f"== 跑完整流水线，共 {len(runner.STAGES)} 层")
        for i, stage in enumerate(runner.STAGES, start=1):
            print(f"   第 {i} 层（并发）：{', '.join(stage)}")
        try:
            results = asyncio.run(runner.run_all_async(stop_on_error=not args.keep_going))
        except Exception as e:  # noqa: BLE001
            print(f"\n== 流水线失败：{e}")
            print(f"   没有发布，data/ 保持上一次的样子；中间产物留在 {STAGING_DIR} 方便排查")
            return 1
        ok, failed = results["success"], results["failed"]
        print(f"\n== 成功 {len(ok)}：{', '.join(ok)}")
        print(f"== 已发布 {len(results['published'])} 个文件："
              f"{', '.join(results['published']) or '（无）'}")

    print(f"\n== 共用时 {time.time() - started:.1f}s")
    for name, e in failed if args.jobs else []:
        print(f"   ✖ {name}: {type(e).__name__}: {e}")

    print(f"\n== {DATA_DIR} 现状：")
    data_files = sorted(DATA_DIR.glob("*.json")) if DATA_DIR.exists() else []
    if not data_files:
        print("   (空的 —— 一个 json 都没有，搜索会查不到任何东西)")
    for f in data_files:
        stat = f.stat()
        age = time.time() - stat.st_mtime
        print(f"   {f.name:<28} {stat.st_size / 1024:>9.1f} KB   {age / 3600:>6.1f} 小时前")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
