"""本地渲染 Geometry Dash 图标：从随插件发布的图集 zip 按 plist 拼出图标。

对照 gdbrowser iconkit 的 icon.js 重写合成逻辑，修掉原来几个问题：

1. 所有图层改用**公共原点 + spriteOffset 定位**（每层锚点在 (offset.x, -offset.y)），
   不再用每层各自画布再居中的做法；
2. ufo 圆顶层 `_3_001`（白色）按 icon.js 图层顺序插入：glow → dome → col2 → col1 → extra；
3. robot/spider 按 icon.js 的 positionPart 逐部件摆放：部件帧是 `_01`~`_04`，
   布局数据取自 iconkit 的 robotAnimations idle 帧；
4. 辉光颜色规则对齐游戏：color3 优先，没有 color3 时若 color2 是黑就用 color1；
   主色是黑时游戏强制开辉光；
5. tint 用逐通道乘法（PIXI tint 语义），不再用亮度近似。
"""

import io
import math
import plistlib
import re
import zipfile

from PIL import Image, ImageColor

from ..paths import ICONS_ZIP

# 颜色索引表（GD 颜色 id -> 十六进制色值）
color_map = {
    0: "#7dff00", 1: "#00ff00", 2: "#00ff7d", 3: "#00ffff",
    4: "#007dff", 5: "#0000ff", 6: "#7d00ff", 7: "#ff00ff",
    8: "#ff007d", 9: "#ff0000", 10: "#ff7d00", 11: "#ffff00",
    12: "#ffffff", 13: "#b900ff", 14: "#ffb900", 15: "#000000",
    16: "#00c8ff", 17: "#afafaf", 18: "#5a5a5a", 19: "#ff7d7d",
    20: "#00af4b", 21: "#007d7d", 22: "#004baf", 23: "#4b00af",
    24: "#7d007d", 25: "#af004b", 26: "#af4b00", 27: "#7d7d00",
    28: "#4baf00", 29: "#ff4b00", 30: "#963200", 31: "#966400",
    32: "#649600", 33: "#009664", 34: "#006496", 35: "#640096",
    36: "#960064", 37: "#960000", 38: "#009600", 39: "#000096",
    40: "#7dffaf", 41: "#7d7dff", 42: "#fffa7f", 43: "#fa7fff",
    44: "#00ffc0", 45: "#50320e", 46: "#cda576", 47: "#b680ff",
    48: "#ff3a3a", 49: "#4d4d8f", 50: "#000a4c", 51: "#fdd4ce",
    52: "#beb5ff", 53: "#700000", 54: "#520200", 55: "#380106",
    56: "#804f4f", 57: "#7a3535", 58: "#512424", 59: "#a36246",
    60: "#754936", 61: "#563528", 62: "#ffb972", 63: "#ffa040",
    64: "#66311e", 65: "#5b2700", 66: "#472000", 67: "#a77b4d",
    68: "#6d5339", 69: "#513e2a", 70: "#ffffc0", 71: "#fde0a0",
    72: "#c0ffa0", 73: "#b1ff6d", 74: "#c0ffe0", 75: "#94ffe4",
    76: "#43a18a", 77: "#316d5f", 78: "#265449", 79: "#006000",
    80: "#004000", 81: "#006060", 82: "#004040", 83: "#a0ffff",
    84: "#010770", 85: "#00496d", 86: "#00324c", 87: "#002638",
    88: "#5080ad", 89: "#335375", 90: "#233c56", 91: "#e0e0e0",
    92: "#3d068c", 93: "#370860", 94: "#404040", 95: "#6f49a4",
    96: "#54367f", 97: "#422a63", 98: "#fcb5ff", 99: "#af57af",
    100: "#824382", 101: "#5e315e", 102: "#808080", 103: "#66033e",
    104: "#470134", 105: "#d2ff32", 106: "#76bdff",
}
FALLBACK_COLOR = "#ffffff"

# 静态渲染时把图标在画布里的纵向位置对齐到和游戏一致
Y_OFFSETS = {"player_ball": -10, "bird": 30, "spider": 7, "swing": -15}
POSITION_MULTIPLIER = 4

