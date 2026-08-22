import asyncio
import io
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar, cast

from nonebot import logger
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..api.aredlapi import Aredl, AREDLLevel
from ..api.gdapi import GDLevel, get_level_by_id
from ..api.gddl_store import get_by_id
from ..api.gddlapi import Gddl, GDDLLevel, GDDLSearchEntry
from ..api.listsapi import Lists
from ..api.nlwapi import Level as NLWLevel
from ..api.nlwapi import Nlw
from ..api.platapi import Platapi, PlatInfo
from ..api.thumbnail import fetch_thumbnail
from ..paths import PLUGIN_DIR, RES_DIR

# Tier 颜色表
TIER_COLOR_MAP = {
    # for one and only NLW
    "Fuck": "#800000", "Beginner": "#3a86e4", "Easy": "#00fffe",
    "Medium": "#00ff37", "Hard": "#ffff3f", "Very Hard": "#ff992b",
    "Insane": "#ff031c", "Extreme": "#ff0cfb", "Remorseless": "#9d0afa",
    "Relentless": "#b287e8", "Terrifying": "#f19eea", "Catastrophic": "#ea6661",
    "Inexorable": "#ffc183", "Excruciating": "#ffe599", #Merciless?
    "Super Fucking Terrifying": "#000000",
    "Low End": "#00c0ed", "Low-Mid Range": "#00ff87", "Mid Range": "#ffcc34",
    "Mid-High Range": "#ff0580", "High End": "#a75df2",
    "Unknown": "#ffffff", "New Rates": "#ffffff", "Potential Extremes": "#ebebeb",
    # for IDS and HDS
    "Demote": "#3a86e4",
    "Legacy": "#808080",
    "Leaderboard Mods Wall Of Shame": "#980000",
    # for LW
    "Merciless": "#a7e58d", "Monstrous": "#5bad96",
    "Apocalyptic": "#528cb1","Demonic": "#6d6ab0", "Menacing": "#9452a2",
    "Unreal": "#913869", "Nightmare": "#832828", "Unfathomable":"#C76E00",
    # for Platinfo
    "1 - BEGINNER": "#7fb8ff", "2 - EASY": "#7fcbff", "3 - MODERATE": "#7fe8ff",
    "4 - INTERMEDIATE": "#7ffff9","5 - TOUGH": "#82ffc9", "6 - CHALLENGING": "#a9ff82",
    "7 - DIFFICULT": "#dbff7f", "8 - FORMIDABLE": "#fffd7f", "9 - CRUEL": "#ffe47f",
    "10 - INSANE": "#ffc37f", "11 - DEADLY": "#ffa57f", "12 - EXTREME": "#ff7f7f",
    "13 - TERRIFYING": "#ff7fb5",
}
DEFAULT_TIER_COLOR = "#ffffff"


def select_tags(level: PlatInfo) -> list[str]:
    return level.tags

# ----------------- 常量 -----------------


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    """加载字体，加载不了就退到 PIL 自带的默认字体。

    resources/ 没进仓库，新克隆一份是没有字体文件的；
    以前这里直接 truetype，缺文件就 OSError，整条出图路径全灭。
    宁可字丑也别整个功能不可用。
    """
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        logger.error(f"字体加载失败，退回默认字体：{path}")
        try:
            return cast("ImageFont.FreeTypeFont", ImageFont.load_default(size))
        except TypeError:  # Pillow < 10.1 的 load_default 不吃 size
            return cast("ImageFont.FreeTypeFont", ImageFont.load_default())

CANVAS_W = 1280
CANVAS_H = 720

PANEL_MAIN_WIDTH = 940
PANEL_MARGIN = 24
PANEL_RIGHT_OFFSET = 8
PANEL_BOTTOM_OFFSET = 24
PANEL_PAD = 28
PANEL_RADIUS = 24

SHADOW_OFFSET = 8
SHADOW_ALPHA = 120
SHADOW_BLUR = 12

PANEL_ALPHA = 230

FONT_PUSAB_TITLE = 60
FONT_PUSAB_SUB = 44
FONT_SANS_SMALL = 22

OUTLINE_TITLE = 4
OUTLINE_SUB = 3

SPACING_SMALL = 16
SPACING_TIER_ROW = 36
SPACING_SONG_LINE = 30

DIFF_SCALE = 1.0
DIFF_Y_EXTRA = 70

THUMB_W = 480
THUMB_H = 270
THUMB_RADIUS = 12
THUMB_SHADOW_OFFSET = 6
THUMB_SHADOW_ALPHA = 100
THUMB_SHADOW_BLUR = 6


SIDEBAR_X_OFFSET = 20
SIDEBAR_ALPHA = 230
SIDEBAR_TEXT_LEFT = 18
SIDEBAR_TEXT_RIGHT_MARGIN = 8
SIDEBAR_MIN_AVAIL_PX = 100
SIDEBAR_LINE_HEIGHT = 22
SIDEBAR_TOP_PAD = 20
SIDEBAR_BOTTOM_PAD = 20

