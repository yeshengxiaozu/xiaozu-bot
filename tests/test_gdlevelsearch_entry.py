"""gdlevelsearch/__init__.py —— 插件的命令入口层。

这个文件之前一条测试都没有（428 条语句覆盖 24%，剩下的全是死角），
但里面并不是「没法测的 IO」：`search_by_name` / `_add_search_result` 是
对三个模块级数据源的纯聚合逻辑，把 `Gddl` / `Nlw` / `Platapi` 换掉就能跑；
命令 handler 也和别的插件一样能用 `run_handler` 直接调。

约定：
- 所有会出图的分支只把 `create_image_from_gdlevel` 换成「返回一张 1x1 图」，
  `send_result` 本体（BytesIO 存盘 + MessageSegment.image + bot.send）照真跑，
  断言的是真会发出去的那条消息里有没有 image 段。
- 模块级的 `search_cache` / `timeout_tasks` 等几个字典是进程全局的，
  每个用例前后都清干净（见 clean_sessions），否则用例之间会串。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest
from PIL import Image

from tests.conftest import DEFAULT_USER_ID, FakeBot
from tests.test_small_plugins import run_handler, sent_texts
from xiaozu_bot.plugins import gdlevelsearch
from xiaozu_bot.plugins.gdlevelsearch import SearchResult, icons
from xiaozu_bot.plugins.gdlevelsearch.gdapi import GDLevel, GDUser
from xiaozu_bot.plugins.gdlevelsearch.gddlapi import GDDLLevel
from xiaozu_bot.plugins.gdlevelsearch.nlwapi import Level as NlwLevel
from xiaozu_bot.plugins.gdlevelsearch.platapi import PlatInfo

# ==========================================================================
# 造数据
# ==========================================================================


def gddl_level(
    level_id: int,
    name: str,
    *,
    rating: Optional[float] = None,
    difficulty: str = "Extreme",
    length: int = 3,
) -> GDDLLevel:
    """真的 GDDLLevel —— 构造函数是硬取 jsondict[...]，字段一个都不能少"""
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
                "Length": length,  # 6 = plat，触发 is_pemon()
                "IsTwoPlayer": False,
                "Difficulty": difficulty,
                "Song": {"ID": -1, "Name": "Stereo Madness", "Author": "Foreverbound"},
            },
        }
    )


def nlw_level(
    level_id: Any, name: str, *, creator: Optional[str] = "Someone",
    tier: Optional[str] = "5", source: str = "NLW",
) -> NlwLevel:
    level = NlwLevel(
        {
            "name": name,
            "creator": creator,
            "length": None,
            "checkpoints": None,
            "id": level_id,
            "description": None,
            "video": None,
        }
    )
    level.tier = tier
    level.source = source
    return level


def plat_info(level_id: str, name: str, **over: Any) -> PlatInfo:
    row = {"id": level_id, "name": name, "creator": "PlatGuy", "tier": "9 - CRUEL"}
    row.update(over)
    return PlatInfo.from_dict(row)


def gd_level(**attrs: Any) -> GDLevel:
    level = GDLevel()
    for key, value in attrs.items():
        setattr(level, key, value)
    return level


class FakeGddl:
    """替掉模块级的 Gddl 门面。只实现 search_by_name / gdrandom 真会调的两个方法。"""

    def __init__(self, levels: Optional[list[GDDLLevel]] = None) -> None:
        self.levels = levels
        self.names: list[str] = []
        self.random_calls: list[tuple] = []
        self.random_result: Any = None

    def getlevelsbyname(self, name: str) -> Optional[list[GDDLLevel]]:
        self.names.append(name)
        return self.levels

    def getrandomlevelbytier(self, *args: Any) -> Any:
        self.random_calls.append(args)
        return self.random_result


class FakeNlw:
    def __init__(self, levels: Optional[list[NlwLevel]] = None) -> None:
        self.levels = levels or []
        self.names: list[str] = []

    def getlevelbyname(self, name: str) -> list[NlwLevel]:
        self.names.append(name)
        return self.levels


class FakePlatapi:
    def __init__(self, info: Optional[PlatInfo] = None) -> None:
        self.info = info
        self.names: list[str] = []

    def getlevelbyname(self, name: str) -> Optional[PlatInfo]:
        self.names.append(name)
        return self.info


class RecordingLogger:
    """收 logger.info/warning/exception 的文本，用来断言日志里那几处拼串"""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def _rec(self, level: str) -> Any:
        def _call(msg: Any, *a: Any, **k: Any) -> None:
            self.lines.append((level, str(msg)))

        return _call

    def __getattr__(self, name: str) -> Any:
        return self._rec(name)

    @property
    def infos(self) -> list[str]:
        return [m for lvl, m in self.lines if lvl == "info"]


# ==========================================================================
# fixture
# ==========================================================================
@pytest.fixture(autouse=True)
def clean_sessions() -> Any:
    """模块级会话字典是进程全局的，用例之间必须互不影响"""
    dicts = [
        gdlevelsearch.search_cache,
        gdlevelsearch.timeout_tasks,
        gdlevelsearch.fullsearch_sessions,
        gdlevelsearch.fullsearch_timeouts,
        gdlevelsearch.ratings_sessions,
        gdlevelsearch.ratings_timeouts,
    ]
    for d in dicts:
        d.clear()
    yield
    for task_map in (
        gdlevelsearch.timeout_tasks,
        gdlevelsearch.fullsearch_timeouts,
        gdlevelsearch.ratings_timeouts,
    ):
        for task in list(task_map.values()):
            task.cancel()
    for d in dicts:
        d.clear()


@pytest.fixture(autouse=True)
def no_timeout_tasks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把三个「N 秒后清缓存」的协程换成立刻返回。

    真的 create_task 一个 sleep(30) 出来，用例结束时事件循环一关就会刷
    "Task was destroyed but it is pending"，而且白等。
    handler 是按模块全局名去找它们的，所以补模块属性就够。
    """
    armed: list[str] = []

    async def _noop_search(bot: Any, event: Any, user_id: str) -> None:
        armed.append(f"search:{user_id}")

    async def _noop_full(bot: Any, event: Any, session_id: str) -> None:
        armed.append(f"full:{session_id}")

    async def _noop_ratings(bot: Any, event: Any, session_id: str) -> None:
        armed.append(f"ratings:{session_id}")

    monkeypatch.setattr(gdlevelsearch, "clear_search_cache", _noop_search)
    monkeypatch.setattr(gdlevelsearch, "clear_fullsearch", _noop_full)
    monkeypatch.setattr(gdlevelsearch, "clear_ratings", _noop_ratings)
    return armed


