import asyncio
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

import requests
from nonebot import logger, require
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment
from nonebot.permission import SUPERUSER

from . import aredlapi, nlwapi, platapi
from .aredlapi import Aredl  # noqa: F401
from .draw import create_image_from_gdlevel
from .gdapi import GDLevel, get_level_by_id, get_user_by_name
from .gddlapi import Gddl
from .imageinfo import send_ttp  # noqa: F401
from .nlwapi import Nlw
from .platapi import Platapi

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


# 关键：只 import updater，让 scheduler 注册生效。
# 必须放在 reload_all 定义之后 —— updater 的定时任务要回头调它。
from . import updater  # noqa: E402, F401, RUF100

# fallback function since I should already get it using gdapi if this get called we f*cked up
def get_creator(level_id: int) -> Optional[str]:
    """通过GDhistory API获取关卡作者信息，没有官方api靠谱且原则上不应该被调用"""
    logger.warning("get_creator got called: " + str(level_id))
    try:
        data = requests.get(
            f"https://history.geometrydash.eu/api/v1/level/{level_id}", timeout=10
        )
        return data.json()["cache_username"]
    except Exception:  # noqa: BLE001
        return None


# nice little function that extract exactly what i need
def get_difficulty(level_id: int) -> Optional[str]:
    """通过GD API获取关卡难度标签，由于会造成api调用最好尽可能少被调用"""
    logger.info("get_difficulty got called: " + str(level_id))
    try:
        data = get_level_by_id(level_id)
    except Exception:  # noqa: BLE001
        return None
    return data.difficulty_label() if data else None

@dataclass
class SearchResult:
    """存储有关搜索结果的基本信息，方便用户进行筛选"""
    id: int
    name: str
    creator: Optional[str] = None
    tier: Optional[str] = None
    difficulty: Optional[str] = None