ICON_NEGATIVE_MARGIN = 3
ICON_DEFAULT_H = 40
ICON_SPACING = 6
TITLE_FONT_SIZE = 36
CARD_LINE_FONT_SIZE = 30

DESIRED_BLOCK_HEIGHT = 157
CARD_WIDTH = 300

ICON_ALLOWED_EXTRA = 48
ICON_MIN_H = 12
ICON_MIN_SCALE = 0.4

CARD_PADDING_LEFT = 16
CARD_TOP_PADDING = 20
CARD_BETWEEN_TITLE_AND_LINES = 26
CARD_INTER_LINE_SPACING = 15
CARD_BOTTOM_PADDING = 15
CARD_EXTRA_SPACING = 4

CARD_BG_RADIUS = 12
CARD_BG_COLOR = (28, 28, 28, 220)

RIGHT_BG_SPACING = 20

# ---------- 配置 ----------
os.environ["NO_PROXY"] = "history.geometrydash.eu,geometrydash.eu"

# ---------------------------------------------------------------------
def draw_outlined_text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont,
                       fill: str = "white", outline: str = "black", outline_width: int = 4,
                       shadow: tuple | None = None) -> None:
    x, y = xy
    if shadow:
        sx, sy, scolor, soff = shadow
        draw.text((x + sx, y + sy), text, fill=scolor, font=font,
                  stroke_width=soff, stroke_fill=scolor)
    draw.text((x, y), text, fill=outline, font=font,
              stroke_width=outline_width, stroke_fill=outline)
    draw.text((x, y), text, fill=fill, font=font)