@pytest.fixture
def stub_image(monkeypatch: pytest.MonkeyPatch) -> list[GDLevel]:
    """出图那一步换成返回一张 1x1 图，send_result 本体照真跑"""
    drawn: list[GDLevel] = []

    async def _fake(level: GDLevel, *a: Any, **k: Any) -> Image.Image:
        drawn.append(level)
        return Image.new("RGB", (1, 1), (7, 8, 9))

    monkeypatch.setattr(gdlevelsearch, "create_image_from_gdlevel", _fake)
    return drawn


def image_segments(bot: FakeBot) -> list[Any]:
    """FakeBot 发出去的所有 image 段"""
    out = []
    for api, data in bot.calls:
        if api != "send_msg":
            continue
        out.extend([seg for seg in data["message"] if seg.type == "image"])
    return out


# ==========================================================================
# _add_search_result
# ==========================================================================
class TestAddSearchResult:
    def test_new_id_inserts_every_field(self) -> None:
        results: dict[int, SearchResult] = {}
        gdlevelsearch._add_search_result(results, 7, "Name", "Cr", "t9", "Extreme Demon")

        assert list(results) == [7]
        item = results[7]
        assert (item.id, item.name, item.creator, item.tier, item.difficulty) == (
            7, "Name", "Cr", "t9", "Extreme Demon",
        )

    def test_second_hit_fills_in_a_missing_creator(self) -> None:
        """同一个 id 第二次进来，只补第一次缺的字段"""
        results: dict[int, SearchResult] = {}
        gdlevelsearch._add_search_result(results, 7, "Name", None, "t9")
        gdlevelsearch._add_search_result(results, 7, "Other", "Cr", None)

        assert results[7].creator == "Cr"
        assert results[7].tier == "t9"

    def test_second_hit_fills_in_a_missing_tier(self) -> None:
        results: dict[int, SearchResult] = {}
        gdlevelsearch._add_search_result(results, 7, "Name", "Cr", None)
        gdlevelsearch._add_search_result(results, 7, "Name", None, "t12")

        assert results[7].tier == "t12"

    def test_first_creator_and_name_win(self) -> None:
        """已经有值就不覆盖，名字更是压根不看第二次的"""
        results: dict[int, SearchResult] = {}
        gdlevelsearch._add_search_result(results, 7, "First", "A", "t1")
        gdlevelsearch._add_search_result(results, 7, "Second", "B", "t2")

        assert results[7].name == "First"
        assert results[7].creator == "A"
        assert results[7].tier == "t1"

    def test_second_hit_fills_in_a_missing_difficulty(self) -> None:
        """合并分支要和 creator / tier 一样把空着的 difficulty 补上

        不补的话，先命中不带 difficulty 的源、后命中带的源的关卡会一直是 None，
        *gdsearch 的列表里就得回头再打一次 gdapi。
        """
        results: dict[int, SearchResult] = {}
        gdlevelsearch._add_search_result(results, 7, "Name", None, None, None)
        gdlevelsearch._add_search_result(results, 7, "Name", None, None, "Extreme Demon")

        assert results[7].difficulty == "Extreme Demon"

    def test_first_difficulty_wins(self) -> None:
        """已经有值就不许后来的源覆盖，和 creator / tier 一个规矩"""
        results: dict[int, SearchResult] = {}
        gdlevelsearch._add_search_result(results, 7, "Name", None, None, "Extreme Demon")
        gdlevelsearch._add_search_result(results, 7, "Name", None, None, "Easy Demon")

        assert results[7].difficulty == "Extreme Demon"

    def test_second_hit_without_difficulty_does_not_blank_it(self) -> None:
        """补的条件是 `and difficulty`，后来的 None 不该把已有值抹掉"""
        results: dict[int, SearchResult] = {}
        gdlevelsearch._add_search_result(results, 7, "Name", None, None, "Extreme Demon")
        gdlevelsearch._add_search_result(results, 7, "Name", "Cr", None, None)

        assert results[7].difficulty == "Extreme Demon"
        assert results[7].creator == "Cr"

    def test_empty_string_difficulty_counts_as_missing(self) -> None:
        """判的是 `not item.difficulty`，空串和 None 一样会被后来的值补上"""
        results: dict[int, SearchResult] = {}
        gdlevelsearch._add_search_result(results, 7, "Name", None, None, "")
        gdlevelsearch._add_search_result(results, 7, "Name", None, None, "Hard Demon")

        assert results[7].difficulty == "Hard Demon"

    def test_empty_string_creator_counts_as_missing(self) -> None:
        """判的是 `not item.creator`，空串会被后来的值补上"""
        results: dict[int, SearchResult] = {}
        gdlevelsearch._add_search_result(results, 7, "Name", "", None)
        gdlevelsearch._add_search_result(results, 7, "Name", "Real", None)

        assert results[7].creator == "Real"

    def test_none_id_is_ignored(self) -> None:
        results: dict[int, SearchResult] = {}
        gdlevelsearch._add_search_result(results, None, "Name")  # type: ignore[arg-type]
        assert results == {}

    def test_id_zero_is_inserted(self) -> None:
        """守卫写的是 `is None` 而不是 falsy，所以 id=0 会被收进来

        NLW 那一路是 `int(level.id or 0)`，没有 id 的行会全部挤进 0 这个槽。
        """
        results: dict[int, SearchResult] = {}
        gdlevelsearch._add_search_result(results, 0, "NoId")
        assert list(results) == [0]


# ==========================================================================
# search_by_name
# ==========================================================================
@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> Any:
    """一次把三个数据源门面都换掉，返回它们方便断言"""

    def _install(
        gddl: Optional[list[GDDLLevel]] = None,
        nlw: Optional[list[NlwLevel]] = None,
        plat: Optional[PlatInfo] = None,
    ) -> tuple[FakeGddl, FakeNlw, FakePlatapi]:
        fake_gddl = FakeGddl(gddl)
        fake_nlw = FakeNlw(nlw)
        fake_plat = FakePlatapi(plat)
        monkeypatch.setattr(gdlevelsearch, "Gddl", fake_gddl)
        monkeypatch.setattr(gdlevelsearch, "Nlw", fake_nlw)
        monkeypatch.setattr(gdlevelsearch, "Platapi", fake_plat)
        return fake_gddl, fake_nlw, fake_plat

    return _install


