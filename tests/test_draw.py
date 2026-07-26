"""gdlevelsearch/draw.py 里那些不需要真出图的部分。

draw.py 是仓库第二大的模块（545 条语句），之前一条测试都没有 —— 19% 的覆盖率
全是 import 期执行的模块级常量，所有函数体都是空白。

`create_level_image` / `create_image_from_gdlevel` 是几百行 PIL 排版，
断言「第 731 个像素是不是 #3a86e4」既没意义又一改就红，这里不碰。
真正值得测的是四个纯函数和两个薄封装：

- `wrap_text_by_width`：贪心断行 + 超长单词的逐字符兜底，是真有分支的算法；
- `_thumbnail_id_for`：一张写死的映射表，边界一擦就错；
- `create_vertical_gradient` / `rounded_image`：像素级契约可以直接读回来验；
- `_load_font`：缺字体文件时的降级路径（新克隆就是这个状态）；
- `_fetch_thumbnail`：重试/404 不重试/退避时长，用 stub_httpx 走完整条真实请求路径。
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

from xiaozu_bot.plugins.gdlevelsearch import draw
from xiaozu_bot.plugins.gdlevelsearch.draw import (
    _fetch_thumbnail,
    _load_font,
    _none,
    _thumbnail_id_for,
    create_vertical_gradient,
    rounded_image,
    select_tags,
    wrap_text_by_width,
)
from xiaozu_bot.plugins.gdlevelsearch.platapi import PlatInfo

# ==========================================================================
# 公共工具
# ==========================================================================


class MonoFont:
    """每个字符固定宽度的假字体。

    `wrap_text_by_width` 对字体的全部要求就是「有 getbbox，返回的元组
    第 2 项减第 0 项是像素宽」。用真 TTF 的话每个字符宽度都不一样，
    断言就得跟着字体文件走，改个字体全红；固定宽度才能把断行算法本身
    的分支一条条钉死。下面另有一条用真 PIL 字体的用例，保证接口没错位。
    """

    def __init__(self, char_width: int = 10) -> None:
        self.char_width = char_width
        self.calls: list[str] = []

    def getbbox(self, text: str) -> tuple[int, int, int, int]:
        self.calls.append(text)
        return (0, 0, self.char_width * len(text), self.char_width)


def png_bytes(size: tuple[int, int] = (8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, (1, 2, 3, 255)).save(buf, format="PNG")
    return buf.getvalue()


# ==========================================================================
# select_tags
# ==========================================================================
class TestSelectTags:
    def test_returns_the_tags_list_itself(self) -> None:
        """就是把 PlatInfo.tags 原样递出来 —— 返回的是同一个 list，不是拷贝

        调用方（create_level_image 里拼侧边栏）如果就地改它，会改到 PlatInfo。
        """
        info = PlatInfo.from_dict({"id": "1", "name": "X", "tags": ["Deathless", "Precision"]})
        assert select_tags(info) == ["Deathless", "Precision"]
        assert select_tags(info) is info.tags

    def test_empty_tags(self) -> None:
        info = PlatInfo.from_dict({"id": "1", "name": "X"})
        assert select_tags(info) == []


# ==========================================================================
# _thumbnail_id_for
# ==========================================================================
class TestThumbnailIdFor:
    def test_none_becomes_empty_string(self) -> None:
        assert _thumbnail_id_for(None) == ""

    @pytest.mark.parametrize(
        ("level_id", "expected"),
        [(0, "0"), (1, "14"), (2, "18"), (3, "20")],
    )
    def test_official_first_four_use_the_lookup_table(
        self, level_id: int, expected: str
    ) -> None:
        """官方前几关在 levelthumbs 上的 id 和关卡 id 对不上，走写死的表"""
        assert _thumbnail_id_for(level_id) == expected

    @pytest.mark.parametrize("level_id", [4, 5, 128, 26681070])
    def test_everything_else_is_just_str(self, level_id: int) -> None:
        assert _thumbnail_id_for(level_id) == str(level_id)

    @pytest.mark.parametrize(
        ("level_id", "expected"),
        [(-1, "20"), (-2, "18"), (-3, "14"), (-4, "0")],
    )
    def test_negative_ids_index_from_the_end(self, level_id: int, expected: str) -> None:
        """⚠️ BUG：守卫写的是 `level_id <= 3`，负数也满足，于是从表尾倒着取

        -1 会拿到 "20"（本该是官方第 4 关的缩略图），静默给出一张错图，
        不会抛异常。守卫应该是 `0 <= level_id <= 3`。
        """
        assert _thumbnail_id_for(level_id) == expected

    def test_id_below_minus_four_raises_indexerror(self) -> None:
        """同一个洞再往下走一步就不是静默错图而是直接崩了"""
        with pytest.raises(IndexError):
            _thumbnail_id_for(-5)


# ==========================================================================
# wrap_text_by_width
# ==========================================================================
class TestWrapTextByWidth:
    def test_empty_string_yields_no_lines(self) -> None:
        """空串 -> []（不是 [""]）：末尾那句 `if current_line:` 把空行滤掉了"""
        assert wrap_text_by_width("", 100, MonoFont()) == []

    def test_short_text_stays_on_one_line(self) -> None:
        assert wrap_text_by_width("ab cd", 100, MonoFont()) == ["ab cd"]

    def test_exact_width_boundary_still_fits(self) -> None:
        """比较用的是 `<=`，正好等于 max_width 不换行"""
        font = MonoFont(char_width=10)
        assert wrap_text_by_width("abc", 30, font) == ["abc"]
        # 再多一个字符就放不下，于是走超长单词的逐字符拆分
        assert wrap_text_by_width("abcd", 30, font) == ["abc", "d"]

    def test_greedy_wrap_between_words(self) -> None:
        """能塞下就继续塞，塞不下才把当前行吐出去"""
        # 宽 10/字符、上限 50：'aa bbb' 是 6 字符 = 60 > 50
        assert wrap_text_by_width("aa bbb cc", 50, MonoFont(10)) == ["aa", "bbb", "cc"]

    def test_overlong_word_falls_back_to_character_split(self) -> None:
        """单个词就超宽时逐字符切，切剩的部分留在 current_line 上继续拼"""
        assert wrap_text_by_width("a bbbbb", 30, MonoFont(10)) == ["a", "bbb", "bb"]

    def test_newlines_split_paragraphs(self) -> None:
        assert wrap_text_by_width("ab\ncd", 100, MonoFont()) == ["ab", "cd"]

    def test_blank_paragraphs_are_dropped(self) -> None:
        """⚠️ 空行会被吃掉：'ab\\n\\ncd' 出来只有两行，排版上那个空行就没了"""
        assert wrap_text_by_width("ab\n\ncd", 100, MonoFont()) == ["ab", "cd"]

    def test_runs_of_spaces_collapse(self) -> None:
        """`f"{cur} {word}".strip()` 会把连续空格压成一个"""
        assert wrap_text_by_width("a  b", 100, MonoFont()) == ["a b"]

    def test_width_smaller_than_one_character_emits_a_leading_empty_line(self) -> None:
        """⚠️ BUG：连一个字符都放不下时，第一次 append 的 sub_line 还是空串

        逐字符分支里 `result.append(sub_line)` 没判空，于是结果里混进一个 ""。
        排版时就是一行看不见的空行。
        """
        assert wrap_text_by_width("ab", 5, MonoFont(10)) == ["", "a", "b"]

    def test_only_getbbox_is_ever_asked(self) -> None:
        """算宽只用 getbbox，没有偷偷调 getlength / getsize 之类"""
        font = MonoFont(10)
        wrap_text_by_width("aa bbb", 50, font)
        assert font.calls  # 确实量过
        assert all(isinstance(c, str) for c in font.calls)

    def test_works_with_a_real_pil_font(self) -> None:
        """拿真 PIL 字体跑一遍，保证上面那个假字体的接口没写错

        真字体的字符宽度不确定，所以只断言「每行都不超宽」这个不变量
        （单字符本身就超宽的情况除外，那种一定会溢出）。
        """
        font = ImageFont.load_default()
        max_width = 60
        lines = wrap_text_by_width(
            "the quick brown fox jumps over the lazy dog", max_width, font
        )
        assert len(lines) > 1
        for line in lines:
            bbox = font.getbbox(line)
            assert bbox[2] - bbox[0] <= max_width, line


# ==========================================================================
# create_vertical_gradient
# ==========================================================================
class TestCreateVerticalGradient:
    def test_size_and_mode(self) -> None:
        img = create_vertical_gradient((5, 7), (0, 0, 0), (255, 255, 255))
        assert img.size == (5, 7)
        assert img.mode == "RGB"

    def test_endpoints_are_exactly_the_given_colors(self) -> None:
        img = create_vertical_gradient((3, 10), (10, 20, 30), (200, 100, 50))
        assert img.getpixel((0, 0)) == (10, 20, 30)
        assert img.getpixel((2, 0)) == (10, 20, 30)
        assert img.getpixel((0, 9)) == (200, 100, 50)
        assert img.getpixel((2, 9)) == (200, 100, 50)

    def test_midpoint_is_linear_and_truncated_not_rounded(self) -> None:
        """插值用的是 int()，是截断不是四舍五入：255*0.5=127.5 -> 127"""
        img = create_vertical_gradient((2, 3), (0, 0, 0), (100, 200, 255))
        assert img.getpixel((0, 1)) == (50, 100, 127)

    def test_every_row_is_a_solid_colour(self) -> None:
        """一行内不做横向变化，整行同色"""
        img = create_vertical_gradient((4, 4), (0, 0, 0), (255, 255, 255))
        for y in range(4):
            row = {img.getpixel((x, y)) for x in range(4)}
            assert len(row) == 1

    def test_height_one_divides_by_zero(self) -> None:
        """⚠️ BUG：`t = i / (h - 1)`，高度为 1 时直接 ZeroDivisionError"""
        with pytest.raises(ZeroDivisionError):
            create_vertical_gradient((4, 1), (0, 0, 0), (255, 255, 255))


# ==========================================================================
# rounded_image
# ==========================================================================
class TestRoundedImage:
    def test_corners_become_transparent_and_centre_stays_opaque(self) -> None:
        src = Image.new("RGB", (40, 40), (255, 0, 0))
        out = rounded_image(src, radius=12)

        assert out.mode == "RGBA"
        assert out.size == (40, 40)
        assert out.getpixel((0, 0))[3] == 0  # 左上角被圆角切掉
        assert out.getpixel((39, 39))[3] == 0
        centre = out.getpixel((20, 20))
        assert centre[3] == 255
        assert centre[:3] == (255, 0, 0)  # 颜色没被动过

    def test_radius_zero_keeps_every_pixel(self) -> None:
        out = rounded_image(Image.new("RGB", (10, 10), (0, 255, 0)), radius=0)
        assert out.getpixel((0, 0)) == (0, 255, 0, 255)


# ==========================================================================
# _load_font
# ==========================================================================
class TestLoadFont:
    def test_loads_a_real_ttf_from_resources(self) -> None:
        """仓库里 resources/PUSAB.TTF 是跟着 git 走的，能直接 truetype 出来"""
        path = draw.RES_DIR / "PUSAB.TTF"
        assert path.is_file(), f"资源文件不见了：{path}"
        font = _load_font(path, 32)
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.size == 32

    def test_missing_file_falls_back_instead_of_raising(self, tmp_path: Path) -> None:
        """字体文件缺了不能让整条出图路径全灭，退到 PIL 自带字体"""
        font = _load_font(tmp_path / "nope.ttf", 24)
        assert font is not None
        assert not isinstance(font, ImageFont.FreeTypeFont) or font.size == 24
        # 退回来的东西还得能量字，否则后面 wrap_text_by_width 会炸
        assert font.getbbox("abc")[2] > 0

    def test_directory_instead_of_file_also_falls_back(self, tmp_path: Path) -> None:
        """传进来的是目录时 truetype 抛的还是 OSError（不是 IsADirectoryError 漏网）"""
        font = _load_font(tmp_path, 18)
        assert font.getbbox("x")[2] >= 0


# ==========================================================================
# draw_outlined_text
# ==========================================================================
class TestDrawOutlinedText:
    def test_fill_and_outline_colours_both_land_on_the_canvas(self) -> None:
        img = Image.new("RGB", (200, 80), (0, 0, 0))
        d = ImageDraw.Draw(img)
        draw.draw_outlined_text(
            d, (10, 10), "AB", ImageFont.load_default(20),
            fill="white", outline="red", outline_width=3,
        )
        colours = {img.getpixel((x, y)) for x in range(200) for y in range(80)}
        assert (255, 255, 255) in colours, "描边把填充色整个盖住了"
        assert (255, 0, 0) in colours, "描边没画出来"

    def test_shadow_layer_is_drawn_when_asked(self) -> None:
        img = Image.new("RGB", (200, 80), (0, 0, 0))
        d = ImageDraw.Draw(img)
        draw.draw_outlined_text(
            d, (10, 10), "AB", ImageFont.load_default(20),
            fill="white", outline="black", outline_width=2,
            shadow=(6, 6, "#00ff00", 3),
        )
        colours = {img.getpixel((x, y)) for x in range(200) for y in range(80)}
        assert (0, 255, 0) in colours, "shadow 分支没画"

    def test_without_shadow_nothing_green_appears(self) -> None:
        """对照组：不给 shadow 就不该有那一层"""
        img = Image.new("RGB", (200, 80), (0, 0, 0))
        d = ImageDraw.Draw(img)
        draw.draw_outlined_text(
            d, (10, 10), "AB", ImageFont.load_default(20),
            fill="white", outline="black", outline_width=2,
        )
        colours = {img.getpixel((x, y)) for x in range(200) for y in range(80)}
        assert (0, 255, 0) not in colours


# ==========================================================================
# _none / 常量
# ==========================================================================
class TestMisc:
    async def test_none_placeholder_returns_none(self) -> None:
        assert await _none() is None

    def test_unknown_tier_falls_back_to_the_default_colour(self) -> None:
        """draw.py:385/422 用的都是 `.get(tier, DEFAULT_TIER_COLOR)`

        所以表里没有的 tier 不会 KeyError，只会变成白色。
        """
        assert draw.DEFAULT_TIER_COLOR == "#ffffff"
        assert draw.TIER_COLOR_MAP.get("这个 tier 不存在", draw.DEFAULT_TIER_COLOR) == (
            "#ffffff"
        )

    def test_all_thirteen_plat_tiers_have_a_colour(self) -> None:
        """plat 源的 tier 只可能是这 13 个串，缺一个整张卡就白给

        `1 - BEGINNER` 到 `13 - TERRIFYING` 是 platapi 数据里的固定写法
        （见 tests/test_gd_sources.py 里的 make_plat_row），
        少一个不会报错，只会静默退成白色 —— 所以在这里显式挡一道。
        """
        names = [
            "BEGINNER", "EASY", "MODERATE", "INTERMEDIATE", "TOUGH",
            "CHALLENGING", "DIFFICULT", "FORMIDABLE", "CRUEL", "INSANE",
            "DEADLY", "EXTREME", "TERRIFYING",
        ]
        missing = [
            f"{i} - {n}"
            for i, n in enumerate(names, start=1)
            if f"{i} - {n}" not in draw.TIER_COLOR_MAP
        ]
        assert missing == []

    def test_every_colour_is_a_six_digit_hex_string(self) -> None:
        """PIL 只认 #rrggbb 这种写法，混进个 3 位缩写就会在出图时才炸"""
        bad = [
            (k, v) for k, v in draw.TIER_COLOR_MAP.items()
            if not (isinstance(v, str) and len(v) == 7 and v.startswith("#"))
        ]
        assert bad == []

    def test_retry_constants(self) -> None:
        assert draw.THUMB_RETRIES == 3
        assert draw.THUMB_BACKOFF == 0.6
        assert (draw.HTTP_OK, draw.HTTP_NOT_FOUND, draw.HTTP_SERVER_ERROR) == (
            200, 404, 500,
        )


