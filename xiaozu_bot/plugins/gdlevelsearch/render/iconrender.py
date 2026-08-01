"""本地渲染 Geometry Dash 图标：从随插件发布的图集 zip 里按 plist 拼出图标。

对照 gdbrowser iconkit 的 icon.js 重写过合成逻辑，修掉原来几个 gamemode 的错位：

1. 所有图层改用**公共原点 + spriteOffset 定位**（每层锚点在 (offset.x, -offset.y)），
   不再用每层各自画布再居中的做法——那是 ufo/robot/spider 错位的根源；
2. ufo 补上圆顶层 `_3_001`（白色），图层顺序按 icon.js：glow → ufo 圆顶 → col2 → col1 → white(extra)；
3. robot/spider 按 icon.js 的 positionPart 逐部件摆放：部件帧是 `_01`~`_04`，
   布局数据来自 iconkit 的 robotAnimations idle 帧（pos/scale/rotation/flip/z + 压暗 tint）；
4. 辉光颜色规则对齐游戏：color3 优先，没有 color3 时 color2 是黑色就用 color1；
   主色是黑色时游戏强制开辉光，也一并处理。
"""

import io
import math
import plistlib
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
# 颜色 id 超出索引表（以后 GD 又加颜色）时兜底，别让整个图标渲染失败
FALLBACK_COLOR = "#ffffff"


# 静态渲染时把图标在画布里的纵向位置对齐到和游戏一致（iconkit 的 yOffsets）
Y_OFFSETS = {"player_ball": -10, "bird": 30, "spider": 7, "swing": -15}

# uhd 图集的坐标倍率（iconkit 的 positionMultipliers.uhd）
POSITION_MULTIPLIER = 4

# robot/spider 的静态部件布局：来自 gdbrowser iconkit 的 iconStuff.robotAnimations，
# 只保留静态渲染需要的 idle 帧。每个 slot 是一个部件位：
# part = 图集帧后缀（_01~_04），其余字段对应 icon.js positionPart 的 pos/scale/rotation/flipped/z；
# tints 里 key 是 slot 下标，非空时该部件要按 tint/255 压暗（游戏里"背面"部件更暗）。
ROBOT_PARTS = {
    "robot": {
        "names": [
            "Back leg",
            "Back connector",
            "Left foot",
            "Head",
            "Front leg",
            "Front connector",
            "Front foot"
        ],
        "tints": {
            "0": 178,
            "1": 178,
            "2": 178
        },
        "slots": [
            {
                "part": 3,
                "pos": [
                    -7.175,
                    -6.875
                ],
                "scale": [
                    0.9969,
                    0.9984
                ],
                "rotation": -29.6729,
                "flipped": [
                    False,
                    False
                ],
                "z": 0
            },
            {
                "part": 2,
                "pos": [
                    -7.175,
                    -1.025
                ],
                "scale": [
                    0.9968,
                    0.9984
                ],
                "rotation": 57.9682,
                "flipped": [
                    False,
                    False
                ],
                "z": 1
            },
            {
                "part": 4,
                "pos": [
                    -2.675,
                    -10.9
                ],
                "scale": [
                    1,
                    1
                ],
                "rotation": 0,
                "flipped": [
                    False,
                    False
                ],
                "z": 2
            },
            {
                "part": 1,
                "pos": [
                    0.25,
                    5.5
                ],
                "scale": [
                    0.9997,
                    0.9998
                ],
                "rotation": -2.2859,
                "flipped": [
                    False,
                    False
                ],
                "z": 3
            },
            {
                "part": 3,
                "pos": [
                    -4.525,
                    -6.625
                ],
                "scale": [
                    0.9999,
                    0.9999
                ],
                "rotation": -42.9415,
                "flipped": [
                    False,
                    False
                ],
                "z": 4
            },
            {
                "part": 2,
                "pos": [
                    -5.75,
                    -2.15
                ],
                "scale": [
                    0.9994,
                    0.9997
                ],
                "rotation": 42.5012,
                "flipped": [
                    False,
                    False
                ],
                "z": 5
            },
            {
                "part": 4,
                "pos": [
                    2.275,
                    -10.9
                ],
                "scale": [
                    1,
                    1
                ],
                "rotation": 0,
                "flipped": [
                    False,
                    False
                ],
                "z": 6
            }
        ]
    },
    "spider": {
        "names": [
            "Leg 3",
            "Leg 4",
            "Connector",
            "Head",
            "Leg 1",
            "Leg 2"
        ],
        "tints": {
            "0": 127,
            "1": 127
        },
        "slots": [
            {
                "part": 2,
                "pos": [
                    5.025,
                    -6.725
                ],
                "scale": [
                    0.8838,
                    0.8838
                ],
                "rotation": 0,
                "flipped": [
                    False,
                    False
                ],
                "z": 0
            },
            {
                "part": 2,
                "pos": [
                    14.35,
                    -6.725
                ],
                "scale": [
                    0.8838,
                    0.8838
                ],
                "rotation": 0,
                "flipped": [
                    True,
                    False
                ],
                "z": 1
            },
            {
                "part": 4,
                "pos": [
                    -4.45,
                    0.075
                ],
                "scale": [
                    1,
                    1
                ],
                "rotation": -7.6821,
                "flipped": [
                    False,
                    False
                ],
                "z": 2
            },
            {
                "part": 1,
                "pos": [
                    0.575,
                    4.05
                ],
                "scale": [
                    1,
                    1
                ],
                "rotation": 0,
                "flipped": [
                    False,
                    False
                ],
                "z": 3
            },
            {
                "part": 3,
                "pos": [
                    -13.3,
                    -6.9
                ],
                "scale": [
                    0.9999,
                    0.9999
                ],
                "rotation": 38.964,
                "flipped": [
                    False,
                    False
                ],
                "z": 4
            },
            {
                "part": 2,
                "pos": [
                    -2.475,
                    -5.975
                ],
                "scale": [
                    1,
                    1
                ],
                "rotation": 0,
                "flipped": [
                    False,
                    False
                ],
                "z": 5
            }
        ]
    }
}