def rounded_image(im: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", im.size, 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle([0, 0, im.size[0], im.size[1]], radius=radius, fill=255)
    im = im.convert("RGBA")
    im.putalpha(mask)
    return im


def create_vertical_gradient(size: tuple[int,int], top_color: tuple[int,int,int], bottom_color: tuple[int,int,int]) -> Image.Image:
    w, h = size
    grad = Image.new("RGB", (w, h))
    draw_grad = ImageDraw.Draw(grad)
    tr, tg, tb = top_color
    br, bg, bb = bottom_color
    for i in range(h):
        # 高度为 1 时没有插值区间（h-1 == 0），取顶色 —— 任何高度下第 0 行都是顶色，
        # 1 px 高的渐变就是「只剩第 0 行」。高度为 0 时循环压根不进，宽度 0 / 负数
        # 由上面的 Image.new 自己管（负数它会抛 ValueError）。
        t = i / (h - 1) if h > 1 else 0.0
        r = int(tr + (br - tr) * t)
        g = int(tg + (bg - tg) * t)
        b = int(tb + (bb - tb) * t)
        draw_grad.line([(0, i), (w, i)], fill=(r, g, b))
    return grad


def wrap_text_by_width(text: str, max_width: int, font: ImageFont.FreeTypeFont) -> list:
    """按像素宽度贪心断行，返回逐行文本。

    几条约定，改之前先看清楚（侧边栏排版全靠它）：

    - 空行会保留成一条空行。调用方的 detail_text 是用 "\\n\\n" 分段的，
      那条空行就是段与段之间的视觉间隔，吞掉的话几段描述会糊在一起。
      只含空格的行同样算空行。整个 text 是空串时返回 []（没内容就没行）。
    - 连续空格会被压成一个，这是有意的：断行以后行首/行尾留着空格，
      画出来就是一段看不见的缩进，行与行对不齐。
    - 单个词超过 max_width 时逐字符拆。max_width 连一个字符都放不下时
      每行只能放一个字符（必然溢出），但不会因此吐出空行。
    """
    if not text:
        return []
    paragraphs = text.split("\n")
    result = []
    for para in paragraphs:
        if not para.strip():
            result.append("")
            continue
        words = para.split(" ")
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip() if current_line else word
            bbox = font.getbbox(test_line)
            if bbox[2] - bbox[0] <= max_width:
                current_line = test_line
            else:
                if current_line:
                    result.append(current_line)
                if font.getbbox(word)[2] - font.getbbox(word)[0] > max_width:
                    sub_line = ""
                    for ch in word:
                        sub_test = sub_line + ch
                        if font.getbbox(sub_test)[2] - font.getbbox(sub_test)[0] <= max_width:
                            sub_line = sub_test
                        else:
                            if sub_line:  # 宽度连一个字符都放不下时 sub_line 还是空的，别吐空行
                                result.append(sub_line)
                            sub_line = ch
                    current_line = sub_line
                else:
                    current_line = word
        if current_line:
            result.append(current_line)
    return result

import json

# NONG 歌曲索引。读不到就当空的用 —— 这只影响歌名显示，
# 不该因为一个缓存文件坏了就让整个插件加载失败。
nong_index: dict = {}
NONG_PATH = PLUGIN_DIR / "data" / "nong_index.json"
try:
    with NONG_PATH.open(encoding="utf-8") as f:
        nong_index = json.load(f)
except (OSError, json.JSONDecodeError):
    logger.warning(f"NONG index not available: {NONG_PATH}")

T = TypeVar("T")

def _optional_remote_result(
    value: T | BaseException,
    source: str,
) -> T | None:
    if isinstance(value, asyncio.CancelledError):
        raise value
    if isinstance(value, BaseException):
        if not isinstance(value, Exception):
            raise value
        logger.warning(f"{source} lookup failed while rendering: {value}")
        return None
    return value



@dataclass(slots=True)
class LevelRenderData:
    level_line: str = ""
    creator_line: str = ""
    id_line: str = ""
    rank_line: str = ""
    tier_prefix: str = ""
    tier_category_line: str = ""
    tier_color: str = ""
    skillset_line: str = ""
    song_line1: str = ""
    song_line2: str = ""
    diff_icon_path: Path = field(
        default_factory=lambda: RES_DIR / "diffIcon/diffIcon_0.png"
    )
    featured_fx_path: Path = field(default_factory=Path)
    line1: str = ""
    line2: str = ""
    tier_icon_path: Path = field(default_factory=lambda: RES_DIR / "tiers/tier_0.png")
    skill_icons: list[Path] = field(default_factory=list)
    detail_text: str = ""
    thumb_bytes: bytes | None = None
    derived_suffix: str = ""
    derived_difficulty: str = ""
    title_text: str = "GDDL"
    pusab_font_path: Path = field(default_factory=lambda: RES_DIR / "PUSAB.TTF")
    sans_font_path: Path = field(default_factory=lambda: RES_DIR / "ARIAL.TTF")
    left_bg_path: Path = field(default_factory=lambda: RES_DIR / "left_bg.png")
    right_bg_path: Path = field(default_factory=lambda: RES_DIR / "right_bg.png")


@dataclass(slots=True)
class FetchedData:
    level_id: int
    gdlevel: GDLevel | None = None
    gddl_info: GDDLLevel | GDDLSearchEntry | None = None
    gddl_tags: list[dict[str, Any]] | None = None
    thumb_bytes: bytes | None = None
    aredl_info: AREDLLevel | None = None
    nlw_info: NLWLevel | None = None
    plat_info: PlatInfo | None = None
    list_rank: str | None = None
    nong_song: dict | None = None


def _panel_rect() -> tuple[int, int, int, int]:
    return (
        PANEL_MARGIN,
        PANEL_MARGIN,
        PANEL_MAIN_WIDTH - PANEL_RIGHT_OFFSET,
        CANVAS_H - PANEL_BOTTOM_OFFSET,
    )


def _sidebar_rect() -> tuple[int, int, int, int]:
    sidebar_x = PANEL_MAIN_WIDTH + SIDEBAR_X_OFFSET
    return (sidebar_x, PANEL_MARGIN, CANVAS_W - PANEL_MARGIN, CANVAS_H - PANEL_BOTTOM_OFFSET)


def _draw_background(data: LevelRenderData) -> Image.Image:
    W, H = CANVAS_W, CANVAS_H  # noqa: N806
    base = create_vertical_gradient((W, H), (255, 180, 220), (255, 240, 250)).convert("RGBA")

    panel_mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(panel_mask).rounded_rectangle(_panel_rect(), radius=PANEL_RADIUS, fill=255)
    sidebar_mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sidebar_mask).rounded_rectangle(_sidebar_rect(), radius=PANEL_RADIUS, fill=255)

    left_bg_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    try:
        left_bg = Image.open(data.left_bg_path).convert("RGBA")
        if left_bg.size != (W, H):
            left_bg = left_bg.resize((W, H), Image.Resampling.LANCZOS)
        left_bg_layer.paste(left_bg, (0, 0), left_bg)
        ImageDraw.Draw(left_bg_layer)
    except Exception as e:
        logger.error("[gdlevelsearch.draw] ERROR: %s", e)
    left_bg_masked = Image.composite(left_bg_layer, Image.new("RGBA", (W, H), (0, 0, 0, 0)), panel_mask)

    right_bg_layer = create_vertical_gradient((W, H), (255, 255, 255), (255, 255, 255)).convert("RGBA")
    right_bg_masked = Image.composite(right_bg_layer, Image.new("RGBA", (W, H), (0, 0, 0, 0)), sidebar_mask)
    base = Image.alpha_composite(base, left_bg_masked)
    ImageDraw.Draw(base)
    base = Image.alpha_composite(base, right_bg_masked)
    ImageDraw.Draw(base)
    return base


