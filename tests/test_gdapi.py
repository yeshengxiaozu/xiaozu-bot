"""`gdlevelsearch/gdapi.py` 与 `gdlevelsearch/aredlapi.py` 的测试。

两个模块都是「把外部数据源的响应解析成结构化对象」，所以测试重点是：

* 纯解析函数在畸形 / 截断 / 缺字段输入下走的到底是哪条兜底分支；
* 整张整数↔标签映射表（官方歌曲表、难度表、恶魔难度表）逐项钉死；
* 网络层只测「请求发了什么」和「响应变成了什么」，请求本身全部打桩。

所有 HTTP 都走 conftest 的 stub_requests，飞机上也能跑过。
aredlapi 有模块级全局（aredllevels / arepllevels / aredl_dict）和落盘缓存，
这里分别用 aredl_globals / aredl_workdir 两个局部 fixture 隔离，
绝不碰仓库工作区里的 data/*.json。
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import requests

from xiaozu_bot.plugins.gdlevelsearch import aredlapi, gdapi
from xiaozu_bot.plugins.gdlevelsearch.api import http as http_transport
from xiaozu_bot.plugins.gdlevelsearch.api.aredlapi import Aredl, AREDLLevel
from xiaozu_bot.plugins.gdlevelsearch.api.gdapi import (
    GDLevel,
    GDUser,
    SearchPage,
    parse_server_key_value_pairs,
    parse_song_object,
)

# --------------------------------------------------------------------------
# 常量 / 小工具
# --------------------------------------------------------------------------
GD_LEVELS_URL = "http://www.boomlings.com/database/getGJLevels21.php"
GD_USERINFO_URL = "http://www.boomlings.com/database/getGJUserInfo20.php"
GD_USERS_URL = "http://www.boomlings.com/database/getGJUsers20.php"
GD_SECRET = "Wmfd2893gb7"

AREDL_URL = "https://api.aredl.net/v2/api/aredl/levels"
AREPL_URL = "https://api.aredl.net/v2/api/arepl/levels"

# 恶魔难度：响应字段 43 的取值 → 显示名。源码 gdapi.py:286 那个字面量列表。
DEMON_NAMES = ["Hard", "Unknown", "Unknown", "Easy", "Medium", "Insane", "Extreme"]


def kv(fields: dict[int, str]) -> str:
    """把 {字段号: 值} 拼成 RobTop 那种 `key:value:key:value` 串。"""
    return ":".join(f"{key}:{value}" for key, value in fields.items())


def b64(text: str) -> str:
    """按 GD 的习惯做 url-safe base64（描述字段用的就是这个）。"""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def xor_password(plain: str) -> str:
    """按 GDLevel.XOR_KEY 反向算一个密码密文，用来测 decrypt_password 往返。"""
    key = GDLevel.XOR_KEY.encode("utf-8")
    raw = bytes(b ^ key[i % len(key)] for i, b in enumerate(plain.encode("utf-8")))
    return base64.b64encode(raw).decode("ascii")


def make_level(**fields: Any) -> GDLevel:
    """直接构造一个 GDLevel，只设指定的属性，其余保持 None。"""
    level = GDLevel()
    for name, value in fields.items():
        setattr(level, name, value)
    return level


# --- 一条尽量真实的 demon 关卡（Bloodbath 的字段形状） -----------------------
DESC_A = "The hardest level in Geometry Dash. Good luck."
LEVEL_A_FIELDS: dict[int, str] = {
    1: "10565740",
    2: "Bloodbath",
    3: b64(DESC_A),
    5: "3",
    6: "503085",
    8: "10",
    9: "50",
    10: "26559300",
    12: "0",
    13: "21",
    14: "1479489",
    15: "3",
    17: "1",
    18: "10",
    19: "12746",
    25: "0",
    28: "2015-06-14",
    29: "2015-06-14",
    30: "0",
    31: "0",
    35: "467339",
    37: "3",
    38: "1",
    39: "10",
    42: "0",
    43: "6",
    44: "0",
    45: "24746",
}
LEVEL_A = kv(LEVEL_A_FIELDS)

# --- 一条 platformer、未评级、用官方歌曲的关卡 ------------------------------
DESC_B = "just a plat"
LEVEL_B_FIELDS: dict[int, str] = {
    1: "90000001",
    2: "Plat Test",
    3: b64(DESC_B),
    6: "999888",
    12: "21",
    15: "5",
    17: "0",
    18: "0",
    35: "0",
    42: "0",
    43: "0",
}
LEVEL_B = kv(LEVEL_B_FIELDS)

SONG_467339 = (
    "1~|~467339~|~2~|~At the Speed of Light~|~3~|~50531~|~4~|~Dimrain47"
    "~|~5~|~9.56~|~6~|~~|~10~|~" + quote("https://audio.ngfiles.com/467000/467339.mp3", safe="")
)

CREATORS_RAW = "503085:Riot:16|999888:PlatGuy:77"


def search_response(
    levels: str = LEVEL_A + "|" + LEVEL_B,
    creators: str = CREATORS_RAW,
    songs: str = SONG_467339,
    page_info: str = "27:0:10",
    tail: str = "deadbeefhash",
) -> str:
    """拼一条 getGJLevels21 的完整响应：关卡#作者#歌曲#分页#hash。"""
    return "#".join([levels, creators, songs, page_info, tail])


# ==========================================================================
# OFFICIAL_SONG_MAP：整张表
# ==========================================================================
class TestOfficialSongMap:
    """官方歌曲表必须逐项钉死，改一条就要有人来改测试。"""

    EXPECTED = {
        -1: ("Practice: Stay Inside Me", "OcularNebula"),
        0: ("Stereo Madness", "Foreverbound"),
        1: ("Back on Track", "DJVI"),
        2: ("Polargeist", "Step"),
        3: ("Dry Out", "DJVI"),
        4: ("Base after Base", "DJVI"),
        5: ("Cant Let Go", "DJVI"),
        6: ("Jumper", "Waterflame"),
        7: ("Time Machine", "Waterflame"),
        8: ("Cycles", "DJVI"),
        9: ("xStep", "DJVI"),
        10: ("Clutterfunk", "Waterflame"),
        11: ("Theory of Everything", "DJ-Nate"),
        12: ("Electroman Adventures", "Waterflame"),
        13: ("Clubstep", "DJ-Nate"),
        14: ("Electrodynamix", "DJ-Nate"),
        15: ("Hexagon Force", "Waterflame"),
        16: ("Blast Processing", "Waterflame"),
        17: ("Theory of Everything 2", "DJ-Nate"),
        18: ("Geometrical Dominator", "Waterflame"),
        19: ("Deadlocked", "F-777"),
        20: ("Fingerdash", "MDK"),
        21: ("Dash", "MDK"),
        22: ("Explorers", "Hinkik"),
        23: ("The Seven Seas", "F-777"),
        24: ("Viking Arena", "F-777"),
        25: ("Airborne Robots", "F-777"),
        26: ("Secret", "RobTop"),
        27: ("Payload", "Dex Arson"),
        28: ("Beast Mode", "Dex Arson"),
        29: ("Machina", "Dex Arson"),
        30: ("Years", "Dex Arson"),
        31: ("Frontlines", "Dex Arson"),
        32: ("Space Pirates", "Waterflame"),
        33: ("Striker", "Waterflame"),
        34: ("Embers", "Dex Arson"),
        35: ("Round 1", "Dex Arson"),
        36: ("Monster Dance Off", "F-777"),
        37: ("Press Start", "MDK"),
        38: ("Nock Em", "Bossfight"),
        39: ("Power Trip", "Boom Kitty"),
    }

    def test_whole_table(self) -> None:
        """整张表逐项相等，不多不少；而且 id 必须连号，中间不能缺。

        连号那一句是从 EXPECTED 自己算边界的：RobTop 加一首歌时只要改 EXPECTED
        这一处，不用再同步维护一个写死的 range(-1, 40)。
        """
        assert gdapi.OFFICIAL_SONG_MAP == self.EXPECTED
        assert sorted(gdapi.OFFICIAL_SONG_MAP) == list(
            range(min(self.EXPECTED), max(self.EXPECTED) + 1)
        )

    def test_constants(self) -> None:
        """几个魔数常量本身也是接口的一部分。"""
        assert gdapi.DEMON_STARS == 10
        assert gdapi.LENGTH_PLAT == 5
        assert gdapi.GD_PAGE_SIZE == 10
        assert gdapi.GD_TOTAL_CAP == 9999
        assert gdapi.GD_TIMEOUT == 15


# ==========================================================================
# parse_server_key_value_pairs
# ==========================================================================
class TestParseServerKeyValuePairs:
    def test_well_formed(self) -> None:
        assert parse_server_key_value_pairs("1:128:2:Bloodbath:18:10") == {
            1: "128",
            2: "Bloodbath",
            18: "10",
        }

    def test_empty_string(self) -> None:
        """空串没有任何 pair。"""
        assert parse_server_key_value_pairs("") == {}

    def test_single_token_dropped(self) -> None:
        """只有 key 没有 value 时循环根本不进，返回空。"""
        assert parse_server_key_value_pairs("1") == {}

    def test_dangling_key_at_end_dropped(self) -> None:
        """结尾多出来的孤零零的 key 会被丢掉，不会拿下一段当值。"""
        assert parse_server_key_value_pairs("1:128:2") == {1: "128"}

    def test_trailing_colon_gives_empty_value(self) -> None:
        """GD 真实响应常以冒号结尾，最后一个字段的值是空串（不是 None）。"""
        assert parse_server_key_value_pairs("1:128:2:") == {1: "128", 2: ""}

    def test_non_numeric_key_resyncs_by_one(self) -> None:
        """key 不是整数时只前进一格重新对齐，后面的 pair 还能救回来。"""
        assert parse_server_key_value_pairs("junk:1:128") == {1: "128"}

    def test_duplicate_key_last_wins(self) -> None:
        assert parse_server_key_value_pairs("1:aaa:1:bbb") == {1: "bbb"}

    def test_negative_key_accepted(self) -> None:
        """int() 认负号，所以负数 key 会被当成合法 key 收下。"""
        assert parse_server_key_value_pairs("-1:x") == {-1: "x"}

    def test_value_may_look_like_key(self) -> None:
        """值本身是数字也不影响对齐，因为是严格两格一跳。"""
        assert parse_server_key_value_pairs("1:2:3:4") == {1: "2", 3: "4"}


