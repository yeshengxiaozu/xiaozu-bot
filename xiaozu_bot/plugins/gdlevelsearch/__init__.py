from .gddlapi import gddl
from .aredlapi import aredl
from .nlwapi import nlw
from .imageinfo import *
import requests
import asyncio
from .imageinfo import send_ttp


def get_creator(id):
    try:
        data = requests.get(f"https://history.geometrydash.eu/api/v1/level/{id}")
        return data.json()["cache_username"]
    except:
        return None


class LevelInfo:
    def __init__(self, gddl_info, aredl_info=None, nlw_info=None, creatorname=None):
        # 基础信息
        self.id = gddl_info.ID
        self.name = gddl_info.Meta.Name
        self.creator = creatorname if creatorname else (nlw_info.creator if nlw_info else None)
        self.creator_sheet = nlw_info.creator if nlw_info else None
        self.description = gddl_info.Meta.Description
        self.length = gddl_info.Meta.Length
        self.length_exact = nlw_info.length if nlw_info else None
        self.checkpoints = nlw_info.checkpoints if nlw_info else None
        self.difficulty = gddl_info.Meta.Difficulty
        self.songid = gddl_info.Meta.Song.ID
        self.songname = gddl_info.Meta.Song.Name
        self.songauthor = gddl_info.Meta.Song.Author
        self.is2p = gddl_info.Meta.IsTwoPlayer

        # GDDL 数据
        self.gddl_rating = gddl_info.Rating
        self.gddl_enjoyment = gddl_info.Enjoyment
        self.gddl_ratingcount = gddl_info.RatingCount
        self.gddl_enjoymentcount = gddl_info.EnjoymentCount
        self.gddl_2prating = gddl_info.TwoPlayerRating
        self.gddl_2penjoyment = gddl_info.TwoPlayerEnjoyment
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

    def __str__(self):
        return self.totext()

    def totext(self):
        lines = []

        # 标题行
        creator_str = f" by {self.creator}" if self.creator else ""
        demon_type = "Demon" if self.length < 6 else "Pemon"
        lines.append(f"ID: {self.id}")
        lines.append(f"{self.name}{creator_str} ({self.difficulty} {demon_type})")

        # 描述与歌曲
        lines.append(f"Description: {self.description}")
        lines.append(f"Song: [{self.songid}] {self.songname} ({self.songauthor})")
        lines.append("")  # 空行，保持原输出风格

        # GDDL 评分与享受度（含双人支持）
        if self.gddl_rating:
            rating_2p = self._format_2p(self.gddl_rating, self.gddl_2prating)
            enjoy_2p = self._format_2p(self.gddl_enjoyment, self.gddl_2penjoyment)
            lines.append(
                f"GDDL Rating: {round(self.gddl_rating, 2)}{rating_2p} ({self.gddl_ratingcount})"
            )
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
            tags_str = ", ".join(self.aredl_tags)
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
        if False and (self.aredl_vid or self.nlw_vid or self.gddl_vid):
            lines.append("Videos:")
            if self.gddl_vid:
                lines.append(f"GDDL: https://youtu.be/{self.gddl_vid}")
            if self.nlw_vid:
                lines.append(f"{self.spredsheet_souce}: {self.nlw_vid}")
            if self.aredl_vid:
                lines.append(f"AREDL: {self.aredl_vid}")

        return "\n".join(lines)

    def _format_2p(self, single_value, two_p_value):
        """格式化双人评分后缀，无数据时返回空字符串"""
        if self.is2p and two_p_value:
            return f" / {round(two_p_value, 2)}(2p)"
        return ""


def getlevelinfo(id) -> LevelInfo:
    gddl_info = gddl.getlevelbyid(id)
    if not gddl_info:
        return None

    aredl_info = aredl.getlevelbyid(id)
    nlw_info = nlw.getlevelbyid(id)
    level_info = LevelInfo(gddl_info, aredl_info, nlw_info, get_creator(id))
    level_info.gddl_tags = gddl.getleveltags(id)
    return level_info


from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.params import CommandArg, ArgPlainText
from nonebot.rule import Rule

