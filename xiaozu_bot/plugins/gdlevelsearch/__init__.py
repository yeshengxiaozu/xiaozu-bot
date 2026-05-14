import asyncio
from dataclasses import dataclass
from typing import Optional

import requests
from nonebot import logger

from .aredlapi import Aredl, AREDLLevel, aredllevels
from .gdapi import GDLevel, get_level_by_id
from .gddlapi import Gddl, GDDLLevel
from .imageinfo import send_ttp
from .nlwapi import Level as NLWLevel
from .nlwapi import Nlw, hdslevels, idslevels, lwlevels, nlwlevels
from .platapi import getderivedlevels, platdata_main_entries
from .platapi import getlevelbyid as get_plat_level_by_id

#i have no idea is this amount of exposal is bad but looked it worked


#fallback function since I should already get it using gdapi
def get_creator(level_id: int) -> Optional[str]:
    logger.warning("get_creator got called: " + str(level_id))
    try:
        data = requests.get(f"https://history.geometrydash.eu/api/v1/level/{level_id}", timeout = 10)
        return data.json()["cache_username"]
    except Exception:  # noqa: BLE001
        return None

#nice little function that extract exactly what i need
def get_difficulty(level_id: int) -> Optional[str]:
    logger.info("get_difficulty got called: " + str(level_id))
    try:
        data = get_level_by_id(level_id)
    except Exception:  # noqa: BLE001
        return None
    return data.difficulty_label() if data else None

#constrants
DEMON_STARS = 10
LENGTH_PLAT = 5