# ==========================================================================
# _parse_server_value
# ==========================================================================
class TestParseServerValueInt:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("128", 128),
            ("0", 0),
            ("-5", -5),
            ("", 0),  # 空串当 0，不是 None
            ("007", 7),
        ],
    )
    def test_int_ok(self, raw: str, expected: int) -> None:
        assert gdapi._parse_server_value(raw, int) == expected

    @pytest.mark.parametrize("raw", ["abc", "1.5", "1,2"])
    def test_int_falls_back_to_raw_string(self, raw: str) -> None:
        """int 解析失败时返回**原始字符串**而不是 None —— 下游要小心类型。"""
        result = gdapi._parse_server_value(raw, int)
        assert result == raw
        assert isinstance(result, str)


class TestParseServerValueBool:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1", True), ("0", False), ("", False), ("true", False), ("2", False)],
    )
    def test_bool_is_strict_equality_to_one(self, raw: str, expected: bool) -> None:
        """只有恰好等于 "1" 才是 True，"true"/"2" 都是 False。"""
        assert gdapi._parse_server_value(raw, bool) is expected


class TestParseServerValueBase64:
    def test_standard_base64(self) -> None:
        assert gdapi._parse_server_value(b64("hello world"), "base64") == "hello world"

    def test_url_safe_alphabet(self) -> None:
        """GD 用的是 url-safe 字母表，`-`/`_` 要先换回 `+`/`/` 才解得开。"""
        text = "这是一条 Bloodbath 的关卡描述"
        raw = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")
        # 这条用例挑的明文保证密文里 `-` 和 `_` 都出现，两条替换都被覆盖到
        assert "-" in raw
        assert "_" in raw
        assert gdapi._parse_server_value(raw, "base64") == text
        # 标准字母表的同一份数据当然也要能解
        std = base64.b64encode(text.encode("utf-8")).decode("ascii")
        assert gdapi._parse_server_value(std, "base64") == text

    def test_missing_padding_is_repaired(self) -> None:
        """GD 经常把结尾的 `=` 吃掉，解析器会自己补回来。"""
        padded = b64("abc")  # "YWJj" 长度正好 4
        unpadded = b64("abcd").rstrip("=")  # "YWJjZA" 长度 6，缺 2 个 =
        assert gdapi._parse_server_value(padded, "base64") == "abc"
        assert gdapi._parse_server_value(unpadded, "base64") == "abcd"

    def test_undecodable_returns_the_repaired_string(self) -> None:
        """解不开时返回的是**替换加补位之后**的串，不是原始输入。"""
        # "//4=" 解出 b"\xff\xfe"，不是合法 utf-8 → UnicodeDecodeError → 走兜底
        assert gdapi._parse_server_value("//4=", "base64") == "//4="
        # "a-b_c" 会先变成 "a+b/c" 再补 "===" 才去解码，兜底返回的是补过的那个
        assert gdapi._parse_server_value("a-b_c", "base64") == "a+b/c==="

    def test_garbage_decodes_to_empty_string(self) -> None:
        """b64decode 默认忽略字母表外的字符，全是垃圾时解出空串而不是报错。"""
        assert gdapi._parse_server_value("!!!!", "base64") == ""


class TestParseServerValueCommaIntList:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", []),
            ("5", [5]),
            ("1,2,3", [1, 2, 3]),
            ("1,,3", [1, 3]),  # 空段被跳过
            ("1,2,", [1, 2]),  # 结尾逗号
        ],
    )
    def test_ok(self, raw: str, expected: list[int]) -> None:
        assert gdapi._parse_server_value(raw, "comma_int_list") == expected

    def test_non_numeric_item_raises(self) -> None:
        """这里没有 try/except：脏数据会把 ValueError 一路抛到 from_server_response 外。

        看起来是个健壮性缺口（其它类型都有兜底，只有这个没有）。
        """
        with pytest.raises(ValueError, match="invalid literal for int"):
            gdapi._parse_server_value("1,a", "comma_int_list")


class TestParseServerValueStr:
    def test_percent_triggers_unquote(self) -> None:
        assert gdapi._parse_server_value("Hello%20World", str) == "Hello World"

    def test_no_percent_is_passed_through(self) -> None:
        """没有 % 就完全不动，`+` 之类不会被当成空格。"""
        assert gdapi._parse_server_value("a+b c", str) == "a+b c"

    def test_unknown_type_is_passed_through(self) -> None:
        """FIELD_MAP 之外的类型标记走最后那条 return value。"""
        assert gdapi._parse_server_value("whatever", "no-such-type") == "whatever"


# ==========================================================================
# GDLevel 解析
# ==========================================================================
class TestGDLevelFromServerResponse:
    def test_field_map_has_no_duplicate_attrs(self) -> None:
        """一个字段号对应一个属性名，重复的话后面的会静默盖掉前面的。"""
        attrs = [attr for attr, _ in GDLevel.FIELD_MAP.values()]
        assert len(attrs) == len(set(attrs))

    def test_fresh_instance_is_all_none(self) -> None:
        """__init__ 把 FIELD_MAP 里所有属性都置 None（注意类注解里的默认值不生效）。"""
        level = GDLevel()
        for attr, _ in GDLevel.FIELD_MAP.values():
            assert getattr(level, attr) is None, attr
        assert level.creator_name is None
        assert level.song_info is None
        # 类注解上写了 stars: int = 0，但实例上其实是 None
        assert level.stars is None

    def test_full_payload(self) -> None:
        level = GDLevel.from_server_response(LEVEL_A)
        assert level.level_id == 10565740
        assert level.level_name == "Bloodbath"
        assert level.description == DESC_A
        assert level.version == 3
        assert level.player_id == 503085
        assert level.difficulty_denominator == 10
        assert level.difficulty_numerator == 50
        assert level.downloads == 26559300
        assert level.official_song == 0
        assert level.game_version == 21
        assert level.likes == 1479489
        assert level.length == 3
        assert level.is_demon is True
        assert level.stars == 10
        assert level.feature_score == 12746
        assert level.is_auto is False
        assert level.upload_date == "2015-06-14"
        assert level.copied_id == 0
        assert level.is_two_player is False
        assert level.custom_song_id == 467339
        assert level.coins == 3
        assert level.verified_coins is True
        assert level.stars_requested == 10
        assert level.epic == 0
        assert level.demon_difficulty == 6
        assert level.is_gauntlet is False
        assert level.objects == 24746

    def test_absent_fields_stay_none(self) -> None:
        """响应里没给的字段保持 None，而不是变成 0 / 空串。"""
        level = GDLevel.from_server_response(LEVEL_A)
        assert level.level_string is None
        assert level.password is None
        assert level.daily_number is None
        assert level.editor_time is None
        assert level.verification_time is None

    def test_unknown_field_numbers_ignored(self) -> None:
        """服务器加了新字段号不会炸，只是被忽略。"""
        level = GDLevel.from_server_response("1:7:999:brand-new:2:X")
        assert level.level_id == 7
        assert level.level_name == "X"
        assert not hasattr(level, "unknown_999")

    def test_from_string_is_an_alias(self) -> None:
        a = GDLevel.from_string(LEVEL_A)
        b = GDLevel.from_server_response(LEVEL_A)
        assert a.to_dict() == b.to_dict()

    def test_truncated_payload_keeps_what_it_could_read(self) -> None:
        """截断在半路的响应不抛异常，读到哪算哪。"""
        level = GDLevel.from_server_response("1:10565740:2:Bloodbath:18")
        assert level.level_id == 10565740
        assert level.level_name == "Bloodbath"
        assert level.stars is None  # 18 后面没值，整对被丢弃

    def test_repr(self) -> None:
        level = GDLevel.from_server_response(LEVEL_A)
        assert repr(level) == "<GDLevel 'Bloodbath' (ID:10565740)>"

    def test_to_dict_is_a_copy(self) -> None:
        level = GDLevel.from_server_response(LEVEL_A)
        dumped = level.to_dict()
        assert dumped is not level.__dict__
        assert dumped["level_name"] == "Bloodbath"
        assert "creator_name" in dumped
        assert "song_info" in dumped
        dumped["level_name"] = "changed"
        assert level.level_name == "Bloodbath"


# ==========================================================================
# GDLevel 歌曲相关
# ==========================================================================
# song_id / song_name / song_author / _get_song_display 读的是同一组输入
# （song_info、official_song、custom_song_id），分支也一一对应，
# 所以合成一张表：(song_info, official_song, custom_song_id) -> 四个输出。
SONG_TABLE: list[tuple[Any, Any, Any, Any, str, str, str | None]] = [
    # song_info 存在时压过一切（song_id 仍然只看 custom_song_id）
    (
        {"name": "NONG", "artist_name": "Nobody", "id": 1},
        13, 467339,
        467339, "NONG", "Nobody", "NONG by Nobody (NG ID:1)",
    ),
    # 有自定义歌曲 ID 但没拿到歌曲信息
    (None, None, 467339, 467339, "Unknown", "Unknown", "Custom song (ID:467339) not loaded"),
    # 官方歌曲：song_id 取负，跟 NG 的自定义 ID 区分开
    (None, 13, 0, -13, "Clubstep", "DJ-Nate", "Clubstep by DJ-Nate (Official)"),
    # Stereo Madness（官方 0 号）取负还是 0，跟「没有歌曲」不好区分
    (None, 0, 0, 0, "Stereo Madness", "Foreverbound", "Stereo Madness by Foreverbound (Official)"),
    # custom_song_id 是 None 时退回官方歌曲
    (None, 21, None, -21, "Dash", "MDK", "Dash by MDK (Official)"),
    # 什么都没有
    (None, None, None, None, "Unknown", "Unknown", None),
    # 官方歌曲表里没有的编号：名字兜底成 Unknown，展示串直接没有
    (None, 999, 0, -999, "Unknown", "Unknown", None),
    (None, -2, 0, 2, "Unknown", "Unknown", None),
]


