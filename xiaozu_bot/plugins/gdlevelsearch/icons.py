"""*gdicon：把玩家各个 gamemode 的图标画出来。

图标 id 和配色 GDUser 已经解析好了（acc_icon / acc_ship / ... 和 color/color2/color3），
这里只负责去拿图和拼图。

用的是 gdicon.oat.zone。实测 gdbrowser 那个 /icon/ 路由只认 form 和 icon，
col1/col2 传什么都一样（RobTop 和 Riot 拿回来的图字节完全相同），
颜色丢了图标就没意义了，所以没用它。
"""

import asyncio
import io
from typing import NamedTuple, Optional

import httpx
from nonebot import logger
from PIL import Image, ImageDraw

from .draw import RES_DIR, _load_font
from .gdapi import GDUser

ICON_BASE = "https://gdicon.oat.zone/icon.png"
ICON_TIMEOUT = 15
ICON_RETRIES = 2
HTTP_OK = 200


class Form(NamedTuple):
    """一个 gamemode：命令里叫什么、接口里叫什么、图标 id 存在 GDUser 的哪个字段"""

    key: str          # 用户输入的名字
    api_type: str     # gdicon 的 type 参数
    attr: str         # GDUser 上的属性名
    label: str        # 拼图时显示的标题


FORMS: tuple[Form, ...] = (
    Form("cube", "cube", "acc_icon", "Cube"),
    Form("ship", "ship", "acc_ship", "Ship"),
    Form("ball", "ball", "acc_ball", "Ball"),
    Form("ufo", "ufo", "acc_bird", "UFO"),
    Form("wave", "wave", "acc_dart", "Wave"),
    Form("robot", "robot", "acc_robot", "Robot"),
    Form("spider", "spider", "acc_spider", "Spider"),
    Form("swing", "swing", "acc_swing", "Swing"),
    Form("jetpack", "jetpack", "acc_jetpack", "Jetpack"),
)

# 一些常见的别名，省得非要记住接口里那个词
ALIASES: dict[str, str] = {
    "bird": "ufo", "ufo": "ufo", "飞碟": "ufo",
    "dart": "wave", "wave": "wave", "波": "wave",
    "方块": "cube", "船": "ship", "球": "ball",
    "机器人": "robot", "蜘蛛": "spider", "秋千": "swing", "喷气背包": "jetpack",
}

FORM_BY_KEY = {f.key: f for f in FORMS}

DEFAULT_FORM = "cube"


def resolve_form(name: str) -> Optional[Form]:
    key = ALIASES.get(name.lower(), name.lower())
    return FORM_BY_KEY.get(key)


def form_names() -> str:
    return " / ".join(f.key for f in FORMS)


async def _fetch_icon(
    client: httpx.AsyncClient,
    form: Form,
    icon_id: int,
    col1: int,
    col2: int,
    glow: bool,  # noqa: FBT001
) -> Optional[Image.Image]:
    """取一个图标。失败返回 None，不抛。"""
    params = {
        "type": form.api_type,
        "value": max(1, icon_id or 1),
        "color1": col1,
        "color2": col2,
    }
    if glow:
        params["glow"] = "true"

    for attempt in range(1, ICON_RETRIES + 1):
        try:
            resp = await client.get(ICON_BASE, params=params, timeout=ICON_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[gdicon] {form.key} 第 {attempt} 次失败: {type(e).__name__}")
        else:
            if resp.status_code == HTTP_OK and resp.content:
                try:
                    return Image.open(io.BytesIO(resp.content)).convert("RGBA")
                except Exception:  # noqa: BLE001
                    logger.warning(f"[gdicon] {form.key} 拿到了但解不开")
                    return None
            logger.warning(f"[gdicon] {form.key} HTTP {resp.status_code}")
        if attempt < ICON_RETRIES:
            await asyncio.sleep(0.5)
    return None


async def fetch_one(user: GDUser, form: Form) -> Optional[Image.Image]:
    """取单个 gamemode 的图标"""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        return await _fetch_icon(
            client,
            form,
            getattr(user, form.attr, 1) or 1,
            user.color or 0,
            user.color2 or 0,
            bool(user.acc_glow),
        )


async def fetch_all(user: GDUser) -> list[tuple[Form, Optional[Image.Image]]]:
    """九个 gamemode 一起取。

    是并发不是连发 —— 九次请求同时出去，一轮就回来，
    而且最后只发一张合成图，不会在群里刷九条。
    """
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            *(
                _fetch_icon(
                    client,
                    f,
                    getattr(user, f.attr, 1) or 1,
                    user.color or 0,
                    user.color2 or 0,
                    bool(user.acc_glow),
                )
                for f in FORMS
            )
        )
    return list(zip(FORMS, results))


# ---------------------------------------------------------------- 拼图
GRID_COLS = 3
CELL = 132              # 正方形格子，紧凑排
ICON_BOX = 112          # 图标等比缩放到这个框内
PAD = 18
TITLE_H = 54
BG_COLOR = (255, 205, 232, 255)   # 纯色底，和卡片那套粉色调一致


def _fit(img: Image.Image, box: int) -> Image.Image:
    """等比缩放到 box×box 以内。各 gamemode 出来的图尺寸不一样，得归一化。"""
    scale = min(box / img.width, box / img.height)
    if scale >= 1:
        return img
    return img.resize(
        (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
        Image.Resampling.LANCZOS,
    )


def compose_sheet(user: GDUser, items: list[tuple[Form, Optional[Image.Image]]]) -> Image.Image:
    """把九个图标拼成一张。

    纯色底，除了顶部居中的用户名之外不放任何文字 ——
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
