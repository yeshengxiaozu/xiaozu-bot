import asyncio
from dataclasses import dataclass
from io import BytesIO

import requests
from nonebot import get_driver, logger, require
from nonebot.adapters import Bot, Event, Message
from nonebot.permission import SUPERUSER

from xiaozu_bot.utils.adapter_compat import send_image

from . import aredlapi, icons, nlwapi, platapi
from .aredlapi import Aredl  # noqa: F401
from .dailydemon import describe_conditions, get_daily_demon
from .draw import create_image_from_gdlevel
from .fullsearch import SESSION_TIMEOUT, ArgError, FullSearchSession
from .fullsearch import start_session as start_fullsearch_session
from .gdapi import GDLevel, get_level_by_id, get_user_by_name
from .gddlapi import Gddl
from .imageinfo import send_ttp  # noqa: F401
from .nlwapi import Nlw
from .platapi import Platapi
from .ratings import ArgError as RatingsArgError
from .ratings import RatingsSession
from .ratings import start_session as start_ratings_session

require("nonebot_plugin_apscheduler")

# i have no idea is this amount of exposal is bad but looked it worked


def reload_all() -> None:
    """把磁盘上的缓存重新读进内存。

    各个 api 模块都是在 import 的时候把数据读进模块级 list/dict 的，
    不主动调这个的话，updater 半夜抓完的新数据要等到下次重启才生效。
    """
    logger.info("[gdlevelsearch] 开始重载本地缓存")
    for name, module in (("nlw", nlwapi), ("plat", platapi), ("aredl", aredlapi)):
        try:
            module.reload()
        except Exception:
            # 单个源挂了不影响别的，内存里保留旧数据总比清空好
            logger.exception(f"[gdlevelsearch] {name} 重载失败，保留原有数据")
    logger.info("[gdlevelsearch] 缓存重载完毕")


@get_driver().on_startup
async def _refresh_aredl_on_startup() -> None:
    """启动之后在后台把 AREDL 刷一遍。

    aredlapi 在 import 期只读本地缓存（缓存旧了也照用），刷新放在这里，
    这样 api.aredl.net 慢或者挂掉都不会卡住插件加载。
    """
    try:
        await asyncio.to_thread(aredlapi.reload)
    except Exception:
        logger.exception("[gdlevelsearch] 启动后刷新 AREDL 失败，继续用缓存")


# 关键：只 import updater，让 scheduler 注册生效。
# 必须放在 reload_all 定义之后 —— updater 的定时任务要回头调它。
from . import updater  # noqa: E402, F401, RUF100


# fallback function since I should already get it using gdapi if this get called we f*cked up
def get_creator(level_id: int) -> str | None:
    """通过GDhistory API获取关卡作者信息，没有官方api靠谱且原则上不应该被调用"""
    logger.warning("get_creator got called: " + str(level_id))
    try:
        data = requests.get(
            f"https://history.geometrydash.eu/api/v1/level/{level_id}", timeout=10
        )
        return data.json()["cache_username"]
    except Exception:
        return None


# nice little function that extract exactly what i need
def get_difficulty(level_id: int) -> str | None:
    """通过GD API获取关卡难度标签，由于会造成api调用最好尽可能少被调用"""
    logger.info("get_difficulty got called: " + str(level_id))
    try:
        data = get_level_by_id(level_id)
    except Exception:
        return None
    return data.difficulty_label() if data else None


@dataclass
class SearchResult:
    """存储有关搜索结果的基本信息，方便用户进行筛选"""

    id: int
    name: str
    creator: str | None = None
    tier: str | None = None
    difficulty: str | None = None