class TestGDLevelSong:
    def test_song_lookup_table(self) -> None:
        """整张歌曲取舍表一次走完，四个输出一起对。"""
        for row in SONG_TABLE:
            song_info, official, custom, song_id, name, author, display = row
            level = make_level(
                song_info=song_info, official_song=official, custom_song_id=custom
            )
            assert level.song_id == song_id, row
            assert level.song_name == name, row
            assert level.song_author == author, row
            assert level._get_song_display() == display, row

    def test_song_info_missing_keys_returns_none(self) -> None:
        """song_info 存在但缺 key 时返回 None（不会退回官方歌曲表）。"""
        level = make_level(official_song=13, song_info={"id": 1})
        assert level.song_name is None
        assert level.song_author is None

    def test_display_string_with_song(self) -> None:
        level = GDLevel.from_server_response(LEVEL_A)
        level.creator_name = "Riot"
        level.song_info = {"name": "ATSOL", "artist_name": "Dimrain47", "id": 467339}
        assert level.get_display_string() == (
            "Bloodbath by Riot (ID:10565740) - Stars:10 "
            "[ATSOL by Dimrain47 (NG ID:467339)]"
        )

    def test_display_string_unknown_creator_and_no_song(self) -> None:
        level = make_level(
            level_name="Nameless",
            level_id=1,
            stars=0,
            official_song=None,
            custom_song_id=0,
        )
        assert level.get_display_string() == "Nameless by Unknown (ID:1) - Stars:0"

    def test_display_string_omits_stars_when_none(self) -> None:
        """stars 为 None 时整段星级都不显示。"""
        level = make_level(
            level_name="X", level_id=2, official_song=None, custom_song_id=0
        )
        assert level.get_display_string() == "X by Unknown (ID:2)"


# ==========================================================================
# GDLevel 长度 / demon 判定
# ==========================================================================
class TestGDLevelFlags:
    # (length, is_demon) -> (is_plat, is_pemon, is_demon_detail)
    # 三个方法读的是同一对字段，合成一张表一次走完。
    FLAG_TABLE: list[tuple[Any, Any, bool, bool, bool]] = [
        (4, False, False, False, False),
        (5, False, True, False, False),      # 长度 5 就是 platformer
        (6, False, False, False, False),     # 越界的长度也不算 plat
        (None, False, False, False, False),  # 长度缺失时不算 plat，而不是报错
        ("5", False, True, False, False),    # int() 转换是显式的，字符串 "5" 也算
        (3, True, False, False, True),       # 普通 demon
        (5, True, True, True, False),        # platformer demon = pemon
        (None, True, False, False, True),    # 长度缺失时按非 plat 处理
    ]

    def test_flag_table(self) -> None:
        for row in self.FLAG_TABLE:
            length, is_demon, plat, pemon, demon_detail = row
            level = make_level(length=length, is_demon=is_demon)
            assert level.is_plat() is plat, row
            assert level.is_pemon() is pemon, row
            assert level.is_demon_detail() is demon_detail, row


# ==========================================================================
# difficulty_label：整张难度表
# ==========================================================================
class TestDifficultyLabel:
    NON_DEMON = [
        "Unrated",
        "1{sign}auto",
        "2{sign}easy",
        "3{sign}normal",
        "4{sign}hard",
        "5{sign}hard",
        "6{sign}harder",
        "7{sign}harder",
        "8{sign}insane",
        "9{sign}insane",
    ]

    @pytest.mark.parametrize(("length", "sign"), [(3, "⭐"), (5, "🌙")])
    def test_nondemon_table(self, length: int, sign: str) -> None:
        """0~9 星的整张表：非 plat 用 ⭐、platformer 用 🌙，其余文案完全一样。

        表本身是 NON_DEMON（对着源码 gdapi.py:275 那个字面量列表抄的），
        表加一行时这里自动跟着多校验一行，不用再写一个函数。
        0 星那一格是纯 "Unrated"，没有星号，两种 sign 下都一样。
        """
        for stars, template in enumerate(self.NON_DEMON):
            level = make_level(stars=stars, length=length, is_demon=False)
            assert level.difficulty_label() == template.format(sign=sign), stars

    @pytest.mark.parametrize(("length", "word"), [(3, "Demon"), (5, "Pemon")])
    def test_demon_table(self, length: int, word: str) -> None:
        """字段 43 的整张恶魔难度表：0=Hard, 1/2=Unknown, 3=Easy, 4=Medium, 5=Insane, 6=Extreme。

        platformer 的 demon 显示成 Pemon，名字部分和普通 demon 共用同一张表。
        """
        for code, name in enumerate(DEMON_NAMES):
            level = make_level(
                stars=10, length=length, is_demon=True, demon_difficulty=code
            )
            assert level.difficulty_label() == f"{name} {word}", code

    def test_demon_without_demon_difficulty(self) -> None:
        """10 星但没给字段 43 时退回统一的 "10⭐demon"（注意这里写死了星号）。"""
        level = make_level(stars=10, length=3, is_demon=True, demon_difficulty=None)
        assert level.difficulty_label() == "10⭐demon"

    def test_plat_demon_without_demon_difficulty_still_says_star(self) -> None:
        """兜底串是硬编码的 "10⭐demon"，plat 也不会变成月亮 —— 看着像个小瑕疵。"""
        level = make_level(stars=10, length=5, is_demon=True, demon_difficulty=None)
        assert level.difficulty_label() == "10⭐demon"

    def test_stars_none_is_unknown(self) -> None:
        assert make_level(stars=None).difficulty_label() == "Unknown"

    def test_stars_above_ten_uses_demon_branch(self) -> None:
        """星数 >10（moons 那套）也走 demon 分支。"""
        level = make_level(stars=14, length=3, is_demon=True, demon_difficulty=5)
        assert level.difficulty_label() == "Insane Demon"

    def test_demon_difficulty_out_of_range_raises(self) -> None:
        """字段 43 超出 0~6 会直接 IndexError —— 解析外部数据时没有兜底。"""
        level = make_level(stars=10, length=3, is_demon=True, demon_difficulty=7)
        with pytest.raises(IndexError):
            level.difficulty_label()

    def test_negative_stars_indexes_from_the_end(self) -> None:
        """负星数会被 Python 的负下标吃掉，静默返回表尾的标签，而不是报错。"""
        level = make_level(stars=-1, length=3, is_demon=False)
        assert level.difficulty_label() == "9⭐insane"

    def test_label_reads_stars_via_int(self) -> None:
        """stars 是字符串时也能工作（int() 显式转换过）。"""
        assert make_level(stars="4", length=3).difficulty_label() == "4⭐hard"


# ==========================================================================
# decrypt_password
# ==========================================================================
class TestDecryptPassword:
    @pytest.mark.parametrize("value", [None, ""])
    def test_falsy_password_returns_none(self, value: str | None) -> None:
        assert make_level(password=value).decrypt_password() is None

    @pytest.mark.parametrize("plain", ["1123456", "0", "1000000", "1"])
    def test_round_trip(self, plain: str) -> None:
        """XOR key 是 "26364"，密文 base64 解开再逐字节异或就是明文。"""
        level = make_level(password=xor_password(plain))
        assert level.decrypt_password() == plain

    def test_non_utf8_result_falls_back_to_hex(self) -> None:
        """解出来不是合法 utf-8 时给十六进制串，不抛异常。"""
        payload = bytes([0xFF, 0xFE, 0x80])
        key = GDLevel.XOR_KEY.encode()
        cipher = bytes(b ^ key[i % len(key)] for i, b in enumerate(payload))
        level = make_level(password=base64.b64encode(cipher).decode())
        assert level.decrypt_password() == "fffe80"

    def test_plain_zero_password_crashes(self) -> None:
        """GD 对「不可复制」的关卡直接返回字面量 "0"，它不是合法 base64。

        `decrypt_password()` 只兜了 UnicodeDecodeError，binascii.Error 会直接抛出去。
        这是生产代码的问题，测试只把现状钉住。
        """
        level = make_level(password="0")
        with pytest.raises(binascii.Error):
            level.decrypt_password()


# ==========================================================================
# GDUser
# ==========================================================================
USER_RESPONSE = kv(
    {
        1: "Riot",
        2: "503085",
        3: "25554",
        4: "1502",
        8: "1085",
        10: "12",
        11: "9",
        13: "149",
        14: "0",
        16: "503085",
        17: "9432",
        18: "0",
        19: "0",
        20: quote("https://youtube.com/riot", safe=""),
        21: "48",
        22: "40",
        29: "1",
        30: "112",
        43: "17",
        46: "8763",
        49: "0",
        52: "1024",
        55: "1,2,3,4,5,6",
        56: "10,20,30,40,50",
        57: "1,2,3",
    }
)


class TestGDUser:
    def test_field_map_has_no_duplicate_attrs(self) -> None:
        attrs = [attr for attr, _ in GDUser.FIELD_MAP.values()]
        assert len(attrs) == len(set(attrs))

    def test_fresh_instance_is_all_none(self) -> None:
        user = GDUser()
        for attr, _ in GDUser.FIELD_MAP.values():
            assert getattr(user, attr) is None, attr

    def test_full_payload(self) -> None:
        user = GDUser.from_server_response(USER_RESPONSE)
        assert user.user_name == "Riot"
        assert user.user_id == 503085
        assert user.stars == 25554
        assert user.demons_count == 1502
        assert user.creator_points == 1085
        assert user.secret_coins == 149
        assert user.account_id == 503085
        assert user.user_coins == 9432
        assert user.youtube == "https://youtube.com/riot"
        assert user.is_registered is True
        assert user.global_rank == 112
        assert user.diamonds == 8763
        assert user.moons == 1024

    def test_comma_int_lists(self) -> None:
        """55/56/57 三个字段是逗号分隔的整数列表。"""
        user = GDUser.from_server_response(USER_RESPONSE)
        assert user.demons_breakdown == [1, 2, 3, 4, 5, 6]
        assert user.classic_levels == [10, 20, 30, 40, 50]
        assert user.platformer_levels == [1, 2, 3]

    def test_absent_fields_stay_none(self) -> None:
        user = GDUser.from_server_response(USER_RESPONSE)
        assert user.twitter is None
        assert user.twitch is None
        assert user.ranking is None
        assert user.age is None

    def test_dirty_comma_list_propagates_valueerror(self) -> None:
        """脏的列表字段会让整个用户解析抛异常（comma_int_list 没有兜底）。"""
        with pytest.raises(ValueError, match="invalid literal for int"):
            GDUser.from_server_response("1:Riot:2:1:55:1,oops")

    def test_from_string_is_an_alias(self) -> None:
        assert (
            GDUser.from_string(USER_RESPONSE).to_dict()
            == GDUser.from_server_response(USER_RESPONSE).to_dict()
        )

    def test_repr(self) -> None:
        user = GDUser.from_server_response(USER_RESPONSE)
        assert repr(user) == "<GDUser 'Riot' (ID:503085)>"

    def test_to_dict_is_a_copy(self) -> None:
        user = GDUser.from_server_response(USER_RESPONSE)
        dumped = user.to_dict()
        assert dumped is not user.__dict__
        assert dumped["user_name"] == "Riot"


