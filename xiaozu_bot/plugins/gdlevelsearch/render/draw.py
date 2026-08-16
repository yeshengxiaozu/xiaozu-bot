import asyncio
import io
import os
from pathlib import Path

from nonebot import logger
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..api.aredlapi import Aredl
from ..api.gdapi import GDLevel
from ..api.gddlapi import Gddl
from ..api.listsapi import Lists
from ..api.nlwapi import Nlw
from ..api.platapi import Platapi, PlatInfo
from ..paths import PLUGIN_DIR, RES_DIR
from . import thumbnail as _thumbnail

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

def select_tags(level:PlatInfo) -> list[str]:
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
            return ImageFont.load_default(size)
        except TypeError:  # Pillow < 10.1 的 load_default 不吃 size
            return ImageFont.load_default()

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
## 已统一圆角半径，删除SIDEBAR_RADIUS，全部用PANEL_RADIUS
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
_nong_path = PLUGIN_DIR / "data" / "nong_index.json"
try:
    with _nong_path.open(encoding="utf-8") as f:
        nong_index = json.load(f)
except (OSError, json.JSONDecodeError):
    logger.warning(f"NONG 索引读不了，歌名会退回 GD 自带的：{_nong_path}")

def _optional_remote_result(value: object, source: str) -> object | None:
    """Convert an optional remote-task exception into a missing value."""
    if isinstance(value, asyncio.CancelledError):
        raise value
    if isinstance(value, BaseException):
        if not isinstance(value, Exception):
            raise value
        logger.warning(f"{source} lookup failed while rendering: {value}")
        return None
    return value
# Keep the historical names importable from draw.py while the implementation
# lives in the focused thumbnail module.
THUMB_RETRIES = _thumbnail.THUMB_RETRIES
THUMB_BACKOFF = _thumbnail.THUMB_BACKOFF
_none = _thumbnail._none
_thumbnail_id_for = _thumbnail._thumbnail_id_for
_fetch_thumbnail = _thumbnail._fetch_thumbnail


