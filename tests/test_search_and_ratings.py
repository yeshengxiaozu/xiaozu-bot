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
from typing import Any, NamedTuple

import pytest

from xiaozu_bot.plugins.gdlevelsearch import gdapi
from xiaozu_bot.plugins.gdlevelsearch.api.gdapi import (
    GD_PAGE_SIZE,
    GD_TOTAL_CAP,
    GDLevel,
    SearchPage,
)
from xiaozu_bot.plugins.gdlevelsearch.api.gddlapi import (
    GDDL_SUBMISSION_LIMIT,
    PROGRESS_FILTERS,
    SORT_DIRECTIONS,
    SUBMISSION_SORTS,
    GDDLLevel,
    GDDLSearchEntry,
    Submission,
    SubmissionPage,
)
from xiaozu_bot.plugins.gdlevelsearch.commands import fullsearch, ratings

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


def _gddl_level(
    level_id: int, name: str, rating: float | None = None
) -> GDDLSearchEntry:
    """造一个搜索接口返回的精简 GDDL 条目。"""
    return GDDLSearchEntry(
        {
            "id": level_id,
            "rating": rating,
            "enjoyment": None,
            "name": name,
            "difficulty": "Extreme",
            "rarity": 0,
            "publisherName": "Publisher",
            "songName": "Stereo Madness",
        }
    )


def _gddl_detail(level_id: int, name: str) -> GDDLLevel:
    """The detail endpoint still returns the full, nested DTO."""
    return GDDLLevel(
        {
            "ID": level_id,
            "Rating": None,
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
                "Rarity": 0,
                "Song": {"ID": -1, "Name": "Stereo Madness", "Author": "Foreverbound"},
            },
        }
    )


def _sub(
    rating: float | None = None,
    enjoyment: float | None = None,
    *,
    name: str | None = None,
    user_id: int | None = None,
    second: str | None = None,
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
        {"total": total, "limit": limit, "page": page, "data": subs}
    )


class FakeGddl:
    """ratings 模块里 Gddl 的替身，记录调用参数。

    ratings 用的是 `Gddl.getsubmissions(...)` 这种类属性调用，
    换成实例之后就是绑定方法，参数照样对得上。
    """

    def __init__(
        self,
        pages: dict[int, SubmissionPage | None] | None = None,
        *,
        by_name: list[Any] | None = None,
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
    ) -> SubmissionPage | None:
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

    def __init__(self, pages: dict[int, SearchPage] | None = None) -> None:
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