# ==========================================================================
# parse_song_object
# ==========================================================================
class TestParseSongObject:
    def test_full_song(self) -> None:
        assert parse_song_object(SONG_467339) == {
            "id": 467339,
            "name": "At the Speed of Light",
            "artist_id": 50531,
            "artist_name": "Dimrain47",
            "size": 9.56,
            "link": "https://audio.ngfiles.com/467000/467339.mp3",
        }

    def test_unneeded_keys_are_dropped(self) -> None:
        """只保留 needed 里那 6 个键，其它（6/7/8...）全丢。"""
        result = parse_song_object("1~|~1~|~6~|~x~|~7~|~y~|~8~|~1")
        assert result == {"id": 1}

    def test_without_id_returns_none(self) -> None:
        """没有字段 1 就没法索引，直接判定为无效。"""
        assert parse_song_object("2~|~NoId~|~4~|~Someone") is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_song_object("") is None

    def test_empty_numeric_values_get_zero_defaults(self) -> None:
        """空值不报错：id/artist_id → 0，size → 0.0，link → ""。"""
        assert parse_song_object("1~|~~|~3~|~~|~5~|~~|~10~|~") == {
            "id": 0,
            "artist_id": 0,
            "size": 0.0,
            "link": "",
        }

    def test_non_numeric_key_resyncs(self) -> None:
        """key 位置上是垃圾时只前进一格，后面的键值对还能对上。"""
        assert parse_song_object("junk~|~1~|~7") == {"id": 7}

    def test_dangling_key_dropped(self) -> None:
        """结尾孤零零的 key 没有值，被循环条件挡掉。"""
        assert parse_song_object("1~|~5~|~2") == {"id": 5}

    @pytest.mark.parametrize("bad", ["1~|~notanint", "1~|~1~|~5~|~notafloat", "1~|~1~|~3~|~x"])
    def test_bad_number_swallows_everything(self, bad: str) -> None:
        """任何一个数值字段解析失败，整首歌都作废返回 None（异常被 except 吞掉）。"""
        assert parse_song_object(bad) is None

    def test_name_is_not_unquoted(self) -> None:
        """只有 link（字段 10）会 unquote，歌名保持原样。"""
        result = parse_song_object("1~|~1~|~2~|~a%20b")
        assert result is not None
        assert result["name"] == "a%20b"


# ==========================================================================
# SearchPage
# ==========================================================================
class TestSearchPage:
    def test_defaults(self) -> None:
        page = SearchPage()
        assert page.levels == []
        assert page.total == 0
        assert page.offset == 0
        assert page.page_size == gdapi.GD_PAGE_SIZE
        assert page.page == 0
        assert page.is_empty is True

    def test_levels_default_is_not_shared(self) -> None:
        """dataclass 用的是 default_factory，两个实例不能共用同一个 list。"""
        a, b = SearchPage(), SearchPage()
        a.levels.append(make_level())
        assert b.levels == []

    def test_total_is_capped(self) -> None:
        """total 到封顶值就是服务器没给真实条数，不能拿去算总页数。

        边界直接从 gdapi.GD_TOTAL_CAP 算，常量改了这里不用跟着改。
        """
        cap = gdapi.GD_TOTAL_CAP
        for total, capped in [(0, False), (cap - 1, False), (cap, True), (cap + 1, True)]:
            assert SearchPage(total=total).total_is_capped is capped, total

    def test_is_empty(self) -> None:
        assert SearchPage(levels=[make_level()]).is_empty is False


# ==========================================================================
# _search_levels：请求构造
# ==========================================================================
class TestSearchLevelsRequest:
    def test_default_payload(self, stub_requests: Any) -> None:
        stub_requests.post(GD_LEVELS_URL, text="-1")
        gdapi.search_levels(query="bloodbath")
        call = stub_requests.calls[0]
        assert call["method"] == "POST"
        assert call["url"] == GD_LEVELS_URL
        assert call["headers"] == {"User-Agent": gdapi.USER_AGENT}
        assert call["timeout"] == gdapi.GD_TIMEOUT
        assert call["data"] == {
            "secret": GD_SECRET,
            "gameVersion": 22,
            "binaryVersion": 42,
            "type": 0,
            "page": 0,
            "gdw": 0,
            "str": "bloodbath",
        }

    def test_query_none_omits_str(self, stub_requests: Any) -> None:
        """query 为 None 时不发 str 参数（用来做「按类型列表」而不是关键词搜）。"""
        stub_requests.post(GD_LEVELS_URL, text="-1")
        gdapi.search_levels(query=None, page=3, search_type=6)
        data = stub_requests.calls[0]["data"]
        assert "str" not in data
        assert data["page"] == 3
        assert data["type"] == 6

    def test_empty_query_is_still_sent(self, stub_requests: Any) -> None:
        """空串不是 None，会被原样发出去。"""
        stub_requests.post(GD_LEVELS_URL, text="-1")
        gdapi.search_levels(query="")
        assert stub_requests.calls[0]["data"]["str"] == ""

    def test_bool_params_become_one_or_zero(self, stub_requests: Any) -> None:
        """布尔筛选 True→"1"、False→"0"、None→整个参数不发。"""
        stub_requests.post(GD_LEVELS_URL, text="-1")
        gdapi.search_levels(
            query="x",
            featured=True,
            original=False,
            two_player=True,
            coins=False,
            epic=True,
            legendary=False,
            mythic=True,
            no_star=False,
            star=True,
            custom_song=False,
            uncompleted=True,
            only_completed=False,
            local=True,
        )
        data = stub_requests.calls[0]["data"]
        assert data["featured"] == "1"
        assert data["original"] == "0"
        assert data["twoPlayer"] == "1"
        assert data["coins"] == "0"
        assert data["epic"] == "1"
        assert data["legendary"] == "0"
        assert data["mythic"] == "1"
        assert data["noStar"] == "0"
        assert data["star"] == "1"
        assert data["customSong"] == "0"
        assert data["uncompleted"] == "1"
        assert data["onlyCompleted"] == "0"
        assert data["local"] == "1"

    def test_bool_params_omitted_when_none(self, stub_requests: Any) -> None:
        stub_requests.post(GD_LEVELS_URL, text="-1")
        gdapi.search_levels(query="x")
        data = stub_requests.calls[0]["data"]
        for name in (
            "featured",
            "original",
            "twoPlayer",
            "coins",
            "epic",
            "legendary",
            "mythic",
            "noStar",
            "star",
            "customSong",
            "uncompleted",
            "onlyCompleted",
            "local",
        ):
            assert name not in data

    def test_optional_params_are_renamed(self, stub_requests: Any) -> None:
        """python 侧的下划线名要映射成 GD 的驼峰 / 缩写参数名。"""
        stub_requests.post(GD_LEVELS_URL, text="-1")
        gdapi.search_levels(
            query="x",
            diff="-2",
            demon_filter=5,
            length="5",
            song=467339,
            completed_levels="(1,2,3)",
            gauntlet=7,
            account_id=1234,
            gjp2="hash",
            udid="udid-x",
            uuid="uuid-y",
        )
        data = stub_requests.calls[0]["data"]
        assert data["diff"] == "-2"
        assert data["demonFilter"] == 5
        assert data["len"] == "5"
        assert data["song"] == 467339
        assert data["completedLevels"] == "(1,2,3)"
        assert data["gauntlet"] == 7
        assert data["accountID"] == 1234
        assert data["gjp2"] == "hash"
        assert data["udid"] == "udid-x"
        assert data["uuid"] == "uuid-y"

    def test_optional_params_omitted_when_none(self, stub_requests: Any) -> None:
        stub_requests.post(GD_LEVELS_URL, text="-1")
        gdapi.search_levels(query="x")
        data = stub_requests.calls[0]["data"]
        for name in (
            "diff",
            "len",
            "demonFilter",
            "song",
            "completedLevels",
            "gauntlet",
            "accountID",
            "gjp2",
            "udid",
            "uuid",
        ):
            assert name not in data

    def test_version_overrides(self, stub_requests: Any) -> None:
        stub_requests.post(GD_LEVELS_URL, text="-1")
        gdapi.search_levels(query="x", game_version=21, binary_version=35, gdw=1)
        data = stub_requests.calls[0]["data"]
        assert data["gameVersion"] == 21
        assert data["binaryVersion"] == 35
        assert data["gdw"] == 1

    def test_extra_kwargs_are_passed_through_raw(self, stub_requests: Any) -> None:
        """没显式声明的参数原样塞进表单，方便试 GD 的新字段。"""
        stub_requests.post(GD_LEVELS_URL, text="-1")
        gdapi.search_levels(query="x", someNewParam="42")
        assert stub_requests.calls[0]["data"]["someNewParam"] == "42"

    def test_kwargs_override_computed_values(self, stub_requests: Any) -> None:
        """kwargs 是最后 update 的，能盖掉前面算好的值。"""
        stub_requests.post(GD_LEVELS_URL, text="-1")
        gdapi.search_levels(query="x", secret="overridden")
        assert stub_requests.calls[0]["data"]["secret"] == "overridden"