async def create_level_image(
    level_line: str,
    creator_line: str,
    id_line: str,
    rank_line: str,
    tier_category_line: str,
    skillset_line: str,
    song_name: str,
    song_artist: str,
    song_id: str,
    diff_icon_path: Path,
    featured_fx_path: Path = Path(),
    line1: str = "",
    line2: str = "",
    tier_value: str = "",
    tier_icon_path: Path = Path(),
    skill_icons: list[Path] | None = None,
    detail_text: str = "",
    thumb_bytes: bytes | None = None,
    derived_suffix: str = "",
    derived_difficulty: str = "",
    tier_prefix: str = "",
    title_text: str = "GDDL",
    # 磁盘上的文件名是全大写的 PUSAB.TTF / ARIAL.TTF。
    # macOS 和 Windows 的文件系统不区分大小写所以写小写也能跑，
    # 但 Linux 上会直接 OSError，整条出图路径全灭。
    pusab_font_path: Path = RES_DIR/"PUSAB.TTF",
    sans_font_path: Path = RES_DIR/"ARIAL.TTF",
    left_bg_path: Path = RES_DIR/"left_bg.png",
    right_bg_path: Path = RES_DIR/"right_bg.png",
) -> Image.Image:
    W, H = CANVAS_W, CANVAS_H  # noqa: N806
    # 1. 先绘制底色渐变（粉白配色，底部更白，顶部更粉）
    base = create_vertical_gradient((W, H), (255, 180, 220), (255, 240, 250)).convert("RGBA")

    # 2. 生成主栏和侧边栏的蒙版
    panel_rect = (PANEL_MARGIN, PANEL_MARGIN, PANEL_MAIN_WIDTH - PANEL_RIGHT_OFFSET, H - PANEL_BOTTOM_OFFSET)
    sidebar_x = PANEL_MAIN_WIDTH + SIDEBAR_X_OFFSET
    sidebar_rect = (sidebar_x, PANEL_MARGIN, W - PANEL_MARGIN, H - PANEL_BOTTOM_OFFSET)

    # 主栏蒙版
    panel_mask = Image.new("L", (W, H), 0)
    panel_draw = ImageDraw.Draw(panel_mask)
    panel_draw.rounded_rectangle(panel_rect, radius=PANEL_RADIUS, fill=255)

    # 侧边栏蒙版
    sidebar_mask = Image.new("L", (W, H), 0)
    sidebar_draw = ImageDraw.Draw(sidebar_mask)
    sidebar_draw.rounded_rectangle(sidebar_rect, radius=PANEL_RADIUS, fill=255)

    # 3. 加载左背景图并用蒙版裁剪
    left_bg_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    try:
        left_bg = Image.open(left_bg_path).convert("RGBA")
        if left_bg.size != (W, H):
            left_bg = left_bg.resize((W, H), Image.Resampling.LANCZOS)
        left_bg_layer.paste(left_bg, (0, 0), left_bg)
    except Exception as e:
        logger.error("无法加载左侧背景图: %s", e)
    left_bg_masked = Image.composite(left_bg_layer, Image.new("RGBA", (W, H), (0,0,0,0)), panel_mask)

    # 右侧背景直接用上下白色渐变
    right_bg_layer = create_vertical_gradient((W, H), (255,255,255), (255,255,255)).convert("RGBA")
    right_bg_masked = Image.composite(right_bg_layer, Image.new("RGBA", (W, H), (0,0,0,0)), sidebar_mask)

    # 4. 合成到底色上
    base = Image.alpha_composite(base, left_bg_masked)
    base = Image.alpha_composite(base, right_bg_masked)

    # 5. 继续后续内容绘制
    img = base.copy()
    draw = ImageDraw.Draw(img)

    # 字体
    font_title = _load_font(pusab_font_path, FONT_PUSAB_TITLE)
    font_sub = _load_font(pusab_font_path, FONT_PUSAB_SUB)
    font_small = _load_font(sans_font_path, FONT_SANS_SMALL)

    # 主面板区域坐标
    panel = (PANEL_MARGIN, PANEL_MARGIN, PANEL_MAIN_WIDTH - PANEL_RIGHT_OFFSET, H - PANEL_BOTTOM_OFFSET)
    panel_pad = PANEL_PAD
    x = panel[0] + panel_pad
    y = panel[1] + panel_pad

    # 主标题
    title = level_line
    title_bbox = draw.textbbox((0, 0), title, font=font_title)
    title_h = int(title_bbox[3] - title_bbox[1])
    draw_outlined_text(draw, (x, y), title, font_title, fill="white", outline="black", outline_width=OUTLINE_TITLE)
    title_y = y
    title_w = draw.textbbox((0, 0), title, font=font_title)[2]

    if derived_suffix or derived_difficulty:
        derived_lines = []
        if derived_suffix:
            derived_lines.append((derived_suffix, (255, 255, 255)))
        if derived_difficulty:
            derived_lines.append((derived_difficulty, TIER_COLOR_MAP.get(derived_difficulty, DEFAULT_TIER_COLOR)))
        if derived_lines:
            derived_line_h = draw.textbbox((0, 0), "A", font=font_sub)[3]
            max_line_w = max(draw.textbbox((0, 0), text, font=font_sub)[2] for text, _ in derived_lines)
            card_w = max_line_w + 24
            card_h = len(derived_lines) * derived_line_h + 24 + max(0, len(derived_lines) - 1) * SPACING_SMALL
            card_x = panel[2] - panel_pad - card_w
            card_y = title_y - 16
            if title_w + card_w > PANEL_MAIN_WIDTH:
                card_y += title_h
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            od.rectangle([card_x, card_y, card_x + card_w, card_y + card_h], fill=(0, 0, 0, 51))
            img = Image.alpha_composite(img, overlay)
            draw = ImageDraw.Draw(img)
            text_x = card_x + 12
            text_y = card_y + 16
            for text, fill in derived_lines:
                draw_outlined_text(draw, (text_x, text_y), text, fill=fill, font=font_sub, outline_width=OUTLINE_SUB)
                text_y += derived_line_h + SPACING_SMALL

    y += title_h + SPACING_SMALL

    # creator/ID/rank
    draw_outlined_text(draw, (x, y), creator_line, font_sub, fill="white", outline="black", outline_width=OUTLINE_SUB)
    y += draw.textbbox((0, 0), creator_line, font=font_sub)[3] + SPACING_SMALL
    draw_outlined_text(draw, (x, y), id_line, font_sub, fill="white", outline="black", outline_width=OUTLINE_SUB)
    y += draw.textbbox((0, 0), id_line, font=font_sub)[3] + SPACING_SMALL
    draw_outlined_text(draw, (x, y), rank_line, font_sub, fill="white", outline="black", outline_width=OUTLINE_SUB)
    y += draw.textbbox((0, 0), rank_line, font=font_sub)[3] + SPACING_SMALL

    # Tier 行
    if tier_prefix:
        draw_outlined_text(draw, (x, y), tier_prefix, font_sub, fill="white", outline="black", outline_width=OUTLINE_SUB)
        prefix_w = draw.textbbox((0, 0), tier_prefix, font=font_sub)[2]
    else:
        prefix_w = 0
    tier_color = TIER_COLOR_MAP.get(tier_value, DEFAULT_TIER_COLOR)
    draw_outlined_text(draw, (x + prefix_w, y), tier_category_line, font_sub, fill=tier_color, outline="black", outline_width=OUTLINE_SUB)
    y += SPACING_TIER_ROW

    # Skillset 行 — 改用白色描边（填充黑色，轮廓白色，宽度3）
    skillset_outline_width = 3
    draw_outlined_text(draw, (x, y), skillset_line, font_small,
                       fill="black", outline="white", outline_width=skillset_outline_width)
    y += draw.textbbox((0, 0), skillset_line, font=font_small)[3] + SPACING_SMALL

    # Song 信息 — 两行分别加白色描边
    song_line1 = f"Song: {song_name}"
    song_line2 = f"Artist: {song_artist}  ID: {song_id}"
    draw_outlined_text(draw, (x, y), song_line1, font_small,
                       fill="black", outline="white", outline_width=skillset_outline_width)
    y += SPACING_SONG_LINE
    draw_outlined_text(draw, (x, y), song_line2, font_small,
                       fill="black", outline="white", outline_width=skillset_outline_width)

    # ---------- 难度图标 ----------
    diff_icon_img = None
    diff_target_size = None
    try:
        diff_icon_img = Image.open(diff_icon_path).convert("RGBA")
        orig_w, orig_h = diff_icon_img.size
        target_w = max(1, int(orig_w * DIFF_SCALE))
        target_h = max(1, int(orig_h * DIFF_SCALE))
        diff_target_size = (target_w, target_h)
    except Exception:
        diff_target_size = (max(1, int(320 * DIFF_SCALE)), max(1, int(280 * DIFF_SCALE)))
        diff_icon_img = None

    diff_w, diff_h = diff_target_size
    diff_x = panel[2] - panel_pad - diff_w
    diff_y = panel[1] + panel_pad + title_h + DIFF_Y_EXTRA

    if featured_fx_path:
        try:
            fx_img = Image.open(featured_fx_path).convert("RGBA")
            fx_img = fx_img.resize(diff_target_size, Image.Resampling.LANCZOS)
            img.paste(fx_img, (diff_x, diff_y), fx_img)
        except Exception:
            pass

    if diff_icon_img is not None:
        try:
            diff_icon_img = diff_icon_img.resize(diff_target_size, Image.Resampling.LANCZOS)
            img.paste(diff_icon_img, (diff_x, diff_y), diff_icon_img)
        except Exception:
            diff_icon_img = None

    if diff_icon_img is None:
        pd = ImageDraw.Draw(img)
        pd.rounded_rectangle([diff_x, diff_y, diff_x + diff_w, diff_y + diff_h],
                             radius=12, outline=(200, 60, 60), width=3)
        draw_outlined_text(draw, (diff_x + 8, diff_y + diff_h // 2 - 24), "No\nImage",
                           font_sub, fill="red", outline="white", outline_width=2)

    # 左下角缩略图
    thumb_w, thumb_h = THUMB_W, THUMB_H
    thumb_x = panel[0] + panel_pad
    thumb_y = panel[3] - panel_pad - thumb_h
    thumb_img = None

    # 缩略图由调用方提前并发取好再传进来，这里只负责解码
    if thumb_bytes:
        try:
            thumb_img = Image.open(io.BytesIO(thumb_bytes)).convert("RGBA")
            thumb_img = thumb_img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        except Exception:
            logger.warning("缩略图拿到了但解不开，退回占位图")
            thumb_img = None

    if thumb_img is None:
        try:
            thumb_img = Image.open(RES_DIR/"noThumb.png").convert("RGBA")
            thumb_img = thumb_img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        except Exception:
            thumb_img = Image.new("RGBA", (thumb_w, thumb_h), (220, 220, 220, 255))

    thumb_round = rounded_image(thumb_img, THUMB_RADIUS)
    tshadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(tshadow)
    td.rounded_rectangle([thumb_x + THUMB_SHADOW_OFFSET, thumb_y + THUMB_SHADOW_OFFSET,
                          thumb_x + thumb_w + THUMB_SHADOW_OFFSET, thumb_y + thumb_h + THUMB_SHADOW_OFFSET],
                         radius=THUMB_RADIUS, fill=(0, 0, 0, THUMB_SHADOW_ALPHA))
    tshadow = tshadow.filter(ImageFilter.GaussianBlur(THUMB_SHADOW_BLUR))
    img = Image.alpha_composite(img, tshadow)
    img.paste(thumb_round, (thumb_x, thumb_y), thumb_round)

    # ---------- 右侧详情栏（去掉白色背景，保留背景图和文字，仍用侧边栏边框截断） ----------
    SIDEBAR_X = PANEL_MAIN_WIDTH + SIDEBAR_X_OFFSET  # noqa: N806
    sb_w = W - SIDEBAR_X - PANEL_MARGIN
    sb_rect = (SIDEBAR_X, PANEL_MARGIN, SIDEBAR_X + sb_w, H - PANEL_BOTTOM_OFFSET)

    # 计算文字高度
    text_left = sb_rect[0] + SIDEBAR_TEXT_LEFT
    text_right = sb_rect[2] - SIDEBAR_TEXT_RIGHT_MARGIN
    avail_px = max(SIDEBAR_MIN_AVAIL_PX, text_right - text_left)
    wrapped_lines = wrap_text_by_width(detail_text, avail_px, font_small)
    max_y = sb_rect[3] - 20

    y_text = sb_rect[1] + 10
    last_y = y_text
    for _line in wrapped_lines:
        if y_text > max_y:
            break
        last_y = y_text + SIDEBAR_LINE_HEIGHT
        y_text += SIDEBAR_LINE_HEIGHT
    text_height = last_y - sb_rect[1]
    white_rect_height = text_height

    spacing = RIGHT_BG_SPACING
    bg_y_top = PANEL_MARGIN + white_rect_height + spacing
    bg_height = H - PANEL_BOTTOM_OFFSET - bg_y_top

    # 保存当前画面用于后续蒙版
    img_before_sidebar = img.copy()

    # 背景图（不缩放，直接裁剪超出部分）
    if bg_height > 0:
        try:
            right_bg_img = Image.open(right_bg_path).convert("RGBA")
            crop_w = min(right_bg_img.width, sb_w+1)
            crop_h = min(right_bg_img.height, bg_height+20)
            cropped = right_bg_img.crop((0, 0, crop_w, crop_h))
            if crop_w < sb_w:
                padded = Image.new("RGBA", (sb_w, bg_height), (0, 0, 0, 0))
                padded.paste(cropped, (0, 0))
                img.paste(padded, (SIDEBAR_X, bg_y_top), padded)
            else:
                img.paste(cropped, (SIDEBAR_X, bg_y_top), cropped)
        except Exception as e:
            logger.error("无法加载右侧背景图: %s", e)

    # 不再绘制白色半透明背景，直接绘制文字
    draw = ImageDraw.Draw(img)  # 确保 draw 是最新的
    y_text = sb_rect[1] + 10
    for line in wrapped_lines:
        if y_text > max_y:
            break
        draw.text((text_left, y_text), line, fill=(0,0,0), font=font_small)
        y_text += SIDEBAR_LINE_HEIGHT

    # 用侧边栏圆角蒙版裁剪整个侧边栏区域
    sidebar_mask = Image.new("L", (W, H), 0)
    mdraw = ImageDraw.Draw(sidebar_mask)
    mdraw.rounded_rectangle(sb_rect, radius=PANEL_RADIUS, fill=255)
    img = Image.composite(img, img_before_sidebar, sidebar_mask)

    # ---------- 右下角卡片 ----------
    if skill_icons is None:
        skill_icons = []
    icon_paths = [tier_icon_path, *skill_icons]
    icons: list[Image.Image] = []
    try:
        for ipath in icon_paths:
            icon = Image.open(ipath).convert("RGBA")
            w, h = icon.size
            new_h = ICON_DEFAULT_H
            new_w = max(1, int(w * new_h / h))
            icon = icon.resize((new_w, new_h), Image.Resampling.LANCZOS)
            icons.append(icon)
    except FileNotFoundError:
        pass

    title_font = _load_font(pusab_font_path, TITLE_FONT_SIZE)
    card_line_font = _load_font(pusab_font_path, CARD_LINE_FONT_SIZE)

    title_w = draw.textbbox((0, 0), title_text, font=title_font)[2]
    icon_spacing = ICON_SPACING
    total_icon_w = sum(icon.width for icon in icons if icon) + max(0, (len(icons) - 1)) * icon_spacing

    min_block_w = title_w + 16 + total_icon_w + 40
    block_w = max(CARD_WIDTH, min_block_w)

    allowed_icon_area = block_w - title_w - ICON_ALLOWED_EXTRA
    if total_icon_w > allowed_icon_area and total_icon_w > 0:
        scale = max(ICON_MIN_SCALE, allowed_icon_area / total_icon_w)
        new_icons = []
        for icon in icons:
            if icon:
                ow, oh = icon.size
                new_h = max(ICON_MIN_H, int(oh * scale))
                new_w = max(1, int(ow * new_h / oh))
                new_icons.append(icon.resize((new_w, new_h), Image.Resampling.LANCZOS))
            else:
                new_icons.append(None)
        icons = new_icons
        total_icon_w = sum(icon.width for icon in icons if icon) + max(0, (len(icons) - 1)) * icon_spacing
        block_w = max(block_w, title_w + 16 + total_icon_w + 40)

    title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
    title_h = title_bbox[3] - title_bbox[1]
    line_h = draw.textbbox((0, 0), "A", font=card_line_font)[3]

    required_height = (CARD_TOP_PADDING + title_h + CARD_BETWEEN_TITLE_AND_LINES +
                       line_h + CARD_INTER_LINE_SPACING + line_h + CARD_BOTTOM_PADDING)
    block_h = max(DESIRED_BLOCK_HEIGHT, required_height)

    block_x = panel[2] - panel_pad - block_w
    block_y = panel[3] - panel_pad - block_h

    card = Image.new("RGBA", img.size, (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle([block_x, block_y, block_x + block_w, block_y + block_h],
                         radius=CARD_BG_RADIUS, fill=CARD_BG_COLOR)
    img = Image.alpha_composite(img, card)
    draw = ImageDraw.Draw(img)

    tx = block_x + 16
    ty = block_y + CARD_TOP_PADDING
    draw.text((tx, ty), title_text, fill=(255, 255, 255), font=title_font)
    center_y = ty + title_h / 2 - ICON_NEGATIVE_MARGIN
    tx += title_w + 12
    if icons:
        for icon in icons:
            if icon:
                icon_y = int(center_y - icon.height / 2)
                img.paste(icon, (int(tx), int(icon_y)), icon)
                tx += icon.width + icon_spacing

    current_y = block_y + CARD_TOP_PADDING + title_h + CARD_BETWEEN_TITLE_AND_LINES
    line1_color = TIER_COLOR_MAP.get(line1, (230, 230, 230))
    draw.text(
        (block_x + 16, current_y),
        line1,
        fill=line1_color,
        font=card_line_font,
    )
    current_y += line_h + CARD_INTER_LINE_SPACING + 4
    draw.text((block_x + 16, current_y), line2, fill=(200, 200, 200), font=card_line_font)

    return img.convert("RGB")

async def create_image_from_gdlevel(gdlevel: GDLevel) -> Image.Image:
    """根据传入的 `GDLevel` 自动查询 GDDL/AREDL/NLW/Plat 数据并生成图片。

    如果某来源不存在对应信息，则对应绘图字段留空。
    """
    level_id = gdlevel.level_id

    # 三个外部请求彼此不依赖，一起发。
    # 以前是串着来的：GDDL 详情 -> GDDL tags -> 缩略图，实测一张图 3.5-5.4 秒，
    # 而且几乎全是在等网络（PIL 那部分可以忽略不计）。
    # 并发之后总耗时约等于最慢的那一个。
    thumbnail_id = _thumbnail_id_for(level_id)
    gddl_info, gddl_tags, thumb_bytes = await asyncio.gather(
        asyncio.to_thread(Gddl.getlevelbyid, level_id, False),
        asyncio.to_thread(Gddl.getleveltags, level_id),
        _fetch_thumbnail(thumbnail_id) if thumbnail_id else _none(),
        return_exceptions=True,
    )
    gddl_info = _optional_remote_result(gddl_info, "GDDL level")
    gddl_tags = _optional_remote_result(gddl_tags, "GDDL tags")
    thumb_bytes = _optional_remote_result(thumb_bytes, "thumbnail")
    if gddl_info is not None and gddl_tags:
        gddl_info.Tags = gddl_tags

    # 这三个查的是内存里的表，不走网络
    aredl_info = Aredl.getlevelbyid(level_id)
    nlw_info = Nlw.getlevelbyid(level_id)
    plat_info = Platapi.getlevelbyid(level_id)

    # level_line / creator_line
    level_line = getattr(gdlevel, "level_name", "") or ""
    creator = getattr(gdlevel, "creator_name", None)
    if not creator and nlw_info and getattr(nlw_info, "creator", None):
        creator = nlw_info.creator
    creator_line = f"By {creator}" if creator else ""

    # id_line
    length_text = f"({nlw_info.length})" if nlw_info and nlw_info.length \
        else f"({['Tiny','Short','Medium','Long','XL',''][gdlevel.length]})" if gdlevel.length != 5 else ""
    id_line = f"Level ID: {level_id} {length_text}" if level_id is not None else ""

    # rank_line: 集成 AREDL / GDDL 等排行信息
    rank_parts = []
    if plat_info:
        if plat_info.tpl: #有platinfo的话再放aredl会写不下
            rank_parts.append(f"{plat_info.tpl}(TPL)")
        if plat_info.pemonlist:
            rank_parts.append(f"{plat_info.pemonlist}(Pemonlist)")
    elif aredl_info:
        if getattr(aredl_info, "status", False) == "Legacy":
            rank_parts.append("AREDL #Legacy")
        else:
            rank_parts.append(f"AREDL #{aredl_info.position}")
        # EDEL 的 enjoyment 是 0-100 的刻度，不是 GDDL 那个 0-10，所以标上 EDEL 前缀区分。
        # pending 的数还没定稿，直接不显示。
        edel = getattr(aredl_info, "edel_enjoyment", None)
        if edel is not None and not getattr(aredl_info, "is_edel_pending", False):
            rank_parts.append(f"EDEL {edel:.1f}")
    elif Lists.search_level(level_id):
        rank_parts.append(Lists.search_level(level_id))
    rank_line = " | ".join(rank_parts) if rank_parts else ""

    # tier 信息与前缀
    tier_prefix = ""
    tier_category_line = ""
    tier_value = None
    if nlw_info and nlw_info.tier:
        tier_prefix = f"{nlw_info.source} "
        tier_category_line = str(nlw_info.tier) + " Tier"
        tier_value = nlw_info.tier
    elif aredl_info and aredl_info.nlw_tier:
        tier_prefix = "NLW " #查到的必然是是nlw tier
        tier_category_line = str(aredl_info.nlw_tier) + " Tier"
        tier_value = aredl_info.nlw_tier
    elif plat_info and plat_info.tier:
        tier_prefix = "Plat "
        tier_category_line = str(plat_info.tier)
        tier_value = plat_info.tier

    # derived
    derived_suffix = ""
    derived_difficulty = ""
    if plat_info and plat_info.derived_levels:
        derived_level = Platapi.getderivedlevels(plat_info)[0]
        derived_suffix = derived_level.name.removeprefix(plat_info.name).strip()
        derived_difficulty = str(derived_level.tier or "")
    # skillset
    skillset_line = f"Skillset: {nlw_info.skillset}" if nlw_info and getattr(nlw_info, "skillset", None) else ""

    # song info
    if str(gdlevel.level_id) in nong_index:
        song_name = nong_index[str(gdlevel.level_id)]["name"]
        song_artist = nong_index[str(gdlevel.level_id)]["artist"]
        song_id = "NONG"
    else:
        song_name = getattr(gdlevel, "song_name", "") or ""
        song_artist = getattr(gdlevel, "song_author", "") or ""
        song_id = getattr(gdlevel, "song_id", "") or ""

    # diff icon mapping
    diff_icon_path = RES_DIR/"diffIcon/diffIcon_0.png"
    try:
        if getattr(gdlevel, "is_demon", False):
            # check readable difficulty label for mapping
            demon_difficulty = "3001245"[gdlevel.demon_difficulty]
            diff_icon_path = RES_DIR/f"diffIcon/diffIcon_1{demon_difficulty}.png"
        else:
            stars = int(getattr(gdlevel, "stars", 0) or 0)
            stars = max(0, min(9, stars))
            diff_icon_path =  RES_DIR/f"diffIcon/diffIcon_{stars}.png"
    except Exception:
        diff_icon_path = RES_DIR/"diffIcon/diffIcon_0.png"

    # tier icon: use rounded gddl rating if present
    tier_icon_path = RES_DIR/"tiers/tier_0.png"
    if gdlevel.is_plat():
        tier_icon_path = RES_DIR/"moon.png"
    elif gddl_info and (gddl_info.Rating or gddl_info.DefaultRating) is not None:
        tier_icon_path =  RES_DIR/f"tiers/tier_{int(gddl_info.Rating+0.5) if gddl_info.Rating else gddl_info.DefaultRating}.png"

    # skill icons from gddl tags
    skill_icons = []
    if gddl_info and getattr(gddl_info, "Tags", None):
        for tag in (gddl_info.Tags or [])[:3]:
            name = tag.get("Name") if isinstance(tag, dict) else None
            if not name:
                continue
            fname = name.replace(" ", "_").replace("-", "_").lower()
            skill_icons.append(RES_DIR/f"skillsets/skillset_{fname}.png")

    if plat_info:
        title_text = "P.Diff"
        line1 = str(plat_info.tier or "")
        line2 = f"Enjoyment: {plat_info.enjoyment}" if plat_info.enjoyment is not None else ""
    elif gddl_info:
        title_text = "GDDL"
        rating = round(gddl_info.Rating, 2) if gddl_info and gddl_info.Rating else "N/A"
        if gdlevel.is_two_player and gddl_info.TwoPlayerRating:
            rating_count = f"/{round(gddl_info.TwoPlayerRating, 2)}(2p)"
        else:
            rating_count = f"({gddl_info.RatingCount})" if gddl_info and gddl_info.RatingCount else ""
        line1 = f"Tier: {rating}{rating_count}"

        enj = round(gddl_info.Enjoyment, 2) if gddl_info and gddl_info.Enjoyment else "N/A"
        if gdlevel.is_two_player and gddl_info.TwoPlayerEnjoyment:
            enj_count = f"/{round(gddl_info.TwoPlayerEnjoyment, 2)}(2p)"
        else:
            enj_count = f"({gddl_info.EnjoymentCount})" if gddl_info and gddl_info.EnjoymentCount else ""
        line2 = f"Enj: {enj}{enj_count}"
    else:
        title_text = "No info"
        line1 = "sorry :("
        line2 = ""

    #detail text
    detail_text = ""
    detail_text += f"Description: {gdlevel.description}"
    if plat_info and plat_info.tags:
        detail_text += f"\n\nDifficulty Chart Tags: {', '.join(plat_info.tags)}"
    if nlw_info and nlw_info.description:
        detail_text += f"\n\n{nlw_info.source} Description: {nlw_info.description}"
    if aredl_info and aredl_info.description:
        detail_text += f"\n\nAREDL Description: {aredl_info.description}"

    # featured fx -x try from aredl tags or leave empty
    featured_level = gdlevel.epic + 1 if gdlevel.epic else 1 if gdlevel.feature_score else 0
    featured_fx = RES_DIR/f"diffIcon/featured_{featured_level}.png" if featured_level else Path()

    # thumbnail id - use level id
    return await create_level_image(
        level_line=level_line,
        creator_line=creator_line,
        id_line=id_line,
        rank_line=rank_line,
        tier_category_line=tier_category_line,
        skillset_line=skillset_line,
        song_name=song_name,
        song_artist=song_artist,
        song_id=str(song_id),
        diff_icon_path=diff_icon_path,
        featured_fx_path=featured_fx,
        line1=line1,
        line2=line2,
        tier_value=tier_value or "",
        tier_icon_path=tier_icon_path,
        skill_icons=skill_icons,
        detail_text=detail_text,
        thumb_bytes=thumb_bytes,
        derived_suffix=derived_suffix,
        derived_difficulty=derived_difficulty,
        tier_prefix=tier_prefix,
        title_text=title_text,
    )
