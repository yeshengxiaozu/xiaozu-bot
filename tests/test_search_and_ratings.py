"""gdlevelsearch 里 fullsearch.py 和 ratings.py 的测试。

这两个模块的 docstring 明说了「刻意不 import nonebot 的 matcher」，
所以参数解析、翻页会话、排版都能当普通函数直接测。

网络一律桩掉，两种方式：
  1. 换掉模块级的 `search_levels_page` / `Gddl`（测这两个模块自己的逻辑）；
  2. 用 stub_requests 从真的 requests 层拦下来（测 as_api_kwargs 真的
     能被下游接口吃进去，参数名没写错）。

会话过期不 sleep 120 秒，而是把模块级的 `time` 换成 FakeClock。
"""

from __future__ import annotations

import inspect
from typing import Any, Optional

import pytest

from xiaozu_bot.plugins.gdlevelsearch import fullsearch, gdapi, ratings
from xiaozu_bot.plugins.gdlevelsearch.gdapi import (
    GD_PAGE_SIZE,
    GD_TOTAL_CAP,
    GDLevel,
    SearchPage,
)
from xiaozu_bot.plugins.gdlevelsearch.gddlapi import (
    GDDL_SUBMISSION_LIMIT,
    PROGRESS_FILTERS,
    SORT_DIRECTIONS,
    SUBMISSION_SORTS,
    GDDLLevel,
    Submission,
    SubmissionPage,
)

# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------


class FakeClock:
    """假的 time 模块，只提供 time()。

    RatingsSession / FullSearchSession 都是 `import time` 之后调 `time.time()`，
    把模块级的 `time` 名字换掉就能精确控制过期判定，不用真等 120 秒。

    注意：dataclass 上 `field(default_factory=time.time)` 的那个 time.time 是
    类定义时就绑死的真函数，所以测过期必须显式设 updated_at。
    """

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _gd_level(**attrs: Any) -> GDLevel:
    """造一个 GDLevel：先按真构造函数把所有字段置 None，再覆盖要用的那几个。"""
    level = GDLevel()
    for key, value in attrs.items():
        setattr(level, key, value)
    return level


def _gddl_level(level_id: int, name: str, rating: Optional[float] = None) -> GDDLLevel:
    """造一个真的 GDDLLevel（字段一个都不能少，构造函数是硬取的）。"""
    return GDDLLevel(
        {
            "ID": level_id,
            "Rating": rating,
            "Enjoyment": None,
            "Deviation": None,
            "RatingCount": 0,
            "EnjoymentCount": 0,
            "SubmissionCount": 0,
            "TwoPlayerRating": None,
            "TwoPlayerEnjoyment": None,
            "TwoPlayerDeviation": None,
            "DefaultRating": None,
            "Showcase": None,
            "Meta": {
                "ID": level_id,
                "Name": name,
                "Description": None,
                "SongID": -1,
                "Length": 3,
                "IsTwoPlayer": False,
                "Difficulty": "Extreme",
                "Song": {"ID": -1, "Name": "Stereo Madness", "Author": "Foreverbound"},
            },
        }
    )


def _sub(
    rating: Optional[float] = None,
    enjoyment: Optional[float] = None,
    *,
    name: Optional[str] = None,
    user_id: Optional[int] = None,
    second: Optional[str] = None,
) -> dict[str, Any]:
    """一条 SubmissionDTO 的原始 json"""
    payload: dict[str, Any] = {
        "ID": 1,
        "Rating": rating,
        "Enjoyment": enjoyment,
        "UserID": user_id,
        "User": {"Name": name} if name is not None else None,
    }
    if second is not None:
        payload["SecondaryUser"] = {"Name": second}
    return payload


def _sub_page(
    subs: list[dict[str, Any]],
    *,
    total: int = 0,
    limit: int = GDDL_SUBMISSION_LIMIT,
    page: int = 0,
) -> SubmissionPage:
    return SubmissionPage(
        {"total": total, "limit": limit, "page": page, "submissions": subs}
    )


class FakeGddl:
    """ratings 模块里 Gddl 的替身，记录调用参数。

    ratings 用的是 `Gddl.getsubmissions(...)` 这种类属性调用，
    换成实例之后就是绑定方法，参数照样对得上。
    """

    def __init__(
        self,
        pages: Optional[dict[int, Optional[SubmissionPage]]] = None,
        *,
        by_name: Optional[list[Any]] = None,
        by_id: Any = None,
    ) -> None:
        self.pages = pages or {}
        self.by_name = by_name if by_name is not None else []
        self.by_id = by_id
        self.calls: list[dict[str, Any]] = []
        self.name_queries: list[str] = []
        self.id_queries: list[int] = []

    def getsubmissions(
        self,
        level_id: Any,
        page: int = 0,
        limit: int = GDDL_SUBMISSION_LIMIT,
        **kwargs: Any,
    ) -> Optional[SubmissionPage]:
        self.calls.append(
            {"level_id": level_id, "page": page, "limit": limit, **kwargs}
        )
        return self.pages.get(page)

    def getlevelsbyname(self, name: str) -> list[Any]:
        self.name_queries.append(name)
        return list(self.by_name)

    def getlevelbyid(self, level_id: Any) -> Any:
        self.id_queries.append(level_id)
        return self.by_id


class FakeSearch:
    """fullsearch 模块里 search_levels_page 的替身"""

    def __init__(self, pages: Optional[dict[int, SearchPage]] = None) -> None:
        self.pages = pages or {}
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, query: str, page: int = 0, **kwargs: Any) -> SearchPage:
        self.calls.append({"query": query, "page": page, **kwargs})
        return self.pages.get(page, SearchPage(page=page))


def _search_page(levels: list[GDLevel], *, total: int = 0, page: int = 0) -> SearchPage:
    return SearchPage(
        levels=levels,
        total=total,
        offset=page * GD_PAGE_SIZE,
        page_size=GD_PAGE_SIZE,
        page=page,
    )


# ===========================================================================
# ratings.py
# ===========================================================================


class TestSortAliases:
    """SORT_ALIASES 这张表本身"""

    @pytest.mark.parametrize(("alias", "api_field"), sorted(ratings.SORT_ALIASES.items()))
    def test_every_alias_is_accepted_by_the_gddl_api(
        self, alias: str, api_field: str
    ) -> None:
        """表里每个别名指向的字段都必须是 gddlapi 认的排序字段。

        gddlapi.getsubmissions 会把不认识的 sort 直接丢掉（只 warning），
        所以别名表写错了不会报错，只是排序悄悄失效 —— 必须在这里拦住。
        """
        assert api_field in SUBMISSION_SORTS

    @pytest.mark.parametrize(
        ("alias", "expected"),
        [
            ("tier", "rating"),
            ("rating", "rating"),
            ("评分", "rating"),
            ("enj", "enjoyment"),
            ("enjoyment", "enjoyment"),
            ("体验", "enjoyment"),
            ("date", "dateAdded"),
            ("time", "dateAdded"),
            ("时间", "dateAdded"),
            ("progress", "progress"),
            ("进度", "progress"),
            ("attempts", "attempts"),
            ("att", "attempts"),
            ("次数", "attempts"),
            ("rr", "refreshRate"),
            ("refreshrate", "refreshRate"),
            ("帧率", "refreshRate"),
        ],
    )
    def test_alias_table_contents(self, alias: str, expected: str) -> None:
        """中英文别名一个都不能漏（这张表就是给用户看的 UI）"""
        assert ratings.SORT_ALIASES[alias] == expected

    def test_username_sort_is_deliberately_not_exposed(self) -> None:
        """注释说 API 的 username 排序实测无效，所以别名表里不该有它"""
        assert "username" in SUBMISSION_SORTS
        assert "username" not in ratings.SORT_ALIASES.values()

    def test_alias_table_has_no_uppercase_keys(self) -> None:
        """parse_args 是拿 lower() 之后的词去查表的，表里有大写就永远命不中"""
        assert all(key == key.lower() for key in ratings.SORT_ALIASES)


