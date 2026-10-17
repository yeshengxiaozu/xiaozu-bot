"""`xiaozu_bot/utils/args.py` 的测试：这层只管拆，不管解释。

所有命令共用它，所以这里钉的是契约：哪些 token 算 flag、剩下什么、出错时给的是
「原因码 + flag 名」而不是给用户的文案。
"""

from __future__ import annotations

import pytest

from xiaozu_bot.utils.args import (
    CONFLICT,
    MISSING_VALUE,
    REPEATED,
    ArgError,
    Flag,
    FlagError,
    split_flags,
)


def _flags() -> tuple[Flag, ...]:
    return (
        Flag(("-a",)),
        Flag(("-d",), True),
        Flag(("-s",), True, str.upper),
        Flag(("-v", "-victors")),
        Flag(("-asc",), conflicts=("-desc",)),
        Flag(("-desc",)),
    )


class TestRecognition:
    def test_bare_flag(self) -> None:
        assert split_flags(["-a", "bloodbath"], _flags()) == (
            {"-a": True},
            ["bloodbath"],
        )

    def test_valued_flag_takes_the_next_token(self) -> None:
        assert split_flags(["-d", "extreme"], _flags()) == ({"-d": "extreme"}, [])

    def test_value_is_passed_through_convert(self) -> None:
        flags, _words = split_flags(["-s", "tier"], _flags())
        assert flags["-s"] == "TIER"

    def test_spellings_share_one_canonical_key(self) -> None:
        """-victors 和 -v 是同一个 flag，返回时统一用第一个拼写做 key"""
        assert split_flags(["-victors"], _flags())[0] == {"-v": True}

    def test_flag_names_are_case_insensitive(self) -> None:
        assert split_flags(["-A"], _flags())[0] == {"-a": True}

    def test_words_keep_their_order(self) -> None:
        _flags_out, words = split_flags(["the", "-a", "-d", "1", "nightmare"], _flags())
        assert words == ["the", "nightmare"]

    def test_unknown_dash_token_is_an_ordinary_word(self) -> None:
        """不认识的 -xxx 不当 flag，原样留在剩下的词里"""
        assert split_flags(["-x", "1"], _flags()) == ({}, ["-x", "1"])

    def test_negative_number_is_an_ordinary_word(self) -> None:
        """`-1` 不是 flag：位置参数里可能出现负数"""
        assert split_flags(["5", "-1"], _flags()) == ({}, ["5", "-1"])

    def test_token_without_dash_is_never_a_flag(self) -> None:
        assert split_flags(["d"], _flags()) == ({}, ["d"])

    def test_value_may_look_like_a_flag(self) -> None:
        """带值的 flag 会无条件吃掉下一个 token，哪怕它长得像 flag"""
        assert split_flags(["-d", "-a"], _flags()) == ({"-d": "-a"}, [])


class TestErrors:
    def test_missing_value(self) -> None:
        with pytest.raises(FlagError) as exc:
            split_flags(["bloodbath", "-d"], _flags())

        assert exc.value.reason == MISSING_VALUE
        assert exc.value.flag == "-d"

    def test_repeated_reports_the_spelling_used(self) -> None:
        """换个拼写重复写也算重复（它们共用一个 canonical key）"""
        with pytest.raises(FlagError) as exc:
            split_flags(["-v", "-victors"], _flags())

        assert exc.value.reason == REPEATED
        assert exc.value.flag == "-victors"

    def test_conflict_is_detected_in_both_orders(self) -> None:
        """冲突是对称的：只在一侧声明，反过来写也得拦住"""
        for tokens in (["-asc", "-desc"], ["-desc", "-asc"]):
            with pytest.raises(FlagError) as exc:
                split_flags(tokens, _flags())
            assert exc.value.reason == CONFLICT

    def test_conflict_names_both_flags(self) -> None:
        with pytest.raises(FlagError) as exc:
            split_flags(["-asc", "-desc"], _flags())

        assert {exc.value.flag, exc.value.other} == {"-asc", "-desc"}

    def test_convert_errors_propagate(self) -> None:
        """converter 抛的 ArgError 原样冒出来，文案由调用方决定"""

        def boom(_text: str) -> str:
            raise ArgError("nope")

        with pytest.raises(ArgError):
            split_flags(["-d", "x"], [Flag(("-d",), True, boom)])

    def test_flag_errors_are_arg_errors(self) -> None:
        assert issubclass(FlagError, ArgError)

    def test_no_user_wording_in_the_message(self) -> None:
        """str() 只是调试用的原因码；给用户看的文案由调用方拼"""
        with pytest.raises(FlagError) as exc:
            split_flags(["-d"], _flags())

        assert MISSING_VALUE in str(exc.value)