# ==========================================================================
# _search_levels：响应解析
# ==========================================================================
class TestSearchLevelsResponse:
    def test_full_response(self, stub_requests: Any) -> None:
        stub_requests.post(GD_LEVELS_URL, text=search_response())
        page = gdapi.search_levels_page(query="bloodbath", page=0)

        assert isinstance(page, SearchPage)
        assert page.total == 27
        assert page.offset == 0
        assert page.page_size == 10
        assert page.page == 0
        assert page.is_empty is False
        assert page.total_is_capped is False
        assert [lv.level_id for lv in page.levels] == [10565740, 90000001]
        assert [lv.level_name for lv in page.levels] == ["Bloodbath", "Plat Test"]

    def test_creators_matched_by_player_id(self, stub_requests: Any) -> None:
        """作者是按 player_id 匹配的，不是按顺序 —— 把作者段倒过来结果应该一样。"""
        reversed_creators = "999888:PlatGuy:77|503085:Riot:16"
        stub_requests.post(
            GD_LEVELS_URL, text=search_response(creators=reversed_creators)
        )
        page = gdapi.search_levels_page(query="x")
        assert [lv.creator_name for lv in page.levels] == ["Riot", "PlatGuy"]

    def test_missing_creator_leaves_none(self, stub_requests: Any) -> None:
        stub_requests.post(GD_LEVELS_URL, text=search_response(creators="1:Nobody:2"))
        page = gdapi.search_levels_page(query="x")
        assert [lv.creator_name for lv in page.levels] == [None, None]

    def test_malformed_creator_entries_are_skipped(self, stub_requests: Any) -> None:
        """作者段里混了脏数据不会拖垮整次解析。"""
        creators = "notanid:Bad:1||503085:Riot:16|onlyonefield"
        stub_requests.post(GD_LEVELS_URL, text=search_response(creators=creators))
        page = gdapi.search_levels_page(query="x")
        assert page.levels[0].creator_name == "Riot"
        assert page.levels[1].creator_name is None

    def test_song_attached_to_matching_level_only(self, stub_requests: Any) -> None:
        stub_requests.post(GD_LEVELS_URL, text=search_response())
        page = gdapi.search_levels_page(query="x")
        assert page.levels[0].song_info is not None
        assert page.levels[0].song_info["name"] == "At the Speed of Light"
        # 第二条是官方歌曲，song_info 明确置 None
        assert page.levels[1].song_info is None
        assert page.levels[1].song_name == "Dash"

    def test_unknown_custom_song_shows_stereo_madness(self, stub_requests: Any) -> None:
        """custom_song_id 在歌曲段里找不到时 song_info 为 None。

        然后 `_get_song_display()` 会**错误地**显示成 "Stereo Madness"：
        用自定义歌曲的关卡，字段 12（official_song）真实值就是 0，
        而 0 在 OFFICIAL_SONG_MAP 里正好是 Stereo Madness，
        官方歌曲那条分支排在 custom 兜底前面，于是永远先命中。
        这里钉的是现状，不是期望行为 —— 详见返回值里的 bug 列表。
        """
        stub_requests.post(GD_LEVELS_URL, text=search_response(songs=""))
        page = gdapi.search_levels_page(query="x")
        level = page.levels[0]
        assert level.custom_song_id == 467339
        assert level.official_song == 0
        assert level.song_info is None
        assert level._get_song_display() == "Stereo Madness by Foreverbound (Official)"
        assert level.song_name == "Stereo Madness"

    def test_not_loaded_branch_needs_absent_official_song(
        self, stub_requests: Any
    ) -> None:
        """只有响应里根本没给字段 12 时，才走得到「Custom song not loaded」那条兜底。"""
        no_track = kv({k: v for k, v in LEVEL_A_FIELDS.items() if k != 12})
        stub_requests.post(GD_LEVELS_URL, text=search_response(levels=no_track, songs=""))
        level = gdapi.search_levels_page(query="x").levels[0]
        assert level.official_song is None
        assert level._get_song_display() == "Custom song (ID:467339) not loaded"

    def test_multiple_songs(self, stub_requests: Any) -> None:
        second = "1~|~123~|~2~|~Other~|~3~|~9~|~4~|~Someone~|~5~|~1.0~|~10~|~x"
        stub_requests.post(
            GD_LEVELS_URL, text=search_response(songs=SONG_467339 + "~:~" + second)
        )
        page = gdapi.search_levels_page(query="x")
        assert page.levels[0].song_info["id"] == 467339

    def test_invalid_song_entry_is_skipped(self, stub_requests: Any) -> None:
        """歌曲段里有一条解析不出来，其它条不受影响。"""
        broken = "2~|~no-id-here"
        stub_requests.post(
            GD_LEVELS_URL, text=search_response(songs=broken + "~:~" + SONG_467339)
        )
        page = gdapi.search_levels_page(query="x")
        assert page.levels[0].song_info["id"] == 467339

    def test_blank_level_segments_skipped(self, stub_requests: Any) -> None:
        """关卡段里的空串（连着两个 |）被忽略，不会造出一个全空的 GDLevel。"""
        stub_requests.post(
            GD_LEVELS_URL, text=search_response(levels=LEVEL_A + "||" + LEVEL_B)
        )
        page = gdapi.search_levels_page(query="x")
        assert len(page.levels) == 2

    def test_search_levels_returns_plain_list(self, stub_requests: Any) -> None:
        """search_levels 是 search_levels_page 的「只要列表」版。"""
        stub_requests.post(GD_LEVELS_URL, text=search_response())
        levels = gdapi.search_levels(query="x")
        assert isinstance(levels, list)
        assert [lv.level_id for lv in levels] == [10565740, 90000001]

    def test_response_is_stripped(self, stub_requests: Any) -> None:
        """响应两头的空白会被 strip 掉，"-1\\n" 也算空页。"""
        stub_requests.post(GD_LEVELS_URL, text="  -1\n")
        assert gdapi.search_levels_page(query="x").is_empty is True


class TestSearchLevelsPagination:
    def test_pagination_parsed(self, stub_requests: Any) -> None:
        stub_requests.post(GD_LEVELS_URL, text=search_response(page_info="153:20:10"))
        page = gdapi.search_levels_page(query="x", page=2)
        assert (page.total, page.offset, page.page_size, page.page) == (153, 20, 10, 2)

    def test_capped_total(self, stub_requests: Any) -> None:
        """搜宽泛关键词时 GD 直接给 9999，这时候不能拿它算总页数。"""
        stub_requests.post(GD_LEVELS_URL, text=search_response(page_info="9999:0:10"))
        page = gdapi.search_levels_page(query="x")
        assert page.total == 9999
        assert page.total_is_capped is True

    def test_short_page_info_falls_back_to_defaults(self, stub_requests: Any) -> None:
        """分页段不足 3 段时用默认值：total=0、offset=page*10、page_size=10。"""
        stub_requests.post(GD_LEVELS_URL, text=search_response(page_info="153:20"))
        page = gdapi.search_levels_page(query="x", page=4)
        assert (page.total, page.offset, page.page_size) == (0, 40, 10)

    def test_non_numeric_page_info_falls_back_to_defaults(
        self, stub_requests: Any
    ) -> None:
        """三段都在但不是数字时同样退回默认值，而且是**整组**退回，不会只填一半。"""
        stub_requests.post(GD_LEVELS_URL, text=search_response(page_info="a:b:c"))
        page = gdapi.search_levels_page(query="x", page=3)
        assert (page.total, page.offset, page.page_size) == (0, 30, 10)

    def test_partially_numeric_page_info_falls_back_entirely(
        self, stub_requests: Any
    ) -> None:
        """前两段是数字、第三段不是 —— 整组一起退回默认值。"""
        stub_requests.post(GD_LEVELS_URL, text=search_response(page_info="153:20:x"))
        page = gdapi.search_levels_page(query="x", page=1)
        assert (page.total, page.offset, page.page_size) == (0, 10, 10)

    def test_zero_page_size_becomes_default(self, stub_requests: Any) -> None:
        """服务器给 page_size=0 时改用 GD_PAGE_SIZE，免得下游拿它做除数。"""
        stub_requests.post(GD_LEVELS_URL, text=search_response(page_info="5:0:0"))
        page = gdapi.search_levels_page(query="x")
        assert page.page_size == gdapi.GD_PAGE_SIZE

    def test_extra_page_info_segments_ignored(self, stub_requests: Any) -> None:
        stub_requests.post(GD_LEVELS_URL, text=search_response(page_info="5:0:10:99"))
        page = gdapi.search_levels_page(query="x")
        assert (page.total, page.offset, page.page_size) == (5, 0, 10)