class TestRatingsParseArgs:
    """*gdratings <关卡> [-s <排序>] [-asc] [-v] 的解析"""

    def test_plain_target(self) -> None:
        query = ratings.parse_args("bloodbath")

        assert query.target == "bloodbath"
        assert query.sort is None
        assert query.ascending is False
        assert query.victors_only is False

    def test_target_with_spaces_is_joined_back(self) -> None:
        """关卡名里的空格会被 split 拆开，再用单空格拼回去"""
        assert ratings.parse_args("the nightmare").target == "the nightmare"

    def test_multiple_spaces_are_collapsed(self) -> None:
        """text.split() 吃掉连续空白，拼回来只剩单空格"""
        assert ratings.parse_args("  the   nightmare  ").target == "the nightmare"

    def test_empty_text_raises(self) -> None:
        with pytest.raises(ratings.ArgError) as exc:
            ratings.parse_args("")

        assert str(exc.value).startswith("请提供关卡名或 id")
        assert ratings.USAGE in str(exc.value)

    def test_whitespace_only_text_raises(self) -> None:
        with pytest.raises(ratings.ArgError):
            ratings.parse_args("   \t  ")

    def test_only_flags_without_target_raises(self) -> None:
        """全是开关没给关卡名，走的是第二处「请提供关卡名或 id」"""
        with pytest.raises(ratings.ArgError) as exc:
            ratings.parse_args("-asc -v")

        assert str(exc.value).startswith("请提供关卡名或 id")

    def test_sort_flag_alone_without_target_raises(self) -> None:
        """`-s tier` 把 tier 吃掉当排序字段了，剩下的关键词是空的"""
        with pytest.raises(ratings.ArgError) as exc:
            ratings.parse_args("-s tier")

        assert str(exc.value).startswith("请提供关卡名或 id")

    @pytest.mark.parametrize(("alias", "api_field"), sorted(ratings.SORT_ALIASES.items()))
    def test_every_alias_parses(self, alias: str, api_field: str) -> None:
        """-s 后面接表里任意一个别名都能解析出对应字段"""
        assert ratings.parse_args(f"bloodbath -s {alias}").sort == api_field

    def test_flags_are_case_insensitive(self) -> None:
        """开关和排序字段都是 lower() 之后比对的"""
        query = ratings.parse_args("bloodbath -S TIER -ASC -V")

        assert query.sort == "rating"
        assert query.ascending is True
        assert query.victors_only is True

    def test_unknown_sort_raises_with_user_facing_message(self) -> None:
        with pytest.raises(ratings.ArgError) as exc:
            ratings.parse_args("bloodbath -s stars")

        message = str(exc.value)
        # 原样回显用户写的那个词（不是 lower 之后的），方便他自己看出打错了
        assert message.startswith("看不懂的排序字段：stars")
        assert "tier / enj / date / progress / attempts / rr" in message

    def test_unknown_sort_keeps_original_case_in_message(self) -> None:
        with pytest.raises(ratings.ArgError) as exc:
            ratings.parse_args("bloodbath -s StArS")

        assert "看不懂的排序字段：StArS" in str(exc.value)

    def test_sort_flag_missing_value_raises_with_field_list(self) -> None:
        """-s 在末尾没值，提示里列的是去重排序后的 API 字段名"""
        with pytest.raises(ratings.ArgError) as exc:
            ratings.parse_args("bloodbath -s")

        assert str(exc.value) == (
            "-s 后面要跟排序字段："
            "attempts / dateAdded / enjoyment / progress / rating / refreshRate"
        )

    def test_victors_long_and_short_flag(self) -> None:
        assert ratings.parse_args("bloodbath -v").victors_only is True
        assert ratings.parse_args("bloodbath -victors").victors_only is True

    def test_flags_in_any_order(self) -> None:
        """开关放哪都一样，关键词只取剩下的词"""
        a = ratings.parse_args("-v -s tier -asc bloodbath")
        b = ratings.parse_args("bloodbath -asc -s tier -v")

        assert a == b
        assert a.target == "bloodbath"
        assert a.sort == "rating"
        assert a.ascending is True
        assert a.victors_only is True

    def test_repeated_sort_flag_last_one_wins(self) -> None:
        assert ratings.parse_args("bloodbath -s tier -s enj").sort == "enjoyment"

    def test_repeated_boolean_flags_are_idempotent(self) -> None:
        query = ratings.parse_args("bloodbath -asc -asc -v -v")

        assert query.ascending is True
        assert query.victors_only is True

    def test_words_around_flags_are_joined_in_order(self) -> None:
        """关键词被开关切开的话，是按出现顺序用单空格拼起来的"""
        assert ratings.parse_args("the -asc nightmare").target == "the nightmare"

    def test_unknown_dash_token_becomes_part_of_the_target(self) -> None:
        """不认识的 -xxx 不报错，会被当成关卡名的一部分（现状如此）"""
        assert ratings.parse_args("-x bloodbath").target == "-x bloodbath"

    def test_numeric_id_target_is_kept_as_text(self) -> None:
        """parse_args 不负责解析 id，原样留给 resolve_level"""
        assert ratings.parse_args("10565740").target == "10565740"


class TestRatingsQueryApiKwargs:
    """RatingsQuery.as_api_kwargs()"""

    def test_no_flags_gives_empty_kwargs(self) -> None:
        assert ratings.RatingsQuery(target="x").as_api_kwargs() == {}

    def test_sort_always_carries_a_direction(self) -> None:
        """注释说「只给 sort 不给方向的话接口按自己默认来」，所以方向必须一起给"""
        kwargs = ratings.RatingsQuery(target="x", sort="rating").as_api_kwargs()

        assert kwargs == {"sort": "rating", "sort_direction": "desc"}

    def test_ascending_flips_the_direction(self) -> None:
        kwargs = ratings.RatingsQuery(
            target="x", sort="rating", ascending=True
        ).as_api_kwargs()

        assert kwargs == {"sort": "rating", "sort_direction": "asc"}

    def test_ascending_without_sort_is_dropped(self) -> None:
        """光给 -asc 不给 -s 的话方向不传（接口默认排序自己说了算）"""
        assert ratings.RatingsQuery(target="x", ascending=True).as_api_kwargs() == {}

    def test_victors_only_maps_to_progress_filter(self) -> None:
        kwargs = ratings.RatingsQuery(target="x", victors_only=True).as_api_kwargs()

        assert kwargs == {"progress_filter": "victors"}

    def test_all_flags_together(self) -> None:
        kwargs = ratings.RatingsQuery(
            target="x", sort="enjoyment", ascending=True, victors_only=True
        ).as_api_kwargs()

        assert kwargs == {
            "sort": "enjoyment",
            "sort_direction": "asc",
            "progress_filter": "victors",
        }

    def test_emitted_values_are_accepted_by_gddlapi(self) -> None:
        """产出的方向 / 过滤值必须落在 gddlapi 的白名单里，否则会被静默丢掉"""
        assert {"asc", "desc"} <= SORT_DIRECTIONS
        assert "victors" in PROGRESS_FILTERS

    def test_kwarg_names_match_getsubmissions_signature(self) -> None:
        """键名写错了不会报错（**kwargs 都能收），只能靠这条守住"""
        from xiaozu_bot.plugins.gdlevelsearch.gddlapi import Gddl

        params = set(inspect.signature(Gddl.getsubmissions).parameters)
        emitted = ratings.RatingsQuery(
            target="x", sort="rating", ascending=True, victors_only=True
        ).as_api_kwargs()

        assert set(emitted) <= params


class TestRatingsQueryDescribe:
    """RatingsQuery.describe()"""

    def test_no_flags_describes_nothing(self) -> None:
        assert ratings.RatingsQuery(target="x").describe() == ""

    def test_sort_desc_by_default(self) -> None:
        assert ratings.RatingsQuery(target="x", sort="rating").describe() == "按 rating 倒序"

    def test_sort_asc(self) -> None:
        query = ratings.RatingsQuery(target="x", sort="rating", ascending=True)

        assert query.describe() == "按 rating 正序"

    def test_victors_only(self) -> None:
        assert ratings.RatingsQuery(target="x", victors_only=True).describe() == "只看通关"

    def test_parts_joined_by_ideographic_comma(self) -> None:
        query = ratings.RatingsQuery(
            target="x", sort="enjoyment", ascending=True, victors_only=True
        )

        assert query.describe() == "按 enjoyment 正序、只看通关"

    def test_ascending_alone_is_invisible(self) -> None:
        """没有 sort 的时候 -asc 不进 describe（跟 as_api_kwargs 一致）"""
        assert ratings.RatingsQuery(target="x", ascending=True).describe() == ""

    def test_describe_uses_api_field_name_not_alias(self) -> None:
        """用户写 -s tier，describe 里显示的是 API 字段名 rating"""
        query = ratings.parse_args("bloodbath -s tier")

        assert query.describe() == "按 rating 倒序"


