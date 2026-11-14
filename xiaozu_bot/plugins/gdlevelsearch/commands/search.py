"""*gdsearch 及其多结果选择器。"""

import asyncio
from contextlib import suppress

from nonebot import on_command, on_message
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot.rule import Rule

from xiaozu_bot.utils.adapter_compat import get_context_id

from ..api.gdapi import GDAPIUnavailable
from ..services.search import (
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
# 只有当**所有**数据源都没答上来时才走这条（search_by_name 返回 None）。
# 之前只要 GDDL 一个源没答上来就报「GDDL 无法访问」，于是打错字、搜了个
# 不存在的关卡也会甩锅给 GDDL。
SOURCES_UNAVAILABLE_MESSAGE = "关卡数据源暂时无法访问，请稍后重试"
# 少于这个长度的纯数字串当关卡名搜，不当 id。保持在 5（= 原来的 `len > 4`），
# 因为 4 位 id 确实存在，改小会把它们当成名字；这里只是把魔数写明。
MIN_ID_LENGTH = 5


def has_cache(event: Event) -> bool:
    return get_context_id(event) in search_cache


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


@gdsearch.handle()
async def handle_gdsearch(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    """处理用户对gdsearch的调用"""
    name = arg.extract_plain_text().strip()
    if name == "":
        await gdsearch.finish("请提供关卡的名字或id")

    # 用会话键而不是裸 user_id：否则同一个人在 A 群搜完，跑到 B 群发个数字
    # 就会选中 A 群的结果。
    session_key = get_context_id(event)
    # 清除旧缓存/任务，两个选择器的都要清（同时只能活一个）
    _clear_all_sessions(event)

    # ID 搜索
    if len(name) >= MIN_ID_LENGTH and name.isdigit():
        level_id = int(name)
        try:
            gdlevel = await asyncio.to_thread(getlevelinfo, level_id)
        except GDAPIUnavailable:
            await gdsearch.finish(GD_API_UNAVAILABLE_MESSAGE)
        # 服务不可用时上面已经 finish 了；走到这里说明服务正常但没这个 id。
        if gdlevel is None:
            await gdsearch.finish("不存在符合这个id的demon关卡")
        await send_result(bot, event, level_id)
        return

    # 名称搜索（要打 GDDL，别堵事件循环）
    results = await asyncio.to_thread(search_by_name, name)
    if results is None:
        await gdsearch.finish(SOURCES_UNAVAILABLE_MESSAGE)
    if not results:
        await gdsearch.finish(f"没有找到名为 '{name}' 的demon关卡")

    if len(results) == 1:
        await send_result(bot, event, int(results[0].id))
        await gdsearch.finish()

    # 多结果缓存
    search_cache[session_key] = results
    timeout_tasks[session_key] = asyncio.create_task(
        clear_search_cache(bot, event, session_key)
    )

    # 缺 difficulty 的条目要挨个去打 gdapi，条数多的时候是一串同步请求，
    # 整段丢线程池里做
    def _render_results() -> str:
        text = f"找到 {len(results)} 个名为 '{name}' 的demon关卡："
        for i, result in enumerate(results, start=1):
            difficulty_str = f" ({result.difficulty or get_difficulty(result.id)})"
            creator_str = f" by {result.creator}" if result.creator else ""
            tier_str = f" t{result.tier}" if result.tier else ""
            text += f"\n{i}. {result.name}{creator_str}{difficulty_str}{tier_str} (ID: {result.id})"
        return text + "\n输入序号以选中关卡,输入“结束”以中止搜索"

    await gdsearch.finish(await asyncio.to_thread(_render_results))


@gdsearchselect.handle()
async def handle_choice(bot: Bot, event: Event) -> None:
    """处理用户对gdsearch返回多结果的回复"""
    session_key = get_context_id(event)
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
