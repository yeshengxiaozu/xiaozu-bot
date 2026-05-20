from .aredlapi import Aredl
from .gdapi import GDLevel
from .gddlapi import Gddl
from .nlwapi import Nlw
from .platapi import Platapi

from pathlib import Path
import json

with Path.open(Path(__file__).parent / "data" / "randomthings.json") as f:
    random_yappin = json.load(f)

def _format_demon_message(level_info: GDLevel) -> str:  # noqa: C901
    """对demon关构造文本部分的信息字符串"""
    level_id = level_info.level_id
    gddl_info = Gddl.getlevelbyid(level_id)
    aredl_info = Aredl.getlevelbyid(level_id)
    nlw_info = Nlw.getlevelbyid(level_id)
    creator = level_info.creator_name
    msgstr = f"{level_info.level_name} {'by ' + creator if creator else ''}"

    if nlw_info and nlw_info.creator and (
        not creator
        or nlw_info.creator.strip().lower() != creator.strip().lower()
    ):
        msgstr += f" (by {nlw_info.creator})"

    msgstr += f" ({level_info.difficulty_label()})"

    length_info = (f"Length: {nlw_info.length}" if nlw_info and nlw_info.length else "")
    msgstr += f"\nID: {level_info.level_id} {length_info}"

    if gddl_info:
        if gddl_info.Rating:
            rating_2p = ""
            if gddl_info.Meta.IsTwoPlayer and gddl_info.TwoPlayerRating:
                rating_2p = f" / {round(gddl_info.TwoPlayerRating, 2)}(2p)"
            msgstr += (
                f"\nGDDL Rating: {round(gddl_info.Rating, 2)}{rating_2p}"
                f" ({gddl_info.RatingCount})"
            )

        if gddl_info.Enjoyment:
            enjoy_2p = ""
            if gddl_info.Meta.IsTwoPlayer and gddl_info.TwoPlayerEnjoyment:
                enjoy_2p = f" / {round(gddl_info.TwoPlayerEnjoyment, 2)}(2p)"
            msgstr += (
                f"\nGDDL Enjoyment: {round(gddl_info.Enjoyment, 2)}{enjoy_2p}"
                f" ({gddl_info.EnjoymentCount})"
            )

        if gddl_info.Tags:
            tags_str = ", ".join(
                f"{tag['Name']}({tag['Count']})" for tag in gddl_info.Tags[:3]
            )
            msgstr += f"\nGDDL Tags: {tags_str}"

    if aredl_info:
        tags_str = (
            ", ".join(aredl_info.tags) if aredl_info.tags else "Unknown"
        )
        if aredl_info.legacy:
            msgstr += f"\nAREDL #Legacy ({tags_str})"
        else:
            msgstr += f"\nAREDL #{aredl_info.position} ({tags_str})"

    if nlw_info:
        skillset = f"({nlw_info.skillset})" if nlw_info.skillset else ""
        msgstr += (
            f"\n{nlw_info.source} Tier: {nlw_info.tier} {skillset}"
        )

    if aredl_info and aredl_info.edel_enjoyment:
        msgstr += f"\nEDEL Enjoyment: {aredl_info.edel_enjoyment}"

    return msgstr