class TestResolveLevel:
    """resolve_level()：关卡名或 id -> level_id"""

    def test_pure_digits_longer_than_min_go_to_id_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl(by_id=_gddl_level(10565740, "Bloodbath"))
        monkeypatch.setattr(ratings, "Gddl", fake)

        level_id, name, err = ratings.resolve_level("10565740")

        assert (level_id, name, err) == (10565740, "Bloodbath", "")
        assert fake.id_queries == [10565740]
        assert fake.name_queries == []

    def test_id_lookup_failure_still_returns_the_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GDDL 查不到这个 id 也照样用它去拉评分，只是没关卡名"""
        monkeypatch.setattr(ratings, "Gddl", FakeGddl(by_id=None))

        assert ratings.resolve_level("10565740") == (10565740, None, "")

    def test_exactly_min_id_len_digits_is_treated_as_a_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """判定是 `len > MIN_ID_LEN`，所以 4 位纯数字走的是按名字搜。

        GD 上确实存在 4 位数 id 的老关卡，这个边界看起来偏严（见返回值里的说明）。
        """
        assert ratings.MIN_ID_LEN == 4
        fake = FakeGddl(by_name=[])
        monkeypatch.setattr(ratings, "Gddl", fake)

        level_id, name, err = ratings.resolve_level("1234")

        assert level_id is None
        assert fake.name_queries == ["1234"]
        assert err == "GDDL 上没有找到「1234」这个关卡"

    def test_digits_mixed_with_letters_go_to_name_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl(by_name=[_gddl_level(1, "level 42")])
        monkeypatch.setattr(ratings, "Gddl", fake)

        assert ratings.resolve_level("level 42")[0] == 1
        assert fake.id_queries == []

    def test_single_name_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ratings, "Gddl", FakeGddl(by_name=[_gddl_level(123, "Cataclysm")])
        )

        assert ratings.resolve_level("cataclysm") == (123, "Cataclysm", "")

    def test_no_match_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ratings, "Gddl", FakeGddl(by_name=[]))

        level_id, name, err = ratings.resolve_level("不存在的关")

        assert (level_id, name) == (None, None)
        assert err == "GDDL 上没有找到「不存在的关」这个关卡"

    def test_exact_name_match_wins_over_fuzzy_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """搜索接口是模糊匹配，名字完全相同的那条要优先，不该弹歧义列表"""
        monkeypatch.setattr(
            ratings,
            "Gddl",
            FakeGddl(
                by_name=[
                    _gddl_level(1, "Bloodbath II"),
                    _gddl_level(2, "BloodBath"),
                    _gddl_level(3, "Bloodbath Remake"),
                ]
            ),
        )

        # 大小写和首尾空格都会被 normalize 掉
        assert ratings.resolve_level(" bloodbath ") == (2, "BloodBath", "")

    def test_multiple_matches_lists_candidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ratings,
            "Gddl",
            FakeGddl(
                by_name=[
                    _gddl_level(1, "Sonic Wave", rating=19.456),
                    _gddl_level(2, "Sonic Wave Rebirth"),
                ]
            ),
        )

        level_id, name, err = ratings.resolve_level("sonic")

        assert (level_id, name) == (None, None)
        assert err == (
            "「sonic」在 GDDL 上匹配到 2 个关卡，请用 id 重新查：\n"
            "  Sonic Wave t19.46 (ID: 1)\n"
            "  Sonic Wave Rebirth (ID: 2)"
        )

    def test_candidate_list_is_capped_at_ten(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ratings,
            "Gddl",
            FakeGddl(by_name=[_gddl_level(i, f"Level {i}") for i in range(1, 14)]),
        )

        _, _, err = ratings.resolve_level("level")
        lines = err.splitlines()

        assert len(lines) == 1 + 10 + 1
        assert lines[1] == "  Level 1 (ID: 1)"
        assert lines[10] == "  Level 10 (ID: 10)"
        assert lines[-1] == "  ...还有 3 个"

    def test_exactly_ten_candidates_has_no_overflow_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ratings,
            "Gddl",
            FakeGddl(by_name=[_gddl_level(i, f"Level {i}") for i in range(1, 11)]),
        )

        _, _, err = ratings.resolve_level("level")

        assert "还有" not in err
        assert len(err.splitlines()) == 11

    def test_zero_rating_shows_no_tier_suffix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rating 是 0 / None 都不显示 tier（判的是真值不是 is None）"""
        monkeypatch.setattr(
            ratings,
            "Gddl",
            FakeGddl(
                by_name=[
                    _gddl_level(1, "A one", rating=0),
                    _gddl_level(2, "A two", rating=None),
                ]
            ),
        )

        _, _, err = ratings.resolve_level("a")

        assert "  A one (ID: 1)" in err
        assert "  A two (ID: 2)" in err

    def test_none_entries_from_the_api_are_filtered_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """列表里混进 None 不该炸，会被 pool 过滤掉"""
        monkeypatch.setattr(
            ratings, "Gddl", FakeGddl(by_name=[None, _gddl_level(7, "Deadlocked")])
        )

        assert ratings.resolve_level("dead") == (7, "Deadlocked", "")


class TestFormatSubmissionLine:
    """format_submission_line()"""

    def test_full_line(self) -> None:
        line = ratings.format_submission_line(
            Submission(_sub(21, 8.5, name="Riot", user_id=99))
        )

        assert line == "Tier 21, Enjoyment 8.5 by Riot"

    def test_missing_rating_and_enjoyment_show_na(self) -> None:
        line = ratings.format_submission_line(
            Submission(_sub(None, None, name="Riot", user_id=99))
        )

        assert line == "Tier N/A, Enjoyment N/A by Riot"

    def test_zero_is_not_na(self) -> None:
        """判的是 `is not None`，所以 enjoyment=0 要老老实实显示 0"""
        line = ratings.format_submission_line(
            Submission(_sub(0, 0, name="Riot", user_id=99))
        )

        assert line == "Tier 0, Enjoyment 0 by Riot"

    def test_missing_user_name_falls_back_to_user_id(self) -> None:
        line = ratings.format_submission_line(Submission(_sub(10, 5, user_id=4242)))

        assert line == "Tier 10, Enjoyment 5 by 用户4242"

    def test_empty_user_name_also_falls_back(self) -> None:
        """用的是 `or`，空字符串一样兜底到用户 id"""
        line = ratings.format_submission_line(
            Submission(_sub(10, 5, name="", user_id=4242))
        )

        assert line == "Tier 10, Enjoyment 5 by 用户4242"

    def test_two_player_submission_shows_both_names(self) -> None:
        line = ratings.format_submission_line(
            Submission(_sub(30, 9, name="A", user_id=1, second="B"))
        )

        assert line == "Tier 30, Enjoyment 9 by A & B"


class TestRatingsSessionFetch:
    """RatingsSession.fetch()：缓存、总数、失败"""

    def _session(self, fake: FakeGddl, **query_kw: Any) -> ratings.RatingsSession:
        return ratings.RatingsSession(
            query=ratings.RatingsQuery(target="bloodbath", **query_kw),
            level_id=10565740,
            level_name="Bloodbath",
        )

    def test_fetch_records_total_and_pages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGddl({0: _sub_page([_sub(1, 1, name="a")], total=12)})
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake)

        subs = session.fetch(0)

        assert subs is not None
        assert len(subs) == 1
        assert session.total == 12
        assert session.total_pages == 2  # 12 条 / 每页 10 条，向上取整

    def test_fetch_passes_paging_and_query_kwargs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl({0: _sub_page([], total=0)})
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, sort="rating", ascending=True, victors_only=True)

        session.fetch(0)

        assert fake.calls == [
            {
                "level_id": 10565740,
                "page": 0,
                "limit": GDDL_SUBMISSION_LIMIT,
                "sort": "rating",
                "sort_direction": "asc",
                "progress_filter": "victors",
            }
        ]

    def test_second_fetch_of_same_page_uses_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl({0: _sub_page([_sub(1, 1, name="a")], total=1)})
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake)

        first = session.fetch(0)
        second = session.fetch(0)

        assert first is second
        assert len(fake.calls) == 1

    def test_failed_request_returns_none_and_caches_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """接口挂了返回 None，不能把 None 塞进缓存"""
        fake = FakeGddl({})  # 任何页都返回 None
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake)

        assert session.fetch(0) is None
        assert session.pages == {}
        assert session.total == 0
        assert session.total_pages == 1

        session.fetch(0)
        assert len(fake.calls) == 2  # 失败没缓存，会重新请求