def _draw_main_panel(img: Image.Image, data: LevelRenderData) -> Image.Image:
    draw = ImageDraw.Draw(img)
    font_title = _load_font(data.pusab_font_path, FONT_PUSAB_TITLE)
    font_sub = _load_font(data.pusab_font_path, FONT_PUSAB_SUB)
    font_small = _load_font(data.sans_font_path, FONT_SANS_SMALL)
    panel = _panel_rect()
    x = panel[0] + PANEL_PAD
    y = panel[1] + PANEL_PAD

    title_bbox = draw.textbbox((0, 0), data.level_line, font=font_title)
    title_h = int(title_bbox[3] - title_bbox[1])
    draw_outlined_text(draw, (x, y), data.level_line, font_title, fill="white", outline="black", outline_width=OUTLINE_TITLE)
    title_y = y
    title_w = draw.textbbox((0, 0), data.level_line, font=font_title)[2]

    if data.derived_suffix or data.derived_difficulty:
        derived_lines = []
        if data.derived_suffix:
            derived_lines.append((data.derived_suffix, (255, 255, 255)))
        if data.derived_difficulty:
            derived_lines.append((data.derived_difficulty, TIER_COLOR_MAP.get(data.derived_difficulty, DEFAULT_TIER_COLOR)))
        derived_line_h = draw.textbbox((0, 0), "A", font=font_sub)[3]
        max_line_w = max(draw.textbbox((0, 0), text, font=font_sub)[2] for text, _ in derived_lines)
        card_w = max_line_w + 24
        card_h = len(derived_lines) * derived_line_h + 24 + max(0, len(derived_lines) - 1) * SPACING_SMALL
        card_x = panel[2] - PANEL_PAD - card_w
        card_y = title_y - 16
        if title_w + card_w > PANEL_MAIN_WIDTH:
            card_y += title_h
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle([card_x, card_y, card_x + card_w, card_y + card_h], fill=(0, 0, 0, 51))
        img = Image.alpha_composite(img, overlay)
        draw = ImageDraw.Draw(img)
        text_x = card_x + 12
        text_y = card_y + 16
        for text, fill in derived_lines:
            draw_outlined_text(draw, (text_x, text_y), text, fill=fill, font=font_sub, outline_width=OUTLINE_SUB)
            text_y += derived_line_h + SPACING_SMALL

    y += title_h + SPACING_SMALL
    for text in (data.creator_line, data.id_line, data.rank_line):
        draw_outlined_text(draw, (x, y), text, font_sub, fill="white", outline="black", outline_width=OUTLINE_SUB)
        y += draw.textbbox((0, 0), text, font=font_sub)[3] + SPACING_SMALL

    if data.tier_prefix:
        draw_outlined_text(draw, (x, y), data.tier_prefix, font_sub, fill="white", outline="black", outline_width=OUTLINE_SUB)
        prefix_w = draw.textbbox((0, 0), data.tier_prefix, font=font_sub)[2]
    else:
        prefix_w = 0
    draw_outlined_text(draw, (x + prefix_w, y), data.tier_category_line, font_sub, fill=data.tier_color, outline="black", outline_width=OUTLINE_SUB)
    y += SPACING_TIER_ROW

    skillset_outline_width = 3
    draw_outlined_text(draw, (x, y), data.skillset_line, font_small, fill="black", outline="white", outline_width=skillset_outline_width)
    y += draw.textbbox((0, 0), data.skillset_line, font=font_small)[3] + SPACING_SMALL
    draw_outlined_text(draw, (x, y), data.song_line1, font_small, fill="black", outline="white", outline_width=skillset_outline_width)
    y += SPACING_SONG_LINE
    draw_outlined_text(draw, (x, y), data.song_line2, font_small, fill="black", outline="white", outline_width=skillset_outline_width)

    diff_icon_img = None
    try:
        diff_icon_img = Image.open(data.diff_icon_path).convert("RGBA")
        orig_w, orig_h = diff_icon_img.size
        diff_target_size = (max(1, int(orig_w * DIFF_SCALE)), max(1, int(orig_h * DIFF_SCALE)))
    except Exception:
        diff_target_size = (max(1, int(320 * DIFF_SCALE)), max(1, int(280 * DIFF_SCALE)))
    diff_w, diff_h = diff_target_size
    diff_x = panel[2] - PANEL_PAD - diff_w
    diff_y = panel[1] + PANEL_PAD + title_h + DIFF_Y_EXTRA

    if data.featured_fx_path:
        try:
            fx_img = Image.open(data.featured_fx_path).convert("RGBA").resize(diff_target_size, Image.Resampling.LANCZOS)
            img.paste(fx_img, (diff_x, diff_y), fx_img)
            draw = ImageDraw.Draw(img)
        except Exception:
            pass
    if diff_icon_img is not None:
        try:
            diff_icon_img = diff_icon_img.resize(diff_target_size, Image.Resampling.LANCZOS)
            img.paste(diff_icon_img, (diff_x, diff_y), diff_icon_img)
            draw = ImageDraw.Draw(img)
        except Exception:
            diff_icon_img = None
    if diff_icon_img is None:
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([diff_x, diff_y, diff_x + diff_w, diff_y + diff_h], radius=12, outline=(200, 60, 60), width=3)
        draw_outlined_text(draw, (diff_x + 8, diff_y + diff_h // 2 - 24), "No\nImage", font_sub, fill="red", outline="white", outline_width=2)
    return img


def _draw_thumbnail(img: Image.Image, data: LevelRenderData) -> Image.Image:
    panel = _panel_rect()
    thumb_w, thumb_h = THUMB_W, THUMB_H
    thumb_x = panel[0] + PANEL_PAD
    thumb_y = panel[3] - PANEL_PAD - thumb_h
    thumb_img = None
    if data.thumb_bytes:
        try:
            thumb_img = Image.open(io.BytesIO(data.thumb_bytes)).convert("RGBA").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        except Exception:
            logger.warning("缂╃暐鍥炬嬁鍒颁簡浣嗚В涓嶅紑锛岄€€鍥炲崰浣嶅浘")
    if thumb_img is None:
        try:
            thumb_img = Image.open(RES_DIR / "noThumb.png").convert("RGBA").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        except Exception:
            thumb_img = Image.new("RGBA", (thumb_w, thumb_h), (220, 220, 220, 255))

    thumb_round = rounded_image(thumb_img, THUMB_RADIUS)
    tshadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(tshadow).rounded_rectangle(
        [thumb_x + THUMB_SHADOW_OFFSET, thumb_y + THUMB_SHADOW_OFFSET,
         thumb_x + thumb_w + THUMB_SHADOW_OFFSET, thumb_y + thumb_h + THUMB_SHADOW_OFFSET],
        radius=THUMB_RADIUS, fill=(0, 0, 0, THUMB_SHADOW_ALPHA),
    )
    tshadow = tshadow.filter(ImageFilter.GaussianBlur(THUMB_SHADOW_BLUR))
    img = Image.alpha_composite(img, tshadow)
    ImageDraw.Draw(img)
    img.paste(thumb_round, (thumb_x, thumb_y), thumb_round)
    ImageDraw.Draw(img)
    return img


def _draw_sidebar(img: Image.Image, data: LevelRenderData) -> Image.Image:
    W, H = CANVAS_W, CANVAS_H  # noqa: N806
    font_small = _load_font(data.sans_font_path, FONT_SANS_SMALL)
    sb_rect = _sidebar_rect()
    sb_w = sb_rect[2] - sb_rect[0]
    text_left = sb_rect[0] + SIDEBAR_TEXT_LEFT
    text_right = sb_rect[2] - SIDEBAR_TEXT_RIGHT_MARGIN
    avail_px = max(SIDEBAR_MIN_AVAIL_PX, text_right - text_left)
    wrapped_lines = wrap_text_by_width(data.detail_text, avail_px, font_small)
    max_y = sb_rect[3] - 20
    y_text = sb_rect[1] + 10
    last_y = y_text
    for _line in wrapped_lines:
        if y_text > max_y:
            break
        last_y = y_text + SIDEBAR_LINE_HEIGHT
        y_text += SIDEBAR_LINE_HEIGHT
    white_rect_height = last_y - sb_rect[1]
    bg_y_top = PANEL_MARGIN + white_rect_height + RIGHT_BG_SPACING
    bg_height = H - PANEL_BOTTOM_OFFSET - bg_y_top
    img_before_sidebar = img.copy()

    if bg_height > 0:
        try:
            right_bg_img = Image.open(data.right_bg_path).convert("RGBA")
            crop_w = min(right_bg_img.width, sb_w + 1)
            crop_h = min(right_bg_img.height, bg_height + 20)
            cropped = right_bg_img.crop((0, 0, crop_w, crop_h))
            if crop_w < sb_w:
                padded = Image.new("RGBA", (sb_w, bg_height), (0, 0, 0, 0))
                padded.paste(cropped, (0, 0))
                ImageDraw.Draw(padded)
                img.paste(padded, (sb_rect[0], bg_y_top), padded)
            else:
                img.paste(cropped, (sb_rect[0], bg_y_top), cropped)
            ImageDraw.Draw(img)
        except Exception as e:
            logger.error("[gdlevelsearch.draw] ERROR: %s", e)

    draw = ImageDraw.Draw(img)
    y_text = sb_rect[1] + 10
    for line in wrapped_lines:
        if y_text > max_y:
            break
        draw.text((text_left, y_text), line, fill=(0, 0, 0), font=font_small)
        y_text += SIDEBAR_LINE_HEIGHT

    sidebar_mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sidebar_mask).rounded_rectangle(sb_rect, radius=PANEL_RADIUS, fill=255)
    img = Image.composite(img, img_before_sidebar, sidebar_mask)
    ImageDraw.Draw(img)
    return img


def _draw_card(img: Image.Image, data: LevelRenderData) -> Image.Image:
    draw = ImageDraw.Draw(img)
    icon_paths = [data.tier_icon_path, *data.skill_icons]
    icons: list[Image.Image | None] = []
    for ipath in icon_paths:
        try:
            icon = Image.open(ipath).convert("RGBA")
            w, h = icon.size
            new_h = ICON_DEFAULT_H
            new_w = max(1, int(w * new_h / h))
            icons.append(icon.resize((new_w, new_h), Image.Resampling.LANCZOS))
        except (FileNotFoundError, OSError, ZeroDivisionError):
            break

    title_font = _load_font(data.pusab_font_path, TITLE_FONT_SIZE)
    card_line_font = _load_font(data.pusab_font_path, CARD_LINE_FONT_SIZE)
    title_w = draw.textbbox((0, 0), data.title_text, font=title_font)[2]
    total_icon_w = sum(icon.width for icon in icons if icon) + max(0, len(icons) - 1) * ICON_SPACING
    block_w = max(CARD_WIDTH, title_w + 16 + total_icon_w + 40)
    allowed_icon_area = block_w - title_w - ICON_ALLOWED_EXTRA
    if total_icon_w > allowed_icon_area and total_icon_w > 0:
        scale = max(ICON_MIN_SCALE, allowed_icon_area / total_icon_w)
        resized_icons: list[Image.Image | None] = []
        for icon in icons:
            if icon:
                ow, oh = icon.size
                new_h = max(ICON_MIN_H, int(oh * scale))
                resized_icons.append(icon.resize((max(1, int(ow * new_h / oh)), new_h), Image.Resampling.LANCZOS))
            else:
                resized_icons.append(None)
        icons = resized_icons
        total_icon_w = sum(icon.width for icon in icons if icon) + max(0, len(icons) - 1) * ICON_SPACING
        block_w = max(block_w, title_w + 16 + total_icon_w + 40)

    title_bbox = draw.textbbox((0, 0), data.title_text, font=title_font)
    title_h = title_bbox[3] - title_bbox[1]
    line_h = draw.textbbox((0, 0), "A", font=card_line_font)[3]
    required_height = CARD_TOP_PADDING + title_h + CARD_BETWEEN_TITLE_AND_LINES + line_h + CARD_INTER_LINE_SPACING + line_h + CARD_BOTTOM_PADDING
    block_h = max(DESIRED_BLOCK_HEIGHT, required_height)
    panel = _panel_rect()
    block_x = panel[2] - PANEL_PAD - block_w
    block_y = panel[3] - PANEL_PAD - block_h

    card = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle([block_x, block_y, block_x + block_w, block_y + block_h], radius=CARD_BG_RADIUS, fill=CARD_BG_COLOR)
    img = Image.alpha_composite(img, card)
    draw = ImageDraw.Draw(img)
    tx = block_x + 16
    ty = block_y + CARD_TOP_PADDING
    draw.text((tx, ty), data.title_text, fill=(255, 255, 255), font=title_font)
    center_y = ty + title_h / 2 - ICON_NEGATIVE_MARGIN
    tx += title_w + 12
    for icon in icons:
        if icon:
            icon_y = int(center_y - icon.height / 2)
            img.paste(icon, (int(tx), int(icon_y)), icon)
            draw = ImageDraw.Draw(img)
            tx += icon.width + ICON_SPACING

    current_y = block_y + CARD_TOP_PADDING + title_h + CARD_BETWEEN_TITLE_AND_LINES
    line1_color = TIER_COLOR_MAP.get(data.line1, (230, 230, 230))
    draw = ImageDraw.Draw(img)
    draw.text((block_x + 16, current_y), data.line1, fill=line1_color, font=card_line_font)
    current_y += line_h + CARD_INTER_LINE_SPACING + 4
    draw.text((block_x + 16, current_y), data.line2, fill=(200, 200, 200), font=card_line_font)
    return img


def create_level_image(data: LevelRenderData) -> Image.Image:
    img = _draw_background(data)
    img = _draw_main_panel(img, data)
    img = _draw_thumbnail(img, data)
    img = _draw_sidebar(img, data)
    img = _draw_card(img, data)
    return img.convert("RGB")


async def _fetch_all_data(level_id: int) -> FetchedData:
    gdlevel, gddl_info, gddl_tags, thumb_bytes = await asyncio.gather(
        asyncio.to_thread(get_level_by_id, level_id),
        asyncio.to_thread(Gddl.getlevelbyid, level_id, False),
        asyncio.to_thread(Gddl.getleveltags, level_id),
        fetch_thumbnail(level_id),
        return_exceptions=True,
    )
    gdlevel = _optional_remote_result(gdlevel, "GD level")
    gddl_info = _optional_remote_result(gddl_info, "GDDL level")
    gddl_tags = _optional_remote_result(gddl_tags, "GDDL tags")
    thumb_bytes = _optional_remote_result(thumb_bytes, "thumbnail")

    aredl_info = Aredl.getlevelbyid(level_id)
    nlw_info = Nlw.getlevelbyid(level_id)
    plat_info = Platapi.getlevelbyid(level_id)
    if gddl_info is None:
        cached_info = get_by_id(level_id)
        if cached_info is not None:
            gddl_info = GDDLSearchEntry(cached_info)
    if isinstance(gddl_info, GDDLLevel) and gddl_tags:
        gddl_info.Tags = gddl_tags

    list_rank = None
    if aredl_info is None and plat_info is None:
        list_rank = Lists.search_level(level_id)
    nong_song = nong_index.get(str(level_id))

    return FetchedData(
        level_id=level_id,
        gdlevel=gdlevel,
        gddl_info=gddl_info,
        gddl_tags=gddl_tags,
        thumb_bytes=thumb_bytes,
        aredl_info=aredl_info,
        nlw_info=nlw_info,
        plat_info=plat_info,
        list_rank=list_rank,
        nong_song=nong_song
    )


def _valid_id(value: Any) -> Any | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return value


def _build_render_data(data: FetchedData) -> LevelRenderData:
    gdlevel = data.gdlevel
    gddl_info = data.gddl_info
    gddl_meta = gddl_info.Meta if isinstance(gddl_info, GDDLLevel) else None
    nlw_info = data.nlw_info
    plat_info = data.plat_info
    aredl_info = data.aredl_info
    nong_song = data.nong_song

    # basic info (name, creator, length)
    level_line = (
        getattr(gdlevel, "level_name", "")
        or getattr(gddl_meta, "Name", "")
        or getattr(nlw_info, "name", "")
        or getattr(plat_info, "name", "")
        or getattr(aredl_info, "name", "")
        or getattr(gddl_info, "Name", "")
        or ""
    )
    creator = getattr(nlw_info, "creator", None) or getattr(plat_info, "creator", None)
    creator = creator or getattr(gdlevel, "creator_name", None)
    creator = creator or getattr(gddl_info, "PublisherName", None)
    creator_line = f"By {creator}" if creator else ""

    length_text = ""
    if nlw_info and getattr(nlw_info, "length", None):
        length_text = f"({nlw_info.length})"
    elif gddl_meta and (seconds := getattr(gddl_meta, "seconds", None)) is not None:
        length_text = f"({int(seconds // 60)}:{int(seconds) % 60})"
    else:
        length = getattr(gdlevel, "length", None)
        if length is None and gddl_meta is not None:
            length = getattr(gddl_meta, "Length", None)
            if length is not None:
                length = int(length) - 1
        if length is not None and length != 5 and 0 <= length < 5:
            length_text = f"({['Tiny', 'Short', 'Medium', 'Long', 'XL'][length]})"
    id_line = f"Level ID: {data.level_id} {length_text}"

    # advanced info (rank, tier)
    rank_parts = []
    if plat_info:
        if plat_info.tpl:
            rank_parts.append(f"{plat_info.tpl}(TPL)")
        if plat_info.pemonlist:
            rank_parts.append(f"{plat_info.pemonlist}(Pemonlist)")
        elif aredl_info:
            rank_parts.append(f"{aredl_info.position}(AREDL)")
    elif aredl_info:
        if getattr(aredl_info, "status", False) == "Legacy":
            rank_parts.append("AREDL #Legacy")
        else:
            rank_parts.append(f"AREDL #{aredl_info.position}")
        edel = getattr(aredl_info, "edel_enjoyment", None)
        if edel is not None and not getattr(aredl_info, "is_edel_pending", False):
            rank_parts.append(f"EDEL {edel:.1f}")
    elif data.list_rank:
        rank_parts.append(data.list_rank)
    rank_line = " | ".join(rank_parts) if rank_parts else ""

    tier_prefix = ""
    tier_category_line = ""
    tier_value = ""
    if nlw_info and getattr(nlw_info, "tier", None):
        tier_prefix = f"{nlw_info.source} "
        tier_category_line = f"{nlw_info.tier} Tier"
        tier_value = str(nlw_info.tier)
    elif aredl_info and getattr(aredl_info, "nlw_tier", None):
        tier_prefix = "NLW "
        tier_category_line = f"{aredl_info.nlw_tier} Tier"
        tier_value = str(aredl_info.nlw_tier)
    elif plat_info and plat_info.tier:
        tier_prefix = "Plat "
        tier_category_line = str(plat_info.tier)
        tier_value = str(plat_info.tier)
    tier_color = TIER_COLOR_MAP.get(tier_value,"#FFFFFF")

    # derived things for derived info
    derived_suffix = ""
    derived_difficulty = ""
    if plat_info and plat_info.derived_levels:
        derived_levels = Platapi.getderivedlevels(plat_info)
        if derived_levels:
            derived_level = derived_levels[0]
            derived_suffix = derived_level.name.removeprefix(plat_info.name).strip()
            derived_difficulty = str(derived_level.tier or "")

    # additional info (skillset, nong)
    skillset = getattr(nlw_info, "skillset", None)
    skillset_line = f"Skillset: {skillset}" if skillset else ""

    if nong_song:
        song_name = nong_song.get("name", "")
        song_artist = nong_song.get("artist", "")
        song_id = "NONG"
    elif gdlevel is None and gddl_meta is not None:
        gddl_song = getattr(gddl_meta, "Song", None)
        song_name = getattr(gddl_song, "Name", "")
        song_artist = getattr(gddl_song, "Author", "")
        song_id = getattr(gddl_song, "ID", "")
    else:
        song_name = getattr(gdlevel, "song_name", "")
        song_artist = getattr(gdlevel, "song_author", "")
        song_id = getattr(gdlevel, "song_id", "")
    song_line1 = f"Song: {song_name}"
    song_line2 = f"Artist: {song_artist}  ID: {song_id}"

    # icons (face/fire, gddl tier/skillset, plat)
    diff_icon_path = RES_DIR / "diffIcon/diffIcon_0.png"
    try:
        if gdlevel is None and gddl_meta is not None:
            demon_difficulty = {
                "Official": 0, "Easy": 1, "Medium": 2,
                "Hard": 3, "Insane": 4, "Extreme": 5,
            }.get(getattr(gddl_meta, "Difficulty", ""),0)
            diff_icon_path = RES_DIR / f"diffIcon/diffIcon_{f'1{demon_difficulty}'}.png"
        elif getattr(gdlevel, "is_demon", False):
            demon_difficulty = "3001245"[getattr(gdlevel, "demon_difficulty", 0)]
            diff_icon_path = RES_DIR / f"diffIcon/diffIcon_1{demon_difficulty}.png"
        else:
            stars = max(0, min(9, int(getattr(gdlevel, "stars", 0) or 0)))
            diff_icon_path = RES_DIR / f"diffIcon/diffIcon_{stars}.png"
    except (IndexError, TypeError, ValueError):
        pass

    rarity = gdlevel.rarity if gdlevel is not None else gddl_meta.Rarity if gddl_meta is not None else 0
    featured_fx = RES_DIR / f"diffIcon/featured_{rarity}.png" if rarity else Path()

    # to do: change everything below to 2 main branch for classic and
    # and figure out how to compose gddl info and plat info at the same pic (another card?)
    tier_icon_path = RES_DIR / "tiers/tier_0.png"
    is_plat = False
    if gdlevel is not None:
        is_plat_method = getattr(gdlevel, "is_plat", None)
        if callable(is_plat_method):
            try:
                is_plat = bool(is_plat_method())
            except Exception:
                is_plat = False
    if is_plat or (gdlevel is None and gddl_meta is not None and gddl_meta.is_pemon()):
        tier_icon_path = RES_DIR / "moon.png"
    else:
        rating = getattr(gddl_info, "Rating", None)
        default_rating = getattr(gddl_info, "DefaultRating", None)
        if gddl_info and (rating or default_rating) is not None:
            rating_value = int(rating + 0.5) if rating else default_rating
            tier_icon_path = RES_DIR / f"tiers/tier_{rating_value}.png"

    skill_icons = []
    if isinstance(gddl_info, GDDLLevel):
        for tag in (getattr(gddl_info, "Tags", None) or [])[:3]:
            name = tag.get("Name") if isinstance(tag, dict) else None
            if name:
                fname = name.replace(" ", "_").replace("-", "_").lower()
                skill_icons.append(RES_DIR / f"skillsets/skillset_{fname}.png")

    if plat_info:
        title_text = "P.Diff"
        line1 = str(plat_info.tier or "")
        line2 = f"Enjoyment: {plat_info.enjoyment}" if plat_info.enjoyment is not None else ""
    elif gddl_info:
        title_text = "GDDL"
        rating = getattr(gddl_info, "Rating", None)
        enjoyment = getattr(gddl_info, "Enjoyment", None)
        is_two_player = getattr(gdlevel, "is_two_player", False) or getattr(gddl_meta, "IsTwoPlayer", False)
        if isinstance(gddl_info, GDDLLevel):
            # this logic is to prevent nine circles display 2p ratings
            rating_suffix = f"/{round(gddl_info.TwoPlayerRating, 2)}(2p)" if is_two_player and gddl_info.TwoPlayerRating else f"({gddl_info.RatingCount})" if gddl_info.RatingCount else ""
            enj_suffix = f"/{round(gddl_info.TwoPlayerEnjoyment, 2)}(2p)" if is_two_player and gddl_info.TwoPlayerEnjoyment else f"({gddl_info.EnjoymentCount})" if gddl_info.EnjoymentCount else ""
        else:
            rating_suffix = ""
            enj_suffix = ""
        line1 = f"Tier: {round(rating, 2) if rating else 'N/A'}{rating_suffix}"
        line2 = f"Enj: {round(enjoyment, 2) if enjoyment else 'N/A'}{enj_suffix}"
    else:
        title_text = "No info"
        line1 = "sorry :("
        line2 = ""

    description = getattr(gdlevel, "description", "") or getattr(gddl_meta, "Description", "")
    detail_text = f"Description: {description}" if description else ""
    if plat_info and plat_info.tags:
        plat_info.tags = list(set(plat_info.tags) - {"On TPL", "On Pemonlist"})
        detail_text += f"\n\nDifficulty Chart Tags: {', '.join(plat_info.tags)}"
    if nlw_info and getattr(nlw_info, "description", None):
        detail_text += f"\n\n{nlw_info.source} Description: {nlw_info.description}"
    if aredl_info and getattr(aredl_info, "description", None):
        detail_text += f"\n\nAREDL Description: {aredl_info.description}"

    return LevelRenderData(
        level_line=level_line,
        creator_line=creator_line,
        id_line=id_line,
        rank_line=rank_line,
        tier_category_line=tier_category_line,
        skillset_line=skillset_line,
        song_line1=song_line1,
        song_line2=song_line2,
        diff_icon_path=diff_icon_path,
        featured_fx_path=featured_fx,
        line1=line1,
        line2=line2,
        tier_color=tier_color,
        tier_icon_path=tier_icon_path,
        skill_icons=skill_icons,
        detail_text=detail_text,
        thumb_bytes=data.thumb_bytes,
        derived_suffix=derived_suffix,
        derived_difficulty=derived_difficulty,
        tier_prefix=tier_prefix,
        title_text=title_text,
    )


async def create_image_from_gdlevel(level_id: int) -> Image.Image:
    fetched = await _fetch_all_data(level_id)
    return create_level_image(_build_render_data(fetched))