def _pages_for(total: int, page_size: int) -> int:
    """总条数 -> 总页数（向上取整，至少一页）。

    分页期望值一律用这个算，别把 10 抄进断言里 —— GD_PAGE_SIZE 真要调，
    改常数就行，不用回来改一堆用例。
    """
    return max(1, -(-total // page_size))


class _ModSpec(NamedTuple):
    """fullsearch / ratings 里那些「除了名字不一样、行为完全一样」的部分。

    生产代码里这两个模块就是复制粘贴的兄弟（各自的 docstring 都写了），
    共通行为参数化到模块上一起测：改了一个忘了改另一个，这里就红。
    """

    mod: Any
    keyword_attr: str   # 解析结果里放关键词的字段名
    only_flags: str     # 一串只有开关、没有关键词的输入
    bare_flag: str      # 一个不吃后面那个词的开关，用来把关键词切成两段


MOD_SPECS = [
    pytest.param(_ModSpec(fullsearch, "query", "-a -d", "-a"), id="fullsearch"),
    pytest.param(_ModSpec(ratings, "target", "-asc -v", "-asc"), id="ratings"),
]


@pytest.mark.parametrize("spec", MOD_SPECS)
class TestParseArgsSharedShape:
    """两个 parse_args 共通的那部分：关键词怎么拼、没关键词怎么报错"""

    def test_empty_text_raises_with_usage(self, spec: _ModSpec) -> None:
        """报错里要带上用法 —— 用法怎么写是模块自己的事，从常量里取"""
        with pytest.raises(spec.mod.ArgError) as exc:
            spec.mod.parse_args("")

        assert spec.mod.USAGE in str(exc.value)

    def test_whitespace_only_raises(self, spec: _ModSpec) -> None:
        with pytest.raises(spec.mod.ArgError):
            spec.mod.parse_args("   \t  ")

    def test_only_flags_without_a_keyword_raises(self, spec: _ModSpec) -> None:
        """全是开关没给关键词，走的是循环之后那处「请提供…」"""
        with pytest.raises(spec.mod.ArgError):
            spec.mod.parse_args(spec.only_flags)

    def test_keywords_are_joined_with_single_spaces(self, spec: _ModSpec) -> None:
        """text.split() 吃掉连续空白，拼回来只剩单空格"""
        query = spec.mod.parse_args("  the   nightmare  ")

        assert getattr(query, spec.keyword_attr) == "the nightmare"

    def test_keywords_split_by_a_flag_are_joined_in_order(self, spec: _ModSpec) -> None:
        """关键词被开关切开的话，是按出现顺序用单空格拼起来的"""
        query = spec.mod.parse_args(f"the {spec.bare_flag} nightmare")

        assert getattr(query, spec.keyword_attr) == "the nightmare"

    def test_unknown_dash_token_becomes_part_of_the_keyword(
        self, spec: _ModSpec
    ) -> None:
        """不认识的 -xxx 不报错，会被当成关键词的一部分（现状如此）"""
        query = spec.mod.parse_args("-x bloodbath")

        assert getattr(query, spec.keyword_attr) == "-x bloodbath"


# ===========================================================================
# ratings.py
# ===========================================================================


class TestSortAliases:
    """SORT_ALIASES 这张表本身。

    表是纯数据，逐行抄一遍等于把表写两份 —— 只守住它必须成立的那几条不变式，
    加别名 / 改别名都不用回来动这里。
    """

    def test_every_alias_points_at_a_field_the_api_accepts(self) -> None:
        """表里每个别名指向的字段都必须是 gddlapi 认的排序字段。

        gddlapi.getsubmissions 会把不认识的 sort 直接丢掉（只 warning），
        所以别名表写错了不会报错，只是排序悄悄失效 —— 必须在这里拦住。
        """
        assert set(ratings.SORT_ALIASES.values()) <= SUBMISSION_SORTS

    def test_every_field_has_a_chinese_alias(self) -> None:
        """这张表就是给用户看的 UI，每个字段的中文别名都不能漏"""
        with_chinese = {
            field for alias, field in ratings.SORT_ALIASES.items() if not alias.isascii()
        }

        assert with_chinese == set(ratings.SORT_ALIASES.values())

    def test_username_sort_is_deliberately_not_exposed(self) -> None:
        """注释说 API 的 username 排序实测无效，所以别名表里不该有它"""
        assert "username" in SUBMISSION_SORTS
        assert "username" not in ratings.SORT_ALIASES.values()

    def test_alias_table_has_no_uppercase_keys(self) -> None:
        """parse_args 是拿 lower() 之后的词去查表的，表里有大写就永远命不中"""
        assert all(key == key.lower() for key in ratings.SORT_ALIASES)


class TestRatingsParseArgs:
    """*gdratings <关卡> [-s <排序>] [-asc] [-v] 的解析。

    「关键词怎么拼、没关键词怎么报错」这部分和 fullsearch 一模一样，
    在 TestParseArgsSharedShape 里一起测了，这里只放 ratings 自己的开关。
    """

    def test_plain_target(self) -> None:
        query = ratings.parse_args("bloodbath")

        assert query.target == "bloodbath"
        assert query.sort is None
        assert query.ascending is False
        assert query.victors_only is False

    def test_sort_flag_alone_without_target_raises(self) -> None:
        """`-s tier` 把 tier 吃掉当排序字段了，剩下的关键词是空的"""
        with pytest.raises(ratings.ArgError):
            ratings.parse_args("-s tier")

    def test_every_alias_parses(self) -> None:
        """-s 后面接表里任意一个别名都能解析出对应字段（表加了别名自动跟着测）"""
        for alias, api_field in ratings.SORT_ALIASES.items():
            assert ratings.parse_args(f"bloodbath -s {alias}").sort == api_field

    def test_flags_are_case_insensitive(self) -> None:
        """开关和排序字段都是 lower() 之后比对的"""
        query = ratings.parse_args("bloodbath -S TIER -ASC -V")

        assert query.sort == "rating"
        assert query.ascending is True
        assert query.victors_only is True

    def test_unknown_sort_raises_and_echoes_what_the_user_wrote(self) -> None:
        with pytest.raises(ratings.ArgError) as exc:
            ratings.parse_args("bloodbath -s StArS")

        # 原样回显用户写的那个词（不是 lower 之后的），方便他自己看出打错了
        assert "StArS" in str(exc.value)

    def test_sort_flag_missing_value_raises(self) -> None:
        """-s 在末尾没值就报错，不能把 -s 自己当成排序字段"""
        with pytest.raises(ratings.ArgError):
            ratings.parse_args("bloodbath -s")

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

    def test_repeated_flags_last_one_wins(self) -> None:
        assert ratings.parse_args("bloodbath -s tier -s enj").sort == "enjoyment"
        # 布尔开关重复给是幂等的
        query = ratings.parse_args("bloodbath -asc -asc -v -v")
        assert query.ascending is True
        assert query.victors_only is True

    def test_numeric_id_target_is_kept_as_text(self) -> None:
        """parse_args 不负责解析 id，原样留给 resolve_level"""
        assert ratings.parse_args("10565740").target == "10565740"


class TestRatingsQueryApiKwargs:
    """RatingsQuery.as_api_kwargs()"""

    def test_sort_always_carries_a_direction(self) -> None:
        """注释说「只给 sort 不给方向的话接口按自己默认来」，所以方向必须一起给"""
        assert ratings.RatingsQuery(target="x").as_api_kwargs() == {}
        assert ratings.RatingsQuery(target="x", sort="rating").as_api_kwargs() == {
            "sort": "rating",
            "sort_direction": "desc",
        }
        assert ratings.RatingsQuery(
            target="x", sort="rating", ascending=True
        ).as_api_kwargs() == {"sort": "rating", "sort_direction": "asc"}
        # 光给 -asc 不给 -s 的话方向不传（接口默认排序自己说了算）
        assert ratings.RatingsQuery(target="x", ascending=True).as_api_kwargs() == {}

    def test_victors_only_maps_to_progress_filter(self) -> None:
        assert ratings.RatingsQuery(target="x", victors_only=True).as_api_kwargs() == {
            "progress_filter": "victors"
        }
        # 三个开关一起给的时候互不干扰
        assert ratings.RatingsQuery(
            target="x", sort="enjoyment", ascending=True, victors_only=True
        ).as_api_kwargs() == {
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
        from xiaozu_bot.plugins.gdlevelsearch.api.gddlapi import Gddl

        params = set(inspect.signature(Gddl.getsubmissions).parameters)
        emitted = ratings.RatingsQuery(
            target="x", sort="rating", ascending=True, victors_only=True
        ).as_api_kwargs()

        assert set(emitted) <= params


class TestRatingsQueryDescribe:
    """RatingsQuery.describe()。

    摘要怎么措辞随便改，这里只守「什么时候有摘要、不同条件的摘要区分得开」。
    """

    def test_nothing_to_describe_is_empty(self) -> None:
        assert ratings.RatingsQuery(target="x").describe() == ""
        # 没有 sort 的时候 -asc 不进 describe（跟 as_api_kwargs 一致）
        assert ratings.RatingsQuery(target="x", ascending=True).describe() == ""

    def test_each_condition_gets_its_own_summary(self) -> None:
        """正序 / 倒序 / 只看通关 三种条件的摘要必须互不相同"""
        desc = ratings.RatingsQuery(target="x", sort="rating").describe()
        asc = ratings.RatingsQuery(target="x", sort="rating", ascending=True).describe()
        victors = ratings.RatingsQuery(target="x", victors_only=True).describe()

        assert len({desc, asc, victors}) == 3
        assert all([desc, asc, victors])

    def test_multiple_conditions_are_all_carried(self) -> None:
        """几个条件一起给的时候，每一段都得在摘要里"""
        both = ratings.RatingsQuery(
            target="x", sort="enjoyment", ascending=True, victors_only=True
        ).describe()

        assert (
            ratings.RatingsQuery(target="x", sort="enjoyment", ascending=True).describe()
            in both
        )
        assert ratings.RatingsQuery(target="x", victors_only=True).describe() in both

    def test_describe_uses_api_field_name_not_alias(self) -> None:
        """用户写 -s tier，describe 里显示的是 API 字段名 rating"""
        summary = ratings.parse_args("bloodbath -s tier").describe()

        assert "rating" in summary
        assert "tier" not in summary


class TestResolveLevel:
    """resolve_level()：关卡名或 id -> level_id"""

    def test_pure_digits_longer_than_min_go_to_id_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl(by_id=_gddl_detail(10565740, "Bloodbath"))
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
        # 走的是「按名字搜、搜不到」这一支，提示里要把用户输的东西回显出来
        assert "1234" in err

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

    def test_no_match_returns_no_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ratings, "Gddl", FakeGddl(by_name=[]))

        level_id, name, err = ratings.resolve_level("不存在的关")

        assert (level_id, name) == (None, None)
        assert "不存在的关" in err  # 搜不到时要回显用户输的关卡名

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
                    _gddl_level(731, "Sonic Wave", rating=19.456),
                    _gddl_level(842, "Sonic Wave Rebirth"),
                ]
            ),
        )

        level_id, name, err = ratings.resolve_level("sonic")

        assert (level_id, name) == (None, None)
        # 认不出是哪一关的时候要把候选一条一行列出来，让用户拿 id 再查一次
        lines = err.splitlines()
        assert len(lines) == 1 + 2  # 一行说明 + 两个候选
        assert "Sonic Wave" in lines[1]
        assert "731" in lines[1]
        assert "19.46" in lines[1]  # Rating 保留两位小数
        assert "Sonic Wave Rebirth" in lines[2]
        assert "842" in lines[2]

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

        # 一行说明 + 10 个候选 + 一行「剩下的」
        assert len(lines) == 1 + 10 + 1
        assert "Level 1" in lines[1]
        assert "Level 10" in lines[10]
        assert "Level 11" not in err  # 第 11 个开始就不列了
        assert "3" in lines[-1]       # 溢出行要说清楚还剩几个

    def test_exactly_ten_candidates_has_no_overflow_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ratings,
            "Gddl",
            FakeGddl(by_name=[_gddl_level(i, f"Level {i}") for i in range(1, 11)]),
        )

        _, _, err = ratings.resolve_level("level")

        # 正好 10 个就没有溢出那一行：说明 + 10 个候选
        assert len(err.splitlines()) == 1 + 10

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

        assert "A one" in err
        assert "A two" in err
        # 两条 id 里都没有 0：rating 那个 0 要是漏判成真值，err 里就会冒出个 0 来
        assert "0" not in err

    def test_none_entries_from_the_api_are_filtered_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """列表里混进 None 不该炸，会被 pool 过滤掉"""
        monkeypatch.setattr(
            ratings, "Gddl", FakeGddl(by_name=[None, _gddl_level(7, "Deadlocked")])
        )

        assert ratings.resolve_level("dead") == (7, "Deadlocked", "")