# ==========================================================================
# _fetch_thumbnail
# ==========================================================================
@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """把重试之间的退避换成立即返回，顺便记下每次等了多久"""
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake(delay: float, *a: Any, **k: Any) -> Any:
        sleeps.append(delay)
        return await real_sleep(0)

    monkeypatch.setattr(draw.asyncio, "sleep", _fake)
    return sleeps


THUMB_URL = "https://levelthumbs.prevter.me/thumbnail/128/medium"


class TestFetchThumbnail:
    async def test_happy_path_returns_the_bytes(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        payload = png_bytes()
        stub_httpx.get(THUMB_URL, httpx.Response(200, content=payload))

        assert await _fetch_thumbnail("128") == payload
        assert stub_httpx.urls == [THUMB_URL]
        assert no_sleep == []  # 一次就成，不该退避

    async def test_url_and_headers_are_what_the_service_expects(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        stub_httpx.get("levelthumbs.prevter.me", httpx.Response(200, content=b"x"))
        await _fetch_thumbnail("26681070")

        req = stub_httpx.requests[0]
        assert str(req.url) == (
            "https://levelthumbs.prevter.me/thumbnail/26681070/medium"
        )
        assert req.headers["user-agent"] == "Mozilla/5.0"
        assert req.headers["accept"] == "image/webp,image/*;q=0.8"

    async def test_404_returns_none_without_retrying(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        """「这关本来就没图」不该白等三倍时间"""
        stub_httpx.get(THUMB_URL, httpx.Response(404))

        assert await _fetch_thumbnail("128") is None
        assert len(stub_httpx.requests) == 1
        assert no_sleep == []

    async def test_other_4xx_also_stops_immediately(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        """4xx（403 之类）重试也没意义，直接放弃"""
        stub_httpx.get(THUMB_URL, httpx.Response(403, content=b"nope"))

        assert await _fetch_thumbnail("128") is None
        assert len(stub_httpx.requests) == 1

    async def test_5xx_is_retried_up_to_three_times(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        """5xx 当成暂时性故障，重试满 3 次，退避 0.6 / 1.2（最后一次不再睡）"""
        stub_httpx.get(THUMB_URL, httpx.Response(503))

        assert await _fetch_thumbnail("128") is None
        assert len(stub_httpx.requests) == 3
        assert no_sleep == [pytest.approx(0.6), pytest.approx(1.2)]

    async def test_recovers_on_a_later_attempt(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        payload = png_bytes()
        seq = [httpx.Response(500), httpx.Response(200, content=payload)]

        def _next(_request: httpx.Request) -> httpx.Response:
            return seq.pop(0)

        stub_httpx.get(THUMB_URL, _next)

        assert await _fetch_thumbnail("128") == payload
        assert len(stub_httpx.requests) == 2
        assert no_sleep == [pytest.approx(0.6)]

    async def test_transport_exceptions_are_retried_too(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        """超时/连接错误走的是 except 分支，同样重试满 3 次"""
        stub_httpx.get(THUMB_URL, httpx.ConnectTimeout("boom"))

        assert await _fetch_thumbnail("128") is None
        assert len(stub_httpx.requests) == 3
        assert no_sleep == [pytest.approx(0.6), pytest.approx(1.2)]

    async def test_200_with_empty_body_is_treated_as_a_failure(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        """`resp.status_code == 200 and resp.content` —— 空 body 不算成功，
        但 200 < 500 所以第一次就放弃了，不重试。
        """
        stub_httpx.get(THUMB_URL, httpx.Response(200, content=b""))

        assert await _fetch_thumbnail("128") is None
        assert len(stub_httpx.requests) == 1
