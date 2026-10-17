"""`xiaozu_bot/utils/gd_args.py` 的测试。

这个模块是「用户能打的词」和「后端要的值」之间唯一的一张翻译表：GD 服务器要
int、GDDL 搜索接口要自己的枚举字符串，命令只从这里取词表。所以这里钉的是表的
形状（key 全小写、值域、两张难度表对得上）和解析函数的失败行为，不是命令文案。
"""

from __future__ import annotations

import pytest

from xiaozu_bot.plugins.gdlevelsearch.commands import fullsearch, ratings
from xiaozu_bot.utils.args import Flag
from xiaozu_bot.utils.gd_args import (
    DEMON_DIFFICULTIES,
    DIFF_DEMON,
    GDDL_DIFFICULTIES,
    NEXT_WORDS,
    NONDEMON_DIFFICULTIES,
    PREV_WORDS,
    STOP_WORDS,
    ArgError,
    alias_name,
    parse_demon_filter,
    parse_diff,
    parse_difficulty,
    parse_flags,
    parse_number,
    parse_sort,
)


class TestArgError:
    def test_is_a_value_error(self) -> None:
        """调用方有的 `except ValueError`、有的 `except ArgError`，必须都接得住"""
        assert issubclass(ArgError, ValueError)

    def test_commands_share_the_same_class(self) -> None:
        """三个命令 catch 的必须是同一个类，否则一处抛另一处接不住"""
        assert fullsearch.ArgError is ArgError
        assert ratings.ArgError is ArgError


class TestWordSets:
    def test_paging_and_stop_words_do_not_overlap(self) -> None:
        """同一个词不能既翻页又退出 —— 撞了的话行为取决于代码里先判哪个"""
        assert not NEXT_WORDS & STOP_WORDS
        assert not PREV_WORDS & STOP_WORDS
        assert not NEXT_WORDS & PREV_WORDS

    def test_ascii_words_are_lowercase(self) -> None:
        """命令都是拿用户输入 lower() 之后去比的，表里有大写就永远命不中"""
        for words in (NEXT_WORDS, PREV_WORDS, STOP_WORDS):
            assert all(word == word.lower() for word in words)


class TestDifficultyTables:
    def test_demon_values_are_within_the_api_range(self) -> None:
        """demonFilter 只认 1-5，超了服务器一条都不返回"""
        assert set(DEMON_DIFFICULTIES.values()) == {1, 2, 3, 4, 5}

    def test_nondemon_values(self) -> None:
        """0 是 auto，对应请求参数 diff=-3"""
        assert NONDEMON_DIFFICULTIES["0"] == -3
        assert NONDEMON_DIFFICULTIES["auto"] == -3
        assert set(NONDEMON_DIFFICULTIES.values()) == {-3, 1, 2, 3, 4, 5}

    def test_tables_have_no_uppercase_keys(self) -> None:
        assert all(k == k.lower() for k in DEMON_DIFFICULTIES)
        assert all(k == k.lower() for k in NONDEMON_DIFFICULTIES)
        assert all(k == k.lower() for k in GDDL_DIFFICULTIES)

    def test_demon_sentinel(self) -> None:
        assert DIFF_DEMON == -2

    def test_numeric_keys_map_to_themselves(self) -> None:
        """用户直接写数字时必须原样传给服务器（"0" 是唯一例外，见上）"""
        for table in (DEMON_DIFFICULTIES, NONDEMON_DIFFICULTIES):
            for key, value in table.items():
                if key.isdigit() and key != "0":
                    assert value == int(key)

    def test_named_aliases_follow_the_ingame_order(self) -> None:
        """名字和数字对错位不会报错，只是搜出来的难度悄悄不对"""
        demon = DEMON_DIFFICULTIES
        assert [demon[k] for k in ("easy", "medium", "hard", "insane", "extreme")] == [
            1, 2, 3, 4, 5
        ]
        # 缩写和全称必须指向同一个难度
        assert demon["med"] == demon["medium"]
        assert demon["ex"] == demon["extreme"]

        nondemon = NONDEMON_DIFFICULTIES
        assert [
            nondemon[k] for k in ("auto", "easy", "normal", "hard", "harder", "insane")
        ] == [-3, 1, 2, 3, 4, 5]


class TestGddlDifficultyTable:
    def test_values_are_the_named_enum_members(self) -> None:
        """GDDL 的 difficulty 枚举只开放这五个名字（Official 不给用户选）"""
        assert set(GDDL_DIFFICULTIES.values()) == {
            "Easy", "Medium", "Hard", "Insane", "Extreme"
        }

    def test_number_and_name_keys_agree(self) -> None:
        """同一个难度的数字写法和英文写法必须落到同一个枚举值"""
        pairs = zip(
            ("1", "2", "3", "4", "5"),
            ("easy", "medium", "hard", "insane", "extreme"),
            strict=True,
        )
        for number, name in pairs:
            assert GDDL_DIFFICULTIES[number] == GDDL_DIFFICULTIES[name]

    def test_matches_the_scale_of_the_demon_table(self) -> None:
        """两张表共用同一批用户词，数字刻度也必须一致。

        对不上的话 `-d 3` 在两个后端会筛不同的东西：GD 服务器那边 3=hard，
        GDDL 这边也必须是 Hard。
        """
        named = {1: "Easy", 2: "Medium", 3: "Hard", 4: "Insane", 5: "Extreme"}
        for key, demon_value in DEMON_DIFFICULTIES.items():
            assert GDDL_DIFFICULTIES[key] == named[demon_value], key


