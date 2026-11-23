"""*gdsearch 及其多结果选择器。"""

import asyncio
from contextlib import suppress

from nonebot import on_command, on_message
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot.rule import Rule

from xiaozu_bot.utils.adapter_compat import get_user_context_id

from ..api.gdapi import GDAPIUnavailable
from ..services.search import (
    SearchResults,
    _clear_all_sessions,
    get_difficulty,
    getlevelinfo,
    search_by_name,
    send_result,
)

gdsearch = on_command("gdsearch")

# 搜索缓存与超时
search_cache = {}
timeout_tasks = {}
GD_API_UNAVAILABLE_MESSAGE = "GD 关卡服务器暂时无法访问，请稍后重试"
# 只有当**所有**数据源都没答上来时才走这条（`results.sources_available` 为假）。
# 之前只要 GDDL 一个源没答上来就报「GDDL 无法访问」，于是打错字、搜了个
# 不存在的关卡也会甩锅给 GDDL。
SOURCES_UNAVAILABLE_MESSAGE = "关卡数据源暂时无法访问，请稍后重试"

# Sentinel for "the official server refused the ID lookup". Returning None
# would be indistinguishable from "no level has this ID", and those two cases
# need different wording.
_ID_LOOKUP_UNAVAILABLE = object()


def _is_tier_number(tier: str) -> bool:
    """True for a GDDL tier value ("35.0"), false for a chart tier name ("10 - INSANE")."""
    try:
        float(tier)
    except (TypeError, ValueError):
        return False
    return True


def _parse_level_id(text: str) -> int | None:
    """ID a pure-digit query could refer to, or None if it is not one.

    Any digit count is accepted: GD has levels with 4-digit IDs and levels
    whose *name* is a number, so the caller tries both routes instead of
    guessing from the length. ``isdigit()`` is true for characters ``int()``
    rejects ("²"), hence the guarded conversion.
    """
    if not text.isdigit():
        return None
    try:
        level_id = int(text)
    except ValueError:
        return None
    return level_id if level_id > 0 else None


async def _lookup_level_by_id(level_id: int):
    """Official record for ``level_id``, or the unavailable sentinel."""
    try:
        return await asyncio.to_thread(getlevelinfo, level_id)
    except GDAPIUnavailable:
        return _ID_LOOKUP_UNAVAILABLE


def has_cache(event: Event) -> bool:
    return get_user_context_id(event) in search_cache


def _drop_search(session_key: str) -> None:
    search_cache.pop(session_key, None)
    task = timeout_tasks.pop(session_key, None)
    if task:
        task.cancel()


rule_cache = Rule(has_cache)
gdsearchselect = on_message(rule_cache, priority=100, block=False)


async def clear_search_cache(bot: Bot, event: Event, session_key: str) -> None:
    """30秒后自动清除搜索缓存"""
    await asyncio.sleep(30)
    search_cache.pop(session_key, None)
    timeout_tasks.pop(session_key, None)
    # 这条消息是"过了一会儿才想起来要发"的，期间 event 可能已经失效，
    # 发不出去不该变成一个没人 await 的任务异常。
    with suppress(Exception):
        await bot.send(event, "输入超时,请重新再试")


def _render_results(results: SearchResults, query: str) -> str:
    """Format the selectable list of name-search hits."""
    text = f"找到 {len(results)} 个名为 '{query}' 的demon关卡："
    for i, result in enumerate(results, start=1):
        # 两个来源都可能没有难度（GDDL 搜索条目不带、名字搜索的 gdapi
        # 补查又被开关关掉）。没有难度就意味着还没评级，显式写 N/A，
        # 不要让用户看见 "(None)"。
        difficulty = result.difficulty or get_difficulty(result.id)
        difficulty_str = f" ({difficulty or 'N/A'})"
        creator_str = f" by {result.creator}" if result.creator else ""
        # tier 这个字段在不同来源里语义不同：GDDL 是档位数值（35.0），
        # Plat 是档名（"10 - INSANE"）。只给纯数值加 "t" 前缀，否则会
        # 拼出 "t10 - INSANE" 这种谁也看不懂的东西。
        tier_str = ""
        if result.tier:
            tier_str = f" t{result.tier}" if _is_tier_number(result.tier) else f" {result.tier}"
        text += f"\n{i}. {result.name}{creator_str}{difficulty_str}{tier_str} (ID: {result.id})"
    return text + "\n输入序号以选中关卡,输入“结束”以中止搜索"