class TestSearchLevelsErrorPaths:
    @pytest.mark.parametrize("page", [0, 5])
    def test_minus_one_is_an_empty_page(self, stub_requests: Any, page: int) -> None:
        """搜不到 / 翻页翻过头，服务器都给 -1，这里统一当空页，不抛异常。"""
        stub_requests.post(GD_LEVELS_URL, text="-1")
        result = gdapi.search_levels_page(query="nope", page=page)
        assert result.levels == []
        assert result.is_empty is True
        assert result.page == page
        assert result.total == 0
        # 注意：这条路径上的 offset 是 dataclass 默认的 0，不是 page*10
        assert result.offset == 0
        assert result.page_size == gdapi.GD_PAGE_SIZE

    def test_request_exception_returns_empty_page(self, stub_requests: Any) -> None:
        """网络挂了返回空页而不是抛异常，页码保留。"""
        stub_requests.post(GD_LEVELS_URL, requests.Timeout("boomlings 又死了"))
        result = gdapi.search_levels_page(query="x", page=2)
        assert result.is_empty is True
        assert result.page == 2

    def test_request_exception_is_retried(self, stub_requests: Any, monkeypatch) -> None:
        monkeypatch.setattr(http_transport.time, "sleep", lambda _seconds: None)
        stub_requests.post(GD_LEVELS_URL, requests.Timeout("boomlings 又死了"))

        result = gdapi.search_levels_page(query="x")

        assert result.is_empty is True
        assert len(stub_requests.calls) == gdapi.GD_RETRIES

    @pytest.mark.parametrize(
        "text",
        ["", "garbage", "a#b", "a#b#c"],
    )
    def test_too_few_sections_raise_valueerror(
        self, stub_requests: Any, text: str
    ) -> None:
        """段数不足 4 段（关卡#作者#歌曲#分页）时直接抛 ValueError。"""
        stub_requests.post(GD_LEVELS_URL, text=text)
        with pytest.raises(ValueError, match="响应格式不正确"):
            gdapi.search_levels_page(query="x")

    def test_exactly_four_sections_is_accepted(self, stub_requests: Any) -> None:
        """没有末尾 hash 段也能解析（只要凑够 4 段）。"""
        stub_requests.post(GD_LEVELS_URL, text=f"{LEVEL_A}#{CREATORS_RAW}##1:0:10")
        page = gdapi.search_levels_page(query="x")
        assert len(page.levels) == 1
        assert page.total == 1

    def test_empty_level_section_gives_no_levels(self, stub_requests: Any) -> None:
        """段数够但关卡段是空的 —— 空列表，不报错。"""
        stub_requests.post(GD_LEVELS_URL, text="###0:0:10#hash")
        page = gdapi.search_levels_page(query="x")
        assert page.levels == []
        assert page.is_empty is True


# ==========================================================================
# get_level_by_id / OFFICIAL_LEVELS
# ==========================================================================
class TestOfficialLevels:
    @pytest.mark.parametrize(
        ("level_id", "name", "official_song"),
        [
            (1, "Clubstep", 13),
            (2, "Theory of everything 2", 17),
            (3, "Deadlocked", 19),
        ],
    )
    def test_hardcoded_official_demons(
        self, level_id: int, name: str, official_song: int
    ) -> None:
        """三个官方 demon 是写死在模块里的假响应，import 期就解析好了。"""
        level = gdapi.OFFICIAL_LEVELS[level_id]
        assert level.level_id == level_id
        assert level.level_name == name
        assert level.official_song == official_song
        assert level.is_demon is True
        assert level.stars == 10
        assert level.length == 3
        assert level.is_plat() is False

    def test_official_level_song_lookup(self) -> None:
        assert gdapi.OFFICIAL_LEVELS[3].song_name == "Deadlocked"
        assert gdapi.OFFICIAL_LEVELS[3].song_author == "F-777"

    def test_official_level_description_decoded(self) -> None:
        assert gdapi.OFFICIAL_LEVELS[1].description.startswith(
            "This is the first official demon"
        )

    def test_official_demon_difficulty_renders_as_unknown(self) -> None:
        """三条假数据的字段 43 都写成 1，而 1 在难度表里是 "Unknown"。

        Clubstep / ToE2 / Deadlocked 现实里都是 Hard Demon，字段 43 应该是 0。
        看着是硬编码数据填错了，这里只钉住现状。
        """
        for level_id in (1, 2, 3):
            assert gdapi.OFFICIAL_LEVELS[level_id].demon_difficulty == 1
            assert gdapi.OFFICIAL_LEVELS[level_id].difficulty_label() == "Unknown Demon"

    @pytest.mark.parametrize("level_id", [1, 2, 3])
    def test_get_level_by_id_shortcuts_official(
        self, stub_requests: Any, level_id: int
    ) -> None:
        """官方关卡直接查表，一个请求都不发。"""
        result = gdapi.get_level_by_id(level_id)
        assert result is gdapi.OFFICIAL_LEVELS[level_id]
        assert stub_requests.calls == []


class TestGetLevelById:
    def test_returns_first_result(self, stub_requests: Any) -> None:
        """按 ID 搜其实是拿 ID 当关键词搜，取第一条。"""
        stub_requests.post(GD_LEVELS_URL, text=search_response())
        level = gdapi.get_level_by_id(10565740)
        assert level is not None
        assert level.level_id == 10565740
        assert stub_requests.calls[0]["data"]["str"] == "10565740"

    def test_not_found_returns_none(self, stub_requests: Any) -> None:
        stub_requests.post(GD_LEVELS_URL, text="-1")
        assert gdapi.get_level_by_id(999999999) is None

    def test_network_failure_raises_after_retries(
        self, stub_requests: Any, monkeypatch
    ) -> None:
        monkeypatch.setattr(http_transport.time, "sleep", lambda _seconds: None)
        stub_requests.post(GD_LEVELS_URL, requests.ConnectionError("no route"))

        with pytest.raises(gdapi.GDAPIUnavailable):
            gdapi.get_level_by_id(999999999)
        assert len(stub_requests.calls) == gdapi.GD_RETRIES

    def test_network_failure_can_recover_on_retry(
        self, stub_requests: Any, make_response: Any, monkeypatch
    ) -> None:
        monkeypatch.setattr(http_transport.time, "sleep", lambda _seconds: None)
        attempts = 0

        def flaky(**_kwargs: Any) -> Any:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise requests.Timeout("temporary outage")
            return make_response(200, text=search_response())

        stub_requests.post(GD_LEVELS_URL, flaky)

        level = gdapi.get_level_by_id(10565740)

        assert level is not None
        assert attempts == 2

    def test_search_is_unfiltered(self, stub_requests: Any) -> None:
        """只带 str，不带任何筛选参数（page 默认 0）。"""
        stub_requests.post(GD_LEVELS_URL, text="-1")
        gdapi.get_level_by_id(123)
        assert stub_requests.calls[0]["data"] == {
            "secret": GD_SECRET,
            "gameVersion": 22,
            "binaryVersion": 42,
            "type": 0,
            "page": 0,
            "gdw": 0,
            "str": "123",
        }


# ==========================================================================
# 用户相关接口
# ==========================================================================
class TestGetUserInfo:
    def test_ok(self, stub_requests: Any) -> None:
        stub_requests.post(GD_USERINFO_URL, text=USER_RESPONSE + "#hash#more")
        user = gdapi.get_user_info(503085)
        assert user is not None
        assert user.user_name == "Riot"
        assert user.account_id == 503085
        call = stub_requests.calls[0]
        assert call["url"] == GD_USERINFO_URL
        assert call["data"] == {"secret": GD_SECRET, "targetAccountID": "503085"}
        assert call["headers"] == {"User-Agent": gdapi.USER_AGENT}
        assert call["timeout"] == gdapi.GD_TIMEOUT

    def test_only_first_section_is_parsed(self, stub_requests: Any) -> None:
        """`#` 后面是校验段，不能混进 key:value 解析。"""
        stub_requests.post(GD_USERINFO_URL, text="1:Riot:2:1#1:NOTAUSER:2:2")
        user = gdapi.get_user_info(1)
        assert user is not None
        assert user.user_name == "Riot"

    def test_minus_one_returns_none(self, stub_requests: Any) -> None:
        stub_requests.post(GD_USERINFO_URL, text="-1")
        assert gdapi.get_user_info(1) is None

    @pytest.mark.parametrize(
        "text",
        ["", "garbage", "1:Riot:2:not-a-number", "1:Riot:2:1:55:1,oops"],
    )
    def test_invalid_response_returns_none(
        self, stub_requests: Any, text: str
    ) -> None:
        """空响应和无法解析的响应不能变成一个空的 GDUser。"""
        stub_requests.post(GD_USERINFO_URL, text=text)
        assert gdapi.get_user_info(1) is None

    def test_request_exception_returns_none(self, stub_requests: Any) -> None:
        stub_requests.post(GD_USERINFO_URL, requests.Timeout("timeout"))
        assert gdapi.get_user_info(1) is None

    def test_non_text_response_returns_none(self, stub_requests: Any) -> None:
        stub_requests.post(GD_USERINFO_URL, text=None)
        assert gdapi.get_user_info(1) is None


class TestSearchUser:
    def test_ok(self, stub_requests: Any) -> None:
        stub_requests.post(GD_USERS_URL, text=USER_RESPONSE + "#9:0:10")
        user = gdapi.search_user("Riot")
        assert user is not None
        assert user.user_name == "Riot"
        call = stub_requests.calls[0]
        assert call["url"] == GD_USERS_URL
        assert call["data"] == {"secret": GD_SECRET, "str": "Riot"}

    def test_minus_one_returns_none(self, stub_requests: Any) -> None:
        stub_requests.post(GD_USERS_URL, text="-1")
        assert gdapi.search_user("nobody") is None

    @pytest.mark.parametrize("text", ["", "garbage", "1:Riot:2:503085"])
    def test_invalid_response_returns_none(
        self, stub_requests: Any, text: str
    ) -> None:
        stub_requests.post(GD_USERS_URL, text=text)
        assert gdapi.search_user("Riot") is None

    def test_request_exception_returns_none(self, stub_requests: Any) -> None:
        stub_requests.post(GD_USERS_URL, requests.ConnectionError("down"))
        assert gdapi.search_user("Riot") is None


class TestGetUserByName:
    def test_chains_search_then_info(self, stub_requests: Any) -> None:
        """先按名字搜到 account_id，再用 account_id 拉完整资料。"""
        stub_requests.post(GD_USERS_URL, text="1:Riot:2:503085:16:777#9:0:10")
        stub_requests.post(GD_USERINFO_URL, text=USER_RESPONSE + "#hash")
        user = gdapi.get_user_by_name("Riot")
        assert user is not None
        assert user.diamonds == 8763  # 只有第二次请求的完整响应里才有
        assert stub_requests.urls == [GD_USERS_URL, GD_USERINFO_URL]
        # 第二次请求用的是搜索结果里的 account_id（字段 16），不是 user_id（字段 2）
        assert stub_requests.calls[1]["data"]["targetAccountID"] == "777"

    def test_search_miss_short_circuits(self, stub_requests: Any) -> None:
        """搜不到就不发第二个请求。"""
        stub_requests.post(GD_USERS_URL, text="-1")
        assert gdapi.get_user_by_name("nobody") is None
        assert stub_requests.urls == [GD_USERS_URL]

    def test_info_failure_returns_none(self, stub_requests: Any) -> None:
        stub_requests.post(GD_USERS_URL, text="1:Riot:2:1:16:777#9:0:10")
        stub_requests.post(GD_USERINFO_URL, text="-1")
        assert gdapi.get_user_by_name("Riot") is None


