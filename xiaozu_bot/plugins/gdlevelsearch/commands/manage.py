"""*gdsearch_manage / *gdsearch_status：手动管理更新器没自动匹配上的关卡。

未匹配清单（metadata_unmatched.json）由 updater/jobs/metadata.py 在自动匹配
「确定失败」（查无此关 / 多条候选）时写入；这里负责展示清单、手动补 id
（写回 metadata.json 缓存，下次 getmetadata 运行时自动应用到榜单）。

两个命令都是管理员专用，公开的 *gdsearchhelp 里不列用法，只留
「*gdsearch_manage help 查看管理命令帮助」一个入口。
"""

import asyncio
import json
import time
from pathlib import Path

from nonebot import logger, on_command, on_message
from nonebot.internal.adapter import Event, Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from .. import paths

try:
    from ..updater.jobs import getmetadata
except ImportError:
    from updater.jobs import getmetadata

# 两个运行时文件都在 data/ 下（gitignore 已覆盖 *.json），不参与 staging/发布
UNMATCHED_PATH: Path = paths.DATA_DIR / "metadata_unmatched.json"
METADATA_CACHE_PATH: Path = paths.DATA_DIR / "metadata.json"

PAGE_SIZE = 10
# 会话 10 分钟没动静就失效（惰性检查，不开 asyncio 任务）
SESSION_SECONDS = 10 * 60

EXIT_WORDS = {"结束", "取消", "退出", "q"}
NEXT_WORDS = {"n", "next", "下一页", "下页"}
PREV_WORDS = {"p", "prev", "上一页", "上页"}
HELP_WORDS = {"help", "-h", "帮助"}

REASON_LABELS = {
    "not-found": "查无此关",
    "ambiguous": "多条候选",
}

USAGE = """*gdsearch_manage —— 手动给自动匹配失败的关卡补 id（管理员）
  不写参数：显示待匹配清单（每页 10 条，带序号）
  输入「序号 关卡id」：把 id 写进 metadata 缓存并从未匹配清单移除
  n / p：翻页；结束：退出会话
*gdsearch_status —— 查看待匹配数量、metadata 缓存与数据源状态（管理员）"""

# session_id -> {"page": int, "expires_at": float}
manage_sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# 文件读写
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        logger.warning(f"[gdsearch_manage] 读不了 {path}，当空处理")
        return []


def _save_json(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=4), encoding="utf-8")


def load_unmatched() -> list[dict]:
    return _load_json(UNMATCHED_PATH)


def save_unmatched(entries: list[dict]) -> None:
    _save_json(UNMATCHED_PATH, entries)


def _entry_key(entry: dict) -> tuple[str, str]:
    return (entry.get("name", ""), entry.get("creator", ""))


# ---------------------------------------------------------------------------
# 会话状态（惰性过期，无 asyncio 任务）
# ---------------------------------------------------------------------------
def _session_active(session_id: str) -> bool:
    entry = manage_sessions.get(session_id)
    if entry is None:
        return False
    if time.time() > entry["expires_at"]:
        manage_sessions.pop(session_id, None)
        return False
    return True


def has_manage_session(event: Event) -> bool:
    return _session_active(event.get_session_id())


def _touch_session(session_id: str, page: int) -> None:
    manage_sessions[session_id] = {
        "page": page,
        "expires_at": time.time() + SESSION_SECONDS,
    }


def _drop_session(session_id: str) -> None:
    manage_sessions.pop(session_id, None)


# ---------------------------------------------------------------------------
# 渲染与处理
# ---------------------------------------------------------------------------
def _format_entry(index: int, entry: dict) -> str:
    reason = REASON_LABELS.get(entry.get("reason", ""), entry.get("reason", ""))
    label = f" [{reason}]" if reason else ""
    return f"{index}. {entry['name']} by {entry['creator']}{label}"