# robot/spider 的静态部件布局（来自 iconkit robotAnimations 的 idle 帧）
ROBOT_PARTS = {
    "robot": {
        "names": [
            "Back leg", "Back connector", "Left foot", "Head",
            "Front leg", "Front connector", "Front foot",
        ],
        "tints": {"0": 178, "1": 178, "2": 178},
        "slots": [
            {"part": 3, "pos": [-7.175, -6.875], "scale": [0.9969, 0.9984],
             "rotation": -29.6729, "flipped": [False, False], "z": 0},
            {"part": 2, "pos": [-7.175, -1.025], "scale": [0.9968, 0.9984],
             "rotation": 57.9682, "flipped": [False, False], "z": 1},
            {"part": 4, "pos": [-2.675, -10.9], "scale": [1, 1],
             "rotation": 0, "flipped": [False, False], "z": 2},
            {"part": 1, "pos": [0.25, 5.5], "scale": [0.9997, 0.9998],
             "rotation": -2.2859, "flipped": [False, False], "z": 3},
            {"part": 3, "pos": [-4.525, -6.625], "scale": [0.9999, 0.9999],
             "rotation": -42.9415, "flipped": [False, False], "z": 4},
            {"part": 2, "pos": [-5.75, -2.15], "scale": [0.9994, 0.9997],
             "rotation": 42.5012, "flipped": [False, False], "z": 5},
            {"part": 4, "pos": [2.275, -10.9], "scale": [1, 1],
             "rotation": 0, "flipped": [False, False], "z": 6},
        ],
    },
    "spider": {
        "names": ["Leg 3", "Leg 4", "Connector", "Head", "Leg 1", "Leg 2"],
        "tints": {"0": 127, "1": 127},
        "slots": [
            {"part": 2, "pos": [5.025, -6.725], "scale": [0.8838, 0.8838],
             "rotation": 0, "flipped": [False, False], "z": 0},
            {"part": 2, "pos": [14.35, -6.725], "scale": [0.8838, 0.8838],
             "rotation": 0, "flipped": [True, False], "z": 1},
            {"part": 4, "pos": [-4.45, 0.075], "scale": [1, 1],
             "rotation": -7.6821, "flipped": [False, False], "z": 2},
            {"part": 1, "pos": [0.575, 4.05], "scale": [1, 1],
             "rotation": 0, "flipped": [False, False], "z": 3},
            {"part": 3, "pos": [-13.3, -6.9], "scale": [0.9999, 0.9999],
             "rotation": 38.964, "flipped": [False, False], "z": 4},
            {"part": 2, "pos": [-2.475, -5.975], "scale": [1, 1],
             "rotation": 0, "flipped": [False, False], "z": 5},
        ],
    },
}


# ---------- 解析辅助 ----------

def parse_rect(text):
    """解析 {{x,y},{w,h}} 格式的矩形"""
    text = text.replace("{", "").replace("}", "")
    nums = [int(float(x.strip())) for x in text.split(",")]
    return nums[0], nums[1], nums[2], nums[3]


def parse_offset(text):
    """解析 {x,y} 格式的偏移"""
    text = text.replace("{", "").replace("}", "")
    x, y = text.split(",")
    return int(float(x)), int(float(y))


# ---------- 染色 ----------