class TestRatingsSessionPaging:
    """go_next / go_prev / expired"""

    def _session(
        self, fake: FakeGddl, *, page: int = 0, total_pages: int = 1
    ) -> ratings.RatingsSession:
        session = ratings.RatingsSession(
            query=ratings.RatingsQuery(target="bloodbath"),
            level_id=10565740,
            level_name="Bloodbath",
            page=page,
        )
        session.total_pages = total_pages
        session.pages = {page: [Submission(_sub(1, 1, name="a"))]}
        return session

    def test_next_moves_forward(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGddl({1: _sub_page([_sub(2, 2, name="b")], total=12)})
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, total_pages=2)

        ok, msg = session.go_next()

        assert (ok, msg) == (True, "")
        assert session.page == 1
        assert fake.calls[-1]["page"] == 1

    def test_next_past_last_page_is_refused_without_a_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl({})
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, page=1, total_pages=2)

        ok, msg = session.go_next()

        assert (ok, msg) == (False, "已经是最后一页了")
        assert session.page == 1
        assert fake.calls == []  # 越界判断在请求之前

    def test_single_page_session_cannot_go_next(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl({})
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, total_pages=1)

        assert session.go_next() == (False, "已经是最后一页了")

    def test_next_with_failed_request_keeps_the_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl({})  # 第 1 页请求失败
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, total_pages=2)

        ok, msg = session.go_next()

        assert (ok, msg) == (False, "翻页失败，GDDL 那边没响应")
        assert session.page == 0

    def test_prev_before_first_page_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl({})
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, total_pages=3)

        assert session.go_prev() == (False, "已经是第一页了")
        assert session.page == 0

    def test_prev_uses_cache_and_never_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl({})
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, page=1, total_pages=2)

        ok, msg = session.go_prev()

        assert (ok, msg) == (True, "")
        assert session.page == 0
        assert fake.calls == []

    def test_current_is_empty_for_a_page_that_was_never_fetched(self) -> None:
        session = ratings.RatingsSession(
            query=ratings.RatingsQuery(target="x"), level_id=1, page=5
        )

        assert session.current == []

    def test_expiry_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """判定是 `> SESSION_TIMEOUT`，卡在 120 秒整还不算过期"""
        clock = FakeClock()
        monkeypatch.setattr(ratings, "time", clock)
        session = ratings.RatingsSession(query=ratings.RatingsQuery(target="x"), level_id=1)
        session.updated_at = clock.now

        assert ratings.SESSION_TIMEOUT == 120
        assert session.expired is False

        clock.advance(ratings.SESSION_TIMEOUT)
        assert session.expired is False

        clock.advance(0.001)
        assert session.expired is True

    def test_touch_resets_the_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock()
        monkeypatch.setattr(ratings, "time", clock)
        session = ratings.RatingsSession(query=ratings.RatingsQuery(target="x"), level_id=1)
        session.updated_at = clock.now - 500

        assert session.expired is True

        session.touch()

        assert session.updated_at == clock.now
        assert session.expired is False

    def test_go_next_touches_the_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock()
        fake = FakeGddl({1: _sub_page([_sub(2, 2, name="b")], total=12)})
        monkeypatch.setattr(ratings, "time", clock)
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, total_pages=2)
        session.updated_at = clock.now - 500

        session.go_next()

        assert session.expired is False

    def test_go_prev_touches_the_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock()
        fake = FakeGddl({})
        monkeypatch.setattr(ratings, "time", clock)
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, page=1, total_pages=2)
        session.updated_at = clock.now - 500

        session.go_prev()

        assert session.expired is False


class TestRatingsRender:
    """RatingsSession.render()"""

    def _session(self, **kw: Any) -> ratings.RatingsSession:
        defaults: dict[str, Any] = {
            "query": ratings.RatingsQuery(target="bloodbath"),
            "level_id": 10565740,
            "level_name": "Bloodbath",
        }
        defaults.update(kw)
        return ratings.RatingsSession(**defaults)

    def test_first_page_of_two(self) -> None:
        session = self._session()
        session.total = 12
        session.total_pages = 2
        session.pages = {
            0: [
                Submission(_sub(21, 8, name="Riot")),
                Submission(_sub(None, 3, user_id=42)),
            ]
        }

        assert session.render() == (
            "「Bloodbath」的提交评分　共 12 条，第 1/2 页\n"
            "Tier 21, Enjoyment 8 by Riot\n"
            "Tier N/A, Enjoyment 3 by 用户42\n"
            "n 下一页 / 结束 取消"
        )

    def test_middle_page_offers_both_directions(self) -> None:
        session = self._session(page=1)
        session.total = 25
        session.total_pages = 3
        session.pages = {1: [Submission(_sub(1, 1, name="a"))]}

        assert session.render().splitlines()[-1] == "n 下一页 / p 上一页 / 结束 取消"
        assert "第 2/3 页" in session.render()

    def test_last_page_has_no_next_hint(self) -> None:
        session = self._session(page=2)
        session.total = 25
        session.total_pages = 3
        session.pages = {2: [Submission(_sub(1, 1, name="a"))]}

        assert session.render().splitlines()[-1] == "p 上一页 / 结束 取消"

    def test_single_page_only_offers_cancel(self) -> None:
        session = self._session()
        session.total = 1
        session.total_pages = 1
        session.pages = {0: [Submission(_sub(1, 1, name="a"))]}

        assert session.render().splitlines()[-1] == "结束 取消"

    def test_query_description_is_appended_to_the_header(self) -> None:
        session = self._session(
            query=ratings.RatingsQuery(
                target="bloodbath", sort="rating", ascending=True, victors_only=True
            )
        )
        session.total = 2
        session.total_pages = 1
        session.pages = {0: [Submission(_sub(1, 1, name="a"))]}

        head = session.render().splitlines()[0]

        assert head == "「Bloodbath」的提交评分　共 2 条，第 1/1 页　[按 rating 正序、只看通关]"

    def test_empty_page_says_nobody_submitted(self) -> None:
        session = self._session()

        assert session.render() == "「Bloodbath」在 GDDL 上还没有人提交评分"

    def test_title_falls_back_to_level_id(self) -> None:
        session = self._session(level_name=None)
        session.total = 1
        session.total_pages = 1
        session.pages = {0: [Submission(_sub(1, 1, name="a"))]}

        assert session.render().startswith("「10565740」的提交评分")