def _add_search_result(
    results: dict[int, SearchResult],
    level_id: int,
    name: str,
    creator: str | None = None,
    tier: str | None = None,
    difficulty: str | None = None,
):
    """向results中加入一条新的搜索结果"""
    if level_id is None:
        return
    if level_id in results:
        item = results[level_id]
        if not item.creator and creator:
            item.creator = creator
        if not item.tier and tier:
            item.tier = tier
        # difficulty 和上面两个一样只补空的：先到的源（GDDL）给的值更权威，
        # 后到的源不许覆盖。漏掉这条的话缺 difficulty 的条目会被 *gdsearch
        # 拿去挨个打 gdapi。
        if not item.difficulty and difficulty:
            item.difficulty = difficulty
        return
    results[level_id] = SearchResult(level_id, name, creator, tier, difficulty)


def search_by_name(name: str) -> list[SearchResult]:
    """使用名称从多个来源中搜索特定关卡，返回所有结果"""
    normalized = name.strip().lower()
    results: dict[int, SearchResult] = {}

    # 1) GDDL exact match
    gddl_candidates = Gddl.getlevelsbyname(name) or []
    for level in gddl_candidates:
        if not level or not getattr(level, "Meta", None):
            continue
        if getattr(level.Meta, "Name", "").strip().lower() == normalized:
            _add_search_result(
                results,
                int(level.ID),
                level.Meta.Name,
                None,
                str(round(level.Rating, 2)) if level.Rating else None,
                level.Meta.Difficulty + (" Pemon" if level.is_pemon() else " Demon"),
            )
            logger.info(
                f"Find a result in GDDL: tier {getattr(level, 'Rating', None) or 'Na'}"
            )
    # 注：不查 AREDL，因为所有 rated demon 理论上都已经在 GDDL 里了

    # 3) NLW exact match
    for level in Nlw.getlevelbyname(name):
        _add_search_result(
            results,
            int(level.id or 0),
            level.name,
            getattr(level, "creator", None),
            None,
        )
        # 原来是 "..." + str(tier) or "Unknown" + " Tier"，+ 比 or 结合得紧，
        # 左边永远非空，or 那一支是死代码，tier 为空时印的是字面量 "None"
        logger.info(f"Find a result in {level.source}: {level.tier or 'Unknown'} Tier")

    # 4) Platdata exact match
    plat_info = Platapi.getlevelbyname(name)
    if plat_info:
        _add_search_result(
            results,
            int(plat_info.id),
            plat_info.name,
            plat_info.creator,
            plat_info.tier,
            None,
        )

    # 不做 gdapi 兜底搜索：已经有个比我这个做得好的 gdsearch bot 了
    return list(results.values())


def getlevelinfo(level_id: int) -> GDLevel | None:
    """调用gdapi获取一个关卡的基本信息"""
    gdlevel = get_level_by_id(level_id)
    if not gdlevel:
        return None
    return gdlevel


from nonebot import on_command, on_message
from nonebot.params import CommandArg
from nonebot.rule import Rule

# 输出统一走图片，pemon / demon / non-demon 三个分支渲染的东西完全一样，所以合并成一条路径


async def send_result(bot: Bot, event: Event, level_info: GDLevel) -> None:
    image = await create_image_from_gdlevel(level_info)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    await send_image(bot, event, buffer)


gdsearch = on_command("gdsearch")
gdsearchhelp = on_command("gdsearchhelp")
gdrandom = on_command("gd随机推关")
gduser = on_command("gduser")

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


# ---------------------------------------------------------------- gdfullsearch
# 直连 GD 服务器搜索，带翻页选择器。逻辑都在 fullsearch.py 里，
# 这里只负责接 nonebot 的事件。

gdfullsearch = on_command("gdfullsearch")

# 按 session_id 存（群里是 group_xxx_yyy，私聊是 private_yyy）。
# 上面那个 search_cache 只按 user_id，同一个人在两个群里搜会串，新的不继承这毛病。
fullsearch_sessions: dict[str, FullSearchSession] = {}
fullsearch_timeouts: dict[str, asyncio.Task] = {}

NEXT_WORDS = {"n", "next", "下一页", "下页"}
PREV_WORDS = {"p", "prev", "上一页", "上页"}
STOP_WORDS = {"结束", "取消", "退出", "q"}


def _drop_fullsearch(session_id: str) -> None:
    fullsearch_sessions.pop(session_id, None)
    task = fullsearch_timeouts.pop(session_id, None)
    if task:
        task.cancel()