# ==========================================================================
# aredlapi
# ==========================================================================
AREDL_SAMPLE: dict[str, Any] = {
    "id": "94fddf8f-5edf-4db6-8ba7-9106d5b67d08",
    "name": "Society",
    "position": 1,
    "points": 5000,
    "status": "MainList",
    "level_id": 127323087,
    "two_player": False,
    "tags": ["2.2", "Long", "NONG"],
    "description": "The sequel to Escalator.",
    "song": None,
    "edel_enjoyment": None,
    "is_edel_pending": False,
    "gddl_tier": 39.0,
    "nlw_tier": None,
}
AREDL_KEYS = tuple(AREDL_SAMPLE)


def aredl_dict_payload(**over: Any) -> dict[str, Any]:
    """造一条合法的 AREDL 关卡 json（14 个键一个都不能少）。"""
    data = dict(AREDL_SAMPLE)
    data.update(over)
    return data


@pytest.fixture
def aredl_globals() -> Any:
    """快照 aredlapi 的三个模块级全局，测完原样还回去。

    这些全局是 import 期 `load_from_cache_only()` 填好的，别的测试文件
    （以及 gdlevelsearch 本身）都拿着同一份引用，不还原会污染整轮测试。
    """
    saved_aredl = list(aredlapi.aredllevels)
    saved_arepl = list(aredlapi.arepllevels)
    saved_dict = dict(aredlapi.aredl_dict)
    try:
        yield
    finally:
        aredlapi.aredllevels[:] = saved_aredl
        aredlapi.arepllevels[:] = saved_arepl
        aredlapi.aredl_dict.clear()
        aredlapi.aredl_dict.update(saved_dict)