class LevelInfo:
    def __init__(
        self,
        gdlevel: GDLevel,
        gddl_info: Optional[GDDLLevel] = None,
        aredl_info: Optional[AREDLLevel] = None,
        nlw_info: Optional[NLWLevel] = None,
        creatorname: Optional[str] = None,
    ) -> None:
        # 基础信息
        self.id = int(gdlevel.level_id) if gdlevel and gdlevel.level_id is not None else None
        self.name = gdlevel.level_name if gdlevel else None
        self.creator_name = creatorname or (gdlevel.creator_name if gdlevel else None)
        self.creator = self.creator_name or (nlw_info.creator if nlw_info else None)
        self.creator_sheet = nlw_info.creator if nlw_info else None
        self.description = gdlevel.description if gdlevel else None
        self.length = (
            gdlevel.length
            if gdlevel and gdlevel.length is not None
            else (gddl_info.Meta.Length if gddl_info and gddl_info.Meta else None)
        )
        self.stars = gdlevel.stars if gdlevel else None
        self.demon_difficulty = gdlevel.demon_difficulty if gdlevel else None
        self.length_exact = nlw_info.length if nlw_info else None
        self.checkpoints = nlw_info.checkpoints if nlw_info else None
        self.difficulty = gddl_info.Meta.Difficulty if gddl_info and gddl_info.Meta else None

        self.songid = gddl_info.Meta.Song.ID if gddl_info and gddl_info.Meta and gddl_info.Meta.Song else (gdlevel.song_id if gdlevel else None)
        self.songname = gddl_info.Meta.Song.Name if gddl_info and gddl_info.Meta and gddl_info.Meta.Song else (gdlevel.song_name if gdlevel else None)
        self.songauthor = gddl_info.Meta.Song.Author if gddl_info and gddl_info.Meta and gddl_info.Meta.Song else (gdlevel.song_author if gdlevel else None)
        self.is2p = gddl_info.Meta.IsTwoPlayer if gddl_info and gddl_info.Meta else gdlevel.is_two_player if gdlevel else None

        # GDDL 数据
        self.gddl_rating = gddl_info.Rating if gddl_info else None
        self.gddl_enjoyment = gddl_info.Enjoyment if gddl_info else None
        self.gddl_ratingcount = gddl_info.RatingCount if gddl_info else None
        self.gddl_enjoymentcount = gddl_info.EnjoymentCount if gddl_info else None
        self.gddl_2prating = gddl_info.TwoPlayerRating if gddl_info else None
        self.gddl_2penjoyment = gddl_info.TwoPlayerEnjoyment if gddl_info else None
        self.gddl_tags = []  # 通过 getlevelinfo 填充

        # AREDL 数据
        self.aredl_position = aredl_info.position if aredl_info else None
        self.aredl_legacy = aredl_info.legacy if aredl_info else None
        self.aredl_tags = aredl_info.tags if aredl_info else None
        self.aredl_description = aredl_info.description if aredl_info else None
        self.edel_pending = aredl_info.is_edel_pending if aredl_info else None
        self.edel_enjoyment = aredl_info.edel_enjoyment if aredl_info else None

        # NLW / 其他来源数据
        self.spredsheet_souce = nlw_info.source if nlw_info else None
        self.nlw_tier = nlw_info.tier if nlw_info else None
        self.nlw_skillset = nlw_info.skillset if nlw_info else None
        self.nlw_description = nlw_info.description if nlw_info else None

        # 视频链接（当前未启用）
        # self.gddl_vid = gddl_info.Showcase if gddl_info.Showcase else None
        # self.nlw_vid = nlw_info.video if nlw_info else None
        # self.aredl_vid = aredl_info.verificationurl if aredl_info else None

    def __str__(self) -> str:
        return self.totext()

    def totext(self) -> str:
        lines = []

        # 标题行
        creator_str = f" by {self.creator}" if self.creator else ""
        difficulty_label = self.difficulty_label()
        lines.append(f"ID: {self.id}")
        lines.append(f"{self.name}{creator_str} ({difficulty_label})")

        # 描述与歌曲
        lines.append(f"Description: {self.description}")
        lines.append(f"Song: [{self.songid}] {self.songname} ({self.songauthor})")
        lines.append("")  # 空行，保持原输出风格

        # GDDL 评分与享受度（含双人支持）
        if self.gddl_rating:
            rating_2p = self._format_2p(self.gddl_2prating)
            lines.append(
                f"GDDL Rating: {round(self.gddl_rating, 2)}{rating_2p} ({self.gddl_ratingcount})"
            )
        if self.gddl_enjoyment:
            enjoy_2p = self._format_2p(self.gddl_2penjoyment)
            lines.append(
                f"GDDL Enjoyment: {round(self.gddl_enjoyment, 2)}{enjoy_2p} ({self.gddl_enjoymentcount})"
            )

        # GDDL 标签（最多三个）
        if self.gddl_tags:
            tags_str = ", ".join(
                f"{tag['Name']}({tag['Count']})" for tag in self.gddl_tags[:3]
            )
            lines.append(f"GDDL Tags: {tags_str}")

        # AREDL 信息
        if self.aredl_position:
            tags_str = ", ".join(self.aredl_tags) if self.aredl_tags else "Unknown"
            if self.aredl_legacy:
                lines.append(f"AREDL #Legacy ({tags_str})")
            else:
                lines.append(f"AREDL #{self.aredl_position} ({tags_str})")

        # NLW Tier / 技能组
        if self.nlw_tier:
            skillset = f"({self.nlw_skillset})" if self.nlw_skillset else ""
            lines.append(f"{self.spredsheet_souce} Tier: {self.nlw_tier}{skillset}")

        # EDEL Enjoyment
        if self.edel_enjoyment and self.edel_enjoyment > 0:
            lines.append(f"EDEL Enjoyment: {self.edel_enjoyment}")

        # 详细描述（NLW & AREDL）
        if self.nlw_description:
            lines.append(f"{self.spredsheet_souce} Description: {self.nlw_description}")
        if self.aredl_description:
            lines.append(f"AREDL Description: {self.aredl_description}")

        # 视频链接（群聊中禁用，保留原逻辑）
        """
        if False and (self.aredl_vid or self.nlw_vid or self.gddl_vid):
            lines.append("Videos:")
            if self.gddl_vid:
                lines.append(f"GDDL: https://youtu.be/{self.gddl_vid}")
            if self.nlw_vid:
                lines.append(f"{self.spredsheet_souce}: {self.nlw_vid}")
            if self.aredl_vid:
                lines.append(f"AREDL: {self.aredl_vid}")
        """
        return "\n".join(lines)

    def _format_2p(self, two_p_value: Optional[float]):
        """格式化双人评分后缀，无数据时返回空字符串"""
        if self.is2p and two_p_value:
            return f" / {round(two_p_value, 2)}(2p)"
        return ""

    def difficulty_label(self) -> str:
        stars = int(self.stars) if self.stars is not None else None
        if stars is None:
            return self.difficulty or "Unknown"
        sign = "🌙" if self.is_pemon() else"⭐"

        if stars < DEMON_STARS: #nondemon
            return ["Unrated",f"1{sign}auto",f"2{sign}easy",f"3{sign}normal",f"4{sign}hard",
                    f"5{sign}hard",f"6{sign}harder",f"7{sign}harder",f"8{sign}insane",f"9{sign}insane"][stars]
        if self.demon_difficulty:
            return f"{['Hard','Unknown','Unknown','Easy','Medium','Insane','Extreme'][self.demon_difficulty]} {'Pemon' if self.is_pemon() else 'Demon'}"
            #bro what is rubtap doing it dont make sense
        if self.difficulty:
            return f"{self.difficulty} {'Pemon' if self.length == LENGTH_PLAT else 'Demon'}" #make sense
        return "10⭐demon"

    def is_pemon(self) -> bool:
        return int(self.length) == LENGTH_PLAT if self.length is not None else False

    def is_demon_detail(self) -> bool:
        return self.stars == DEMON_STARS and not self.is_pemon()


