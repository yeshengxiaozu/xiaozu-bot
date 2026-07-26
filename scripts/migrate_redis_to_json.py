#!/usr/bin/env python
"""把还在 Redis 里的运行时数据搬到 JsonRedis 的 storage.json。

game / jrrp / roulette / zhua_api 这四个插件已经从 redis 换成 JsonRedis 了。
换完之后 bot 是读 json 文件的，**不会**再去看 Redis —— 所以不跑这个脚本的话，
莓币余额、今日人品、轮盘奖池在群里全都会变成 0（Redis 里的数据没删，只是没人读了）。

用法（在 bot 所在的机器上、仓库根目录跑）：

    pip install -e ".[migrate]"                        # 只有这个脚本要 redis 库
    python scripts/migrate_redis_to_json.py            # 只看不写，先确认搬什么
    python scripts/migrate_redis_to_json.py --write    # 真的写

已经存在的 storage.json 默认不覆盖，要覆盖加 --force。
Redis 里的东西一个都不会删，随时可以重来。
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# 每个插件搬哪些键。
#   patterns: 普通键的 glob（带 TTL 的会连剩余时间一起搬）
#   hashes  : 整个哈希表搬过去
PLANS = {
    "jrrp": {"patterns": ["jrrp_*"], "hashes": []},
    "roulette": {
        "patterns": ["roulette_pool", "roulette_total", "roulette_status_*"],
        "hashes": [],
    },
    "zhua_api": {
        "patterns": ["forbid_*", "coins_status_*"],
        "hashes": ["berit_coins"],
    },
    "game": {"patterns": [], "hashes": ["game_mode"]},
    # guess / zhua 之前就换成 JsonRedis 了，当时没搬数据，
    # 所以它们在 Redis 里的旧记录一直是孤儿（猜图的累计次数就在这里面）。
    # 顺手一起搬回来。
    "guess": {
        "patterns": ["guess_total_tries", "guess_total_right", "guess_cooldown_*"],
        "hashes": ["guess_answer", "guess_answer_position", "guess_ori"],
    },
    "zhua": {"patterns": ["zhua_cd_*"], "hashes": []},
}


def main() -> int:  # noqa: C901, PLR0912
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="真的写文件（默认只看不写）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 storage.json")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--db", type=int, default=0)
    args = parser.parse_args()

    if Path.cwd() != REPO_ROOT:
        print(f"! 请在仓库根目录运行：cd {REPO_ROOT}", file=sys.stderr)
        return 2

    import redis

    from xiaozu_bot.utils.json_storage import JsonRedis

    src = redis.Redis(
        host=args.host, port=args.port, db=args.db, decode_responses=True
    )
    try:
        src.ping()
    except Exception as e:  # noqa: BLE001
        print(f"! 连不上 Redis ({args.host}:{args.port}): {e}", file=sys.stderr)
        print("  Redis 没在跑的话，说明没有数据要搬，直接用空的 json 起就行。")
        return 1

    if not args.write:
        print("== 预演模式，什么都不会写。确认没问题之后加 --write\n")

    claimed: set[str] = set()

    total = 0
    for plugin, plan in PLANS.items():
        target = REPO_ROOT / "xiaozu_bot" / "plugins" / plugin / "data" / "storage.json"

        if target.exists() and not args.force:
            print(f"-- {plugin}: {target.name} 已存在，跳过（要覆盖加 --force）")
            continue

        moved = []

        for pattern in plan["patterns"]:
            for key in sorted(src.keys(pattern)):
                claimed.add(key)
                if src.type(key) != "string":
                    print(f"   ? {key} 不是 string（是 {src.type(key)}），跳过")
                    continue
                ttl = src.ttl(key)
                moved.append((key, src.get(key), ttl if ttl and ttl > 0 else None))

        hashes = []
        for name in plan["hashes"]:
            if src.exists(name):
                claimed.add(name)
                if src.type(name) != "hash":
                    print(f"   ? {name} 不是 hash（是 {src.type(name)}），跳过")
                    continue
                hashes.append((name, src.hgetall(name)))

        count = len(moved) + sum(len(fields) for _, fields in hashes)
        total += count
        print(f"-- {plugin}: {len(moved)} 个键 + {len(hashes)} 张哈希表，共 {count} 项")
        for key, value, ttl in moved[:5]:
            shown = f"{value[:40]}..." if len(str(value)) > 40 else value  # noqa: PLR2004
            print(f"     {key} = {shown}" + (f"  (ttl {ttl}s)" if ttl else ""))
        if len(moved) > 5:  # noqa: PLR2004
            print(f"     ... 另外 {len(moved) - 5} 个")
        for name, fields in hashes:
            print(f"     {name}: {len(fields)} 个字段")

        if not args.write or count == 0:
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        dst = JsonRedis(str(target))
        for key, value, ttl in moved:
            dst.set(key, value, ex=ttl)
        for name, fields in hashes:
            for field, value in fields.items():
                dst.hset(name, field, value)
        print(f"     -> 已写入 {target}")

    print(f"\n== 合计 {total} 项")

    # 明确说清楚哪些键没搬，别让人以为"全都搬过去了"
    leftover = sorted(set(src.keys("*")) - claimed)
    if leftover:
        print(f"\n== 有 {len(leftover)} 个键不在迁移范围内，会留在 Redis 里不动：")
        for key in leftover[:20]:
            print(f"   {key}  (type={src.type(key)})")
        if len(leftover) > 20:  # noqa: PLR2004
            print(f"   ... 另外 {len(leftover) - 20} 个")
        print("   现在的代码没有读这些键的地方，确认过就不用管。")

    if not args.write:
        print("\n   （预演，没写任何东西。加 --write 真正执行）")
    else:
        print("\n   Redis 里的数据一条都没删，对不上的话可以删掉 json 重来。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