class TestRatingsStartSession:
    """start_session()：解析 -> 定位关卡 -> 取第一页"""

    def test_arg_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ArgError 不在这里接，调用方（matcher）自己接"""
        monkeypatch.setattr(ratings, "Gddl", FakeGddl({}))

        with pytest.raises(ratings.ArgError):
            ratings.start_session("")

    def test_level_not_found_returns_the_resolver_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ratings, "Gddl", FakeGddl(by_name=[]))

        session, err = ratings.start_session("不存在的关")

        assert session is None
        assert err == "GDDL 上没有找到「不存在的关」这个关卡"

    def test_request_failure_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGddl({}, by_name=[_gddl_level(1, "Cataclysm")])
        monkeypatch.setattr(ratings, "Gddl", fake)

        session, err = ratings.start_session("cataclysm")

        assert session is None
        assert err == "GDDL 那边没响应，等会再试试"

    def test_no_submissions_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGddl({0: _sub_page([], total=0)}, by_name=[_gddl_level(1, "Cataclysm")])
        monkeypatch.setattr(ratings, "Gddl", fake)

        session, err = ratings.start_session("cataclysm")

        assert session is None
        assert err == "「Cataclysm」在 GDDL 上还没有人提交评分"

    def test_success_returns_a_primed_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl(
            {0: _sub_page([_sub(21, 8, name="Riot")], total=12)},
            by_name=[_gddl_level(10565740, "Bloodbath")],
        )
        monkeypatch.setattr(ratings, "Gddl", fake)

        session, err = ratings.start_session("bloodbath -s tier -asc -v")

        assert err == ""
        assert session is not None
        assert session.level_id == 10565740
        assert session.level_name == "Bloodbath"
        assert session.page == 0
        assert session.total == 12
        assert session.total_pages == 2
        assert list(session.pages) == [0]  # 第一页已经在缓存里了
        assert session.query.as_api_kwargs() == {
            "sort": "rating",
            "sort_direction": "asc",
            "progress_filter": "victors",
        }


class TestRatingsAgainstStubbedHttp:
    """走真的 gddlapi + requests（被 stub_requests 拦住），确认参数名没写错"""

    def test_api_kwargs_reach_the_wire_as_query_params(self, stub_requests) -> None:
        url = "https://gdladder.com/api/level/10565740/submissions"
        stub_requests.get(
            url,
            json_data={
                "total": 12,
                "limit": 10,
                "page": 0,
                "submissions": [
                    {"ID": 1, "Rating": 21, "Enjoyment": 8, "User": {"Name": "Riot"}}
                ],
            },
        )
        session = ratings.RatingsSession(
            query=ratings.RatingsQuery(
                target="bloodbath", sort="rating", ascending=True, victors_only=True
            ),
            level_id=10565740,
            level_name="Bloodbath",
        )

        subs = session.fetch(0)

        assert subs is not None
        assert stub_requests.urls == [url]
        assert stub_requests.calls[0]["params"] == {
            "page": 0,
            "limit": 10,
            "sort": "rating",
            "sortDirection": "asc",
            "progressFilter": "victors",
        }
        assert session.render() == (
            "「Bloodbath」的提交评分　共 12 条，第 1/2 页　[按 rating 正序、只看通关]\n"
            "Tier 21, Enjoyment 8 by Riot\n"
            "n 下一页 / 结束 取消"
        )

    def test_http_error_becomes_a_failed_fetch(self, stub_requests) -> None:
        """接口 500 时 gddlapi 返回 None，会话保持原样"""
        stub_requests.get(
            "https://gdladder.com/api/level/1/submissions", status_code=500, text="boom"
        )
        session = ratings.RatingsSession(
            query=ratings.RatingsQuery(target="1"), level_id=1
        )

        assert session.fetch(0) is None
        assert session.pages == {}


# ===========================================================================
# fullsearch.py
# ===========================================================================


class TestDifficultyTables:
    """两张难度别名表"""

    def test_demon_values_are_within_the_api_range(self) -> None:
        """demonFilter 只认 1-5，超了服务器一条都不返回"""
        assert set(fullsearch.DEMON_DIFFICULTIES.values()) == {1, 2, 3, 4, 5}

    def test_nondemon_values(self) -> None:
        """0 是 auto，对应请求参数 diff=-3"""
        assert fullsearch.NONDEMON_DIFFICULTIES["0"] == -3
        assert fullsearch.NONDEMON_DIFFICULTIES["auto"] == -3
        assert set(fullsearch.NONDEMON_DIFFICULTIES.values()) == {-3, 1, 2, 3, 4, 5}

    def test_tables_have_no_uppercase_keys(self) -> None:
        """查表前都 lower() 过，表里有大写键就永远命不中"""
        assert all(k == k.lower() for k in fullsearch.DEMON_DIFFICULTIES)
        assert all(k == k.lower() for k in fullsearch.NONDEMON_DIFFICULTIES)

    def test_demon_sentinel(self) -> None:
        assert fullsearch.DIFF_DEMON == -2

    @pytest.mark.parametrize(
        ("alias", "value"),
        [
            ("1", 1), ("easy", 1),
            ("2", 2), ("medium", 2), ("med", 2),
            ("3", 3), ("hard", 3),
            ("4", 4), ("insane", 4),
            ("5", 5), ("extreme", 5), ("ex", 5),
        ],
    )
    def test_demon_alias_contents(self, alias: str, value: int) -> None:
        assert fullsearch.DEMON_DIFFICULTIES[alias] == value

    @pytest.mark.parametrize(
        ("alias", "value"),
        [
            ("0", -3), ("auto", -3),
            ("1", 1), ("easy", 1),
            ("2", 2), ("normal", 2),
            ("3", 3), ("hard", 3),
            ("4", 4), ("harder", 4),
            ("5", 5), ("insane", 5),
        ],
    )
    def test_nondemon_alias_contents(self, alias: str, value: int) -> None:
        assert fullsearch.NONDEMON_DIFFICULTIES[alias] == value


class TestAliasName:
    """_alias_name()：反查一个人类看得懂的名字"""

    @pytest.mark.parametrize(
        ("value", "expected"), [(1, "easy"), (2, "medium"), (3, "hard"), (4, "insane"), (5, "extreme")]
    )
    def test_demon_names_skip_numeric_keys(self, value: int, expected: str) -> None:
        """纯数字的 key 要跳过，取表里第一个非数字别名"""
        assert fullsearch._alias_name(fullsearch.DEMON_DIFFICULTIES, value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(-3, "auto"), (1, "easy"), (2, "normal"), (3, "hard"), (4, "harder"), (5, "insane")],
    )
    def test_nondemon_names(self, value: int, expected: str) -> None:
        assert fullsearch._alias_name(fullsearch.NONDEMON_DIFFICULTIES, value) == expected

    def test_unknown_value_falls_back_to_the_number(self) -> None:
        assert fullsearch._alias_name(fullsearch.DEMON_DIFFICULTIES, 99) == "99"


class TestFullSearchParseArgs:
    """*gdfullsearch <关键词> [-a] [-d [难度]] [-u <难度>] 的解析"""

    def test_defaults_are_rated_only(self) -> None:
        query = fullsearch.parse_args("bloodbath")

        assert query.query == "bloodbath"
        assert query.rated_only is True
        assert query.diff is None
        assert query.demon_filter is None

    def test_empty_text_raises(self) -> None:
        with pytest.raises(fullsearch.ArgError) as exc:
            fullsearch.parse_args("")

        assert str(exc.value).startswith("请提供要搜索的关卡名")
        assert fullsearch.USAGE in str(exc.value)

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(fullsearch.ArgError):
            fullsearch.parse_args("   ")

    def test_only_flags_raises(self) -> None:
        with pytest.raises(fullsearch.ArgError) as exc:
            fullsearch.parse_args("-a -d")

        assert str(exc.value).startswith("请提供要搜索的关卡名")

    def test_all_flag_drops_rated_only(self) -> None:
        assert fullsearch.parse_args("bloodbath -a").rated_only is False

    def test_flags_are_case_insensitive(self) -> None:
        query = fullsearch.parse_args("bloodbath -A -D EXTREME")

        assert query.rated_only is False
        assert query.demon_filter == 5

    def test_bare_demon_flag_sets_diff_only(self) -> None:
        query = fullsearch.parse_args("bloodbath -d")

        assert query.diff == fullsearch.DIFF_DEMON
        assert query.demon_filter is None

    @pytest.mark.parametrize(
        ("alias", "value"), sorted(fullsearch.DEMON_DIFFICULTIES.items())
    )
    def test_every_demon_alias_is_consumed(self, alias: str, value: int) -> None:
        query = fullsearch.parse_args(f"bloodbath -d {alias}")

        assert query.demon_filter == value
        assert query.diff == fullsearch.DIFF_DEMON
        assert query.query == "bloodbath"

    def test_demon_flag_keeps_a_non_difficulty_word_as_a_keyword(self) -> None:
        """-d 后面不是合法难度就不吃掉，留给关键词（docstring 明说的行为）"""
        query = fullsearch.parse_args("-d bloodbath")

        assert query.query == "bloodbath"
        assert query.diff == fullsearch.DIFF_DEMON
        assert query.demon_filter is None

    def test_demon_scale_six_is_not_a_valid_filter(self) -> None:
        """响应字段 43 里 6=extreme，但 demonFilter 只到 5，
        所以 `-d 6` 的 6 会被当成关键词 —— 这正是注释警告的那个坑。
        """
        query = fullsearch.parse_args("nine circles -d 6")

        assert query.demon_filter is None
        assert query.query == "nine circles 6"

    def test_keyword_before_flag_survives(self) -> None:
        """docstring 举的例子：想搜名字里带 extreme 的就把关键词写前面"""
        query = fullsearch.parse_args("extreme demon -d")

        assert query.query == "extreme demon"
        assert query.demon_filter is None
        assert query.diff == fullsearch.DIFF_DEMON

    def test_difficulty_right_after_the_flag_is_eaten(self) -> None:
        """反过来写就会被吃掉一个词"""
        query = fullsearch.parse_args("-d extreme demon")

        assert query.query == "demon"
        assert query.demon_filter == 5

    def test_demon_flag_eating_the_only_keyword_raises(self) -> None:
        with pytest.raises(fullsearch.ArgError) as exc:
            fullsearch.parse_args("-d easy")

        assert str(exc.value).startswith("请提供要搜索的关卡名")

    @pytest.mark.parametrize(
        ("alias", "value"), sorted(fullsearch.NONDEMON_DIFFICULTIES.items())
    )
    def test_every_nondemon_alias_parses(self, alias: str, value: int) -> None:
        query = fullsearch.parse_args(f"bloodbath -u {alias}")

        assert query.diff == value
        assert query.demon_filter is None

    def test_nondemon_flag_without_value_raises(self) -> None:
        with pytest.raises(fullsearch.ArgError) as exc:
            fullsearch.parse_args("bloodbath -u")

        message = str(exc.value)
        assert message.startswith("-u 后面要跟难度（0-5 或 auto/easy/normal/hard/harder/insane）")
        assert fullsearch.USAGE in message

    def test_unknown_nondemon_difficulty_raises(self) -> None:
        with pytest.raises(fullsearch.ArgError) as exc:
            fullsearch.parse_args("bloodbath -u demon")

        message = str(exc.value)
        assert message.startswith("看不懂的非 demon 难度：demon")
        assert "0 就是 auto" in message

    def test_nondemon_flag_always_eats_the_next_token(self) -> None:
        """和 -d 不一样，-u 后面那个词一定会被当成难度（不合法就直接报错）"""
        with pytest.raises(fullsearch.ArgError):
            fullsearch.parse_args("-u bloodbath")

    def test_demon_and_nondemon_together_raise(self) -> None:
        with pytest.raises(fullsearch.ArgError) as exc:
            fullsearch.parse_args("bloodbath -d -u 3")

        assert str(exc.value) == "-d 和 -u 不能一起用：一个是只搜 demon，一个是只搜非 demon"

    def test_conflict_is_detected_in_either_order(self) -> None:
        with pytest.raises(fullsearch.ArgError):
            fullsearch.parse_args("bloodbath -u 3 -d")
        with pytest.raises(fullsearch.ArgError):
            fullsearch.parse_args("bloodbath -d 5 -u 3")

    def test_bad_nondemon_value_wins_over_the_conflict_error(self) -> None:
        """冲突检查在循环之后，所以循环里先炸的是难度不合法"""
        with pytest.raises(fullsearch.ArgError) as exc:
            fullsearch.parse_args("bloodbath -d -u nope")

        assert "看不懂的非 demon 难度" in str(exc.value)

    def test_repeated_flags_last_one_wins(self) -> None:
        assert fullsearch.parse_args("x -d 1 -d 5").demon_filter == 5
        assert fullsearch.parse_args("x -u 1 -u 5").diff == 5
        assert fullsearch.parse_args("x -a -a").rated_only is False

    def test_keywords_are_joined_with_single_spaces(self) -> None:
        assert fullsearch.parse_args("  the   nightmare  ").query == "the nightmare"

    def test_keywords_split_by_a_flag_are_joined_in_order(self) -> None:
        assert fullsearch.parse_args("the -a nightmare").query == "the nightmare"

    def test_unknown_dash_token_becomes_a_keyword(self) -> None:
        assert fullsearch.parse_args("-x bloodbath").query == "-x bloodbath"


class TestFullSearchQueryApiKwargs:
    """FullSearchQuery.as_api_kwargs()"""

    def test_rated_only_sends_star(self) -> None:
        assert fullsearch.FullSearchQuery(query="x").as_api_kwargs() == {"star": True}

    def test_all_levels_omits_star_entirely(self) -> None:
        """注释：-a 的时候是「不传 star」而不是「传 star=0」"""
        query = fullsearch.FullSearchQuery(query="x", rated_only=False)

        assert query.as_api_kwargs() == {}

    def test_diff_and_demon_filter_pass_through(self) -> None:
        query = fullsearch.FullSearchQuery(
            query="x", diff=fullsearch.DIFF_DEMON, demon_filter=5
        )

        assert query.as_api_kwargs() == {"star": True, "diff": -2, "demon_filter": 5}

    def test_nondemon_diff_only(self) -> None:
        query = fullsearch.FullSearchQuery(query="x", rated_only=False, diff=-3)

        assert query.as_api_kwargs() == {"diff": -3}

    def test_kwarg_names_match_the_gdapi_signature(self) -> None:
        """search_levels_page 是 **kwargs 收的，键名写错了不会报错，只能这么守"""
        params = set(inspect.signature(gdapi._search_levels).parameters)
        emitted = fullsearch.FullSearchQuery(
            query="x", diff=-2, demon_filter=5
        ).as_api_kwargs()

        assert set(emitted) <= params


class TestFullSearchDescribe:
    """FullSearchQuery.describe()"""

    def test_default(self) -> None:
        assert fullsearch.FullSearchQuery(query="x").describe() == "rated"

    def test_all_levels(self) -> None:
        assert fullsearch.FullSearchQuery(query="x", rated_only=False).describe() == "全部关卡"

    def test_bare_demon(self) -> None:
        query = fullsearch.FullSearchQuery(query="x", diff=fullsearch.DIFF_DEMON)

        assert query.describe() == "rated、demon"

    def test_demon_with_filter_wins_over_the_diff_branch(self) -> None:
        query = fullsearch.FullSearchQuery(
            query="x", diff=fullsearch.DIFF_DEMON, demon_filter=5
        )

        assert query.describe() == "rated、extreme demon"

    def test_nondemon(self) -> None:
        query = fullsearch.FullSearchQuery(query="x", diff=3)

        assert query.describe() == "rated、非 demon / hard"

    def test_auto_with_all_levels(self) -> None:
        query = fullsearch.FullSearchQuery(query="x", rated_only=False, diff=-3)

        assert query.describe() == "全部关卡、非 demon / auto"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("bloodbath", "rated"),
            ("bloodbath -a", "全部关卡"),
            ("bloodbath -d", "rated、demon"),
            ("bloodbath -d 2", "rated、medium demon"),
            ("bloodbath -a -d ex", "全部关卡、extreme demon"),
            ("bloodbath -u 0", "rated、非 demon / auto"),
            ("bloodbath -u 4", "rated、非 demon / harder"),
        ],
    )
    def test_end_to_end_from_the_command_line(self, text: str, expected: str) -> None:
        assert fullsearch.parse_args(text).describe() == expected


class TestFormatLevelLine:
    """format_level_line()"""

    def test_non_demon_line(self) -> None:
        level = _gd_level(
            level_id=128, level_name="Nine Circles", creator_name="Zobros", stars=9, length=3
        )

        assert (
            fullsearch.format_level_line(1, level)
            == "1. Nine Circles by Zobros 9⭐insane (ID: 128)"
        )

    def test_demon_line_gets_a_star_prefix(self) -> None:
        """difficulty_label() 对 demon 只给 "Extreme Demon"，星数是这里补的"""
        level = _gd_level(
            level_id=10565740,
            level_name="Bloodbath",
            creator_name="Riot",
            stars=10,
            length=3,
            is_demon=True,
            demon_difficulty=6,
        )

        assert (
            fullsearch.format_level_line(3, level)
            == "3. Bloodbath by Riot 10⭐Extreme Demon (ID: 10565740)"
        )

    def test_platformer_demon_uses_the_moon_sign(self) -> None:
        level = _gd_level(
            level_id=1,
            level_name="Plat",
            creator_name="Someone",
            stars=10,
            length=5,  # LENGTH_PLAT
            is_demon=True,
            demon_difficulty=6,
        )

        assert fullsearch.format_level_line(1, level) == "1. Plat by Someone 10🌙Extreme Pemon (ID: 1)"

    def test_unrated_level(self) -> None:
        level = _gd_level(level_id=7, level_name="Unrated one", stars=0, length=3)

        assert fullsearch.format_level_line(1, level) == "1. Unrated one Unrated (ID: 7)"

    def test_missing_name_falls_back(self) -> None:
        level = _gd_level(level_id=7, level_name=None, stars=1, length=3)

        assert fullsearch.format_level_line(1, level) == "1. 未知关卡 1⭐auto (ID: 7)"

    def test_missing_stars_renders_unknown(self) -> None:
        """stars 是 None 时 difficulty_label 返回 Unknown，也不会加星数前缀"""
        level = _gd_level(level_id=7, level_name="X", stars=None, length=3)

        assert fullsearch.format_level_line(1, level) == "1. X Unknown (ID: 7)"

    def test_index_is_used_verbatim(self) -> None:
        level = _gd_level(level_id=1, level_name="X", stars=0, length=3)

        assert fullsearch.format_level_line(10, level).startswith("10. X ")

    def test_ten_star_level_without_demon_difficulty_duplicates_the_prefix(self) -> None:
        """⚠️ 看起来是 bug：stars>=10 但没有 demon_difficulty 时，
        difficulty_label() 已经返回了 "10⭐demon"，format_level_line 又补一次前缀，
        结果是 "10⭐10⭐demon"。这里记录现状，见返回值里的说明。
        """
        level = _gd_level(
            level_id=1, level_name="X", stars=10, length=3, demon_difficulty=None
        )

        assert fullsearch.format_level_line(1, level) == "1. X 10⭐10⭐demon (ID: 1)"

    def test_line_built_from_a_real_server_response(self) -> None:
        """用真的服务器 key:value 串解析出来的关卡也排得对"""
        level = GDLevel.from_server_response(
            "1:11097037:2:Sonic Wave:5:1:6:1497203:9:50:10:9000000:12:0:13:21:"
            "14:600000:15:3:17:1:18:10:19:14003:43:5:45:12000"
        )
        level.creator_name = "Cyclic"

        assert (
            fullsearch.format_level_line(2, level)
            == "2. Sonic Wave by Cyclic 10⭐Insane Demon (ID: 11097037)"
        )


class TestFullSearchSessionFetch:
    """FullSearchSession.fetch()：缓存、total、越界"""

    def _session(self, **query_kw: Any) -> fullsearch.FullSearchSession:
        return fullsearch.FullSearchSession(
            query=fullsearch.FullSearchQuery(query="bloodbath", **query_kw)
        )

    def test_fetch_records_total_when_not_capped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        level = _gd_level(level_id=1, level_name="X", stars=0, length=3)
        fake = FakeSearch({0: _search_page([level], total=25)})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session()

        levels = session.fetch(0)

        assert levels == [level]
        assert session.total == 25
        assert session.total_is_capped is False
        assert session.known_last_page == 2  # ceil(25/10) - 1

    def test_capped_total_stays_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """服务器给 9999 就是封顶，不能拿它算总页数"""
        level = _gd_level(level_id=1, level_name="X", stars=0, length=3)
        fake = FakeSearch({0: _search_page([level], total=GD_TOTAL_CAP)})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session()

        session.fetch(0)

        assert session.total_is_capped is True
        assert session.known_last_page is None

    def test_fetch_passes_query_and_kwargs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeSearch({})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session(rated_only=False, diff=-2, demon_filter=5)

        session.fetch(2)

        assert fake.calls == [
            {"query": "bloodbath", "page": 2, "diff": -2, "demon_filter": 5}
        ]

    def test_cached_page_is_not_refetched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        level = _gd_level(level_id=1, level_name="X", stars=0, length=3)
        fake = FakeSearch({0: _search_page([level], total=25)})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session()

        first = session.fetch(0)
        second = session.fetch(0)

        assert first is second
        assert len(fake.calls) == 1

    def test_empty_page_marks_the_previous_one_as_last(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """服务器回 -1 = 翻过头了，上一页就是最后一页"""
        fake = FakeSearch({})  # 任何页都空
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session()
        session.page = 2

        levels = session.fetch(3)

        assert levels == []
        assert session.last_page == 2
        assert session.known_last_page == 2

    def test_empty_first_page_clamps_last_page_to_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(fullsearch, "search_levels_page", FakeSearch({}))
        session = self._session()

        session.fetch(0)

        assert session.last_page == 0  # max(0, -1)

    def test_empty_page_is_still_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeSearch({})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session()

        session.fetch(1)
        session.fetch(1)

        assert session.pages[1] == []
        assert len(fake.calls) == 1

    def test_empty_page_does_not_overwrite_a_known_total(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        level = _gd_level(level_id=1, level_name="X", stars=0, length=3)
        fake = FakeSearch({0: _search_page([level], total=25)})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session()

        session.fetch(0)
        session.fetch(1)  # 空页

        assert session.total == 25
        assert session.total_is_capped is False


class TestFullSearchKnownLastPage:
    """known_last_page 的两个来源"""

    def _session(self) -> fullsearch.FullSearchSession:
        return fullsearch.FullSearchSession(
            query=fullsearch.FullSearchQuery(query="x")
        )

    def test_unknown_by_default(self) -> None:
        session = self._session()

        assert session.total_is_capped is True
        assert session.known_last_page is None

    @pytest.mark.parametrize(
        ("total", "expected"), [(1, 0), (10, 0), (11, 1), (25, 2), (30, 2)]
    )
    def test_derived_from_an_uncapped_total(self, total: int, expected: int) -> None:
        session = self._session()
        session.total = total
        session.total_is_capped = False

        assert session.known_last_page == expected

    def test_zero_total_gives_unknown(self) -> None:
        session = self._session()
        session.total = 0
        session.total_is_capped = False

        assert session.known_last_page is None

    def test_probed_last_page_wins_over_the_computed_one(self) -> None:
        session = self._session()
        session.total = 25
        session.total_is_capped = False
        session.last_page = 1

        assert session.known_last_page == 1

    def test_page_size_matches_gdapi(self) -> None:
        assert self._session().page_size == GD_PAGE_SIZE == 10


class TestFullSearchSessionPaging:
    """go_next / go_prev / expired"""

    def _session(self, fake: FakeSearch, **kw: Any) -> fullsearch.FullSearchSession:
        session = fullsearch.FullSearchSession(
            query=fullsearch.FullSearchQuery(query="bloodbath"), **kw
        )
        session.pages = {
            session.page: [_gd_level(level_id=1, level_name="X", stars=0, length=3)]
        }
        return session

    def test_next_moves_forward(self, monkeypatch: pytest.MonkeyPatch) -> None:
        level = _gd_level(level_id=2, level_name="Y", stars=0, length=3)
        fake = FakeSearch({1: _search_page([level], total=GD_TOTAL_CAP, page=1)})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session(fake)

        ok, msg = session.go_next()

        assert (ok, msg) == (True, "")
        assert session.page == 1
        assert session.current_levels == [level]

    def test_next_past_a_known_last_page_never_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeSearch({})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session(fake)
        session.total = 5
        session.total_is_capped = False  # known_last_page == 0

        ok, msg = session.go_next()

        assert (ok, msg) == (False, "已经是最后一页了")
        assert session.page == 0
        assert fake.calls == []

    def test_next_onto_an_empty_page_reports_last_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total 封顶时只能靠翻过头才知道到底了"""
        fake = FakeSearch({})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session(fake)

        ok, msg = session.go_next()

        assert (ok, msg) == (False, "已经是最后一页了")
        assert session.page == 0
        assert len(fake.calls) == 1
        # 探到底之后再翻就不请求了
        assert session.go_next() == (False, "已经是最后一页了")
        assert len(fake.calls) == 1

    def test_prev_before_first_page_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeSearch({})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session(fake)

        assert session.go_prev() == (False, "已经是第一页了")
        assert session.page == 0
        assert fake.calls == []

    def test_prev_uses_the_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeSearch({})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session(fake, page=1)
        session.pages[0] = [_gd_level(level_id=9, level_name="Z", stars=0, length=3)]

        ok, msg = session.go_prev()

        assert (ok, msg) == (True, "")
        assert session.page == 0
        assert session.current_levels[0].level_id == 9
        assert fake.calls == []

    def test_current_levels_is_empty_for_an_unfetched_page(self) -> None:
        session = fullsearch.FullSearchSession(
            query=fullsearch.FullSearchQuery(query="x"), page=3
        )

        assert session.current_levels == []

    def test_expiry_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock()
        monkeypatch.setattr(fullsearch, "time", clock)
        session = fullsearch.FullSearchSession(query=fullsearch.FullSearchQuery(query="x"))
        session.updated_at = clock.now

        assert fullsearch.SESSION_TIMEOUT == 120
        assert session.expired is False

        clock.advance(fullsearch.SESSION_TIMEOUT)
        assert session.expired is False

        clock.advance(0.001)
        assert session.expired is True

    def test_touch_resets_the_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock()
        monkeypatch.setattr(fullsearch, "time", clock)
        session = fullsearch.FullSearchSession(query=fullsearch.FullSearchQuery(query="x"))
        session.updated_at = clock.now - 500

        assert session.expired is True
        session.touch()

        assert session.updated_at == clock.now
        assert session.expired is False

    def test_successful_paging_touches_the_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = FakeClock()
        level = _gd_level(level_id=2, level_name="Y", stars=0, length=3)
        fake = FakeSearch({1: _search_page([level], total=GD_TOTAL_CAP, page=1)})
        monkeypatch.setattr(fullsearch, "time", clock)
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session(fake)
        session.updated_at = clock.now - 500

        session.go_next()
        assert session.expired is False

        session.updated_at = clock.now - 500
        session.go_prev()
        assert session.expired is False

    def test_failed_paging_does_not_touch_the_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """翻不动的时候不刷新时间戳，会话该过期还是过期"""
        clock = FakeClock()
        fake = FakeSearch({})
        monkeypatch.setattr(fullsearch, "time", clock)
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session(fake)
        session.updated_at = clock.now - 500

        session.go_next()

        assert session.expired is True