def _format_pemon_message(level_info: GDLevel) -> str:  # noqa: C901, PLR0912
    """对pemon关构造文本部分的信息字符串"""
    level_id = level_info.level_id
    plat_info = Platapi.getlevelbyid(level_info.level_id)
    gddl_info = Gddl.getlevelbyid(level_id)
    aredl_info = Aredl.getlevelbyid(level_id)
    nlw_info = Nlw.getlevelbyid(level_id)
    creator = level_info.creator_name

    _firstline = f"{level_info.level_name} {'by ' + creator if creator else ''}"

    if nlw_info and nlw_info.creator and (
        not creator
        or nlw_info.creator.strip().lower() != creator.strip().lower()
    ):
        _firstline += f" (by {nlw_info.creator})"
    _firstline += f" ({level_info.difficulty_label()})"

    lines = [_firstline]

    check_info = (f"Checkpoints: {nlw_info.checkpoints}" if nlw_info and nlw_info.checkpoints else "")
    lines.append(f"ID: {level_info.level_id} {check_info}")

    if plat_info and plat_info.tier:
        lines.append(f"Difficulty：{plat_info.tier}")

    rank_parts = []
    if plat_info and plat_info.tpl:
        rank_parts.append(f"{plat_info.tpl}(TPL)")
    if plat_info and plat_info.pemonlist:
        rank_parts.append(f"{plat_info.pemonlist}(Pemonlist)")
    if aredl_info:
        rank_parts.append(f"{aredl_info.position}(AREDL)")
    if rank_parts:
        lines.append(f"Rank：{'/'.join(rank_parts)}")

    if plat_info and plat_info.tags:
        lines.append(f"Tags：{', '.join(plat_info.tags)}")

    enjoyment_parts = []
    if plat_info and plat_info.enjoyment is not None:
        enjoyment_parts.append(f"{plat_info.enjoyment}(PEL)")
    if aredl_info and aredl_info.edel_enjoyment is not None:
        enjoyment_parts.append(f"{round(aredl_info.edel_enjoyment, 0)}(EDEL)")
    if gddl_info and gddl_info.Enjoyment:
        enjoyment_parts.append(f"{round(gddl_info.Enjoyment,2)}(GDDL)")
    if enjoyment_parts:
        lines.append(f"Enjoyment: {'/'.join(enjoyment_parts)}")

    if aredl_info:
        tags_str = ", ".join(aredl_info.tags) if aredl_info.tags else "Unknown"
        if aredl_info.legacy:
            lines.append(f"\nAREDL #Legacy ({tags_str})")
        else:
            lines.append(f"\nAREDL #{aredl_info.position} ({tags_str})")

    if nlw_info:
        skillset = f"({nlw_info.skillset})" if nlw_info.skillset else ""
        lines.append(f"\n{nlw_info.source} Tier: {nlw_info.tier} {skillset}")

    if plat_info and plat_info.derived_levels:
        for child in Platapi.getderivedlevels(plat_info):
            lines.append(f"-- {child.name}")
            if child.tier:
                lines.append(f"Difficulty: {child.tier}")
            if child.enjoyment is not None:
                lines.append(f"PEL Enjoyment: {child.enjoyment}")

    return "\n".join(lines)


def _format_non_demon_message(level_info: GDLevel) -> str:
    """对nondemon关构造文本部分的信息字符串"""
    level_id = level_info.level_id
    creator = level_info.creator_name
    lines = [f"{level_info.level_name} {'by ' + creator if creator else ''}"]

    difficulty_label = level_info.difficulty_label()
    lines.append(f"Difficulty: {difficulty_label}")

    plat_info = Platapi.getlevelbyid(level_id)
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

    if plat_info and plat_info.derived_levels:
        for child in Platapi.getderivedlevels(plat_info):
            lines.append(f"-- {child.name}")
            if child.tier:
                lines.append(f"Difficulty: {child.tier}")
            if child.enjoyment is not None:
                lines.append(f"PEL Enjoyment: {child.enjoyment}")
    return "\n".join(lines)


def _format_demon_image_text(level_info: GDLevel) -> str:
    """对demon关构造图片部分的信息字符串"""
    level_id = level_info.level_id
    nlw_info = Nlw.getlevelbyid(level_id)
    aredl_info = Aredl.getlevelbyid(level_id)
    parts = []
    parts.append(f"Song: [{level_info.song_id}] {level_info.song_name} ({level_info.song_author})<br>" + f"Description: {level_info.description}")
    if nlw_info and nlw_info.description:
        parts.append(
            f"{nlw_info.source} Description: {nlw_info.description}"
        )
    if aredl_info and aredl_info.description:
        parts.append(f"AREDL Description: {aredl_info.description}")
    if str(level_id) in random_yappin:
        parts.append(f"Random Yaps: {random_yappin[str(level_id)]}")
    return "<p>".join(parts)


def _format_pemon_image_text(level_info: GDLevel) -> str:
    """对pemon关构造图片部分的信息字符串"""
    level_id = level_info.level_id
    nlw_info = Nlw.getlevelbyid(level_id)
    aredl_info = Aredl.getlevelbyid(level_id)
    parts = []
    parts.append(f"Song: [{level_info.song_id}] {level_info.song_name} ({level_info.song_author})<br>" + f"Description: {level_info.description}")
    if nlw_info and nlw_info.description:
        parts.append(f"{nlw_info.source} Description: {nlw_info.description}")
    if aredl_info and aredl_info.description:
        parts.append(f"AREDL Description: {aredl_info.description}")
    if str(level_id) in random_yappin:
        parts.append(f"Random Yaps: {random_yappin[str(level_id)]}")
    return "<p>".join(parts)