class TestParseDifficulty:
    @pytest.mark.parametrize("text", ["extreme", "Extreme", "EXTREME", "  ex  ", "5"])
    def test_case_and_whitespace_insensitive(self, text: str) -> None:
        assert parse_difficulty(text) == "Extreme"

    @pytest.mark.parametrize("text", ["", "official", "6", "insanee", "0"])
    def test_unknown_raises(self, text: str) -> None:
        """数字只到 5；Official 虽然合法但不对用户开放"""
        with pytest.raises(ArgError):
            parse_difficulty(text)

    def test_message_echoes_what_the_user_typed(self) -> None:
        """报错要把用户写的东西回显出来，不然他不知道是哪个词错了"""
        with pytest.raises(ArgError) as exc:
            parse_difficulty("legendary")
        assert "legendary" in str(exc.value)


class TestParseNumber:
    def test_returns_a_float(self) -> None:
        assert parse_number("20", "tier", 1, 39) == 20.0

    @pytest.mark.parametrize("text", ["1", "39"])
    def test_bounds_are_inclusive(self, text: str) -> None:
        assert parse_number(text, "tier", 1, 39) == float(text)

    @pytest.mark.parametrize("text", ["0.5", "40", "-3"])
    def test_out_of_range_raises(self, text: str) -> None:
        with pytest.raises(ArgError):
            parse_number(text, "tier", 1, 39)

    @pytest.mark.parametrize("text", ["", "abc", "1,5"])
    def test_non_numeric_raises(self, text: str) -> None:
        with pytest.raises(ArgError):
            parse_number(text, "tier", 1, 39)

    def test_message_names_the_argument(self) -> None:
        """报错要说清是哪个参数错了，不然用户不知道该改哪一个"""
        with pytest.raises(ArgError) as exc:
            parse_number("abc", "enjoyment", 0, 10)
        assert "enjoyment" in str(exc.value)


class TestAliasName:
    """alias_name()：反查一个人类看得懂的名字"""

    def test_demon_names_skip_numeric_keys(self) -> None:
        """纯数字的 key 要跳过，取表里第一个非数字别名"""
        names = [alias_name(DEMON_DIFFICULTIES, v) for v in range(1, 6)]

        assert names == ["easy", "medium", "hard", "insane", "extreme"]

    def test_nondemon_names(self) -> None:
        names = [alias_name(NONDEMON_DIFFICULTIES, v) for v in (-3, 1, 2, 3, 4, 5)]

        assert names == ["auto", "easy", "normal", "hard", "harder", "insane"]

    def test_unknown_value_falls_back_to_the_number(self) -> None:
        assert alias_name(DEMON_DIFFICULTIES, 99) == "99"


class TestConverters:
    """喂给 split_flags 的 convert 回调，用户看到的报错文案也归它们。"""

    def test_demon_filter_accepts_the_any_word(self) -> None:
        """-d any：只要 demon，不限难度（返回 None 就不加 demonFilter）"""
        for word in ("any", "ANY", "all", "任意"):
            assert parse_demon_filter(word) is None

    def test_demon_filter_maps_aliases(self) -> None:
        assert parse_demon_filter("EX") == 5
        assert parse_demon_filter("3") == 3

    def test_demon_filter_unknown_raises(self) -> None:
        with pytest.raises(ArgError):
            parse_demon_filter("demon")

    def test_diff_maps_auto_to_minus_three(self) -> None:
        assert parse_diff("0") == -3
        assert parse_diff("auto") == -3
        assert parse_diff("harder") == 4

    def test_diff_unknown_raises(self) -> None:
        with pytest.raises(ArgError):
            parse_diff("demon")

    def test_sort_is_case_insensitive_and_echoes_the_original(self) -> None:
        assert parse_sort("Tier") == "rating"

        with pytest.raises(ArgError) as exc:
            parse_sort("StArS")
        assert "StArS" in str(exc.value)


class TestParseFlagsAdapter:
    """parse_flags()：把 flag 原因码换成给用户看的话，并带上用法。"""

    def test_ok_passes_through(self) -> None:
        flags, words = parse_flags(["bloodbath", "-a"], (Flag(("-a",)),), "用法")

        assert flags == {"-a": True}
        assert words == ["bloodbath"]

    def test_flag_error_becomes_a_ready_message(self) -> None:
        with pytest.raises(ArgError) as exc:
            parse_flags(["-d"], (Flag(("-d",), True),), "用法XYZ")

        message = str(exc.value)
        assert "-d" in message
        assert "用法XYZ" in message

    def test_conflict_message_names_both_flags(self) -> None:
        flags = (Flag(("-asc",), conflicts=("-desc",)), Flag(("-desc",)))

        with pytest.raises(ArgError) as exc:
            parse_flags(["-asc", "-desc"], flags, "用法XYZ")

        assert "-asc" in str(exc.value)
        assert "-desc" in str(exc.value)

    def test_converter_error_keeps_its_own_text(self) -> None:
        """converter 自己抛的 ArgError 原样透传，不套 flag 文案"""
        def boom(_text: str) -> str:
            raise ArgError("看不懂的难度")

        with pytest.raises(ArgError) as exc:
            parse_flags(["-d", "x"], (Flag(("-d",), True, boom),), "用法XYZ")

        assert str(exc.value) == "看不懂的难度"