async def send_result(bot: Bot, event: Event, level_info: LevelInfo):
    # 主信息块
    msgstr = f'{level_info.name} {"by " + level_info.creator if level_info.creator else ""}'

    # 创建者别名（仅当与主创作者不同时显示）
    if level_info.creator_sheet and (
        not level_info.creator
        or level_info.creator_sheet.strip().lower() != level_info.creator.strip().lower()
    ):
        msgstr += f" (by {level_info.creator_sheet})"

    demon_type = "Demon" if level_info.length < 6 else "Pemon"
    msgstr += f" ({level_info.difficulty} {demon_type})"

    # ID、长度、检查点
    length_info = f"Length: {level_info.length_exact}" if level_info.length_exact else ""
    check_info = f"Checkpoints: {level_info.checkpoints}" if level_info.checkpoints else ""
    msgstr += f'\nID: {level_info.id} {length_info}{" " if length_info and check_info else ""}{check_info}'

    # GDDL 评分（与 totext 逻辑一致）
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
        tags_str = ", ".join(level_info.aredl_tags)
        if level_info.aredl_legacy:
            msgstr += f"\nAREDL #Legacy ({tags_str})"
        else:
            msgstr += f"\nAREDL #{level_info.aredl_position} ({tags_str})"

    if level_info.nlw_tier:
        skillset = f"({level_info.nlw_skillset})" if level_info.nlw_skillset else ""
        msgstr += f"\n{level_info.spredsheet_souce} Tier: {level_info.nlw_tier}{skillset}"

    if level_info.edel_enjoyment and level_info.edel_enjoyment > 0:
        msgstr += f"\nEDEL Enjoyment: {level_info.edel_enjoyment}"

    await bot.send(event=event, message=msgstr)

    # 额外描述块（图片化发送）
    desc_parts = [
        f'Song: [{level_info.songid}] {level_info.songname} ({level_info.songauthor})<br>Description: {level_info.description}'
    ]
    if level_info.nlw_description:
        desc_parts.append(
            f"{level_info.spredsheet_souce} Description: {level_info.nlw_description}"
        )
    if level_info.aredl_description:
        desc_parts.append(f"AREDL Description: {level_info.aredl_description}")
    await send_ttp(bot, event, "<p>".join(desc_parts))


gdsearch = on_command("gdsearch")

# 搜索缓存与超时
search_cache = {}
timeout_tasks = {}


def has_cache(event: MessageEvent):
    return str(event.get_user_id()) in search_cache


rule_cache = Rule(has_cache)
gdsearchselect = on_message(rule_cache, priority=100, block=False)


async def clear_search_cache(bot: Bot, event: Event, user_id: str):
    """30秒后自动清除搜索缓存"""
    await asyncio.sleep(30)
    if user_id in search_cache:
        del search_cache[user_id]
    if user_id in timeout_tasks:
        del timeout_tasks[user_id]
    await bot.send(event, "输入超时,请重新再试")


@gdsearch.handle()
async def handle_gdsearch(bot: Bot, event: Event, arg: Message = CommandArg()):
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
    if len(name) > 2 and name.isdigit():
        level = getlevelinfo(int(name))
        if level:
            await send_result(bot, event, level)
        else:
            await gdsearch.finish("不存在符合这个id的demon关卡")
        return

    # 名称搜索
    levels = gddl.getlevelsbyname(name)
    if levels is None:
        await gdsearch.finish("查询过程中发生错误。")

    levels = [
        level for level in levels
        if level and level.Meta.Name.strip().lower() == name.lower()
    ]
    if not levels:
        await gdsearch.finish(f"没有找到名为 '{name}' 的demon关卡")

    if len(levels) == 1:
        await send_result(bot, event, getlevelinfo(int(levels[0].ID)))
        await gdsearch.finish()

    # 多结果缓存
    search_cache[user_id] = levels
    timeout_tasks[user_id] = asyncio.create_task(
        clear_search_cache(bot, event, user_id)
    )

    msgstr = f"找到 {len(levels)} 个名为 '{name}' 的demon关卡："
    for i, level in enumerate(levels, start=1):
        rating = round(level.Rating, 2) if level.Rating else "Na"
        enjoy = round(level.Enjoyment, 2) if level.Enjoyment else "Na"
        msgstr += (
            f"\n{i}. {level.Meta.Difficulty} Demon, "
            f"tier {rating}, enj {enjoy} "
            f"(ID: {level.ID})"
        )
    msgstr += "\n输入序号以选中关卡,输入“结束”以中止搜索"
    await gdsearch.finish(msgstr)


@gdsearchselect.handle()
async def handle_choice(bot: Bot, event: Event):
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
    levels = search_cache[user_id]
    if index < 1 or index > len(levels):
        await gdsearchselect.finish("请输入正确的序号")

    level = levels[index - 1]
    # 清理缓存
    search_cache.pop(user_id, None)
    if user_id in timeout_tasks:
        timeout_tasks[user_id].cancel()
        del timeout_tasks[user_id]

    await send_result(bot, event, getlevelinfo(int(level.ID)))
    await gdsearchselect.finish()