class TestFullSearchRender:
    """FullSearchSession.render()"""

    def _session(self, **kw: Any) -> fullsearch.FullSearchSession:
        defaults: dict[str, Any] = {"query": fullsearch.FullSearchQuery(query="bloodbath")}
        defaults.update(kw)
        session = fullsearch.FullSearchSession(**defaults)
        session.pages = {
            session.page: [
                _gd_level(
                    level_id=10565740,
                    level_name="Bloodbath",
                    creator_name="Riot",
                    stars=10,
                    length=3,
                    is_demon=True,
                    demon_difficulty=6,
                ),
                _gd_level(
                    level_id=128, level_name="Nine Circles", creator_name="Zobros",
                    stars=9, length=3,
                ),
            ]
        }
        return session

    def test_capped_total_hides_the_count(self) -> None:
        """total 是 9999 那种封顶值，不能显示出来骗人"""
        session = self._session()
        session.total = GD_TOTAL_CAP

        assert session.render() == (
            "「bloodbath」第 1 页　[rated]\n"
            "1. Bloodbath by Riot 10⭐Extreme Demon (ID: 10565740)\n"
            "2. Nine Circles by Zobros 9⭐insane (ID: 128)\n"
            "输入序号选中 / n 下一页 / 结束 取消"
        )

    def test_uncapped_total_shows_count_and_page_count(self) -> None:
        session = self._session()
        session.total = 25
        session.total_is_capped = False

        assert session.render().splitlines()[0] == "「bloodbath」共 25 条，第 1/3 页　[rated]"

    def test_uncapped_total_below_one_page_still_says_one_page(self) -> None:
        session = self._session()
        session.total = 2
        session.total_is_capped = False

        assert session.render().splitlines()[0] == "「bloodbath」共 2 条，第 1/1 页　[rated]"
        # 只有一页，就不该提示 n 下一页
        assert session.render().splitlines()[-1] == "输入序号选中 / 结束 取消"

    def test_middle_page_hints(self) -> None:
        session = self._session(page=1)
        session.total = 25
        session.total_is_capped = False

        assert session.render().splitlines()[-1] == "输入序号选中 / n 下一页 / p 上一页 / 结束 取消"

    def test_last_page_hints(self) -> None:
        session = self._session(page=2)
        session.total = 25
        session.total_is_capped = False

        assert session.render().splitlines()[-1] == "输入序号选中 / p 上一页 / 结束 取消"

    def test_unknown_last_page_always_offers_next(self) -> None:
        session = self._session(page=3)
        session.total = GD_TOTAL_CAP

        assert session.render().splitlines()[-1] == "输入序号选中 / n 下一页 / p 上一页 / 结束 取消"

    def test_filters_are_shown_in_the_header(self) -> None:
        session = self._session(
            query=fullsearch.FullSearchQuery(
                query="bloodbath", rated_only=False, diff=-2, demon_filter=5
            )
        )
        session.total = GD_TOTAL_CAP

        assert session.render().splitlines()[0] == "「bloodbath」第 1 页　[全部关卡、extreme demon]"

    def test_empty_page_message_includes_the_filters(self) -> None:
        session = fullsearch.FullSearchSession(
            query=fullsearch.FullSearchQuery(query="bloodbath", diff=3)
        )

        assert session.render() == "没有找到符合条件的关卡（rated、非 demon / hard）"

    def test_numbering_starts_at_one_on_every_page(self) -> None:
        """序号是每页从 1 开始的（选中时按当前页的序号找）"""
        session = self._session(page=2)
        session.total = GD_TOTAL_CAP
        lines = session.render().splitlines()

        assert lines[1].startswith("1. ")
        assert lines[2].startswith("2. ")