class TestSearchByName:
    def test_nothing_anywhere_gives_an_empty_list(self, sources: Any) -> None:
        sources()
        assert gdlevelsearch.search_by_name("Nope") == []

    def test_gddl_none_is_tolerated(self, sources: Any) -> None:
        """`Gddl.getlevelsbyname(name) or []` —— 接口返回 None 不该炸"""
        sources(gddl=None)
        assert gdlevelsearch.search_by_name("Nope") == []

    def test_gddl_exact_match_only(self, sources: Any) -> None:
        """GDDL 是模糊搜索，这里只留名字完全对得上的（去空格 + 忽略大小写）"""
        sources(
            gddl=[
                gddl_level(1, "Bloodbath"),
                gddl_level(2, "Bloodbath II"),
                gddl_level(3, "  bLoOdBaTh  "),
            ]
        )
        got = gdlevelsearch.search_by_name("  BLOODBATH ")
        assert sorted(r.id for r in got) == [1, 3]

    def test_gddl_name_keeps_the_original_spelling(self, sources: Any) -> None:
        """比对用小写，存下来的还是接口给的原名"""
        sources(gddl=[gddl_level(1, "BloodBath")])
        assert gdlevelsearch.search_by_name("bloodbath")[0].name == "BloodBath"

    def test_rating_becomes_the_tier_string_rounded_to_two_places(
        self, sources: Any
    ) -> None:
        sources(gddl=[gddl_level(1, "X", rating=19.456)])
        assert gdlevelsearch.search_by_name("X")[0].tier == "19.46"

    def test_rating_none_leaves_tier_none(self, sources: Any) -> None:
        sources(gddl=[gddl_level(1, "X", rating=None)])
        assert gdlevelsearch.search_by_name("X")[0].tier is None

    def test_rating_zero_also_leaves_tier_none(self, sources: Any) -> None:
        """⚠️ 判的是 `if level.Rating`，0 是 falsy，tier 变 None 而不是 "0"

        tier 的合法范围是 1-39，所以实际上碰不到；但改成 0 有意义的字段时会踩。
        """
        sources(gddl=[gddl_level(1, "X", rating=0)])
        assert gdlevelsearch.search_by_name("X")[0].tier is None

    def test_classic_difficulty_gets_a_demon_suffix(self, sources: Any) -> None:
        sources(gddl=[gddl_level(1, "X", difficulty="Insane", length=3)])
        assert gdlevelsearch.search_by_name("X")[0].difficulty == "Insane Demon"

    def test_plat_length_gets_a_pemon_suffix(self, sources: Any) -> None:
        """Length == 6 是 platformer，后缀改成 Pemon"""
        sources(gddl=[gddl_level(1, "X", difficulty="Extreme", length=6)])
        assert gdlevelsearch.search_by_name("X")[0].difficulty == "Extreme Pemon"

    def test_gddl_entry_without_meta_is_skipped(self, sources: Any) -> None:
        """`if not level or not getattr(level, "Meta", None): continue`"""
        broken = gddl_level(1, "X")
        broken.Meta = None  # type: ignore[assignment]
        sources(gddl=[broken, None])  # type: ignore[list-item]
        assert gdlevelsearch.search_by_name("X") == []

    def test_nlw_results_are_included(self, sources: Any) -> None:
        sources(nlw=[nlw_level("42", "Sonic Wave", creator="Cyclic")])
        got = gdlevelsearch.search_by_name("Sonic Wave")
        assert len(got) == 1
        assert (got[0].id, got[0].name, got[0].creator) == (42, "Sonic Wave", "Cyclic")

    def test_nlw_tier_is_deliberately_not_recorded(self, sources: Any) -> None:
        """NLW 那一路给 _add_search_result 传的 tier 是写死的 None"""
        sources(nlw=[nlw_level("42", "Sonic Wave", tier="Extreme")])
        assert gdlevelsearch.search_by_name("Sonic Wave")[0].tier is None

    def test_nlw_missing_id_collapses_into_zero(self, sources: Any) -> None:
        """`int(level.id or 0)` —— 没 id 的行全部落进 id=0，互相顶掉"""
        sources(
            nlw=[
                nlw_level(None, "A", creator="First"),
                nlw_level(None, "B", creator="Second"),
            ]
        )
        got = gdlevelsearch.search_by_name("whatever")
        assert [(r.id, r.name, r.creator) for r in got] == [(0, "A", "First")]

    def test_plat_result_is_included_with_its_tier(self, sources: Any) -> None:
        sources(plat=plat_info("999", "Platty"))
        got = gdlevelsearch.search_by_name("Platty")
        assert (got[0].id, got[0].name, got[0].creator, got[0].tier) == (
            999, "Platty", "PlatGuy", "9 - CRUEL",
        )

    def test_same_level_from_three_sources_is_merged_once(self, sources: Any) -> None:
        """去重的键是 id：GDDL 给难度和 tier，NLW 补作者，plat 不再覆盖"""
        sources(
            gddl=[gddl_level(500, "Tidal Wave", rating=35.0, difficulty="Extreme")],
            nlw=[nlw_level("500", "Tidal Wave", creator="OniLink")],
            plat=plat_info("500", "Tidal Wave", creator="SomeoneElse", tier="1 - BEGINNER"),
        )
        got = gdlevelsearch.search_by_name("Tidal Wave")

        assert len(got) == 1
        assert got[0].id == 500
        assert got[0].creator == "OniLink"       # NLW 先到，补上了空着的 creator
        assert got[0].tier == "35.0"             # GDDL 的 Rating 先占住了 tier
        assert got[0].difficulty == "Extreme Demon"

    def test_every_source_gets_the_raw_name(self, sources: Any) -> None:
        """三个源拿到的都是原始入参，不是 normalized 之后的"""
        gddl, nlw, plat = sources()
        gdlevelsearch.search_by_name("  MiXeD  ")
        assert gddl.names == ["  MiXeD  "]
        assert nlw.names == ["  MiXeD  "]
        assert plat.names == ["  MiXeD  "]

    def test_result_order_follows_insertion(self, sources: Any) -> None:
        """返回的是 dict.values()，Python 3.7+ 保证是插入序：GDDL -> NLW -> plat"""
        sources(
            gddl=[gddl_level(1, "N")],
            nlw=[nlw_level("2", "N")],
            plat=plat_info("3", "N"),
        )
        assert [r.id for r in gdlevelsearch.search_by_name("N")] == [1, 2, 3]

    def test_nlw_log_line_says_unknown_tier_when_there_is_no_tier(
        self, sources: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        r"""tier 缺失时日志要落到 "Unknown Tier"，不能印字面量 "None"

        原来写的是

            logger.info(f"Find a result in {level.source}: " +
                        str(level.tier) or "Unknown" + " Tier")

        `+` 比 `or` 紧，实际是 `(f"..." + str(tier)) or ("Unknown" + " Tier")`，
        左边永远非空，or 那一支根本走不到。
        """
        rec = RecordingLogger()
        monkeypatch.setattr(gdlevelsearch, "logger", rec)
        sources(nlw=[nlw_level("42", "X", tier=None, source="IDS")])

        gdlevelsearch.search_by_name("X")

        assert "Find a result in IDS: Unknown Tier" in rec.infos
        assert not any("None" in line for line in rec.infos)

    def test_nlw_log_line_keeps_the_tier_when_there_is_one(
        self, sources: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """有 tier 就照印，后缀 " Tier" 这下也真的跟上了"""
        rec = RecordingLogger()
        monkeypatch.setattr(gdlevelsearch, "logger", rec)
        sources(nlw=[nlw_level("42", "X", tier="7", source="NLW")])

        gdlevelsearch.search_by_name("X")

        assert "Find a result in NLW: 7 Tier" in rec.infos

    def test_nlw_log_line_treats_an_empty_tier_as_unknown(
        self, sources: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空串也算没有 tier —— `or` 判的是 falsy"""
        rec = RecordingLogger()
        monkeypatch.setattr(gdlevelsearch, "logger", rec)
        sources(nlw=[nlw_level("42", "X", tier="", source="LW")])

        gdlevelsearch.search_by_name("X")

        assert "Find a result in LW: Unknown Tier" in rec.infos


# ==========================================================================
# reload_all
# ==========================================================================
class TestReloadAll:
    def test_reloads_all_three_sources_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        order: list[str] = []
        for name, module in (
            ("nlw", gdlevelsearch.nlwapi),
            ("plat", gdlevelsearch.platapi),
            ("aredl", gdlevelsearch.aredlapi),
        ):
            monkeypatch.setattr(
                module, "reload", (lambda n: lambda: order.append(n))(name)
            )

        gdlevelsearch.reload_all()
        assert order == ["nlw", "plat", "aredl"]

    def test_one_broken_source_does_not_stop_the_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """单个源挂了要吞掉继续 —— 内存里留着旧数据也比整张表清空强"""
        order: list[str] = []

        def boom() -> None:
            order.append("nlw-boom")
            raise RuntimeError("缓存文件坏了")

        monkeypatch.setattr(gdlevelsearch.nlwapi, "reload", boom)
        monkeypatch.setattr(
            gdlevelsearch.platapi, "reload", lambda: order.append("plat")
        )
        monkeypatch.setattr(
            gdlevelsearch.aredlapi, "reload", lambda: order.append("aredl")
        )

        gdlevelsearch.reload_all()  # 不该往外抛
        assert order == ["nlw-boom", "plat", "aredl"]

    def test_all_three_broken_still_returns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for module in (
            gdlevelsearch.nlwapi, gdlevelsearch.platapi, gdlevelsearch.aredlapi,
        ):
            monkeypatch.setattr(
                module, "reload", lambda: (_ for _ in ()).throw(OSError("no file"))
            )
        assert gdlevelsearch.reload_all() is None


# ==========================================================================
# get_creator / get_difficulty / getlevelinfo
# ==========================================================================
class TestGetCreator:
    URL = "https://history.geometrydash.eu/api/v1/level/128"

    def test_pulls_cache_username_off_the_history_api(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get(self.URL, make_response(json_data={"cache_username": "RobTop"}))
        assert gdlevelsearch.get_creator(128) == "RobTop"
        assert stub_requests.calls[0]["timeout"] == 10

    def test_missing_key_returns_none(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        """KeyError 也被那个裸 except 吞掉，调用方只看到 None"""
        stub_requests.get(self.URL, make_response(json_data={"other": 1}))
        assert gdlevelsearch.get_creator(128) is None

    def test_network_error_returns_none(self, stub_requests: Any) -> None:
        stub_requests.get(self.URL, TimeoutError("timed out"))
        assert gdlevelsearch.get_creator(128) is None

    def test_logs_a_warning_because_it_should_not_be_called(
        self, stub_requests: Any, make_response: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """这是兜底函数，被调到本身就说明上游出问题了，所以是 warning"""
        rec = RecordingLogger()
        monkeypatch.setattr(gdlevelsearch, "logger", rec)
        stub_requests.get(self.URL, make_response(json_data={"cache_username": "R"}))

        gdlevelsearch.get_creator(128)
        assert ("warning", "get_creator got called: 128") in rec.lines


class TestGetDifficulty:
    def test_returns_the_label(self, monkeypatch: pytest.MonkeyPatch) -> None:
        level = gd_level(stars=10, demon_difficulty=3)
        monkeypatch.setattr(gdlevelsearch, "get_level_by_id", lambda _id: level)
        assert gdlevelsearch.get_difficulty(128) == level.difficulty_label()

    def test_none_level_gives_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gdlevelsearch, "get_level_by_id", lambda _id: None)
        assert gdlevelsearch.get_difficulty(128) is None

    def test_exception_gives_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_id: int) -> Any:
            raise RuntimeError("GD 服务器又挂了")

        monkeypatch.setattr(gdlevelsearch, "get_level_by_id", boom)
        assert gdlevelsearch.get_difficulty(128) is None


class TestGetLevelInfo:
    def test_passes_the_level_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        level = gd_level(name="X")
        monkeypatch.setattr(gdlevelsearch, "get_level_by_id", lambda _id: level)
        assert gdlevelsearch.getlevelinfo(1) is level

    def test_none_stays_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gdlevelsearch, "get_level_by_id", lambda _id: None)
        assert gdlevelsearch.getlevelinfo(1) is None

    def test_exception_is_not_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """和 get_difficulty 不一样，这里没有 try，异常直接往上抛"""

        def boom(_id: int) -> Any:
            raise RuntimeError("boom")

        monkeypatch.setattr(gdlevelsearch, "get_level_by_id", boom)
        with pytest.raises(RuntimeError):
            gdlevelsearch.getlevelinfo(1)


# ==========================================================================
# 会话字典与几个 Rule
# ==========================================================================
class TestSessionBookkeeping:
    def test_has_cache_looks_at_user_id(self, make_group_event: Any) -> None:
        event = make_group_event("随便")
        assert gdlevelsearch.has_cache(event) is False
        gdlevelsearch.search_cache[str(DEFAULT_USER_ID)] = []
        assert gdlevelsearch.has_cache(event) is True

    def test_has_fullsearch_and_has_ratings_look_at_session_id(
        self, make_group_event: Any
    ) -> None:
        event = make_group_event("随便")
        session_id = event.get_session_id()
        assert gdlevelsearch.has_fullsearch(event) is False
        assert gdlevelsearch.has_ratings(event) is False

        gdlevelsearch.fullsearch_sessions[session_id] = object()  # type: ignore[assignment]
        gdlevelsearch.ratings_sessions[session_id] = object()  # type: ignore[assignment]
        assert gdlevelsearch.has_fullsearch(event) is True
        assert gdlevelsearch.has_ratings(event) is True

    async def test_drop_fullsearch_cancels_the_timeout_task(self) -> None:
        async def forever() -> None:
            await asyncio.sleep(3600)

        task = asyncio.create_task(forever())
        gdlevelsearch.fullsearch_sessions["s"] = object()  # type: ignore[assignment]
        gdlevelsearch.fullsearch_timeouts["s"] = task

        gdlevelsearch._drop_fullsearch("s")
        await asyncio.sleep(0)

        assert "s" not in gdlevelsearch.fullsearch_sessions
        assert "s" not in gdlevelsearch.fullsearch_timeouts
        assert task.cancelled()

    def test_drop_on_an_unknown_session_is_a_no_op(self) -> None:
        gdlevelsearch._drop_fullsearch("没这个会话")
        gdlevelsearch._drop_ratings("没这个会话")

    async def test_clear_all_sessions_wipes_all_three(
        self, make_group_event: Any
    ) -> None:
        """三个选择器同时只能活一个，开新的之前把旧的全清掉"""
        event = make_group_event("随便")
        session_id = event.get_session_id()
        user_id = str(DEFAULT_USER_ID)

        async def forever() -> None:
            await asyncio.sleep(3600)

        tasks = [asyncio.create_task(forever()) for _ in range(3)]
        gdlevelsearch.fullsearch_sessions[session_id] = object()  # type: ignore[assignment]
        gdlevelsearch.fullsearch_timeouts[session_id] = tasks[0]
        gdlevelsearch.ratings_sessions[session_id] = object()  # type: ignore[assignment]
        gdlevelsearch.ratings_timeouts[session_id] = tasks[1]
        gdlevelsearch.search_cache[user_id] = []
        gdlevelsearch.timeout_tasks[user_id] = tasks[2]

        gdlevelsearch._clear_all_sessions(event)
        await asyncio.sleep(0)

        assert gdlevelsearch.fullsearch_sessions == {}
        assert gdlevelsearch.ratings_sessions == {}
        assert gdlevelsearch.search_cache == {}
        assert all(t.cancelled() for t in tasks)


# ==========================================================================
# send_result
# ==========================================================================
class TestSendResult:
    async def test_sends_a_png_image_segment(
        self, fake_bot: FakeBot, make_group_event: Any, stub_image: list[GDLevel]
    ) -> None:
        event = make_group_event("随便")
        level = gd_level(name="X")

        await gdlevelsearch.send_result(fake_bot, event, level)

        assert stub_image == [level]
        segs = image_segments(fake_bot)
        assert len(segs) == 1
        assert segs[0].data["file"].startswith("base64://")


# ==========================================================================
# *gdsearch
# ==========================================================================
class TestHandleGdsearch:
    async def test_empty_argument_asks_for_one(
        self, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        ok = await run_handler(
            gdlevelsearch.gdsearch, fake_bot, make_group_event("*gdsearch"), arg=""
        )
        assert ok is True
        assert sent_texts(fake_bot) == ["请提供关卡的名字或id"]

    @pytest.mark.parametrize("text", ["128", "1234", "12a45", "abcde"])
    async def test_short_or_non_numeric_input_goes_down_the_name_path(
        self, text: str, fake_bot: FakeBot, make_group_event: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """走 id 分支的条件是 `len(name) > 4 and name.isdigit()`

        4 位数字（GD 早期关卡确实有）会被当成名字去搜，这是当前真实行为。
        """
        seen: list[str] = []
        monkeypatch.setattr(
            gdlevelsearch, "search_by_name", lambda n: seen.append(n) or []
        )
        await run_handler(
            gdlevelsearch.gdsearch, fake_bot, make_group_event("*gdsearch"), arg=text
        )
        assert seen == [text]

    async def test_five_digit_input_goes_down_the_id_path(
        self, fake_bot: FakeBot, make_group_event: Any,
        monkeypatch: pytest.MonkeyPatch, stub_image: list[GDLevel],
    ) -> None:
        level = gd_level(name="By ID")
        seen: list[int] = []
        monkeypatch.setattr(
            gdlevelsearch, "getlevelinfo", lambda i: (seen.append(i), level)[1]
        )
        monkeypatch.setattr(
            gdlevelsearch, "search_by_name",
            lambda n: pytest.fail("id 分支不该再去搜名字"),
        )

        await run_handler(
            gdlevelsearch.gdsearch, fake_bot, make_group_event("*gdsearch"), arg="12345"
        )
        assert seen == [12345]
        assert len(image_segments(fake_bot)) == 1

    async def test_unknown_id_says_so(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gdlevelsearch, "getlevelinfo", lambda _i: None)
        await run_handler(
            gdlevelsearch.gdsearch, fake_bot, make_group_event("*gdsearch"), arg="12345"
        )
        assert sent_texts(fake_bot) == ["不存在符合这个id的demon关卡"]

    async def test_no_name_match_says_so(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gdlevelsearch, "search_by_name", lambda _n: [])
        await run_handler(
            gdlevelsearch.gdsearch, fake_bot, make_group_event("*gdsearch"), arg="Nope"
        )
        assert sent_texts(fake_bot) == ["没有找到名为 'Nope' 的demon关卡"]

    async def test_single_match_is_sent_straight_away(
        self, fake_bot: FakeBot, make_group_event: Any,
        monkeypatch: pytest.MonkeyPatch, stub_image: list[GDLevel],
    ) -> None:
        """只有一个结果就不再让人选一次，也不建缓存"""
        monkeypatch.setattr(
            gdlevelsearch, "search_by_name", lambda _n: [SearchResult(9, "Only")]
        )
        monkeypatch.setattr(gdlevelsearch, "getlevelinfo", lambda _i: gd_level(name="Only"))

        await run_handler(
            gdlevelsearch.gdsearch, fake_bot, make_group_event("*gdsearch"), arg="Only"
        )
        assert len(image_segments(fake_bot)) == 1
        assert gdlevelsearch.search_cache == {}

    async def test_multiple_matches_render_a_numbered_list(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """列表里每一段（作者 / 难度 / tier）都是按有没有值决定加不加的"""
        monkeypatch.setattr(
            gdlevelsearch, "search_by_name",
            lambda _n: [
                SearchResult(1, "Full", "Cr", "35.0", "Extreme Demon"),
                SearchResult(2, "NoCreator", None, "12.5", "Insane Demon"),
                SearchResult(3, "NoTier", "Cr2", None, "Hard Demon"),
            ],
        )
        monkeypatch.setattr(
            gdlevelsearch, "get_difficulty", lambda _i: pytest.fail("有 difficulty 就不该再打 gdapi")
        )

        await run_handler(
            gdlevelsearch.gdsearch, fake_bot, make_group_event("*gdsearch"), arg="X"
        )

        assert sent_texts(fake_bot) == [
            "找到 3 个名为 'X' 的demon关卡："
            "\n1. Full by Cr (Extreme Demon) t35.0 (ID: 1)"
            "\n2. NoCreator (Insane Demon) t12.5 (ID: 2)"
            "\n3. NoTier by Cr2 (Hard Demon) (ID: 3)"
            "\n输入序号以选中关卡,输入“结束”以中止搜索"
        ]

    async def test_missing_difficulty_falls_back_to_gdapi(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """difficulty 是 None 的条目才会挨个去打 gdapi 补"""
        asked: list[int] = []
        monkeypatch.setattr(
            gdlevelsearch, "search_by_name",
            lambda _n: [SearchResult(1, "A"), SearchResult(2, "B", difficulty="已知")],
        )
        monkeypatch.setattr(
            gdlevelsearch, "get_difficulty",
            lambda i: (asked.append(i), "现查的")[1],
        )

        await run_handler(
            gdlevelsearch.gdsearch, fake_bot, make_group_event("*gdsearch"), arg="X"
        )

        assert asked == [1]
        assert "1. A (现查的) (ID: 1)" in sent_texts(fake_bot)[0]
        assert "2. B (已知) (ID: 2)" in sent_texts(fake_bot)[0]

    async def test_multiple_matches_arm_the_cache_and_timeout(
        self, fake_bot: FakeBot, make_group_event: Any,
        monkeypatch: pytest.MonkeyPatch, no_timeout_tasks: list[str],
    ) -> None:
        results = [SearchResult(1, "A", difficulty="d"), SearchResult(2, "B", difficulty="d")]
        monkeypatch.setattr(gdlevelsearch, "search_by_name", lambda _n: results)

        await run_handler(
            gdlevelsearch.gdsearch, fake_bot, make_group_event("*gdsearch"), arg="X"
        )
        await asyncio.sleep(0)

        user_id = str(DEFAULT_USER_ID)
        assert gdlevelsearch.search_cache[user_id] is results
        assert user_id in gdlevelsearch.timeout_tasks
        assert no_timeout_tasks == [f"search:{user_id}"]


# ==========================================================================
# gdsearch 的序号选择器
# ==========================================================================
class TestHandleChoice:
    def _prime(self, results: Optional[list[SearchResult]] = None) -> list[SearchResult]:
        results = results or [
            SearchResult(11, "First"), SearchResult(22, "Second"),
        ]
        gdlevelsearch.search_cache[str(DEFAULT_USER_ID)] = results
        return results

    async def test_no_cache_means_silence(
        self, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        ok = await run_handler(
            gdlevelsearch.gdsearchselect, fake_bot, make_group_event("1")
        )
        assert ok is True
        assert fake_bot.calls == []

    @pytest.mark.parametrize("word", ["结束", "取消", "我要结束了"])
    async def test_stop_words_cancel(
        self, word: str, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        """判的是 `in`，所以整句里带「结束」两个字就算"""
        self._prime()
        await run_handler(gdlevelsearch.gdsearchselect, fake_bot, make_group_event(word))

        assert sent_texts(fake_bot) == ["已取消搜索"]
        assert gdlevelsearch.search_cache == {}

    async def test_non_numeric_input_is_ignored_silently(
        self, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        """不是序号就当没看见，别把群聊别的消息吞了"""
        self._prime()
        await run_handler(
            gdlevelsearch.gdsearchselect, fake_bot, make_group_event("今天天气不错")
        )
        assert fake_bot.calls == []
        assert str(DEFAULT_USER_ID) in gdlevelsearch.search_cache  # 缓存还在

    @pytest.mark.parametrize("choice", ["0", "3", "99"])
    async def test_out_of_range_index_complains(
        self, choice: str, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        self._prime()
        await run_handler(
            gdlevelsearch.gdsearchselect, fake_bot, make_group_event(choice)
        )
        assert sent_texts(fake_bot) == ["请输入正确的序号"]
        assert str(DEFAULT_USER_ID) in gdlevelsearch.search_cache  # 没清掉，可以再选

    async def test_valid_index_sends_the_level_and_clears_the_cache(
        self, fake_bot: FakeBot, make_group_event: Any,
        monkeypatch: pytest.MonkeyPatch, stub_image: list[GDLevel],
    ) -> None:
        self._prime()
        asked: list[int] = []
        monkeypatch.setattr(
            gdlevelsearch, "getlevelinfo",
            lambda i: (asked.append(i), gd_level(name="Second"))[1],
        )

        await run_handler(gdlevelsearch.gdsearchselect, fake_bot, make_group_event("2"))

        assert asked == [22]
        assert len(image_segments(fake_bot)) == 1
        assert gdlevelsearch.search_cache == {}

    async def test_level_lookup_failure_reports_the_id(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._prime()
        monkeypatch.setattr(gdlevelsearch, "getlevelinfo", lambda _i: None)

        await run_handler(gdlevelsearch.gdsearchselect, fake_bot, make_group_event("1"))
        assert sent_texts(fake_bot) == ["发生未知错误。相关id: 11"]


# ==========================================================================
# *gduser
# ==========================================================================
def make_gduser(**over: Any) -> GDUser:
    user = GDUser()
    user.user_name = "Player"
    user.stars = 1000
    user.moons = 50
    user.demons_count = 30
    user.creator_points = 0
    user.classic_levels = None
    user.platformer_levels = None
    user.demons_breakdown = None
    for key, value in over.items():
        setattr(user, key, value)
    return user


class TestHandleGduser:
    async def test_empty_name_asks_for_one(
        self, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        await run_handler(
            gdlevelsearch.gduser, fake_bot, make_group_event("*gduser"), arg=""
        )
        assert sent_texts(fake_bot) == ["请输入想要搜索的用户名"]

    async def test_unknown_user(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gdlevelsearch, "get_user_by_name", lambda _n: None)
        await run_handler(
            gdlevelsearch.gduser, fake_bot, make_group_event("*gduser"), arg="Nobody"
        )
        assert sent_texts(fake_bot) == ["没有找到对应的用户"]

    async def test_minimal_user_only_gets_the_basic_line(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """三段可选信息都为空时只剩第一行，而且 creator_points=0 那截也不印"""
        monkeypatch.setattr(gdlevelsearch, "get_user_by_name", lambda _n: make_gduser())
        await run_handler(
            gdlevelsearch.gduser, fake_bot, make_group_event("*gduser"), arg="Player"
        )
        assert sent_texts(fake_bot) == ["Player\n1000⭐ 50🌙 30👿"]

    async def test_creator_points_are_appended_when_non_zero(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            gdlevelsearch, "get_user_by_name", lambda _n: make_gduser(creator_points=7)
        )
        await run_handler(
            gdlevelsearch.gduser, fake_bot, make_group_event("*gduser"), arg="Player"
        )
        assert sent_texts(fake_bot)[0].endswith("30👿 7🔧")

    async def test_full_breakdown(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = make_gduser(
            classic_levels=[1, 2, 3, 4, 5, 6, 7, 8],
            platformer_levels=[9, 10, 11, 12, 13, 14],
            demons_breakdown=list(range(20, 32)),
        )
        monkeypatch.setattr(gdlevelsearch, "get_user_by_name", lambda _n: user)

        await run_handler(
            gdlevelsearch.gduser, fake_bot, make_group_event("*gduser"), arg="Player"
        )

        text = sent_texts(fake_bot)[0]
        assert "Classic: 1🤖 2💙 3💚 4💛 5🧡 6💜;" in text
        assert "7 Daily; 8 Gauntlet" in text
        assert "Platformer: 9🤖 10💙 11💚 12💛 13🧡 14💜" in text
        assert "Classic Demons: 20 / 21 / 22 / 23 / 24;" in text
        assert "30 Weekly; 31 Gauntlet" in text
        assert "Platformer Demons: 25 / 26 / 27 / 28 / 29" in text


# ==========================================================================
# *gdrandom 的参数校验
# ==========================================================================
class TestHandleGdrandom:
    async def test_no_arguments_prints_usage(
        self, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        await run_handler(
            gdlevelsearch.gdrandom, fake_bot, make_group_event("*gd随机推关"), arg=""
        )
        assert sent_texts(fake_bot)[0].startswith("用法：*gd随机推关")

    @pytest.mark.parametrize(
        ("arg", "expected"),
        [
            ("abc", "tier 要是个数字，你写的是「abc」"),
            ("0", "tier 要在 1-39 之间，你写的是 0"),
            ("40", "tier 要在 1-39 之间，你写的是 40"),
            ("5 x", "tier 要是个数字，你写的是「x」"),
            ("5 10 11", "enjoyment 要在 0-10 之间，你写的是 11"),
            ("5 10 3 -1", "enjoyment 要在 0-10 之间，你写的是 -1"),
        ],
    )
    async def test_bad_arguments_are_rejected(
        self, arg: str, expected: str, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        await run_handler(
            gdlevelsearch.gdrandom, fake_bot, make_group_event("*gd随机推关"), arg=arg
        )
        assert sent_texts(fake_bot) == [expected]

    async def test_swapped_bounds_are_put_back_in_order(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """写反了也认：tier 高低、enjoyment 高低都会自动交换"""
        fake = FakeGddl()
        monkeypatch.setattr(gdlevelsearch, "Gddl", fake)

        await run_handler(
            gdlevelsearch.gdrandom, fake_bot, make_group_event("*gd随机推关"),
            arg="20 15 8 3",
        )
        assert fake.random_calls == [(15, 20, 3.0, 8.0)]

    async def test_omitted_upper_bound_becomes_minus_one(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGddl()
        monkeypatch.setattr(gdlevelsearch, "Gddl", fake)

        await run_handler(
            gdlevelsearch.gdrandom, fake_bot, make_group_event("*gd随机推关"), arg="15"
        )
        assert fake.random_calls == [(15, -1, None, None)]

    async def test_no_match_suggests_relaxing(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gdlevelsearch, "Gddl", FakeGddl())
        await run_handler(
            gdlevelsearch.gdrandom, fake_bot, make_group_event("*gd随机推关"), arg="15"
        )
        assert sent_texts(fake_bot) == ["没有找到符合条件的关卡，把条件放宽点试试"]

    async def test_hit_sends_the_image(
        self, fake_bot: FakeBot, make_group_event: Any,
        monkeypatch: pytest.MonkeyPatch, stub_image: list[GDLevel],
    ) -> None:
        fake = FakeGddl()
        fake.random_result = gddl_level(4321, "Random Pick")
        monkeypatch.setattr(gdlevelsearch, "Gddl", fake)
        monkeypatch.setattr(gdlevelsearch, "getlevelinfo", lambda _i: gd_level(name="R"))

        await run_handler(
            gdlevelsearch.gdrandom, fake_bot, make_group_event("*gd随机推关"), arg="15"
        )
        assert len(image_segments(fake_bot)) == 1


# ==========================================================================
# *gdicon 的参数解析
# ==========================================================================
class TestHandleGdiconArgs:
    async def test_no_arguments_prints_usage(
        self, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        await run_handler(
            gdlevelsearch.gdicon, fake_bot, make_group_event("*gdicon"), arg=""
        )
        text = sent_texts(fake_bot)[0]
        assert text.startswith("用法：*gdicon 用户名 [gamemode]")
        assert icons.form_names() in text

    async def test_gamemode_after_the_name_is_taken_as_a_gamemode(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        monkeypatch.setattr(
            gdlevelsearch, "get_user_by_name", lambda n: seen.append(n) or None
        )
        await run_handler(
            gdlevelsearch.gdicon, fake_bot, make_group_event("*gdicon"), arg="RobTop ship"
        )
        assert seen == ["RobTop"]
        assert sent_texts(fake_bot) == ["没有找到用户「RobTop」"]

    async def test_a_gamemode_word_in_first_position_is_part_of_the_name(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`and words` 这个条件：第一个词就算叫 wave 也当用户名，有人 ID 就叫这个"""
        seen: list[str] = []
        monkeypatch.setattr(
            gdlevelsearch, "get_user_by_name", lambda n: seen.append(n) or None
        )
        await run_handler(
            gdlevelsearch.gdicon, fake_bot, make_group_event("*gdicon"), arg="wave"
        )
        assert seen == ["wave"]

    async def test_unknown_trailing_word_stays_part_of_the_name(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        monkeypatch.setattr(
            gdlevelsearch, "get_user_by_name", lambda n: seen.append(n) or None
        )
        await run_handler(
            gdlevelsearch.gdicon, fake_bot, make_group_event("*gdicon"),
            arg="Some Player Name",
        )
        assert seen == ["Some Player Name"]

    async def test_dash_a_is_stripped_out_of_the_name(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        monkeypatch.setattr(
            gdlevelsearch, "get_user_by_name", lambda n: seen.append(n) or None
        )
        await run_handler(
            gdlevelsearch.gdicon, fake_bot, make_group_event("*gdicon"), arg="-a RobTop"
        )
        assert seen == ["RobTop"]

    async def test_only_flags_leaves_no_name(
        self, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        await run_handler(
            gdlevelsearch.gdicon, fake_bot, make_group_event("*gdicon"), arg="-a"
        )
        assert sent_texts(fake_bot) == ["请给一个 GD 用户名"]

    async def test_single_icon_is_sent_with_a_caption(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = GDUser()
        user.user_name = "RobTop"
        monkeypatch.setattr(gdlevelsearch, "get_user_by_name", lambda _n: user)

        async def fake_fetch_one(u: GDUser, form: Any) -> Image.Image:
            return Image.new("RGBA", (2, 2))

        monkeypatch.setattr(icons, "fetch_one", fake_fetch_one)

        await run_handler(
            gdlevelsearch.gdicon, fake_bot, make_group_event("*gdicon"), arg="RobTop ship"
        )

        assert sent_texts(fake_bot) == ["RobTop 的 Ship："]
        assert len(image_segments(fake_bot)) == 1

    async def test_icon_service_failure_is_reported(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = GDUser()
        user.user_name = "RobTop"
        monkeypatch.setattr(gdlevelsearch, "get_user_by_name", lambda _n: user)

        async def fake_fetch_one(u: GDUser, form: Any) -> None:
            return None

        monkeypatch.setattr(icons, "fetch_one", fake_fetch_one)

        await run_handler(
            gdlevelsearch.gdicon, fake_bot, make_group_event("*gdicon"), arg="RobTop"
        )
        assert sent_texts(fake_bot) == ["RobTop 的 Cube 图标没取到，等会再试"]


# ==========================================================================
# *dailydemon
# ==========================================================================
class TestHandleDailyDemon:
    async def test_error_from_the_picker_is_relayed(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            gdlevelsearch, "get_daily_demon", lambda: (None, 0, "今天没挑出来")
        )
        await run_handler(
            gdlevelsearch.dailydemon, fake_bot, make_group_event("*dailydemon")
        )
        assert sent_texts(fake_bot) == ["今天没挑出来"]

    async def test_detail_lookup_failure_still_names_the_level(
        self, fake_bot: FakeBot, make_group_event: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            gdlevelsearch, "get_daily_demon",
            lambda: (gddl_level(777, "Daily One"), 42, ""),
        )
        monkeypatch.setattr(gdlevelsearch, "getlevelinfo", lambda _i: None)

        await run_handler(
            gdlevelsearch.dailydemon, fake_bot, make_group_event("*dailydemon")
        )
        assert sent_texts(fake_bot) == [
            "今日关卡是 Daily One（ID 777），但是拿详细信息的时候出错了"
        ]

    async def test_happy_path_sends_a_caption_then_the_image(
        self, fake_bot: FakeBot, make_group_event: Any,
        monkeypatch: pytest.MonkeyPatch, stub_image: list[GDLevel],
    ) -> None:
        monkeypatch.setattr(
            gdlevelsearch, "get_daily_demon",
            lambda: (gddl_level(777, "Daily One"), 42, ""),
        )
        monkeypatch.setattr(gdlevelsearch, "getlevelinfo", lambda _i: gd_level(name="D"))
        monkeypatch.setattr(gdlevelsearch, "describe_conditions", lambda: "tier 20-25")

        await run_handler(
            gdlevelsearch.dailydemon, fake_bot, make_group_event("*dailydemon")
        )

        assert sent_texts(fake_bot)[0] == "今日关卡（tier 20-25，候选 42 关）："
        assert len(image_segments(fake_bot)) == 1


# ==========================================================================
# *gdsearchhelp
# ==========================================================================
class TestHandleGdsearchHelp:
    async def test_mentions_every_command_it_documents(
        self, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        await run_handler(
            gdlevelsearch.gdsearchhelp, fake_bot, make_group_event("*gdsearchhelp")
        )
        text = sent_texts(fake_bot)[0]
        for cmd in ("*gdsearch", "*gdfullsearch", "*gdratings", "*references"):
            assert cmd in text