def tint_image(img, color):
    """逐通道乘法染色，等价 PIXI 的 tint：结果 = 贴图RGB × 目标色 / 255。

    白色贴图行为就是"白→目标色、黑→黑"；关键是不能用亮度灰度近似——
    有些图标贴图自带彩色（比如 ball 75 的红色舌头），乘白色必须原样保留红色。
    """
    img = img.convert("RGBA")
    target_r, target_g, target_b = ImageColor.getrgb(color)
    r, g, b, a = img.split()
    r_lut = [v * target_r // 255 for v in range(256)]
    g_lut = [v * target_g // 255 for v in range(256)]
    b_lut = [v * target_b // 255 for v in range(256)]
    return Image.merge("RGBA", (r.point(r_lut), g.point(g_lut), b.point(b_lut), a))


def _darken(img, factor):
    """按 factor 压暗 RGB（对应 icon.js 对 robot/spider"背面"部件的黑色叠加滤镜）。"""
    if factor >= 1.0:
        return img
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    lut = [round(v * factor) for v in range(256)]
    return Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))


# ---------- 图集读取 ----------

def crop_frame(atlas, frame):
    """从图集裁剪，处理旋转及宽高交换。"""
    x, y, w, h = parse_rect(frame["textureRect"])
    if frame.get("textureRotated"):
        img = atlas.crop((x, y, x + h, y + w))
        img = img.transpose(Image.Transpose.ROTATE_90)
    else:
        img = atlas.crop((x, y, x + w, y + h))
    return img


def _read_archive() -> dict[str, bytes]:
    """Decompress the icon atlas zip into memory.

    It holds 1207 plists plus 1207 atlases (~11 MB raw). Re-opening the zip for
    every plist and every atlas lookup was the slow path, and the archive never
    changes while the bot runs, so it is read once here, at import time
    (plugin load == bot startup).
    """
    with zipfile.ZipFile(ICONS_ZIP) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


#: ICONS_ZIP entry name -> raw bytes, filled once at startup.
ICON_ARCHIVE: dict[str, bytes] = _read_archive()


def _read_entry(name: str) -> bytes:
    """Read one archive entry; a missing name reports like a missing file."""
    try:
        return ICON_ARCHIVE[name]
    except KeyError:
        raise FileNotFoundError(f"{name} is not in {ICONS_ZIP.name}") from None


def _load_plist(plist_file):
    return plistlib.loads(_read_entry(plist_file))


def _load_atlas(data):
    # textureFileName looks like "icons/player_01-uhd.png"; archive entries are flat.
    png_name = data["metadata"]["textureFileName"].removeprefix("icons/")
    return Image.open(io.BytesIO(_read_entry(png_name))).convert("RGBA")


# ---------- 图层合成 ----------

def _compose_layers(layers, form=""):
    """把 (图, 中心x, 中心y) 按顺序合成到公共画布，再套用该 gamemode 的纵向偏移。"""
    if not layers:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    min_x = min(cx - img.width / 2 for img, cx, _cy in layers)
    max_x = max(cx + img.width / 2 for img, cx, _cy in layers)
    min_y = min(cy - img.height / 2 for img, _cx, cy in layers)
    max_y = max(cy + img.height / 2 for img, _cx, cy in layers)
    width = max(1, math.ceil(max_x - min_x))
    height = max(1, math.ceil(max_y - min_y))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for img, cx, cy in layers:
        px = round(cx - min_x - img.width / 2)
        py = round(cy - min_y - img.height / 2)
        canvas.alpha_composite(img, (px, py))

    offset = Y_OFFSETS.get(form, 0)
    if offset:
        out = Image.new("RGBA", (width, height + abs(offset)), (0, 0, 0, 0))
        out.alpha_composite(canvas, (0, max(offset, 0)))
        canvas = out
    return canvas


def _place_part(img, offset, slot):
    """按 icon.js 的 positionPart 摆放 robot/spider 的一个部件图层。

    spriteOffset 是部件图层自己的偏移，动画帧的 pos 按 uhd 倍率放大；
    容器会先缩放/翻转、再绕容器原点旋转。
    """
    ox, oy = offset
    sx, sy = slot["scale"]
    rot = slot["rotation"]
    fx, fy = slot["flipped"]
    px, py = slot["pos"]

    center = (px * POSITION_MULTIPLIER, -py * POSITION_MULTIPLIER)
    lx, ly = ox * sx, oy * sy
    if fx:
        lx = -lx
    if fy:
        ly = -ly
    rad = math.radians(rot)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    cx = center[0] + lx * cos_r - ly * sin_r
    cy = center[1] + lx * sin_r + ly * cos_r

    width, height = img.size
    new_w = max(1, round(width * abs(sx)))
    new_h = max(1, round(height * abs(sy)))
    if (new_w, new_h) != (width, height):
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    if fx:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if fy:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if rot:
        # PIXI 正角 = 屏幕顺时针，PIL 正角 = 逆时针，取反
        img = img.rotate(-rot, resample=Image.Resampling.BICUBIC,
                         expand=True, fillcolor=(0, 0, 0, 0))
    return img, (cx, cy)


# ---------- 前缀 / 帧查找 ----------

def _detect_prefix(frames):
    """普通形态：找主体帧（不含 _2/_extra/_glow），去掉尾随 _<num> 得到前缀。"""
    for n in frames:
        if not n.endswith(".png"):
            continue
        if "_2_" in n or "_extra_" in n or "_glow_" in n:
            continue
        stem = n[:-4]
        return re.sub(r"_\d+$", "", stem)
    raise RuntimeError("无法找到主体图片")


def _detect_prefix_complex(frames, fallback):
    """robot/spider：期望 <prefix>_<part>_<num>，去掉两级尾缀。"""
    for n in frames:
        if not n.endswith(".png"):
            continue
        if "_2_" in n or "_extra_" in n or "_glow_" in n:
            continue
        m = re.match(r"^(.*_\d+)_\d+_\d+$", n[:-4])
        if m:
            return m.group(1)
    return fallback


def _section(frames, atlas, prefix, suffix):
    """按 <prefix><suffix>_*.png 匹配帧，返回 (img, cx, cy)。suffix 为空即主体。"""
    for name, frame in frames.items():
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        if suffix:
            if not rest.startswith(suffix):
                continue
            after = rest[len(suffix):]
            if after and not after.startswith("_"):
                continue
        elif (rest.startswith("_2") or rest.startswith("_extra")
                or rest.startswith("_glow")):
            continue
        img = crop_frame(atlas, frame)
        ox, oy = parse_offset(frame["spriteOffset"])
        return img, float(ox), float(-oy)
    return None


# ---------- 构建器 ----------

def _build_simple(plist_file, color1, color2, extra_color, glow_on, glow_color, form):
    """普通单部件 gamemode（cube/ball/ship/ufo/wave/swing/jetpack）。"""
    data = _load_plist(plist_file)
    frames = data["frames"]
    atlas = _load_atlas(data)
    prefix = _detect_prefix(frames)

    # 顺序跟 icon.js 一致：glow → (dome) → col2 → col1 → white(extra)
    layers = []
    if glow_on:
        s = _section(frames, atlas, prefix, "_glow")
        if s:
            layers.append((tint_image(s[0], glow_color), s[1], s[2]))

    # ufo 圆顶：白色 _3 帧
    if form == "bird":
        s = _section(frames, atlas, prefix, "_3")
        if s:
            layers.append((tint_image(s[0], "#FFFFFF"), s[1], s[2]))

    s = _section(frames, atlas, prefix, "_2")
    if s:
        layers.append((tint_image(s[0], color2), s[1], s[2]))

    s = _section(frames, atlas, prefix, "")
    if s:
        layers.append((tint_image(s[0], color1), s[1], s[2]))

    s = _section(frames, atlas, prefix, "_extra")
    if s:
        layers.append((tint_image(s[0], extra_color), s[1], s[2]))

    return _compose_layers(layers, form)


def _build_complex(plist_file, color1, color2, glow_on, glow_color,
                   form, fallback_prefix):
    """robot / spider：按 idle 帧逐部件摆放，发光层垫底。"""
    data = _load_plist(plist_file)
    frames = data["frames"]
    atlas = _load_atlas(data)
    prefix = _detect_prefix_complex(frames, fallback_prefix)
    layout = ROBOT_PARTS[form]

    def part_section(part_no, suffix, tint_hex):
        # 兼容补零与不补零两种编号
        candidates = [
            f"{prefix}_{part_no:02d}{suffix}_001.png",
            f"{prefix}_{part_no}{suffix}_001.png",
        ]
        for name in candidates:
            frame = frames.get(name)
            if frame is None:
                continue
            img = tint_image(crop_frame(atlas, frame), tint_hex)
            ox, oy = parse_offset(frame["spriteOffset"])
            return img, (float(ox), float(-oy))
        return None

    glow_layers = []
    part_layers = []
    for slot_idx, slot in enumerate(layout["slots"]):
        part_no = slot["part"]
        darken = layout["tints"].get(str(slot_idx))

        if glow_on and (gl := part_section(part_no, "_glow", glow_color)):
            img, offset = gl
            img, (cx, cy) = _place_part(img, offset, slot)
            glow_layers.append((img, cx, cy, slot["z"]))

        # 顺序跟 icon.js 一致：col2 在底、col1 在上、白色 extra 最顶
        for suffix, tint_hex in (("_2", color2), ("", color1), ("_extra", "#FFFFFF")):
            if piece := part_section(part_no, suffix, tint_hex):
                img, offset = piece
                if darken:
                    img = _darken(img, darken / 255.0)
                img, (cx, cy) = _place_part(img, offset, slot)
                part_layers.append((img, cx, cy, slot["z"]))

    glow_layers.sort(key=lambda x: x[3])
    part_layers.sort(key=lambda x: x[3])
    layers = [(img, cx, cy) for img, cx, cy, _z in glow_layers]
    layers += [(img, cx, cy) for img, cx, cy, _z in part_layers]
    return _compose_layers(layers, form)


# ---------- 对外接口 ----------

def _color_index(value, default=0):
    """把颜色字段规整成颜色表里的 int 下标。

    GDUser 正常解析出来是 int，但解析失败时 gdapi 会把原始字符串原样留下，
    字符串/超范围值会被 color_map.get 兜底成白色，整个图标就白了。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_icon_from_cols(type, icon, color1, color2, glow, glowc=-1):
    """根据颜色索引快速生成图标。

    glowc 对应用户的 color3（GD 叫 glow color）。游戏规则：
    - 有 color3 用它当辉光色；没有时 color2 是黑色（id 15）就用 color1，否则用 color2；
    - 主色是黑色时游戏强制开辉光。
    """
    icon_id = max(1, int(icon or 1))
    color1 = _color_index(color1)
    color2 = _color_index(color2)
    glowc = None if glowc is None or glowc == -1 else _color_index(glowc)
    glow_id = (color1 if color2 == 15 else color2) if glowc is None else glowc
    glow_on = bool(glow) or color1 == 15

    # Archive entries are zero-padded to two digits: player_01-uhd.plist
    plist_file = f"{type}_{icon_id:02d}-uhd.plist"
    c1 = color_map.get(color1, FALLBACK_COLOR)
    c2 = color_map.get(color2, FALLBACK_COLOR)
    cg = color_map.get(glow_id, FALLBACK_COLOR)

    if type in ROBOT_PARTS:
        return _build_complex(plist_file, c1, c2, glow_on, cg, type,
                              f"{type}_{icon_id:02d}")
    return _build_simple(plist_file, c1, c2, "#FFFFFF", glow_on, cg, type)