class TestFullSearchStartSession:
    """start_session()"""

    def test_arg_error_propagates(self) -> None:
        with pytest.raises(fullsearch.ArgError):
            fullsearch.start_session("")

    def test_no_results_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeSearch({})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)

        session, err = fullsearch.start_session("bloodbath -a -d 5")

        assert session is None
        assert err == "没有找到「bloodbath」相关的关卡（全部关卡、extreme demon）"

    def test_success_returns_a_primed_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        level = _gd_level(level_id=1, level_name="X", stars=0, length=3)
        fake = FakeSearch({0: _search_page([level], total=25)})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)

        session, err = fullsearch.start_session("bloodbath -u 3")

        assert err == ""
        assert session is not None
        assert session.page == 0
        assert list(session.pages) == [0]
        assert session.query.diff == 3
        assert fake.calls == [
            {"query": "bloodbath", "page": 0, "star": True, "diff": 3}
        ]


class TestFullSearchAgainstStubbedHttp:
    """走真的 gdapi + requests（被 stub_requests 拦住）"""

    GD_URL = "http://www.boomlings.com/database/getGJLevels21.php"

    def _response_text(self) -> str:
        """一段最小但格式真实的 getGJLevels21 响应：关卡#作者#歌曲#分页#hash"""
        levels = "|".join(
            [
                "1:10565740:2:Bloodbath:5:2:6:503085:8:10:9:50:10:26758346:12:0:"
                "13:21:14:1387586:15:3:17:1:18:10:19:14003:43:6:45:24746",
                "1:11097037:2:Sonic Wave:5:1:6:1497203:8:10:9:50:10:9000000:12:0:"
                "13:21:14:600000:15:3:18:9:19:0:45:12000",
            ]
        )
        creators = "503085:Riot:16|1497203:Cyclic:16"
        return f"{levels}#{creators}##5:0:10#somehash"

    def test_full_round_trip(self, stub_requests, make_response) -> None:
        stub_requests.post(self.GD_URL, make_response(200, text=self._response_text()))

        session, err = fullsearch.start_session("bloodbath")

        assert err == ""
        assert session is not None
        # -a 没给 -> star=1；没给 -d/-u -> 请求里根本不带 diff / demonFilter
        sent = stub_requests.calls[0]["data"]
        assert sent["str"] == "bloodbath"
        assert sent["star"] == "1"
        assert sent["page"] == 0
        assert "diff" not in sent
        assert "demonFilter" not in sent

        assert session.render() == (
            "「bloodbath」共 5 条，第 1/1 页　[rated]\n"
            "1. Bloodbath by Riot 10⭐Extreme Demon (ID: 10565740)\n"
            "2. Sonic Wave by Cyclic 9⭐insane (ID: 11097037)\n"
            "输入序号选中 / 结束 取消"
        )

    def test_filters_are_translated_into_request_fields(
        self, stub_requests, make_response
    ) -> None:
        stub_requests.post(self.GD_URL, make_response(200, text=self._response_text()))

        fullsearch.start_session("bloodbath -a -d extreme")
        sent = stub_requests.calls[0]["data"]

        assert "star" not in sent  # -a = 不传 star
        assert sent["diff"] == fullsearch.DIFF_DEMON
        assert sent["demonFilter"] == 5

    def test_server_minus_one_is_an_empty_result(self, stub_requests, make_response) -> None:
        """搜不到东西服务器就回 -1，这里要变成友好提示而不是异常"""
        stub_requests.post(self.GD_URL, make_response(200, text="-1"))

        session, err = fullsearch.start_session("绝对搜不到的关卡")

        assert session is None
        assert err == "没有找到「绝对搜不到的关卡」相关的关卡（rated）"

    def test_network_failure_is_swallowed_by_gdapi(self, stub_requests) -> None:
        """requests 抛异常时 gdapi 返回空页，start_session 走「没找到」分支"""
        import requests as _requests

        stub_requests.post(self.GD_URL, _requests.ConnectTimeout("boom"))

        session, err = fullsearch.start_session("bloodbath")

        assert session is None
        assert err.startswith("没有找到「bloodbath」相关的关卡")