@dataclass
class SearchResult:
    id: int
    name: str
    creator: Optional[str] = None
    gddl_tier: Optional[str] = None
    difficulty: Optional[str] = None


def _add_search_result(results: dict[int, SearchResult], level_id: int, name: str,  # noqa: PLR0913
                        creator: Optional[str] = None, gddl_tier: Optional[str] = None, difficulty: Optional[str] = None):
    if level_id is None:
        return
    if level_id in results:
        item = results[level_id]
        if not item.creator and creator:
            item.creator = creator
        if not item.gddl_tier and gddl_tier:
            item.gddl_tier = gddl_tier
        return
    results[level_id] = SearchResult(level_id, name, creator, gddl_tier, difficulty)


def search_by_name(name: str) -> list[SearchResult]:  # noqa: C901, PLR0912
    normalized = name.strip().lower()
    results: dict[int, SearchResult] = {}

    # 1) GDDL exact match
    gddl_candidates = Gddl.getlevelsbyname(name) or []
    for level in gddl_candidates:
        if not level or not getattr(level, "Meta", None):
            continue
        if getattr(level.Meta, "Name", "").strip().lower() == normalized:
            _add_search_result(results, int(level.ID), level.Meta.Name, None, str(round(level.Rating,2)) if level.Rating else None,
                                level.Meta.Difficulty + (" Pemon" if level.is_pemon() else " Demon"))
            logger.info(f"Find a result in GDDL: tier {getattr(level, 'Rating', 'Unknown')}")


    # 2) AREDL exact match
    for level in aredllevels:
        if not level or not getattr(level, "name", None):
            continue
        if level.name.strip().lower() == normalized:
            _add_search_result(results, int(level.level_id), level.name, None, getattr(level, "gddl_tier", None), "Extreme Demon")
            logger.info("Find a result in AREDL: #" + str(level.position) or "Unknown")

    # 3) NLW exact match
    for level in (*nlwlevels, *idslevels, *lwlevels, *hdslevels):
        if not level or not getattr(level, "name", None):
            continue
        if level.name.strip().lower() == normalized:
            _add_search_result(results, int(level.id or "0"), level.name, getattr(level, "creator", None), None)
            logger.info(f"Find a result in {level.source}: " + str(level.tier) or "Unknown" + " Tier", None)

    # 4) Platdata exact match
    for level in (platdata_main_entries):
        if not level or not getattr(level, "name", None):
            continue
        if level.name.strip().lower() == normalized:
            _add_search_result(results, int(level.id), level.name, getattr(level, "creator", None), getattr(level, "tier", "").strip(), None)

    if results:
        return list(results.values())

    # No point to do these fallback thing since theres already a gdsearch bot that is better than mine
    # Fallback to gdapi exact match
    """
    try:
        rated = search_levels_by_name(name, star=True)
    except Exception:
        rated = []
    exact_rated = [lvl for lvl in (rated or []) if getattr(lvl, "level_name", "").strip().lower() == normalized]
    if exact_rated:
        return [SearchResult(int(lvl.level_id), lvl.level_name, None, None, "GDAPI") for lvl in exact_rated]

    try:
        unrated = search_levels_by_name(name, star=False)
    except Exception:
        unrated = []
    exact_unrated = [lvl for lvl in (unrated or []) if getattr(lvl, "level_name", "").strip().lower() == normalized]
    return [SearchResult(int(lvl.level_id), lvl.level_name, None, None, "GDAPI") for lvl in exact_unrated[:5]]
    """
    return []