def _clear_all_sessions(event: Event) -> None:
    """几个选择器同时只能活一个，不然 on_message 会互相打架"""
    _drop_fullsearch(event.get_session_id())
    _drop_ratings(event.get_session_id())
    user_id = str(event.get_user_id())
    search_cache.pop(user_id, None)
    task = timeout_tasks.pop(user_id, None)
    if task:
        task.cancel()


def has_fullsearch(event: Event) -> bool:
    return event.get_session_id() in fullsearch_sessions


gdfullsearchselect = on_message(Rule(has_fullsearch), priority=100, block=False)


async def clear_fullsearch(bot: Bot, event: Event, session_id: str) -> None:
    await asyncio.sleep(SESSION_TIMEOUT)
    if session_id in fullsearch_sessions:
        fullsearch_sessions.pop(session_id, None)
        fullsearch_timeouts.pop(session_id, None)
        await bot.send(event, "搜索超时，已结束")


def _arm_timeout(bot: Bot, event: Event, session_id: str) -> None:
    old = fullsearch_timeouts.pop(session_id, None)
    if old:
        old.cancel()
    fullsearch_timeouts[session_id] = asyncio.create_task(
        clear_fullsearch(bot, event, session_id)
    )


@gdfullsearch.handle()
async def handle_gdfullsearch(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    """直接问 GD 服务器要结果，默认只搜 rated"""
    _clear_all_sessions(event)
    session_id = event.get_session_id()

    try:
        # 请求和解析都是同步阻塞的，别堵在事件循环上
        session, err = await asyncio.to_thread(
            start_fullsearch_session, arg.extract_plain_text().strip()
        )
    except ArgError as e:
        await gdfullsearch.finish(str(e))
    except Exception as e:
        logger.exception("[gdfullsearch] 搜索失败")
        await gdfullsearch.finish(f"搜索出错了：{e}")

    if session is None:
        await gdfullsearch.finish(err)

    # 只有一条就别让人再选一次了，和 gdsearch 的行为保持一致
    if len(session.current_levels) == 1:
        await send_result(bot, event, session.current_levels[0])
        await gdfullsearch.finish()

    fullsearch_sessions[session_id] = session
    _arm_timeout(bot, event, session_id)
    await gdfullsearch.finish(session.render())


@gdfullsearchselect.handle()
async def handle_fullsearch_choice(bot: Bot, event: Event) -> None:
    """处理翻页选择器里的输入"""
    session_id = event.get_session_id()
    session = fullsearch_sessions.get(session_id)
    if session is None:
        await gdfullsearchselect.finish()

    choice = event.get_message().extract_plain_text().strip().lower()

    if choice in STOP_WORDS:
        _drop_fullsearch(session_id)
        await gdfullsearchselect.finish("已结束搜索")

    if choice in NEXT_WORDS or choice in PREV_WORDS:
        go = session.go_next if choice in NEXT_WORDS else session.go_prev
        ok, msg = await asyncio.to_thread(go)
        _arm_timeout(bot, event, session_id)
        await gdfullsearchselect.finish(session.render() if ok else msg)

    if not choice.isdigit():
        # 不是给我们的消息，别吞群聊
        await gdfullsearchselect.finish()

    levels = session.current_levels
    index = int(choice)
    if index < 1 or index > len(levels):
        await gdfullsearchselect.finish(f"请输入 1-{len(levels)} 之间的序号")

    level = levels[index - 1]
    _drop_fullsearch(session_id)
    await send_result(bot, event, level)
    await gdfullsearchselect.finish()


# ------------------------------------------------------------------ gdratings
# 看某关卡在 GDDL 上的提交评分（网页上「Submitted ratings」那块）。
# 逻辑在 ratings.py，这里只接 nonebot 的事件。

gdratings = on_command("gdratings")

ratings_sessions: dict[str, RatingsSession] = {}
ratings_timeouts: dict[str, asyncio.Task] = {}


def _drop_ratings(session_id: str) -> None:
    ratings_sessions.pop(session_id, None)
    task = ratings_timeouts.pop(session_id, None)
    if task:
        task.cancel()


def has_ratings(event: Event) -> bool:
    return event.get_session_id() in ratings_sessions


gdratingsselect = on_message(Rule(has_ratings), priority=100, block=False)


async def clear_ratings(bot: Bot, event: Event, session_id: str) -> None:
    await asyncio.sleep(SESSION_TIMEOUT)
    if session_id in ratings_sessions:
        ratings_sessions.pop(session_id, None)
        ratings_timeouts.pop(session_id, None)
        await bot.send(event, "评分列表超时，已结束")


def _arm_ratings_timeout(bot: Bot, event: Event, session_id: str) -> None:
    old = ratings_timeouts.pop(session_id, None)
    if old:
        old.cancel()
    ratings_timeouts[session_id] = asyncio.create_task(
        clear_ratings(bot, event, session_id)
    )


@gdratings.handle()
async def handle_gdratings(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    """看某关卡在 GDDL 上每个人给的 tier / enjoyment"""
    _clear_all_sessions(event)
    session_id = event.get_session_id()

    try:
        session, err = await asyncio.to_thread(
            start_ratings_session, arg.extract_plain_text().strip()
        )
    except RatingsArgError as e:
        await gdratings.finish(str(e))
    except Exception as e:
        logger.exception("[gdratings] 查询失败")
        await gdratings.finish(f"查询出错了：{e}")

    if session is None:
        await gdratings.finish(err)

    # 只有一页就不用挂会话了，发完拉倒
    if session.total_pages <= 1:
        await gdratings.finish(session.render())

    ratings_sessions[session_id] = session
    _arm_ratings_timeout(bot, event, session_id)
    await gdratings.finish(session.render())


@gdratingsselect.handle()
async def handle_ratings_choice(bot: Bot, event: Event) -> None:
    """gdratings 只需要翻页，没有选中这一说"""
    session_id = event.get_session_id()
    session = ratings_sessions.get(session_id)
    if session is None:
        await gdratingsselect.finish()

    choice = event.get_message().extract_plain_text().strip().lower()

    if choice in STOP_WORDS:
        _drop_ratings(session_id)
        await gdratingsselect.finish("已结束")

    if choice in NEXT_WORDS or choice in PREV_WORDS:
        go = session.go_next if choice in NEXT_WORDS else session.go_prev
        ok, msg = await asyncio.to_thread(go)
        _arm_ratings_timeout(bot, event, session_id)
        await gdratingsselect.finish(session.render() if ok else msg)

    # 其他消息一概不理，别吞群聊
    await gdratingsselect.finish()


@gdrandom.handle()
async def handle_gdrandom(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    """*gd随机推关 tier低 [tier高] [enj低] [enj高]"""
    args = arg.extract_plain_text().strip().split()
    if len(args) < 1:
        await gdrandom.finish(
            "用法：*gd随机推关 tier低 [tier高] [enj低] [enj高]\n"
            "tier 是 1-39，enj 是 0-10，后面三个都可以不写\n"
            "例：*gd随机推关 15 20 7 —— 15-20 tier、enjoyment 7 以上"
        )

    def _num(text: str, name: str, low: float, high: float) -> float:
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{name} 要是个数字，你写的是「{text}」") from None
        if not low <= value <= high:
            raise ValueError(f"{name} 要在 {low:g}-{high:g} 之间，你写的是 {value:g}")
        return value

    try:
        tier_low = int(_num(args[0], "tier", 1, 39))
        tier_high = int(_num(args[1], "tier", 1, 39)) if len(args) > 1 else -1
        enj_min = _num(args[2], "enjoyment", 0, 10) if len(args) > 2 else None
        enj_max = _num(args[3], "enjoyment", 0, 10) if len(args) > 3 else None
    except ValueError as e:
        await gdrandom.finish(str(e))

    if tier_high != -1 and tier_high < tier_low:
        tier_low, tier_high = tier_high, tier_low
    if enj_min is not None and enj_max is not None and enj_max < enj_min:
        enj_min, enj_max = enj_max, enj_min

    result = await asyncio.to_thread(
        Gddl.getrandomlevelbytier, tier_low, tier_high, enj_min, enj_max
    )
    if not result:
        await gdrandom.finish("没有找到符合条件的关卡，把条件放宽点试试")

    level = await asyncio.to_thread(getlevelinfo, result.ID)
    if level:
        await send_result(bot, event, level)
    else:
        await gdrandom.finish("发生未知错误。相关id: " + str(result.ID))
    await gdrandom.finish()


# -------------------------------------------------------------------- gdicon
# 单独一条命令，不塞进 *gduser —— 九个 gamemode 就是九次外部请求，
# 挂在 gduser 上会把那条命令拖慢。

gdicon = on_command("gdicon")


@gdicon.handle()
async def handle_gdicon(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    """*gdicon 用户名 [gamemode]，加 -a 出全部九个"""
    args = arg.extract_plain_text().strip().split()
    if not args:
        await gdicon.finish(
            "用法：*gdicon 用户名 [gamemode]\n"
            f"gamemode 可选：{icons.form_names()}，不写默认 cube\n"
            "加 -a 把九个 gamemode 拼成一张图\n"
            "例：*gdicon RobTop ship / *gdicon RobTop -a"
        )

    show_all = False
    form_name = ""
    words: list[str] = []
    for token in args:
        lowered = token.lower()
        if lowered in ("-a", "-all"):
            show_all = True
        elif icons.resolve_form(lowered) is not None and words:
            # 名字后面跟的那个词能对上 gamemode 才当 gamemode，
            # 否则当成用户名的一部分（有人 ID 就叫 wave）
            form_name = lowered
        else:
            words.append(token)

    name = " ".join(words).strip()
    if not name:
        await gdicon.finish("请给一个 GD 用户名")

    user = await asyncio.to_thread(get_user_by_name, name)
    if not user:
        await gdicon.finish(f"没有找到用户「{name}」")

    if show_all:
        items = await icons.fetch_all(user)
        got = sum(1 for _, im in items if im is not None)
        if got == 0:
            await gdicon.finish("一个图标都没取到，图标服务可能挂了")
        sheet = await asyncio.to_thread(icons.compose_sheet, user, items)
        buffer = BytesIO()
        sheet.save(buffer, format="PNG")
        await send_image(bot, event, buffer)
        if got < len(items):
            await gdicon.finish(f"（有 {len(items) - got} 个没取到，显示成问号了）")
        await gdicon.finish()

    form = icons.resolve_form(form_name or icons.DEFAULT_FORM)
    if form is None:
        await gdicon.finish(
            f"看不懂的 gamemode「{form_name}」，可选：{icons.form_names()}"
        )

    icon = await icons.fetch_one(user, form)
    if icon is None:
        await gdicon.finish(f"{user.user_name} 的 {form.label} 图标没取到，等会再试")

    buffer = BytesIO()
    icon.save(buffer, format="PNG")
    await send_image(
        bot,
        event,
        buffer,
        before=f"{user.user_name} 的 {form.label}：",
    )
    await gdicon.finish()


# ----------------------------------------------------------------- dailydemon
# 每天一关，条件写死在 dailydemon.py 里，按日期定死所以一天之内不会变。

dailydemon = on_command("dailydemon")


@dailydemon.handle()
async def handle_dailydemon(bot: Bot, event: Event) -> None:
    level_info, total, err = await asyncio.to_thread(get_daily_demon)
    if level_info is None:
        await dailydemon.finish(err)

    level = await asyncio.to_thread(getlevelinfo, level_info.ID)
    if not level:
        await dailydemon.finish(
            f"今日关卡是 {level_info.Meta.Name}（ID {level_info.ID}），"
            "但是拿详细信息的时候出错了"
        )

    await bot.send(
        event,
        f"今日关卡（{describe_conditions()}，候选 {total} 关）：",
    )
    await send_result(bot, event, level)
    await dailydemon.finish()


@gduser.handle()
async def handle_gduser(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    name = arg.extract_plain_text().strip()
    if not name:
        await gduser.finish("请输入想要搜索的用户名")
    user = await asyncio.to_thread(get_user_by_name, name)
    if not user:
        await gduser.finish("没有找到对应的用户")
    user_basic_info = f"{user.user_name}\n{user.stars}⭐ {user.moons}🌙 {user.demons_count}👿 {str(user.creator_points) + '🔧' if user.creator_points else ''}"
    user_classic_nondemon = (
        f"\nClassic: {user.classic_levels[0]}🤖 {user.classic_levels[1]}💙 {user.classic_levels[2]}💚 {user.classic_levels[3]}💛 {user.classic_levels[4]}🧡 {user.classic_levels[5]}💜;\n{user.classic_levels[6]} Daily; {user.classic_levels[7]} Gauntlet"
        if user.classic_levels
        else ""
    )
    user_plat_nondemon = (
        f"\nPlatformer: {user.platformer_levels[0]}🤖 {user.platformer_levels[1]}💙 {user.platformer_levels[2]}💚 {user.platformer_levels[3]}💛 {user.platformer_levels[4]}🧡 {user.platformer_levels[5]}💜"
        if user.platformer_levels
        else ""
    )
    user_demon = (
        f"\nClassic Demons: {user.demons_breakdown[0]} / {user.demons_breakdown[1]} / {user.demons_breakdown[2]} / {user.demons_breakdown[3]} / {user.demons_breakdown[4]};\n{user.demons_breakdown[10]} Weekly; {user.demons_breakdown[11]} Gauntlet\nPlatformer Demons: {user.demons_breakdown[5]} / {user.demons_breakdown[6]} / {user.demons_breakdown[7]} / {user.demons_breakdown[8]} / {user.demons_breakdown[9]}"
        if user.demons_breakdown
        else ""
    )
    user_info = (
        user_basic_info + user_classic_nondemon + user_plat_nondemon + user_demon
    )
    await gduser.finish(user_info)


@gdsearchhelp.handle()
async def handle_gdsearchhelp() -> None:
    HELP_STR = """使用*gdsearch 关卡名或id 以搜索关卡
数据来源包括GDDL NLW等chart AREDL
以及Plat difficulty chart等plat chart
可以使用*references (gddl/nlw/plat)查询对应的参考线

*gdsearch 只查本地收录的榜单，所以基本只有demon
想搜服务器上的任意关卡用*gdfullsearch，它直接问GD服务器要数据：
  *gdfullsearch 关卡名         默认只搜rated
  *gdfullsearch 关卡名 -a      连没评级的一起搜
  *gdfullsearch 关卡名 -d      只搜demon，后面可以跟1-5或easy/medium/hard/insane/extreme
  *gdfullsearch 关卡名 -u 难度  只搜非demon，0-5或auto/easy/normal/hard/harder/insane（0是auto）
结果多的时候会分页，输入序号选中，n下一页，p上一页，结束取消

*gdratings 关卡名或id 看这关在GDDL上每个人给的tier和enjoyment
  -s 排序   tier / enj / date / progress / attempts / rr
  -asc      正序（默认倒序）
  -v        只看通关的人
"""  # noqa: N806
    # 那几个references的实现我扔给xiaozubot_help模块了
    await gdsearchhelp.finish(HELP_STR)


update_cmd = on_command("gdsearch_update", permission=SUPERUSER, priority=1, block=True)

from .updater.runner import run_all_async


@update_cmd.handle()
async def _handle(event: Event):
    await update_cmd.send("🚀 开始执行手动更新...")
    try:
        result = await run_all_async()
    except Exception as e:
        await update_cmd.finish(f"❌ 更新失败\n{e}")
    else:
        # 抓完立刻重载，这样不用重启就能查到新数据
        await asyncio.to_thread(reload_all)
        await update_cmd.finish(f"✅ 更新完成，缓存已重载\n{result}")
