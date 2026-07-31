"""*gdicon：把玩家各 gamemode 的图标画出来。

图标 id 和配色 GDUser 已经解析好了（acc_icon / acc_ship / ... 和 color/color2/color3），
这里负责用本地图集把图标渲染出来，不再请求 gdicon.oat.zone。

辉光颜色对应用户的 color3（GD 叫 glow color）：没设置 color3 时游戏里就是用 color2
当辉光色，所以渲染器也这么回退——以前走网络 API 只传 color1/color2，color3 被丢掉，
辉光颜色才会不对。
"""

import asyncio
from typing import NamedTuple

from nonebot import logger
from PIL import Image, ImageDraw

from . import iconrender
from .draw import RES_DIR, _load_font
from .gdapi import GDUser


class Form(NamedTuple):
    """一个 gamemode：命令里叫什么、图集文件叫什么、图标 id 存在 GDUser 的哪个字段。"""

    key: str          # 用户输入的名字
    api_type: str     # 和旧 gdicon API 的 type 参数同名，保持兼容
    attr: str         # GDUser 上的属性名
    label: str        # 拼图时显示的标题
    resource: str     # 本地图集里的文件名前缀


FORMS: tuple[Form, ...] = (
    Form("cube", "cube", "acc_icon", "Cube", "player"),
    Form("ship", "ship", "acc_ship", "Ship", "ship"),
    Form("ball", "ball", "acc_ball", "Ball", "player_ball"),
    Form("ufo", "ufo", "acc_bird", "UFO", "bird"),
    Form("wave", "wave", "acc_dart", "Wave", "dart"),
    Form("robot", "robot", "acc_robot", "Robot", "robot"),
    Form("spider", "spider", "acc_spider", "Spider", "spider"),
    Form("swing", "swing", "acc_swing", "Swing", "swing"),
    Form("jetpack", "jetpack", "acc_jetpack", "Jetpack", "jetpack"),
)

# 一些常见的别名，省得非记着 API 里那个词
ALIASES: dict[str, str] = {
    "bird": "ufo", "ufo": "ufo", "飞碟": "ufo",
    "dart": "wave", "wave": "wave", "波": "wave",
    "方块": "cube", "船": "ship", "球": "ball",
    "机器人": "robot", "蜘蛛": "spider", "秋千": "swing", "喷气背包": "jetpack",
}

FORM_BY_KEY = {f.key: f for f in FORMS}

DEFAULT_FORM = "cube"


def resolve_form(name: str) -> Form | None:
    key = ALIASES.get(name.lower(), name.lower())
    return FORM_BY_KEY.get(key)


def form_names() -> str:
    return " / ".join(f.key for f in FORMS)


def _render_one(user: GDUser, form: Form) -> Image.Image | None:
    """同步渲染一个图标。失败返回 None，不抛。"""
    try:
        return iconrender.get_icon_from_cols(
            form.resource,
            max(1, getattr(user, form.attr, 1) or 1),
            user.color or 0,
            user.color2 or 0,
            bool(user.acc_glow),
            user.color3,
        )
    except Exception as e:
        logger.warning(f"[gdicon] {form.key} 本地渲染失败: {type(e).__name__}: {e}")
        return None


async def fetch_one(user: GDUser, form: Form) -> Image.Image | None:
    """取单个 gamemode 的图标。渲染是 CPU 活，丢到线程里跑。"""
    return await asyncio.to_thread(_render_one, user, form)


async def fetch_all(user: GDUser) -> list[tuple[Form, Image.Image | None]]:
    """九个 gamemode 一起渲染。

    to_thread 走的是默认线程池，九个一起丢进去并行跑，最后只发一张拼好的图。
    """
    results = await asyncio.gather(*(fetch_one(user, f) for f in FORMS))
    return list(zip(FORMS, results, strict=True))


# ---------------------------------------------------------------- 拼图
GRID_COLS = 3
CELL = 132              # 正方形格子，紧紧挨着
ICON_BOX = 112          # 图标等比缩放到这个框内
PAD = 18
TITLE_H = 54
BG_COLOR = (255, 205, 232, 255)   # 粉色底，和卡片那套粉色调一致


def _fit(img: Image.Image, box: int) -> Image.Image:
    """等比缩放到 box×box 以内。各 gamemode 出来的图尺寸不一样，得归一化。"""
    scale = min(box / img.width, box / img.height)
    if scale >= 1:
        return img
    return img.resize(
        (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
        Image.Resampling.LANCZOS,
    )


def compose_sheet(user: GDUser, items: list[tuple[Form, Image.Image | None]]) -> Image.Image:
    """把九个图标拼成一张。

    粉色底，除了顶部居中的用户名之外不放任何文字 ——
    九个 gamemode 的顺序是固定的（cube/ship/ball 一行，以此类推），
    看图就知道哪个是哪个，标签纯属占地方。
    """
    from .draw import draw_outlined_text

    rows = -(-len(items) // GRID_COLS)
    width = PAD * 2 + CELL * GRID_COLS
    height = PAD * 2 + TITLE_H + CELL * rows

    canvas = Image.new("RGBA", (width, height), BG_COLOR)
    draw = ImageDraw.Draw(canvas)

    title_font = _load_font(RES_DIR / "PUSAB.TTF", 38)
    name = user.user_name or "?"
    name_w = draw.textbbox((0, 0), name, font=title_font)[2]
    draw_outlined_text(draw, ((width - name_w) // 2, PAD - 2), name, title_font,
                       fill="white", outline="black", outline_width=3)

    for idx, (_form, icon) in enumerate(items):
        cx = PAD + (idx % GRID_COLS) * CELL
        cy = PAD + TITLE_H + (idx // GRID_COLS) * CELL
        if icon is None:
            continue
        fitted = _fit(icon, ICON_BOX)
        canvas.paste(
            fitted,
            (cx + (CELL - fitted.width) // 2, cy + (CELL - fitted.height) // 2),
            fitted,
        )

    return canvas