def getlevelinfo(level_id: int) -> Optional[LevelInfo]:
    gdlevel = get_level_by_id(level_id)
    if not gdlevel:
        return None

    gddl_info = Gddl.getlevelbyid(level_id)
    aredl_info = Aredl.getlevelbyid(level_id)
    nlw_info = Nlw.getlevelbyid(level_id)
    creator_name = gdlevel.creator_name or get_creator(level_id)
    level_info = LevelInfo(gdlevel, gddl_info, aredl_info, nlw_info, creator_name)
    level_info.gddl_tags = Gddl.getleveltags(level_id) if gddl_info else []
    return level_info


from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.rule import Rule


def _format_demon_message(level_info: LevelInfo) -> str:  # noqa: C901
    creator = level_info.creator or level_info.creator_name
    msgstr = f'{level_info.name} {"by " + creator if creator else ""}'

    if level_info.creator_sheet and (
        not creator
        or level_info.creator_sheet.strip().lower() != creator.strip().lower()
    ):
        msgstr += f" (by {level_info.creator_sheet})"

    difficulty_label = level_info.difficulty_label()
    msgstr += f" ({difficulty_label})"

    length_info = f"Length: {level_info.length_exact}" if level_info.length_exact else ""
    check_info = f"Checkpoints: {level_info.checkpoints}" if level_info.checkpoints else ""
    msgstr += f'\nID: {level_info.id} {length_info}{" " if length_info and check_info else ""}{check_info}'

    if level_info.gddl_rating:
        rating_2p = ""
        if level_info.is2p and level_info.gddl_2prating:
            rating_2p = f" / {round(level_info.gddl_2prating, 2)}(2p)"
        msgstr += (
            f"\nGDDL Rating: {round(level_info.gddl_rating, 2)}{rating_2p}"
            f" ({level_info.gddl_ratingcount})"
        )

    if level_info.gddl_enjoyment:
        enjoy_2p = ""
        if level_info.is2p and level_info.gddl_2penjoyment:
            enjoy_2p = f" / {round(level_info.gddl_2penjoyment, 2)}(2p)"
        msgstr += (
            f"\nGDDL Enjoyment: {round(level_info.gddl_enjoyment, 2)}{enjoy_2p}"
            f" ({level_info.gddl_enjoymentcount})"
        )

    if level_info.gddl_tags:
        tags_str = ", ".join(
            f"{tag['Name']}({tag['Count']})" for tag in level_info.gddl_tags[:3]
        )
        msgstr += f"\nGDDL Tags: {tags_str}"

    if level_info.aredl_position:
        tags_str = ", ".join(level_info.aredl_tags) if level_info.aredl_tags else "Unknown"
        if level_info.aredl_legacy:
            msgstr += f"\nAREDL #Legacy ({tags_str})"
        else:
            msgstr += f"\nAREDL #{level_info.aredl_position} ({tags_str})"

    if level_info.nlw_tier:
        skillset = f"({level_info.nlw_skillset})" if level_info.nlw_skillset else ""
        msgstr += f"\n{level_info.spredsheet_souce} Tier: {level_info.nlw_tier}{skillset}"

    if level_info.edel_enjoyment and level_info.edel_enjoyment > 0:
        msgstr += f"\nEDEL Enjoyment: {level_info.edel_enjoyment}"

    return msgstr


def _format_pemon_message(level_info: LevelInfo) -> str:  # noqa: C901, PLR0912
    plat_info = get_plat_level_by_id(level_info.id)
    creator = level_info.creator or level_info.creator_name
    lines = [f"{level_info.name} by {creator} ({level_info.difficulty_label()})"] if creator else [f"{level_info.name} ({level_info.difficulty_label()})"]

    id_line = f"ID: {level_info.id}"
    if level_info.checkpoints is not None:
        id_line += f" Checkpoints: {level_info.checkpoints}"
    lines.append(id_line)

    if plat_info and plat_info.tier:
        lines.append(f"Difficulty：{plat_info.tier}")

    rank_parts = []
    if plat_info and plat_info.tpl:
        rank_parts.append(f"{plat_info.tpl}(TPL)")
    if plat_info and plat_info.pemonlist:
        rank_parts.append(f"{plat_info.pemonlist}(Pemonlist)")
    if level_info.aredl_position:
        rank_parts.append(f"{level_info.aredl_position}(AREDL)")
    if rank_parts:
        lines.append(f"Rank：{'/'.join(rank_parts)}")

    if plat_info and plat_info.tags:
        lines.append(f"Tags：{', '.join(plat_info.tags)}")

    enjoyment_parts = []
    if plat_info and plat_info.enjoyment is not None:
        enjoyment_parts.append(f"{plat_info.enjoyment}(PEL)")
    if level_info.edel_enjoyment is not None:
        enjoyment_parts.append(f"{round(level_info.edel_enjoyment,0)}(EDEL)")
    if enjoyment_parts:
        lines.append(f"Enjoyment: {'/'.join(enjoyment_parts)}")

    if level_info.nlw_tier:
        skillset = f"({level_info.nlw_skillset})" if level_info.nlw_skillset else ""
        lines.append(f"{level_info.spredsheet_souce} Tier: {level_info.nlw_tier}{skillset}")

    if plat_info and plat_info.derived_levels:
        for child in getderivedlevels(plat_info):
            lines.append(f"-- {child.name}")
            if child.tier:
                lines.append(f"Difficulty {child.tier}")
            if child.enjoyment is not None:
                lines.append(f"PEL Enjoyment: {child.enjoyment}")

    return "\n".join(lines)


