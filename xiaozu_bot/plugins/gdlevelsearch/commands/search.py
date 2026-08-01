"""*gdsearch 及其多结果选择器。"""

import asyncio

from nonebot import on_command, on_message
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot.rule import Rule

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


def has_cache(event: Event) -> bool:
    return str(event.get_user_id()) in search_cache


rule_cache = Rule(has_cache)
gdsearchselect = on_message(rule_cache, priority=100, block=False)


async def clear_search_cache(bot: Bot, event: Event, user_id: str) -> None:
    """30秒后自动清除搜索缓存"""
    await asyncio.sleep(30)
    search_cache.pop(user_id, None)
    timeout_tasks.pop(user_id, None)
    await bot.send(event, "输入超时,请重新再试")


@gdsearch.handle()
async def handle_gdsearch(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    """处理用户对gdsearch的调用"""
    name = arg.extract_plain_text().strip()
    if name == "":
        await gdsearch.finish("请提供关卡的名字或id")

    user_id = str(event.get_user_id())
    # 清除旧缓存/任务，两个选择器的都要清（同时只能活一个）
    _clear_all_sessions(event)

    # ID 搜索
    if len(name) > 4 and name.isdigit():
        level = await asyncio.to_thread(getlevelinfo, int(name))
        if level:
            await send_result(bot, event, level)
        else:
            await gdsearch.finish("不存在符合这个id的demon关卡")
        return

    # 名称搜索（要打 GDDL，别堵事件循环）
    results = await asyncio.to_thread(search_by_name, name)
    if not results:
        await gdsearch.finish(f"没有找到名为 '{name}' 的demon关卡")

    if len(results) == 1:
        level = await asyncio.to_thread(getlevelinfo, results[0].id)
        if level:
            await send_result(bot, event, level)
        else:
            await gdsearch.finish("发生未知错误。相关id: " + str(results[0].id))
        await gdsearch.finish()

    # 多结果缓存
    search_cache[user_id] = results
    timeout_tasks[user_id] = asyncio.create_task(
        clear_search_cache(bot, event, user_id)
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
    user_id = str(event.get_user_id())
    if user_id not in search_cache:
        await gdsearchselect.finish()

    choice = event.get_message().extract_plain_text().strip()

    # 手动取消
    if "结束" in choice or "取消" in choice:
        search_cache.pop(user_id, None)
        if user_id in timeout_tasks:
            timeout_tasks[user_id].cancel()
            del timeout_tasks[user_id]
        await gdsearchselect.finish("已取消搜索")

    if not choice.isdigit():
        await gdsearchselect.finish()

    index = int(choice)
    results = search_cache[user_id]
    if index < 1 or index > len(results):
        await gdsearchselect.finish("请输入正确的序号")

    result = results[index - 1]
    # 清理缓存
    search_cache.pop(user_id, None)
    if user_id in timeout_tasks:
        timeout_tasks[user_id].cancel()
        del timeout_tasks[user_id]

    level = await asyncio.to_thread(getlevelinfo, result.id)
    if level:
        await send_result(bot, event, level)
    else:
        await gdsearchselect.finish("发生未知错误。相关id: " + str(result.id))
    await gdsearchselect.finish()