def _read_zip_entry(name: str) -> bytes:
    """从图集 zip 里读一个文件（plist 或 atlas png）。"""
    with zipfile.ZipFile(ICONS_ZIP) as zf:
        return zf.read(name)


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


def tint_image(img, color):
    """逐通道乘法染色，等价 PIXI 的 tint：结果 = 贴图RGB × 目标色 / 255。

    白色贴图行为就是"白→目标色、黑→黑"；关键是不能用亮度灰度近似——
    有些图标贴图自带彩色（比如 ball 75 的红色舌头），乘白色必须原样保留红色，
    用亮度近似会把红色抹成深灰/黑。
    """
    img = img.convert("RGBA")
    target_r, target_g, target_b = ImageColor.getrgb(color)
    r, g, b, a = img.split()
    r_lut = [v * target_r // 255 for v in range(256)]
    g_lut = [v * target_g // 255 for v in range(256)]
    b_lut = [v * target_b // 255 for v in range(256)]
    return Image.merge("RGBA", (r.point(r_lut), g.point(g_lut), b.point(b_lut), a))


def _darken(img: Image.Image, factor: float) -> Image.Image:
    """按 factor 压暗 RGB（对应 icon.js 里对 robot/spider"背面"部件的黑色叠加滤镜）。"""
    if factor >= 1.0:
        return img
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    lut = [round(v * factor) for v in range(256)]
    return Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))


def crop_frame(atlas, frame):
    """从图集裁剪，处理旋转及宽高交换。"""
    x, y, w, h = parse_rect(frame["textureRect"])
    if frame.get("textureRotated"):
        img = atlas.crop((x, y, x + h, y + w))
        img = img.transpose(Image.Transpose.ROTATE_90)
    else:
        img = atlas.crop((x, y, x + w, y + h))
    return img


def _load_atlas(data):
    png_name = data["metadata"]["textureFileName"]
    return Image.open(io.BytesIO(_read_zip_entry(png_name[6:]))).convert("RGBA")


def _compose_layers(layers, form: str) -> Image.Image:
    """把 (图, 中心x, 中心y) 按顺序合成一张，再套用该 gamemode 的纵向偏移。"""
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


def _build_simple(form: str, icon_id: int, color1_id: int, color2_id: int, glow_on: bool, glow_id: int) -> Image.Image:
    """普通单部件 gamemode（cube/ball/ship/ufo/wave/swing/jetpack）。"""
    plist_file = f"{form}_{icon_id:02d}-uhd.plist"
    data = plistlib.loads(_read_zip_entry(plist_file))
    frames = data["frames"]
    atlas = _load_atlas(data)
    color1 = color_map.get(color1_id, FALLBACK_COLOR)
    color2 = color_map.get(color2_id, FALLBACK_COLOR)
    glow_col = color_map.get(glow_id, FALLBACK_COLOR)

    def section(suffix: str, tint_hex: str) -> tuple[Image.Image, float, float] | None:
        name = f"{form}_{icon_id:02d}{suffix}_001.png"
        frame = frames.get(name)
        if frame is None:
            return None
        img = tint_image(crop_frame(atlas, frame), tint_hex)
        ox, oy = parse_offset(frame["spriteOffset"])
        return img, float(ox), float(-oy)

    # 顺序跟 icon.js 一致：glow → ufo 圆顶 → col2 → col1 → white(extra)
    layers: list[tuple[Image.Image, float, float]] = []
    if glow_on and (glow := section("_glow", glow_col)):
        layers.append(glow)
    if form == "bird" and (dome := section("_3", "#FFFFFF")):
        layers.append(dome)
    if second := section("_2", color2):
        layers.append(second)
    if main := section("", color1):
        layers.append(main)
    if extra := section("_extra", "#FFFFFF"):
        layers.append(extra)
    return _compose_layers(layers, form)


def _place_part(img: Image.Image, offset, slot) -> tuple[Image.Image, tuple[float, float]]:
    """按 icon.js 的 positionPart 摆放 robot/spider 的一个部件图层。

    spriteOffset 是部件图层自己的偏移（iconkit 里不做倍率换算），
    动画帧的 pos 按 uhd 倍率放大；容器会先缩放/翻转、再绕容器原点旋转。
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
        img = img.rotate(
            -rot, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(0, 0, 0, 0)
        )
    return img, (cx, cy)


def _build_complex(form: str, icon_id: int, color1_id: int, color2_id: int, glow_on: bool, glow_id: int) -> Image.Image:
    """robot / spider：按 idle 帧逐部件摆放，发光层垫底。"""
    plist_file = f"{form}_{icon_id:02d}-uhd.plist"
    data = plistlib.loads(_read_zip_entry(plist_file))
    frames = data["frames"]
    atlas = _load_atlas(data)
    layout = ROBOT_PARTS[form]
    color1 = color_map.get(color1_id, FALLBACK_COLOR)
    color2 = color_map.get(color2_id, FALLBACK_COLOR)
    glow_col = color_map.get(glow_id, FALLBACK_COLOR)

    def part_section(part_no: int, suffix: str, tint_hex: str):
        name = f"{form}_{icon_id:02d}_{part_no:02d}{suffix}_001.png"
        frame = frames.get(name)
        if frame is None:
            return None
        img = tint_image(crop_frame(atlas, frame), tint_hex)
        ox, oy = parse_offset(frame["spriteOffset"])
        return img, (float(ox), float(-oy))

    glow_layers: list[tuple[Image.Image, float, float, int]] = []
    part_layers: list[tuple[Image.Image, float, float, int]] = []
    for slot_idx, slot in enumerate(layout["slots"]):
        part_no = slot["part"]
        darken = layout["tints"].get(str(slot_idx))

        if glow_on and (gl := part_section(part_no, "_glow", glow_col)):
            img, offset = gl
            img, (cx, cy) = _place_part(img, offset, slot)
            glow_layers.append((img, cx, cy, slot["z"]))

        # 顺序跟 icon.js 一致：col2 在底、col1 在上、白色 extra 最顶（不能反过来，否则第二色会盖住主体）
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


def _color_index(value: object, default: int = 0) -> int:
    """把颜色字段规整成颜色表里的 int 下标。

    GDUser 正常解析出来是 int，但解析失败时 gdapi 会把原始字符串原样留下，
    字符串/超范围值会被 color_map.get 兜底成白色，整个图标就白了。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_icon_from_cols(type: str, icon: int, color1: int, color2: int, glow: bool, glowc: int | None = None):
    """根据颜色索引快速生成图标。

    glowc 对应用户的 color3（GD 叫 glow color）。游戏规则：
    - 有 color3 用它当辉光色；没有时 color2 是黑色（id 15）就用 color1，否则用 color2；
    - 主色是黑色时游戏强制开辉光。
    """
    icon_id = max(1, int(icon or 1))
    color1 = _color_index(color1)
    color2 = _color_index(color2)
    glowc = None if glowc is None else _color_index(glowc, -1)
    glow_id = (color1 if color2 == 15 else color2) if glowc is None else glowc
    glow_on = bool(glow) or color1 == 15
    if type in ROBOT_PARTS:
        return _build_complex(type, icon_id, color1, color2, glow_on, glow_id)
    return _build_simple(type, icon_id, color1, color2, glow_on, glow_id)