def _format_non_demon_message(level_info: LevelInfo) -> str:
    creator = level_info.creator or level_info.creator_name
    header = f"{level_info.name}"
    if creator:
        header += f" by {creator}"
    lines = [header]

    difficulty_label = level_info.difficulty_label()
    lines.append(f"Difficulty: {difficulty_label}")

    plat_info = get_plat_level_by_id(level_info.id)
    if plat_info:
        plat_line = f"Plat: {plat_info.tier or 'Unknown'}"
        if plat_info.tpl:
            plat_line += f", TPL {plat_info.tpl}"
        if plat_info.pemonlist:
            plat_line += f", Pemonlist {plat_info.pemonlist}"
        lines.append(plat_line)
        if plat_info.tags:
            lines.append(f"Tags: {', '.join(plat_info.tags)}")
        if plat_info.enjoyment is not None:
            lines.append(f"Enjoyment: {plat_info.enjoyment}(PEL)")
        if plat_info.derived_from:
            parent = get_plat_level_by_id(plat_info.derived_from)
            if parent:
                lines.append(f"Derived from: {parent.name}")
    return "\n".join(lines)


def _format_pemon_image_text(level_info: LevelInfo) -> str:
    parts = [
        f"Song: [{level_info.songid}] {level_info.songname} ({level_info.songauthor})<br>Description: {level_info.description}",
    ]
    if level_info.nlw_description:
        parts.append(f"{level_info.spredsheet_souce} Description: {level_info.nlw_description}")
    if level_info.aredl_description:
        parts.append(f"AREDL Description: {level_info.aredl_description}")
    return "<p>".join(parts)


def _format_demon_image_text(level_info: LevelInfo) -> str:
    parts = [
        f"Song: [{level_info.songid}] {level_info.songname} ({level_info.songauthor})<br>Description: {level_info.description}",
    ]
    if level_info.nlw_description:
        parts.append(f"{level_info.spredsheet_souce} Description: {level_info.nlw_description}")
    if level_info.aredl_description:
        parts.append(f"AREDL Description: {level_info.aredl_description}")
    return "<p>".join(parts)


async def send_result(bot: Bot, event: Event, level_info: LevelInfo) -> None:
    if level_info.is_pemon():
        msgstr = _format_pemon_message(level_info)
        await bot.send(event=event, message=msgstr)
        await send_ttp(bot, event, _format_pemon_image_text(level_info))
    elif level_info.is_demon_detail():
        msgstr = _format_demon_message(level_info)
        await bot.send(event=event, message=msgstr)
        await send_ttp(bot, event, _format_demon_image_text(level_info))
    else:
        msgstr = _format_non_demon_message(level_info)
        await bot.send(event=event, message=msgstr)


gdsearch = on_command("gdsearch")

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
    if len(name) > 2 and name.isdigit():  # noqa: PLR2004 yes its a magic number but it help user
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
        tier_str = f" t{result.gddl_tier}" if result.gddl_tier else ""
        msgstr += f"\n{i}. {result.name}{creator_str}{difficulty_str}{tier_str} (ID: {result.id})"
    msgstr += "\n输入序号以选中关卡,输入“结束”以中止搜索"
    await gdsearch.finish(msgstr)


@gdsearchselect.handle()
async def handle_choice(bot: Bot, event: Event) -> None:
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
        await gdsearchselect.finish("发生未知错误。相关id: " + str(results[0].id))
    await gdsearchselect.finish()