def _render_page(entries: list[dict], page: int, session_id: str) -> str:
    total_pages = max(1, (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    _touch_session(session_id, page)
    start = (page - 1) * PAGE_SIZE
    lines = [f"待手动匹配关卡（共 {len(entries)} 条，第 {page}/{total_pages} 页）："]
    lines += [
        _format_entry(i, e)
        for i, e in enumerate(entries[start : start + PAGE_SIZE], start=start + 1)
    ]
    lines.append("输入「序号 关卡id」匹配；n 下一页 / p 上一页；结束 退出")
    return "\n".join(lines)


def _apply_manual_id(entry: dict, level_id: int) -> None:
    """把手动 id 写进 metadata.json 缓存（同 key 旧条目替换）。"""
    key = _entry_key(entry)
    cache = _load_json(METADATA_CACHE_PATH)
    cache = [e for e in cache if _entry_key(e) != key]
    cache.append({"name": entry["name"], "creator": entry["creator"], "id": level_id})
    _save_json(METADATA_CACHE_PATH, cache)


async def _process_input(event: Event, text: str) -> str | None:
    """处理一次管理输入；返回要发的文本，None 表示看不懂（调用方决定怎么回）。"""
    session_id = event.get_session_id()

    if text in EXIT_WORDS:
        _drop_session(session_id)
        return "已退出管理会话"

    if text in HELP_WORDS:
        return USAGE

    entries = load_unmatched()
    if not entries:
        _drop_session(session_id)
        return "没有待手动匹配的关卡"

    parts = text.split()
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        index = int(parts[0])
        level_id = int(parts[1])
        if level_id <= 0:
            return "关卡 id 得是正整数"
        page = manage_sessions.get(session_id, {}).get("page", 1)
        start = (page - 1) * PAGE_SIZE
        if index < 1 or index > PAGE_SIZE or start + index > len(entries):
            return "序号超出范围"
        entry = entries[start + index - 1]
        _apply_manual_id(entry, level_id)
        remaining = [e for e in load_unmatched() if _entry_key(e) != _entry_key(entry)]
        save_unmatched(remaining)
        # Trigger getmetadata so the manual id is applied immediately.
        try:
            await asyncio.to_thread(getmetadata.main)
        except Exception:
            logger.exception("[gdsearch_manage] 自动触发 getmetadata 失败")
        if remaining:
            return (
                f"已记录：{entry['name']} by {entry['creator']} -> {level_id}"
                f"（剩余 {len(remaining)} 条）"
            )
        _drop_session(session_id)
        return (
            f"已记录：{entry['name']} by {entry['creator']} -> {level_id}"
            "（没有剩余待匹配关卡了）"
        )

    if text in NEXT_WORDS or text in PREV_WORDS:
        page = manage_sessions.get(session_id, {}).get("page", 1)
        page = page + 1 if text in NEXT_WORDS else page - 1
        return _render_page(entries, page, session_id)

    if text == "":
        return _render_page(entries, 1, session_id)

    return None


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------
gdsearchmanage = on_command("gdsearch_manage", permission=SUPERUSER, priority=1, block=True)
gdsearchmanage_select = on_message(Rule(has_manage_session), priority=100, block=False)


@gdsearchmanage.handle()
async def handle_gdsearchmanage(event: Event, arg: Message = CommandArg()) -> None:
    text = arg.extract_plain_text().strip()
    msg = await _process_input(event, text)
    if msg is None:
        msg = USAGE
    await gdsearchmanage.finish(msg)


@gdsearchmanage_select.handle()
async def handle_gdsearchmanage_select(event: Event) -> None:
    """会话期间的后续输入：序号 id / 翻页词直接处理，其他消息不吞。"""
    text = event.get_message().extract_plain_text().strip()
    msg = await _process_input(event, text)
    if msg is None:
        await gdsearchmanage_select.finish()
    await gdsearchmanage_select.finish(msg)


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------
def _file_count(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return len(data.get("levels", []))
    except Exception:
        return 0


def _file_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _fmt_time(ts: float | None) -> str:
    if ts is None:
        return "无"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


gdsearchstatus = on_command("gdsearch_status", permission=SUPERUSER, priority=1, block=True)


@gdsearchstatus.handle()
async def handle_gdsearchstatus() -> None:
    unmatched = load_unmatched()
    cache = _load_json(METADATA_CACHE_PATH)

    sources = ("nlw_levels.json", "ids_levels.json", "lw_levels.json", "hds_levels.json")
    counts = []
    mtimes: list[float] = []
    for name in sources:
        path = paths.DATA_DIR / name
        counts.append(f"{name.split('_')[0]}: {_file_count(path)}")
        t = _file_mtime(path)
        if t is not None:
            mtimes.append(t)

    aredl_path = paths.DATA_DIR / "aredl_levels.json"
    arepl_path = paths.DATA_DIR / "arepl_levels.json"
    aredl_mtimes = [t for t in (_file_mtime(aredl_path), _file_mtime(arepl_path)) if t is not None]

    lines = [
        f"待手动匹配关卡：{len(unmatched)}",
        f"metadata 缓存：{len(cache)} 条（最近更新 {_fmt_time(_file_mtime(METADATA_CACHE_PATH))}）",
        "榜单关卡数：" + "、".join(counts),
        f"榜单最后修改时间（四者最早）：{_fmt_time(min(mtimes) if mtimes else None)}",
        f"AREDL 关卡数：aredl {_file_count(aredl_path)}、arepl {_file_count(arepl_path)}",
        f"AREDL 最后修改时间（两者最早）：{_fmt_time(min(aredl_mtimes) if aredl_mtimes else None)}",
    ]
    await gdsearchstatus.finish("\n".join(lines))