class TestFormatSubmissionLine:
    """format_submission_line()。

    一行里该有的东西：tier、enjoyment、谁提交的。排版怎么写不管。
    """

    def test_line_carries_the_values_and_who_submitted(self) -> None:
        fmt = ratings.format_submission_line

        line = fmt(Submission(_sub(21, 8.5, name="Riot", user_id=99)))
        assert "21" in line
        assert "8.5" in line
        assert "Riot" in line

        # 双人提交两个名字都要出现
        both = fmt(Submission(_sub(30, 9, name="A", user_id=1, second="B")))
        assert "A" in both
        assert "B" in both

    def test_zero_is_not_confused_with_missing(self) -> None:
        """判的是 `is not None`，所以 0 分要老老实实显示成 0，不能当成没填"""
        fmt = ratings.format_submission_line

        zero = fmt(Submission(_sub(0, 0, name="Riot", user_id=99)))
        missing = fmt(Submission(_sub(None, None, name="Riot", user_id=99)))

        assert zero != missing
        assert "0" in zero

    def test_user_name_falls_back_to_user_id(self) -> None:
        """用的是 `or`，None 和空字符串一样兜底到用户 id"""
        fmt = ratings.format_submission_line

        assert "4242" in fmt(Submission(_sub(10, 5, user_id=4242)))
        assert "4242" in fmt(Submission(_sub(10, 5, name="", user_id=4242)))


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

        assert (ok, bool(msg)) == (False, True)  # 翻不动，而且说明了原因
        assert session.page == 1
        assert fake.calls == []  # 越界判断在请求之前

    def test_single_page_session_cannot_go_next(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl({})
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, total_pages=1)

        ok, msg = session.go_next()

        assert (ok, bool(msg)) == (False, True)
        assert fake.calls == []

    def test_next_with_failed_request_keeps_the_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl({})  # 第 1 页请求失败
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, total_pages=2)

        ok, msg = session.go_next()

        assert (ok, bool(msg)) == (False, True)
        assert session.page == 0
        # 和「越界不请求」那条区分开：这里是真发了请求才失败的
        assert len(fake.calls) == 1

    def test_prev_before_first_page_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl({})
        monkeypatch.setattr(ratings, "Gddl", fake)
        session = self._session(fake, total_pages=3)

        ok, msg = session.go_prev()

        assert (ok, bool(msg)) == (False, True)
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

    def _filled(self, *, page: int = 0, total: int, total_pages: int, **kw: Any):
        session = self._session(page=page, **kw)
        session.total = total
        session.total_pages = total_pages
        session.pages = {page: [Submission(_sub(1, 1, name="a"))]}
        return session

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

        lines = session.render().splitlines()

        # 标题行只断言会随行为变的：关卡名、总条数。怎么排版随便改
        assert "Bloodbath" in lines[0]
        assert str(session.total) in lines[0]
        # 中间是每条提交一行，顺序和 pages 里一致
        assert len(lines) == 1 + 2 + 1  # 标题 + 两条提交 + 提示行
        assert lines[1] == ratings.format_submission_line(session.current[0])
        assert lines[2] == ratings.format_submission_line(session.current[1])

    def test_hints_depend_on_where_the_page_is(self) -> None:
        """翻页提示只在真能翻的方向上出现 —— 四个位置的提示互不相同"""
        first = self._filled(total=25, total_pages=3).render().splitlines()[-1]
        middle = self._filled(page=1, total=25, total_pages=3).render().splitlines()[-1]
        last = self._filled(page=2, total=25, total_pages=3).render().splitlines()[-1]
        only = self._filled(total=1, total_pages=1).render().splitlines()[-1]

        assert len({first, middle, last, only}) == 4

    def test_query_description_is_appended_to_the_header(self) -> None:
        session = self._filled(
            total=2,
            total_pages=1,
            query=ratings.RatingsQuery(
                target="bloodbath", sort="rating", ascending=True, victors_only=True
            ),
        )

        head = session.render().splitlines()[0]

        # 只断言 describe() 的内容被带进了标题行，标题怎么排版不管
        assert session.query.describe() in head

    def test_empty_page_is_a_single_line_message(self) -> None:
        """一条提交都没有时不列表格、不给翻页提示，只发一句话"""
        text = self._session().render()

        assert len(text.splitlines()) == 1
        assert "Bloodbath" in text

    def test_title_falls_back_to_level_id(self) -> None:
        session = self._filled(total=1, total_pages=1, level_name=None)

        assert "10565740" in session.render().splitlines()[0]


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
        # 定位失败时把 resolve_level 的原话透传出去
        assert err == ratings.resolve_level("不存在的关")[2]
        assert "不存在的关" in err

    def test_request_failure_and_no_submissions_are_different_branches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """关卡找得到、但拉评分的请求挂了 —— 和「没人提交」是两条不同的提示"""
        level = [_gddl_level(1, "Cataclysm")]

        monkeypatch.setattr(ratings, "Gddl", FakeGddl({}, by_name=level))
        failed_session, failed_err = ratings.start_session("cataclysm")

        monkeypatch.setattr(
            ratings, "Gddl", FakeGddl({0: _sub_page([], total=0)}, by_name=level)
        )
        empty_session, empty_err = ratings.start_session("cataclysm")

        assert (failed_session, empty_session) == (None, None)
        assert failed_err and empty_err
        assert failed_err != empty_err   # 请求挂了 ≠ 没人提交
        assert "Cataclysm" in empty_err  # 没人提交时要说清是哪一关

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
        url = "https://gdladder.com/api/levels/10565740/submissions"
        stub_requests.get(
            url,
            json_data={
                "total": 12,
                "limit": 10,
                "page": 0,
                "data": [
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
        # 响应也要被吃进会话里：总条数原样收下，总页数由每页条数推出来
        assert len(subs) == 1
        assert session.total == 12
        assert session.total_pages == _pages_for(12, GDDL_SUBMISSION_LIMIT)

    def test_http_error_becomes_a_failed_fetch(self, stub_requests) -> None:
        """接口 500 时 gddlapi 返回 None，会话保持原样"""
        stub_requests.get(
            "https://gdladder.com/api/levels/1/submissions", status_code=500, text="boom"
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
    """两张难度别名表。

    表是纯数据，逐行抄一遍等于把表写两份 —— 只守住必须成立的那几条不变式。
    """

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

    def test_numeric_keys_map_to_themselves(self) -> None:
        """用户直接写数字时必须原样传给服务器（"0" 是唯一例外，见上）"""
        for table in (fullsearch.DEMON_DIFFICULTIES, fullsearch.NONDEMON_DIFFICULTIES):
            for key, value in table.items():
                if key.isdigit() and key != "0":
                    assert value == int(key)

    def test_named_aliases_follow_the_ingame_order(self) -> None:
        """名字和数字对错位不会报错，只是搜出来的难度悄悄不对"""
        demon = fullsearch.DEMON_DIFFICULTIES
        assert [demon[k] for k in ("easy", "medium", "hard", "insane", "extreme")] == [
            1, 2, 3, 4, 5
        ]
        # 缩写和全称必须指向同一个难度
        assert demon["med"] == demon["medium"]
        assert demon["ex"] == demon["extreme"]

        nondemon = fullsearch.NONDEMON_DIFFICULTIES
        assert [
            nondemon[k] for k in ("auto", "easy", "normal", "hard", "harder", "insane")
        ] == [-3, 1, 2, 3, 4, 5]


class TestAliasName:
    """_alias_name()：反查一个人类看得懂的名字"""

    def test_demon_names_skip_numeric_keys(self) -> None:
        """纯数字的 key 要跳过，取表里第一个非数字别名"""
        names = [fullsearch._alias_name(fullsearch.DEMON_DIFFICULTIES, v) for v in range(1, 6)]

        assert names == ["easy", "medium", "hard", "insane", "extreme"]

    def test_nondemon_names(self) -> None:
        values = (-3, 1, 2, 3, 4, 5)
        names = [fullsearch._alias_name(fullsearch.NONDEMON_DIFFICULTIES, v) for v in values]

        assert names == ["auto", "easy", "normal", "hard", "harder", "insane"]

    def test_unknown_value_falls_back_to_the_number(self) -> None:
        assert fullsearch._alias_name(fullsearch.DEMON_DIFFICULTIES, 99) == "99"


class TestFullSearchParseArgs:
    """*gdfullsearch <关键词> [-a] [-d [难度]] [-u <难度>] 的解析。

    「关键词怎么拼、没关键词怎么报错」这部分和 ratings 一模一样，
    在 TestParseArgsSharedShape 里一起测了，这里只放 fullsearch 自己的开关。
    """

    def test_defaults_are_rated_only(self) -> None:
        query = fullsearch.parse_args("bloodbath")

        assert query.query == "bloodbath"
        assert query.rated_only is True
        assert query.diff is None
        assert query.demon_filter is None

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

    def test_every_demon_alias_is_consumed(self) -> None:
        """-d 后面接表里任意一个别名都会被吃掉（表加了别名自动跟着测）"""
        for alias, value in fullsearch.DEMON_DIFFICULTIES.items():
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
        """`-d easy` 里的 easy 被当难度吃掉了，一个关键词都不剩"""
        with pytest.raises(fullsearch.ArgError):
            fullsearch.parse_args("-d easy")

    def test_every_nondemon_alias_parses(self) -> None:
        for alias, value in fullsearch.NONDEMON_DIFFICULTIES.items():
            query = fullsearch.parse_args(f"bloodbath -u {alias}")

            assert query.diff == value
            assert query.demon_filter is None

    def test_nondemon_flag_without_value_raises(self) -> None:
        """-u 后面什么都没有就报错，报错里要带上用法"""
        with pytest.raises(fullsearch.ArgError) as exc:
            fullsearch.parse_args("bloodbath -u")

        assert fullsearch.USAGE in str(exc.value)

    def test_unknown_nondemon_difficulty_raises(self) -> None:
        """demon 不是合法的「非 demon 难度」，-u 不认它"""
        with pytest.raises(fullsearch.ArgError):
            fullsearch.parse_args("bloodbath -u demon")

    def test_nondemon_flag_always_eats_the_next_token(self) -> None:
        """和 -d 不一样，-u 后面那个词一定会被当成难度（不合法就直接报错）"""
        with pytest.raises(fullsearch.ArgError):
            fullsearch.parse_args("-u bloodbath")

    def test_demon_and_nondemon_together_raise(self) -> None:
        """两个开关谁先谁后都要拦住（难度本身都是合法的，只可能是冲突炸的）"""
        for text in ("bloodbath -d -u 3", "bloodbath -u 3 -d", "bloodbath -d 5 -u 3"):
            with pytest.raises(fullsearch.ArgError):
                fullsearch.parse_args(text)

    def test_repeated_flags_last_one_wins(self) -> None:
        assert fullsearch.parse_args("x -d 1 -d 5").demon_filter == 5
        assert fullsearch.parse_args("x -u 1 -u 5").diff == 5
        assert fullsearch.parse_args("x -a -a").rated_only is False


class TestFullSearchQueryApiKwargs:
    """FullSearchQuery.as_api_kwargs()"""

    def test_star_is_sent_only_for_rated_only(self) -> None:
        """注释：-a 的时候是「不传 star」而不是「传 star=0」"""
        assert fullsearch.FullSearchQuery(query="x").as_api_kwargs() == {"star": True}
        assert (
            fullsearch.FullSearchQuery(query="x", rated_only=False).as_api_kwargs() == {}
        )

    def test_diff_and_demon_filter_pass_through(self) -> None:
        assert fullsearch.FullSearchQuery(
            query="x", diff=fullsearch.DIFF_DEMON, demon_filter=5
        ).as_api_kwargs() == {"star": True, "diff": -2, "demon_filter": 5}
        # 非 demon 只有 diff，没有 demon_filter
        assert fullsearch.FullSearchQuery(
            query="x", rated_only=False, diff=-3
        ).as_api_kwargs() == {"diff": -3}

    def test_kwarg_names_match_the_gdapi_signature(self) -> None:
        """search_levels_page 是 **kwargs 收的，键名写错了不会报错，只能这么守"""
        params = set(inspect.signature(gdapi._search_levels).parameters)
        emitted = fullsearch.FullSearchQuery(
            query="x", diff=-2, demon_filter=5
        ).as_api_kwargs()

        assert set(emitted) <= params


class TestFullSearchDescribe:
    """FullSearchQuery.describe()。

    摘要是给用户看的一句话，措辞随便改；这里只守「每种筛选都说得出话、
    而且互相区分得开」—— 从命令行原文一路走到摘要，describe() 的分支全覆盖到了。
    """

    def test_every_filter_combo_gets_its_own_summary(self) -> None:
        texts = [
            "bloodbath",        # 默认只搜 rated
            "bloodbath -a",     # 全部关卡
            "bloodbath -d",     # 只说 demon，没具体难度
            "bloodbath -d 2",   # 有 demon_filter 就走它那一支
            "bloodbath -a -d ex",
            "bloodbath -u 0",   # 非 demon，0 就是 auto
            "bloodbath -u 4",
        ]

        summaries = [fullsearch.parse_args(t).describe() for t in texts]

        assert all(summaries)                      # 每种组合都得说点什么
        assert len(set(summaries)) == len(texts)   # 而且互相区分得开


class TestFormatLevelLine:
    """format_level_line()"""

    def test_line_carries_index_name_creator_difficulty_and_id(self) -> None:
        """一行里该有的东西：序号、关卡名、作者、难度标签、id。排版怎么写不管"""
        level = _gd_level(
            level_id=128, level_name="Nine Circles", creator_name="Zobros", stars=9, length=3
        )

        line = fullsearch.format_level_line(7, level)

        assert "7" in line  # 序号原样用，不重新编号
        assert "Nine Circles" in line
        assert "Zobros" in line
        assert level.difficulty_label() in line  # "9⭐insane"
        assert "128" in line

    def test_missing_fields_do_not_blow_up(self) -> None:
        """名字 / 作者 / 星数缺了都不能炸，各有各的兜底"""
        no_name = _gd_level(level_id=7, level_name=None, stars=1, length=3)
        no_creator = _gd_level(level_id=7, level_name="Unrated one", stars=0, length=3)
        # stars 是 None 时 difficulty_label 返回 Unknown，也不会加星数前缀
        no_stars = _gd_level(level_id=7, level_name="X", stars=None, length=3)

        for level in (no_name, no_creator, no_stars):
            line = fullsearch.format_level_line(1, level)

            assert line.strip()                       # 兜底之后不能是空行
            assert "None" not in line                 # 更不能把 None 印出来
            assert "7" in line                        # id 一定还在
            assert level.difficulty_label() in line   # 难度标签原样透传

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

        line = fullsearch.format_level_line(3, level)

        assert f"10⭐{level.difficulty_label()}" in line
        assert "Bloodbath" in line
        assert "Riot" in line
        assert "10565740" in line

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

        # plat 关卡补的是月亮不是星星
        assert f"10🌙{level.difficulty_label()}" in fullsearch.format_level_line(1, level)

    def test_ten_star_level_without_demon_difficulty_keeps_one_prefix(self) -> None:
        """stars>=10 但没有 demon_difficulty 时 difficulty_label() 兜底返回 "10⭐demon"，
        自己就带了星数，format_level_line 不能再补一次（以前是 "10⭐10⭐demon"）。
        """
        level = _gd_level(
            level_id=1, level_name="X", stars=10, length=3, demon_difficulty=None
        )
        label = level.difficulty_label()

        line = fullsearch.format_level_line(1, level)

        assert label in line              # 标签原样透传
        assert line.count("⭐") == 1       # 只有标签自带的那一个星号，没被补第二次

    def test_other_self_starred_labels_also_keep_one_prefix(self) -> None:
        """plat 和 11 星走的是同一个「标签自带星数就别再补」的判断。

        兜底串是 gdapi 硬编码的 "10⭐demon"（星号不是月亮），那是
        difficulty_label() 自己的小瑕疵，这里只保证不再翻倍成 "11⭐10⭐demon"。
        """
        plat = _gd_level(level_id=1, level_name="X", stars=10, length=5, demon_difficulty=None)
        above_ten = _gd_level(
            level_id=1, level_name="X", stars=11, length=3, demon_difficulty=None
        )

        for level in (plat, above_ten):
            line = fullsearch.format_level_line(1, level)

            assert level.difficulty_label() in line
            assert line.count("⭐") == 1
            assert "🌙" not in line

    def test_below_ten_stars_never_gets_an_extra_prefix(self) -> None:
        """difficulty_label() 在 10 星以下已经把星数写进去了，这里一个字都不该加。

        比的是 difficulty_label() 的返回值 —— 要守的是「原样透传」，
        标签本身长什么样是 gdapi 的事。
        """
        for stars in range(gdapi.DEMON_STARS):
            level = _gd_level(level_id=1, level_name="X", stars=stars, length=3)
            label = level.difficulty_label()

            line = fullsearch.format_level_line(1, level)

            assert label in line
            assert line.count("⭐") == label.count("⭐")  # 一颗星都没多加

    def test_demon_labels_all_get_exactly_one_prefix(self) -> None:
        """有 demon_difficulty 的那一支返回纯文字（"Extreme Demon"），星数得这里补"""
        for demon_difficulty in (0, 3, 4, 5, 6):
            level = _gd_level(
                level_id=1,
                level_name="X",
                stars=10,
                length=3,
                is_demon=True,
                demon_difficulty=demon_difficulty,
            )
            label = level.difficulty_label()

            assert "⭐" not in label  # 前提：这一支不自带星数
            line = fullsearch.format_level_line(1, level)

            assert f"10⭐{label}" in line
            assert line.count("⭐") == 1

    def test_line_built_from_a_real_server_response(self) -> None:
        """用真的服务器 key:value 串解析出来的关卡也排得对"""
        level = GDLevel.from_server_response(
            "1:11097037:2:Sonic Wave:5:1:6:1497203:9:50:10:9000000:12:0:13:21:"
            "14:600000:15:3:17:1:18:10:19:14003:43:5:45:12000"
        )
        level.creator_name = "Cyclic"

        line = fullsearch.format_level_line(2, level)

        # 解析出来的字段一样样都得进这一行
        assert "Sonic Wave" in line
        assert "Cyclic" in line
        assert "11097037" in line
        assert f"10⭐{level.difficulty_label()}" in line


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

        assert (ok, bool(msg)) == (False, True)  # 翻不动，而且说明了原因
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

        assert (ok, bool(msg)) == (False, True)
        assert session.page == 0
        assert len(fake.calls) == 1
        # 探到底之后再翻就不请求了
        assert session.go_next()[0] is False
        assert len(fake.calls) == 1

    def test_prev_before_first_page_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeSearch({})
        monkeypatch.setattr(fullsearch, "search_levels_page", fake)
        session = self._session(fake)

        ok, msg = session.go_prev()

        assert (ok, bool(msg)) == (False, True)
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

    def _uncapped(self, total: int, *, page: int = 0) -> fullsearch.FullSearchSession:
        session = self._session(page=page)
        session.total = total
        session.total_is_capped = False
        return session

    def test_capped_total_hides_the_count(self) -> None:
        """total 是 9999 那种封顶值，不能显示出来骗人"""
        session = self._session()
        session.total = GD_TOTAL_CAP

        lines = session.render().splitlines()

        assert "bloodbath" in lines[0]                 # 搜的是什么词要写出来
        assert str(GD_TOTAL_CAP) not in lines[0]       # 封顶值一个字都不能露出来
        assert len(lines) == 1 + 2 + 1                 # 标题 + 两条结果 + 提示行
        assert "Bloodbath" in lines[1]
        assert "Nine Circles" in lines[2]

    def test_uncapped_total_shows_count_and_page_count(self) -> None:
        """总条数和总页数都要出现在标题里，总页数由 GD_PAGE_SIZE 推出来"""
        head = self._uncapped(25).render().splitlines()[0]

        assert "25" in head
        assert str(_pages_for(25, GD_PAGE_SIZE)) in head

    def test_hints_depend_on_where_the_page_is(self) -> None:
        """能往哪翻就只提示哪边 —— 四个位置的提示互不相同"""
        first = self._uncapped(25).render().splitlines()[-1]
        middle = self._uncapped(25, page=1).render().splitlines()[-1]
        last = self._uncapped(25, page=2).render().splitlines()[-1]
        only = self._uncapped(2).render().splitlines()[-1]  # 一页就装下了

        assert len({first, middle, last, only}) == 4

        # total 封顶时不知道哪一页是最后一页，只能一直给「下一页」，跟中间页一样
        capped = self._session(page=3)
        capped.total = GD_TOTAL_CAP
        assert capped.render().splitlines()[-1] == middle

    def test_filters_are_shown_in_the_header(self) -> None:
        session = self._session(
            query=fullsearch.FullSearchQuery(
                query="bloodbath", rated_only=False, diff=-2, demon_filter=5
            )
        )
        session.total = GD_TOTAL_CAP

        head = session.render().splitlines()[0]

        assert session.query.describe() in head

    def test_empty_page_message_includes_the_filters(self) -> None:
        """一条都没搜到时要说清楚是带着哪些筛选没搜到"""
        session = fullsearch.FullSearchSession(
            query=fullsearch.FullSearchQuery(query="bloodbath", diff=3)
        )

        text = session.render()

        assert len(text.splitlines()) == 1  # 没结果就不列表、不给翻页提示
        assert session.query.describe() in text

    def test_numbering_starts_at_one_on_every_page(self) -> None:
        """序号是每页从 1 开始的（选中时按当前页的序号找）"""
        session = self._session(page=2)
        session.total = GD_TOTAL_CAP
        lines = session.render().splitlines()
        levels = session.current_levels

        assert lines[1] == fullsearch.format_level_line(1, levels[0])
        assert lines[2] == fullsearch.format_level_line(2, levels[1])


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
        # 提示里要带上关键词和筛选条件，用户才知道是「哪一次搜索」没结果
        assert "bloodbath" in err
        assert fullsearch.parse_args("bloodbath -a -d 5").describe() in err

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

        # 响应整段被解析进会话：total、两条关卡、以及另一段里的作者名
        assert session.total == 5
        assert session.total_is_capped is False
        assert _pages_for(session.total, GD_PAGE_SIZE) == 1  # 5 条一页放得下
        levels = session.current_levels
        assert [lv.level_id for lv in levels] == [10565740, 11097037]
        assert [lv.level_name for lv in levels] == ["Bloodbath", "Sonic Wave"]
        assert [lv.creator_name for lv in levels] == ["Riot", "Cyclic"]

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
        assert "绝对搜不到的关卡" in err  # 提示里要回显搜的是什么

    def test_network_failure_is_swallowed_by_gdapi(self, stub_requests) -> None:
        """requests 抛异常时 gdapi 返回空页，start_session 走「没找到」分支"""
        import requests as _requests

        stub_requests.post(self.GD_URL, _requests.ConnectTimeout("boom"))

        session, err = fullsearch.start_session("bloodbath")

        assert session is None
        assert "bloodbath" in err  # 网络挂了也走「没找到」那条提示，不是异常