@pytest.fixture
def aredl_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 aredlapi 的数据目录挪到 tmp_path，绝对不能写进仓库工作区。"""
    workdir = tmp_path / "aredl_data"
    workdir.mkdir()
    monkeypatch.setattr(aredlapi, "WORK_FOLDER", workdir)
    return workdir


def write_cache(path: Path, levels: list[dict[str, Any]], age_seconds: float) -> None:
    """写一份 age_seconds 秒前生成的缓存文件。"""
    payload = {"timestamp": time.time() - age_seconds, "levels": levels}
    path.write_text(json.dumps(payload), encoding="utf-8")


class TestAREDLLevel:
    def test_every_key_lands_on_the_attribute_of_the_same_name(self) -> None:
        """14 个键原样落到同名属性上；可空字段填了值也要留住。

        照着 AREDL_SAMPLE 的键走，样本加一个字段这里自动跟着校验。
        """
        payloads = [
            AREDL_SAMPLE,
            # 五个可空字段全部填上真值，确认不是被写死成 None
            aredl_dict_payload(
                status="legacy",
                song=489111,
                edel_enjoyment=42.5,
                is_edel_pending=True,
                nlw_tier="Top Extreme",
            ),
        ]
        for payload in payloads:
            level = AREDLLevel(payload)
            for key in AREDL_KEYS:
                assert getattr(level, key) == payload[key], key

    def test_to_dict_round_trip(self) -> None:
        """to_dict 出来的 14 个键正好能再喂回构造函数。"""
        level = AREDLLevel(AREDL_SAMPLE)
        dumped = level.to_dict()
        assert set(dumped) == set(AREDL_KEYS)
        assert AREDLLevel(dumped).to_dict() == dumped
        assert dumped is not level.__dict__

    @pytest.mark.parametrize("missing", AREDL_KEYS)
    def test_any_missing_key_raises(self, missing: str) -> None:
        """14 个键**全部**用 [] 取，缺任何一个都是 KeyError。

        但 aredlapi.py:20-37 的 schema 注释里 description/song/edel_enjoyment/
        gddl_tier/nlw_tier 是没有 `*` 标记的可选键 —— 上游一旦省略就会炸。
        """
        payload = dict(AREDL_SAMPLE)
        del payload[missing]
        with pytest.raises(KeyError, match=missing):
            AREDLLevel(payload)


class TestFetchAredlLevels:
    def test_ok(self, stub_requests: Any) -> None:
        payload = [
            aredl_dict_payload(),
            aredl_dict_payload(position=2, level_id=119544028, name="Thinking Space II"),
        ]
        stub_requests.get(AREDL_URL, json_data=payload)
        levels = aredlapi.fetch_aredl_levels()
        assert [lv.level_id for lv in levels] == [127323087, 119544028]
        assert all(isinstance(lv, AREDLLevel) for lv in levels)
        call = stub_requests.calls[0]
        assert call["method"] == "GET"
        assert call["url"] == AREDL_URL
        assert call["headers"] == {"Content-Type": "application/json"}
        assert call["timeout"] == aredlapi.AREDL_TIMEOUT

    def test_empty_list(self, stub_requests: Any) -> None:
        stub_requests.get(AREDL_URL, json_data=[])
        assert aredlapi.fetch_aredl_levels() == []

    @pytest.mark.parametrize("status", [404, 429, 500, 503])
    def test_non_200_returns_empty(self, stub_requests: Any, status: int) -> None:
        """非 200 直接返回空列表，连 json() 都不碰。"""
        stub_requests.get(AREDL_URL, status_code=status)
        assert aredlapi.fetch_aredl_levels() == []

    @pytest.mark.parametrize(
        "exc",
        [requests.Timeout("timeout"), requests.ConnectionError("dns"), requests.RequestException("?")],
    )
    def test_request_exception_returns_empty(
        self, stub_requests: Any, exc: Exception
    ) -> None:
        stub_requests.get(AREDL_URL, exc)
        assert aredlapi.fetch_aredl_levels() == []

    def test_retry_budget_is_shared(self, stub_requests: Any, monkeypatch) -> None:
        """失败就是失败，不重试 —— 只发一次请求。"""
        stub_requests.get(AREDL_URL, requests.Timeout("timeout"))
        monkeypatch.setattr(http_transport.time, "sleep", lambda _seconds: None)
        aredlapi.fetch_aredl_levels()
        assert len(stub_requests.calls) == http_transport.DEFAULT_POLICY.attempts


class TestFetchAreplLevels:
    def test_uses_the_arepl_endpoint(self, stub_requests: Any) -> None:
        """AREPL 是另一个 URL（platformer 榜），别和 AREDL 搞混。"""
        stub_requests.get(AREPL_URL, json_data=[aredl_dict_payload(level_id=42)])
        levels = aredlapi.fetch_arepl_levels()
        assert [lv.level_id for lv in levels] == [42]
        assert stub_requests.urls == [AREPL_URL]

    def test_non_200_returns_empty(self, stub_requests: Any) -> None:
        stub_requests.get(AREPL_URL, status_code=500)
        assert aredlapi.fetch_arepl_levels() == []

    def test_request_exception_returns_empty(self, stub_requests: Any) -> None:
        stub_requests.get(AREPL_URL, requests.Timeout("timeout"))
        assert aredlapi.fetch_arepl_levels() == []


class TestGetAredlLevels:
    def test_fresh_cache_skips_network(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        """24 小时内的缓存直接用，一个请求都不发。"""
        write_cache(
            aredl_workdir / "aredl_levels.json", [aredl_dict_payload()], age_seconds=60
        )
        levels = aredlapi.get_aredl_levels()
        assert [lv.level_id for lv in levels] == [127323087]
        assert stub_requests.calls == []

    def test_expired_cache_refetches_and_rewrites(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        """超过 24 小时就重新拉，并把新数据连同新时间戳写回去。"""
        cache = aredl_workdir / "aredl_levels.json"
        write_cache(cache, [aredl_dict_payload(name="stale")], age_seconds=25 * 3600)
        stub_requests.get(AREDL_URL, json_data=[aredl_dict_payload(name="fresh")])

        before = time.time()
        levels = aredlapi.get_aredl_levels()

        assert [lv.name for lv in levels] == ["fresh"]
        saved = json.loads(cache.read_text(encoding="utf-8"))
        assert saved["timestamp"] >= before
        assert [lv["name"] for lv in saved["levels"]] == ["fresh"]
        assert set(saved["levels"][0]) == set(AREDL_KEYS)

    def test_missing_timestamp_counts_as_expired(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        cache = aredl_workdir / "aredl_levels.json"
        cache.write_text(json.dumps({"levels": [aredl_dict_payload()]}), encoding="utf-8")
        stub_requests.get(AREDL_URL, json_data=[aredl_dict_payload(name="fresh")])
        assert [lv.name for lv in aredlapi.get_aredl_levels()] == ["fresh"]

    def test_zero_timestamp_counts_as_expired(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        """timestamp=0 是 falsy，一样当过期处理。"""
        cache = aredl_workdir / "aredl_levels.json"
        cache.write_text(
            json.dumps({"timestamp": 0, "levels": [aredl_dict_payload()]}),
            encoding="utf-8",
        )
        stub_requests.get(AREDL_URL, json_data=[aredl_dict_payload(name="fresh")])
        assert [lv.name for lv in aredlapi.get_aredl_levels()] == ["fresh"]

    def test_corrupt_cache_counts_as_expired(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        """缓存文件坏了不能把整个流程带崩，当过期重新拉。"""
        (aredl_workdir / "aredl_levels.json").write_text("{not json", encoding="utf-8")
        stub_requests.get(AREDL_URL, json_data=[aredl_dict_payload(name="fresh")])
        assert [lv.name for lv in aredlapi.get_aredl_levels()] == ["fresh"]

    def test_no_cache_file_fetches(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        stub_requests.get(AREDL_URL, json_data=[aredl_dict_payload()])
        assert len(aredlapi.get_aredl_levels()) == 1
        assert (aredl_workdir / "aredl_levels.json").exists()

    def test_empty_fetch_does_not_write_cache(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        """拉到 0 条时不写文件，免得把好缓存覆盖成空的。"""
        stub_requests.get(AREDL_URL, json_data=[])
        assert aredlapi.get_aredl_levels() == []
        assert not (aredl_workdir / "aredl_levels.json").exists()

    def test_failed_fetch_leaves_stale_cache_intact(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        cache = aredl_workdir / "aredl_levels.json"
        write_cache(cache, [aredl_dict_payload(name="stale")], age_seconds=25 * 3600)
        stub_requests.get(AREDL_URL, requests.Timeout("timeout"))
        assert [level.name for level in aredlapi.get_aredl_levels()] == ["stale"]
        saved = json.loads(cache.read_text(encoding="utf-8"))
        assert [lv["name"] for lv in saved["levels"]] == ["stale"]


class TestGetAreplLevels:
    def test_fresh_cache_skips_network(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        write_cache(
            aredl_workdir / "arepl_levels.json",
            [aredl_dict_payload(level_id=555)],
            age_seconds=60,
        )
        assert [lv.level_id for lv in aredlapi.get_arepl_levels()] == [555]
        assert stub_requests.calls == []

    def test_expired_cache_refetches(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        cache = aredl_workdir / "arepl_levels.json"
        write_cache(cache, [aredl_dict_payload(name="stale")], age_seconds=25 * 3600)
        stub_requests.get(AREPL_URL, json_data=[aredl_dict_payload(name="fresh")])
        assert [lv.name for lv in aredlapi.get_arepl_levels()] == ["fresh"]
        assert json.loads(cache.read_text(encoding="utf-8"))["levels"][0]["name"] == "fresh"

    def test_empty_fetch_does_not_write_cache(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        stub_requests.get(AREPL_URL, json_data=[])
        assert aredlapi.get_arepl_levels() == []
        assert not (aredl_workdir / "arepl_levels.json").exists()

    def test_failed_fetch_keeps_stale_cache_in_memory(
        self, stub_requests: Any, aredl_workdir: Path
    ) -> None:
        write_cache(
            aredl_workdir / "arepl_levels.json",
            [aredl_dict_payload(name="stale")],
            age_seconds=25 * 3600,
        )
        stub_requests.get(AREPL_URL, requests.Timeout("timeout"))

        assert [level.name for level in aredlapi.get_arepl_levels()] == ["stale"]


class TestReload:
    def test_rebuilds_globals_in_place(
        self, stub_requests: Any, aredl_workdir: Path, aredl_globals: Any
    ) -> None:
        """必须原地改 list/dict —— 别的模块是 `from .aredlapi import aredllevels` 拿的引用。"""
        levels_obj = aredlapi.aredllevels
        plat_obj = aredlapi.arepllevels
        dict_obj = aredlapi.aredl_dict

        stub_requests.get(AREDL_URL, json_data=[aredl_dict_payload(level_id=100)])
        stub_requests.get(AREPL_URL, json_data=[aredl_dict_payload(level_id=200)])
        aredlapi.reload()

        assert aredlapi.aredllevels is levels_obj
        assert aredlapi.arepllevels is plat_obj
        assert aredlapi.aredl_dict is dict_obj
        assert [lv.level_id for lv in aredlapi.aredllevels] == [100]
        assert [lv.level_id for lv in aredlapi.arepllevels] == [200]
        assert set(aredlapi.aredl_dict) == {100, 200}

    def test_duplicate_level_id_keeps_the_higher_placement(
        self, stub_requests: Any, aredl_workdir: Path, aredl_globals: Any
    ) -> None:
        """同一个 level_id 出现两次（2P 版本）时保留排位靠前的那条。"""
        stub_requests.get(
            AREDL_URL,
            json_data=[
                aredl_dict_payload(level_id=100, position=1, name="solo"),
                aredl_dict_payload(level_id=100, position=50, name="2p", two_player=True),
            ],
        )
        stub_requests.get(AREPL_URL, json_data=[])
        aredlapi.reload()
        assert len(aredlapi.aredllevels) == 2
        assert aredlapi.aredl_dict[100].name == "solo"
        assert aredlapi.aredl_dict[100].position == 1

    def test_arepl_does_not_override_aredl(
        self, stub_requests: Any, aredl_workdir: Path, aredl_globals: Any
    ) -> None:
        """AREDL 先入字典，AREPL 里同 ID 的条目不覆盖，只补新的。"""
        stub_requests.get(
            AREDL_URL, json_data=[aredl_dict_payload(level_id=100, name="from-aredl")]
        )
        stub_requests.get(
            AREPL_URL,
            json_data=[
                aredl_dict_payload(level_id=100, name="from-arepl"),
                aredl_dict_payload(level_id=300, name="plat-only"),
            ],
        )
        aredlapi.reload()
        assert aredlapi.aredl_dict[100].name == "from-aredl"
        assert aredlapi.aredl_dict[300].name == "plat-only"

    def test_total_failure_empties_everything(
        self, stub_requests: Any, aredl_workdir: Path, aredl_globals: Any
    ) -> None:
        """两个源都拉失败时全局会被清空 —— 旧数据不会保留。"""
        stub_requests.get(AREDL_URL, requests.Timeout("timeout"))
        stub_requests.get(AREPL_URL, requests.Timeout("timeout"))
        aredlapi.reload()
        assert aredlapi.aredllevels == []
        assert aredlapi.arepllevels == []
        assert aredlapi.aredl_dict == {}


class TestLoadFromCacheOnly:
    def test_loads_both_caches_without_network(
        self, stub_requests: Any, aredl_workdir: Path, aredl_globals: Any
    ) -> None:
        """启动路径：只读盘，绝不联网（时间戳新不新都读）。"""
        write_cache(
            aredl_workdir / "aredl_levels.json",
            [aredl_dict_payload(level_id=100)],
            age_seconds=99 * 3600,
        )
        write_cache(
            aredl_workdir / "arepl_levels.json",
            [aredl_dict_payload(level_id=300)],
            age_seconds=99 * 3600,
        )
        aredlapi.load_from_cache_only()
        assert [lv.level_id for lv in aredlapi.aredllevels] == [100]
        assert [lv.level_id for lv in aredlapi.arepllevels] == [300]
        assert set(aredlapi.aredl_dict) == {100, 300}
        assert stub_requests.calls == []

    def test_missing_cache_leaves_globals_untouched(
        self, aredl_workdir: Path, aredl_globals: Any
    ) -> None:
        """读不到缓存时直接返回，不清空已有数据（第二个文件缺失也算）。"""
        write_cache(
            aredl_workdir / "aredl_levels.json",
            [aredl_dict_payload(level_id=100)],
            age_seconds=0,
        )
        sentinel = AREDLLevel(aredl_dict_payload(level_id=42))
        aredlapi.aredllevels[:] = [sentinel]
        aredlapi.arepllevels[:] = []
        aredlapi.aredl_dict.clear()
        aredlapi.aredl_dict[42] = sentinel

        aredlapi.load_from_cache_only()  # arepl_levels.json 不存在

        assert aredlapi.aredllevels == [sentinel]
        assert aredlapi.aredl_dict == {42: sentinel}

    def test_corrupt_cache_leaves_globals_untouched(
        self, aredl_workdir: Path, aredl_globals: Any
    ) -> None:
        (aredl_workdir / "aredl_levels.json").write_text("{oops", encoding="utf-8")
        (aredl_workdir / "arepl_levels.json").write_text("{oops", encoding="utf-8")
        sentinel = AREDLLevel(aredl_dict_payload(level_id=42))
        aredlapi.aredllevels[:] = [sentinel]
        aredlapi.aredl_dict.clear()
        aredlapi.aredl_dict[42] = sentinel

        aredlapi.load_from_cache_only()

        assert aredlapi.aredllevels == [sentinel]
        assert aredlapi.aredl_dict == {42: sentinel}

    def test_cache_without_levels_key_gives_empty_globals(
        self, aredl_workdir: Path, aredl_globals: Any
    ) -> None:
        """文件是合法 json 但没有 levels 键时按空处理（不抛异常）。"""
        for name in ("aredl_levels.json", "arepl_levels.json"):
            (aredl_workdir / name).write_text("{}", encoding="utf-8")
        aredlapi.load_from_cache_only()
        assert aredlapi.aredllevels == []
        assert aredlapi.aredl_dict == {}


class TestAredlGetLevelById:
    def test_dict_hit(self, aredl_globals: Any) -> None:
        level = AREDLLevel(aredl_dict_payload(level_id=100))
        aredlapi.aredl_dict.clear()
        aredlapi.aredl_dict[100] = level
        assert Aredl.getlevelbyid(100) is level

    def test_falls_back_to_linear_scan(self, aredl_globals: Any) -> None:
        """字典没建好时还有一条线性扫描的兜底路径。"""
        level = AREDLLevel(aredl_dict_payload(level_id=100))
        aredlapi.aredl_dict.clear()
        aredlapi.aredllevels[:] = [level]
        assert Aredl.getlevelbyid(100) is level

    def test_dict_wins_over_list(self, aredl_globals: Any) -> None:
        from_dict = AREDLLevel(aredl_dict_payload(level_id=100, name="dict"))
        from_list = AREDLLevel(aredl_dict_payload(level_id=100, name="list"))
        aredlapi.aredl_dict.clear()
        aredlapi.aredl_dict[100] = from_dict
        aredlapi.aredllevels[:] = [from_list]
        assert Aredl.getlevelbyid(100) is from_dict

    def test_miss_returns_none(self, aredl_globals: Any) -> None:
        aredlapi.aredl_dict.clear()
        aredlapi.aredllevels[:] = [AREDLLevel(aredl_dict_payload(level_id=100))]
        assert Aredl.getlevelbyid(999) is None

    def test_arepl_only_level_is_reachable(self, aredl_globals: Any) -> None:
        """platformer 榜的关卡是通过 aredl_dict 暴露的（aredllevels 里没有）。"""
        plat = AREDLLevel(aredl_dict_payload(level_id=300))
        aredlapi.aredl_dict.clear()
        aredlapi.aredl_dict[300] = plat
        aredlapi.aredllevels[:] = []
        aredlapi.arepllevels[:] = [plat]
        assert Aredl.getlevelbyid(300) is plat