async def _show_name_results(
    bot: Bot,
    event: Event,
    session_key: str,
    query: str,
    results: SearchResults,
) -> None:
    """Show name-search hits: a lone hit is rendered, several become a picker."""
    if len(results) == 1:
        await send_result(bot, event, int(results[0].id))
        await gdsearch.finish()

    # 多结果缓存
    search_cache[session_key] = results
    timeout_tasks[session_key] = asyncio.create_task(
        clear_search_cache(bot, event, session_key)
    )
    await gdsearch.finish(await asyncio.to_thread(_render_results, results, query))


async def _handle_name_only(
    bot: Bot, event: Event, session_key: str, name: str
) -> None:
    """The plain name path, used when the input cannot be an ID."""
    # 名称搜索（要打 GDDL，别堵事件循环）
    results = await asyncio.to_thread(search_by_name, name)
    if not results.sources_available:
        await gdsearch.finish(SOURCES_UNAVAILABLE_MESSAGE)
    if not results:
        await gdsearch.finish(f"没有找到名为 '{name}' 的demon关卡")
    await _show_name_results(bot, event, session_key, name, results)


@gdsearch.handle()
async def handle_gdsearch(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    """处理用户对gdsearch的调用"""
    name = arg.extract_plain_text().strip()
    if name == "":
        await gdsearch.finish("请提供关卡的名字或id")

    # Keyed by chat *and* user: a bare user_id would leak across groups, while
    # a bare chat id would let any group member answer someone else's prompt.
    session_key = get_user_context_id(event)
    # 清除旧缓存/任务，两个选择器的都要清（同时只能活一个）
    _clear_all_sessions(event)

    level_id = _parse_level_id(name)
    if level_id is None:
        await _handle_name_only(bot, event, session_key, name)
        return

    # 纯数字的输入两种解释都成立，别再按位数猜：ID 和名称同时查，谁有结果
    # 就显示谁，两边都有就都显示。ID 有结果时先出图（那是最精确的答案），
    # 名称命中随其后，同样直接出图或进选择器，不额外加说明文字。
    id_level, results = await asyncio.gather(
        _lookup_level_by_id(level_id),
        asyncio.to_thread(search_by_name, name),
    )

    if id_level is not _ID_LOOKUP_UNAVAILABLE and id_level is not None:
        # 同一个关卡可能同时被 ID 和名称命中，列表里去掉重复项。
        extra = SearchResults(
            (r for r in results if int(r.id) != level_id),
            sources_available=results.sources_available,
        )
        # 官方记录已经拿到，交给出图复用，别再打一次 boomlings
        await send_result(bot, event, level_id, id_level)
        if extra:
            await _show_name_results(bot, event, session_key, name, extra)
        await gdsearch.finish()

    # ID 那半张没命中（或查不了）：名称那边命中什么就展示什么。
    if results:
        await _show_name_results(bot, event, session_key, name, results)
    if not results.sources_available:
        await gdsearch.finish(SOURCES_UNAVAILABLE_MESSAGE)
    if id_level is _ID_LOOKUP_UNAVAILABLE:
        await gdsearch.finish(GD_API_UNAVAILABLE_MESSAGE)
    await gdsearch.finish(f"没有找到 ID 为 {level_id} 的关卡")


@gdsearchselect.handle()
async def handle_choice(bot: Bot, event: Event) -> None:
    """处理用户对gdsearch返回多结果的回复"""
    session_key = get_user_context_id(event)
    if session_key not in search_cache:
        await gdsearchselect.finish()

    choice = event.get_message().extract_plain_text().strip()

    # 手动取消
    if "结束" in choice or "取消" in choice:
        _drop_search(session_key)
        await gdsearchselect.finish("已取消搜索")

    if not choice.isdigit():
        await gdsearchselect.finish()

    index = int(choice)
    results = search_cache[session_key]
    if index < 1 or index > len(results):
        await gdsearchselect.finish("请输入正确的序号")

    result = results[index - 1]
    # 清理缓存
    _drop_search(session_key)

    await send_result(bot, event, int(result.id))
    await gdsearchselect.finish()