def _add_search_result(  # noqa: PLR0913
    results: dict[int, SearchResult],
    level_id: int,
    name: str,
    creator: Optional[str] = None,
    tier: Optional[str] = None,
    difficulty: Optional[str] = None,
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
        logger.info(f"Find a result in {level.source}: " +
                    str(level.tier) or "Unknown" + " Tier")

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


def getlevelinfo(level_id: int) -> Optional[GDLevel]:
    """调用gdapi获取一个关卡的基本信息"""
    gdlevel = get_level_by_id(level_id)
    if not gdlevel:
        return None
    return gdlevel

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageEvent  # noqa: F811
from nonebot.params import CommandArg
from nonebot.rule import Rule

# 输出统一走图片，pemon / demon / non-demon 三个分支渲染的东西完全一样，所以合并成一条路径


async def send_result(bot: Bot, event: Event, level_info: GDLevel) -> None:
    image = await create_image_from_gdlevel(level_info)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    await bot.send(event, MessageSegment.image(buffer))


gdsearch = on_command("gdsearch")
gdsearchhelp = on_command("gdsearchhelp")
gdrandom = on_command("gd随机推关")
gduser = on_command("gduser")

# 搜索缓存与超时
search_cache = {}
timeout_tasks = {}

def has_cache(event: MessageEvent) -> bool:
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
async def handle_gdsearch(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    """处理用户对gdsearch的调用"""
    name = arg.extract_plain_text().strip()
    if name == "":
        await gdsearch.finish("请提供关卡的名字或id")

    user_id = str(event.get_user_id())
    # 清除旧缓存/任务
    search_cache.pop(user_id, None)
    if user_id in timeout_tasks:
        timeout_tasks[user_id].cancel()
        del timeout_tasks[user_id]

    # ID 搜索
    if len(name) > 4 and name.isdigit():  # noqa: PLR2004 yes its a magic number but it help user
        level = getlevelinfo(int(name))
        if level:
            await send_result(bot, event, level)
        else:
            await gdsearch.finish("不存在符合这个id的demon关卡")
        return

    # 名称搜索
    results = search_by_name(name)
    if not results:
        await gdsearch.finish(f"没有找到名为 '{name}' 的demon关卡")

    if len(results) == 1:
        level = getlevelinfo(results[0].id)
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

    msgstr = f"找到 {len(results)} 个名为 '{name}' 的demon关卡："
    for i, result in enumerate(results, start=1):
        difficulty_str = f" ({result.difficulty or get_difficulty(result.id)})"
        creator_str = f" by {result.creator}" if result.creator else ""
        tier_str = f" t{result.tier}" if result.tier else ""
        msgstr += f"\n{i}. {result.name}{creator_str}{difficulty_str}{tier_str} (ID: {result.id})"
    msgstr += "\n输入序号以选中关卡,输入“结束”以中止搜索"
    await gdsearch.finish(msgstr)


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

    level = getlevelinfo(result.id)
    if level:
        await send_result(bot, event, level)
    else:
        await gdsearchselect.finish("发生未知错误。相关id: " + str(result.id))
    await gdsearchselect.finish()

@gdrandom.handle()
async def handle_gdrandom(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = arg.extract_plain_text().strip().split()
    if len(args) < 1:
        await gdrandom.finish("请输入至少一个数字以指定tier范围！（懒得写enj筛选喵）")
    low = int(args[0])
    high = int(args[1]) if len(args) > 1 else -1

    result = Gddl.getrandomlevelbytier(low,high)
    if not result:
        await gdsearch.finish("没有找到符合条件的demon关卡")

    level = getlevelinfo(result.ID)
    if level:
        await send_result(bot, event, level)
    else:
        await gdsearch.finish("发生未知错误。相关id: " + str(result.ID))
    await gdsearch.finish()

@gduser.handle()
async def handle_gduser(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:  # noqa: ARG001
    name = arg.extract_plain_text().strip()
    if not name:
        await gduser.finish("请输入想要搜索的用户名")
    user = get_user_by_name(name)
    if not user:
        await gduser.finish("没有找到对应的用户")
    user_basic_info = f"{user.user_name}\n{user.stars}⭐ {user.moons}🌙 {user.demons_count}👿 {str(user.creator_points) + '🔧' if user.creator_points else ''}"
    user_classic_nondemon = f"\nClassic: {user.classic_levels[0]}🤖 {user.classic_levels[1]}💙 {user.classic_levels[2]}💚 {user.classic_levels[3]}💛 {user.classic_levels[4]}🧡 {user.classic_levels[5]}💜;\n{user.classic_levels[6]} Daily; {user.classic_levels[7]} Gauntlet" if user.classic_levels else ""  # noqa: E501
    user_plat_nondemon = f"\nPlatformer: {user.platformer_levels[0]}🤖 {user.platformer_levels[1]}💙 {user.platformer_levels[2]}💚 {user.platformer_levels[3]}💛 {user.platformer_levels[4]}🧡 {user.platformer_levels[5]}💜" if user.platformer_levels else ""  # noqa: E501
    user_demon = f"\nClassic Demons: {user.demons_breakdown[0]} / {user.demons_breakdown[1]} / {user.demons_breakdown[2]} / {user.demons_breakdown[3]} / {user.demons_breakdown[4]};\n{user.demons_breakdown[10]} Weekly; {user.demons_breakdown[11]} Gauntlet\nPlatformer Demons: {user.demons_breakdown[5]} / {user.demons_breakdown[6]} / {user.demons_breakdown[7]} / {user.demons_breakdown[8]} / {user.demons_breakdown[9]}" if user.demons_breakdown else ""  # noqa: E501
    user_info = user_basic_info + user_classic_nondemon + user_plat_nondemon + user_demon
    await gduser.finish(user_info)

@gdsearchhelp.handle()
async def handle_gdsearchhelp() -> None:
    HELP_STR = """使用*gdsearch 关卡名或id 以搜索关卡
数据来源包括GDDL NLW等chart AREDL
以及Plat difficulty chart等plat chart
可以使用*references (gddl/nlw/plat)查询对应的参考线
"""  # noqa: N806
    #那几个references的实现我扔给xiaozubot_help模块了
    await gdsearchhelp.finish(HELP_STR)

update_cmd = on_command("gdsearch_update", permission=SUPERUSER, priority=1, block=True)

from .updater.runner import run_all_async


@update_cmd.handle()
async def _handle(event: MessageEvent):  # noqa: ARG001
    await update_cmd.send("🚀 开始执行手动更新...")
    try:
        result = await run_all_async()
    except Exception as e:  # noqa: BLE001
        await update_cmd.finish(f"❌ 更新失败\n{e}")
    else:
        # 抓完立刻重载，这样不用重启就能查到新数据
        await asyncio.to_thread(reload_all)
        await update_cmd.finish(f"✅ 更新完成，缓存已重载\n{result}")
