"""gdlevelsearch 里几个数据源模块的单元测试。

覆盖：gddlapi（GDDL 网站接口 + 分页）、nlwapi（NLW/IDS/LW/HDS 本地缓存）、
platapi（plat_combined.json）、dailydemon（每日一关的挑选/落地逻辑）、
icons（gamemode 图标的参数拼装与拼图几何）。

三条硬约束：
1. 不联网 —— 所有 HTTP 都走 conftest 的 stub_requests / stub_httpx；
2. 不碰仓库工作区 —— 所有数据文件都写在 tmp_path 里，模块级的全局状态
   （nlwapi 的四张表、platapi 的六个全局、dailydemon 的 r）用 fixture 换掉并还原；
3. 不依赖真实时钟 —— dailydemon 的日期一律显式传入。

出图部分只断言尺寸/模式/没抛异常，不去比像素。
"""

from __future__ import annotations

import asyncio
import io
import json
import random
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest
from PIL import Image

from xiaozu_bot.plugins.gdlevelsearch import gddlapi, icons, nlwapi, platapi
from xiaozu_bot.plugins.gdlevelsearch.gdapi import GDUser
from xiaozu_bot.plugins.gdlevelsearch.gddlapi import (
    GDDL_LIMIT_MAX,
    GDDL_LIMIT_MIN,
    GDDL_PLAT_LENGTH,
    GDDL_SUBMISSION_LIMIT,
    Gddl,
    GDDLLevel,
    LevelMeta,
    SongInfo,
    Submission,
    SubmissionPage,
)
from xiaozu_bot.plugins.gdlevelsearch.nlwapi import (
    HDSlevel,
    IDSlevel,
    Level,
    LWlevel,
    Nlw,
    NLWlevel,
)
from xiaozu_bot.plugins.gdlevelsearch.platapi import PlatData, PlatInfo, Platapi

# datetime 只在这里用来造固定日期，绝不取 now()
from datetime import date  # noqa: E402

# dailydemon 必须用 import_module 拿：gdlevelsearch/__init__.py:655 有一句
# `dailydemon = on_command("dailydemon")`，把同名子模块在包命名空间里盖掉了，
# `from ... import dailydemon` 拿到的是那个 Matcher，不是模块。
from importlib import import_module  # noqa: E402

dailydemon = import_module("xiaozu_bot.plugins.gdlevelsearch.dailydemon")

# ==========================================================================
# 造数据的小工具
# ==========================================================================

GDDL_LEVEL_URL = "https://gdladder.com/api/level/"
GDDL_SEARCH_URL = "https://gdladder.com/api/level/search"


def make_song_payload(**over: Any) -> dict[str, Any]:
    """SongDTO"""
    payload = {"ID": -1, "Name": "Stereo Madness", "Author": "ForeverBound", "Size": 1.5}
    payload.update(over)
    return payload


def make_meta_payload(**over: Any) -> dict[str, Any]:
    """LevelMetaDTO。Length 默认 4（Long），要测 plat 就传 6。"""
    payload = {
        "ID": 1000,
        "Name": "Test Level",
        "Description": "a description",
        "SongID": -1,
        "Length": 4,
        "IsTwoPlayer": False,
        "Difficulty": "Extreme",
        "PublisherID": 7,
        "UploadedAt": None,
        "Song": make_song_payload(),
    }
    payload.update(over)
    return payload


def make_level_payload(**over: Any) -> dict[str, Any]:
    """LevelDTO，14 个字段一个不少（GDDLLevel 全是 jsondict[...] 硬取）。"""
    meta_over = over.pop("meta", {})
    payload = {
        "ID": 1000,
        "Rating": 20.5,
        "Enjoyment": 7.5,
        "Deviation": 1.25,
        "RatingCount": 12,
        "EnjoymentCount": 10,
        "SubmissionCount": 15,
        "TwoPlayerRating": None,
        "TwoPlayerEnjoyment": None,
        "TwoPlayerDeviation": None,
        "DefaultRating": None,
        "Showcase": "dQw4w9WgXcQ",
        "Meta": make_meta_payload(**meta_over),
    }
    payload.update(over)
    return payload


def make_nlw_row(level_id: int = 1, name: str = "Alpha", **over: Any) -> dict[str, Any]:
    """一行表格数据，四个源（NLW/IDS/LW/HDS）共用同一份字段。"""
    row = {
        "name": name,
        "creator": "SomeCreator",
        "length": "1:23",
        "checkpoints": None,
        "id": level_id,
        "description": "some description",
        "video": "https://youtu.be/xxxx",
        "tier": "1",
        "skillset": "wave",
        "enjoyment": 5.0,
    }
    row.update(over)
    return row


def make_plat_row(level_id: str = "111", name: str = "Plat A", **over: Any) -> dict[str, Any]:
    row = {
        "name": name,
        "id": level_id,
        "tier": "9 - CRUEL",
        "tpl": "100",
        "pemonlist": "41",
        "creator": "No ob",
        "tags": ["Deathless", "Precision"],
        "enjoyment": 8.5,
        "video": "https://youtu.be/xxxx",
        "weight": "100",
        "section": "TPL",
        "derived_from": None,
        "derived_levels": [],
    }
    row.update(over)
    return row


def png_bytes(size: tuple[int, int] = (60, 60), color: tuple[int, int, int, int] = (255, 0, 0, 255)) -> bytes:
    """现造一张 PNG 的字节，喂给 icons 的假响应"""
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


# ==========================================================================
# gddlapi —— 常量
# ==========================================================================
class TestGddlConstants:
    def test_submission_limit_matches_web_page(self) -> None:
        """网页上一页 10 条，代码里的默认值必须跟着"""
        assert GDDL_SUBMISSION_LIMIT == 10

    def test_limit_range_is_1_to_30(self) -> None:
        """接口只认 1-30，超了直接 400"""
        assert (GDDL_LIMIT_MIN, GDDL_LIMIT_MAX) == (1, 30)

    def test_plat_length_is_6(self) -> None:
        """Length 枚举里 6 = platformer"""
        assert GDDL_PLAT_LENGTH == 6

    def test_sort_and_filter_whitelists(self) -> None:
        assert gddlapi.SUBMISSION_SORTS == {
            "attempts", "dateAdded", "enjoyment", "rating",
            "progress", "refreshRate", "username",
        }
        assert gddlapi.SORT_DIRECTIONS == {"asc", "desc"}
        assert gddlapi.PROGRESS_FILTERS == {"all", "victors", "incomplete"}


# ==========================================================================
# gddlapi —— 数据类
# ==========================================================================
class TestGddlModels:
    def test_song_info_only_keeps_three_fields(self) -> None:
        """SongDTO 有 Size，但 SongInfo 不收，to_dict 里不该冒出来"""
        song = SongInfo(make_song_payload(Size=99.0))
        assert (song.ID, song.Name, song.Author) == (-1, "Stereo Madness", "ForeverBound")
        assert set(song.to_dict()) == {"ID", "Name", "Author"}

    def test_song_info_str(self) -> None:
        song = SongInfo(make_song_payload(ID=5, Name="N", Author="A"))
        assert str(song) == "ID: 5\nName: N\nAuthor: A"

    def test_song_info_requires_all_three_keys(self) -> None:
        with pytest.raises(KeyError):
            SongInfo({"ID": 1, "Name": "n"})

    @pytest.mark.parametrize(
        ("length", "expected"),
        [(1, False), (4, False), (5, False), (GDDL_PLAT_LENGTH, True), (7, False)],
    )
    def test_level_meta_is_pemon_boundary(self, length: int, expected: bool) -> None:
        """只有 Length == 6 才算 plat，5（XL）和 7（不存在的值）都不算"""
        meta = LevelMeta(make_meta_payload(Length=length))
        assert meta.is_pemon() is expected

    def test_level_meta_drops_publisher_and_uploaded_at(self) -> None:
        """__init__ 压根没读 PublisherID / UploadedAt，实例上就不该有 PublisherID。

        UploadedAt 因为类上有默认值 None 还能取到，PublisherID 只有类型注解，
        取它会 AttributeError —— 这是源码现状，不是笔误。
        """
        meta = LevelMeta(make_meta_payload())
        assert set(meta.to_dict()) == {
            "ID", "Name", "Description", "SongID", "Length",
            "IsTwoPlayer", "Difficulty", "Song",
        }
        assert meta.UploadedAt is None
        assert not hasattr(meta, "PublisherID")

    def test_gddl_level_full_parse(self) -> None:
        level = GDDLLevel(make_level_payload())
        assert level.ID == 1000
        assert level.Rating == 20.5
        assert level.Enjoyment == 7.5
        assert level.SubmissionCount == 15
        assert level.Showcase == "dQw4w9WgXcQ"
        assert level.Meta.Song.Name == "Stereo Madness"
        assert level.Tags == []

    def test_gddl_level_nullable_fields_stay_none(self) -> None:
        """Rating / Enjoyment 这些是 number|null，null 要原样保留而不是变 0"""
        level = GDDLLevel(make_level_payload(Rating=None, Enjoyment=None, Deviation=None))
        assert level.Rating is None
        assert level.Enjoyment is None
        assert level.Deviation is None

    def test_gddl_level_tags_default_and_passthrough(self) -> None:
        tags = [{"Name": "Timings", "Count": 3}]
        assert GDDLLevel(make_level_payload(), tags).Tags == tags
        # 显式传空列表也走 `tags or []` 那条，结果还是空列表
        assert GDDLLevel(make_level_payload(), []).Tags == []

    def test_gddl_level_is_pemon_delegates_to_meta(self) -> None:
        assert GDDLLevel(make_level_payload(meta={"Length": 6})).is_pemon() is True
        assert GDDLLevel(make_level_payload(meta={"Length": 5})).is_pemon() is False

    def test_gddl_level_missing_key_raises(self) -> None:
        """所有字段都是硬取，少一个就 KeyError（调用方得自己接住）"""
        payload = make_level_payload()
        del payload["Deviation"]
        with pytest.raises(KeyError):
            GDDLLevel(payload)


class TestSubmission:
    def test_all_fields_optional(self) -> None:
        """Submission 全用 .get，空 dict 也能构造出来，is_solo 默认 True"""
        sub = Submission({})
        assert sub.id is None
        assert sub.rating is None
        assert sub.enjoyment is None
        assert sub.user_name is None
        assert sub.second_user_name is None
        assert sub.is_solo is True

    def test_full_payload(self) -> None:
        sub = Submission({
            "ID": 7, "Rating": 21, "Enjoyment": 8.5, "RefreshRate": 240,
            "Device": "PC", "Proof": "https://youtu.be/p", "IsSolo": False,
            "Progress": 100, "Attempts": 1234, "DateAdded": "2026-01-02T03:04:05Z",
            "UserID": 42, "User": {"Name": "someone"},
            "SecondaryUser": {"Name": "partner"},
        })
        assert sub.id == 7
        assert (sub.rating, sub.enjoyment) == (21, 8.5)
        assert sub.refresh_rate == 240
        assert sub.is_solo is False
        assert (sub.progress, sub.attempts) == (100, 1234)
        assert (sub.user_id, sub.user_name) == (42, "someone")
        assert sub.second_user_name == "partner"

    def test_null_user_objects_do_not_explode(self) -> None:
        """User / SecondaryUser 是 null 的时候要退化成空 dict，不能 AttributeError"""
        sub = Submission({"User": None, "SecondaryUser": None})
        assert sub.user_name is None
        assert sub.second_user_name is None

    def test_is_solo_false_is_kept(self) -> None:
        """.get(key, True) 的坑：显式 False 必须留住，不能被默认值顶掉"""
        assert Submission({"IsSolo": False}).is_solo is False

    def test_to_dict_is_a_copy(self) -> None:
        sub = Submission({"ID": 1})
        dumped = sub.to_dict()
        dumped["id"] = 999
        assert sub.id == 1


class TestSubmissionPage:
    def test_defaults_on_empty_payload(self) -> None:
        page = SubmissionPage({})
        assert page.total == 0
        assert page.limit == GDDL_SUBMISSION_LIMIT
        assert page.page == 0
        assert page.submissions == []
        assert page.total_pages == 1

    def test_parses_submissions(self) -> None:
        page = SubmissionPage({
            "total": 3, "limit": 10, "page": 0,
            "submissions": [{"ID": 1}, {"ID": 2}, {"ID": 3}],
        })
        assert [s.id for s in page.submissions] == [1, 2, 3]
        assert all(isinstance(s, Submission) for s in page.submissions)

    @pytest.mark.parametrize(
        ("total", "limit", "expected"),
        [
            (0, 10, 1),     # 没有提交也至少算一页
            (1, 10, 1),
            (9, 10, 1),     # 最后一页不满
            (10, 10, 1),    # 正好一页，不能多算出一页
            (11, 10, 2),    # 刚过界
            (20, 10, 2),
            (21, 10, 3),
            (5, 1, 5),
            (30, 30, 1),
            (31, 30, 2),
        ],
    )
    def test_total_pages_boundaries(self, total: int, limit: int, expected: int) -> None:
        """向上取整，且至少 1 页。整除的边界两侧都要对。"""
        page = SubmissionPage({"total": total, "limit": limit})
        assert page.total_pages == expected

    @pytest.mark.parametrize("limit", [0, -1, -30])
    def test_total_pages_guards_against_bad_limit(self, limit: int) -> None:
        """limit <= 0 直接返回 1，不能除零也不能出负数页"""
        assert SubmissionPage({"total": 100, "limit": limit}).total_pages == 1

    def test_page_number_is_taken_as_is(self) -> None:
        """page 是接口回什么就是什么（0 起），这里不做任何换算"""
        assert SubmissionPage({"page": 3}).page == 3


# ==========================================================================
# gddlapi —— HTTP
# ==========================================================================
class TestGddlGetSubmissions:
    def test_happy_path(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get(
            "https://gdladder.com/api/level/1000/submissions",
            make_response(json_data={"total": 2, "limit": 10, "page": 0,
                                     "submissions": [{"ID": 1}, {"ID": 2}]}),
        )
        page = Gddl.getsubmissions(1000)
        assert page is not None
        assert page.total == 2
        assert [s.id for s in page.submissions] == [1, 2]
        call = stub_requests.calls[-1]
        assert call["url"] == "https://gdladder.com/api/level/1000/submissions"
        assert call["params"] == {"page": 0, "limit": GDDL_SUBMISSION_LIMIT}
        assert call["timeout"] == 15
        assert call["headers"]["Authorization"] == f"Bearer {gddlapi.apikey}"

    @pytest.mark.parametrize(
        ("page_in", "expected"),
        [(-100, 0), (-1, 0), (0, 0), (1, 1), (7, 7)],
    )
    def test_page_is_clamped_to_non_negative(
        self, stub_requests: Any, make_response: Any, page_in: int, expected: int
    ) -> None:
        stub_requests.get("/submissions", make_response(json_data={}))
        Gddl.getsubmissions(1, page=page_in)
        assert stub_requests.calls[-1]["params"]["page"] == expected

    @pytest.mark.parametrize(
        ("limit_in", "expected"),
        [(-5, 1), (0, 1), (1, 1), (2, 2), (29, 29), (30, 30), (31, 30), (999, 30)],
    )
    def test_limit_is_clamped_to_1_30(
        self, stub_requests: Any, make_response: Any, limit_in: int, expected: int
    ) -> None:
        """两端都要卡住：0 和 31 分别被顶成 1 和 30，1/30 本身不动"""
        stub_requests.get("/submissions", make_response(json_data={}))
        Gddl.getsubmissions(1, limit=limit_in)
        assert stub_requests.calls[-1]["params"]["limit"] == expected

    @pytest.mark.parametrize("sort", sorted(gddlapi.SUBMISSION_SORTS))
    def test_known_sorts_are_forwarded(
        self, stub_requests: Any, make_response: Any, sort: str
    ) -> None:
        stub_requests.get("/submissions", make_response(json_data={}))
        Gddl.getsubmissions(1, sort=sort)
        assert stub_requests.calls[-1]["params"]["sort"] == sort

    @pytest.mark.parametrize("sort", ["DateAdded", "dateadded", "id", ""])
    def test_unknown_sort_is_dropped(
        self, stub_requests: Any, make_response: Any, sort: str
    ) -> None:
        """大小写不对也算不认识（接口是大小写敏感的），空串则连判断都不进"""
        stub_requests.get("/submissions", make_response(json_data={}))
        Gddl.getsubmissions(1, sort=sort)
        assert "sort" not in stub_requests.calls[-1]["params"]

    @pytest.mark.parametrize(
        ("direction", "expected"),
        [("asc", "asc"), ("desc", "desc"), ("ASC", "asc"), ("DeSc", "desc")],
    )
    def test_sort_direction_is_lowercased(
        self, stub_requests: Any, make_response: Any, direction: str, expected: str
    ) -> None:
        """传 ASC 接口会 400，这里要先转小写再发"""
        stub_requests.get("/submissions", make_response(json_data={}))
        Gddl.getsubmissions(1, sort_direction=direction)
        assert stub_requests.calls[-1]["params"]["sortDirection"] == expected

    @pytest.mark.parametrize("direction", ["ascending", "up", "1"])
    def test_bad_sort_direction_is_dropped(
        self, stub_requests: Any, make_response: Any, direction: str
    ) -> None:
        stub_requests.get("/submissions", make_response(json_data={}))
        Gddl.getsubmissions(1, sort_direction=direction)
        assert "sortDirection" not in stub_requests.calls[-1]["params"]

    @pytest.mark.parametrize("pf", sorted(gddlapi.PROGRESS_FILTERS))
    def test_known_progress_filters_forwarded(
        self, stub_requests: Any, make_response: Any, pf: str
    ) -> None:
        stub_requests.get("/submissions", make_response(json_data={}))
        Gddl.getsubmissions(1, progress_filter=pf)
        assert stub_requests.calls[-1]["params"]["progressFilter"] == pf

    @pytest.mark.parametrize("pf", ["ALL", "winners", "none"])
    def test_unknown_progress_filter_dropped(
        self, stub_requests: Any, make_response: Any, pf: str
    ) -> None:
        stub_requests.get("/submissions", make_response(json_data={}))
        Gddl.getsubmissions(1, progress_filter=pf)
        assert "progressFilter" not in stub_requests.calls[-1]["params"]

    @pytest.mark.parametrize("status", [400, 401, 404, 429, 500])
    def test_non_200_returns_none(
        self, stub_requests: Any, make_response: Any, status: int
    ) -> None:
        """非 200 返回 None，和「有一页但是空的」区分开"""
        stub_requests.get("/submissions", make_response(status, json_data={}))
        assert Gddl.getsubmissions(1) is None

    def test_request_exception_returns_none(self, stub_requests: Any) -> None:
        import requests as _requests
        stub_requests.get("/submissions", _requests.Timeout("timed out"))
        assert Gddl.getsubmissions(1) is None

    def test_empty_result_is_a_page_not_none(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        """0 条提交要返回一个空的 SubmissionPage，而不是 None"""
        stub_requests.get(
            "/submissions", make_response(json_data={"total": 0, "submissions": []})
        )
        page = Gddl.getsubmissions(1)
        assert isinstance(page, SubmissionPage)
        assert page.submissions == []
        assert page.total_pages == 1


class TestGddlSpreadAndTags:
    def test_spread_happy_path(self, stub_requests: Any, make_response: Any) -> None:
        payload = {"rating": {"20": 3}, "enjoyment": {"8": 5}}
        stub_requests.get("/submissions/spread", make_response(json_data=payload))
        assert Gddl.getspread(1000) == payload
        assert stub_requests.urls[-1] == (
            "https://gdladder.com/api/level/1000/submissions/spread"
        )

    def test_spread_non_200_returns_none(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get("/submissions/spread", make_response(503, json_data={}))
        assert Gddl.getspread(1000) is None

    def test_spread_exception_returns_none(self, stub_requests: Any) -> None:
        import requests as _requests
        stub_requests.get("/submissions/spread", _requests.ConnectionError("nope"))
        assert Gddl.getspread(1000) is None

    def test_tags_are_flattened(self, stub_requests: Any, make_response: Any) -> None:
        """接口回的是嵌套的 GetLevelTagsResponseDTO，这里压成 Name/Count 两个键"""
        stub_requests.get("/tags", make_response(json_data=[
            {"TagID": 1, "ReactCount": 9, "HasVoted": 0,
             "Tag": {"ID": 1, "Name": "Timings", "Description": "", "Ordering": 1}},
            {"TagID": 2, "ReactCount": 0, "HasVoted": 1,
             "Tag": {"ID": 2, "Name": "Memory", "Description": "", "Ordering": 2}},
        ]))
        assert Gddl.getleveltags(500) == [
            {"Name": "Timings", "Count": 9},
            {"Name": "Memory", "Count": 0},
        ]

    def test_tags_empty_list(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get("/tags", make_response(json_data=[]))
        assert Gddl.getleveltags(500) == []

    @pytest.mark.parametrize("status", [404, 500])
    def test_tags_non_200_returns_empty_list(
        self, stub_requests: Any, make_response: Any, status: int
    ) -> None:
        """失败返回空列表而不是 None —— 调用方直接 for 循环，不做判空"""
        stub_requests.get("/tags", make_response(status, json_data=[]))
        assert Gddl.getleveltags(500) == []

    def test_tags_exception_returns_empty_list(self, stub_requests: Any) -> None:
        import requests as _requests
        stub_requests.get("/tags", _requests.Timeout("t"))
        assert Gddl.getleveltags(500) == []

    def test_tags_uses_gddl_timeout(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get("/tags", make_response(json_data=[]))
        Gddl.getleveltags(500)
        assert stub_requests.calls[-1]["timeout"] == gddlapi.GDDL_TIMEOUT


class TestGddlLevelLookup:
    def test_getlevelsbyname(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get(GDDL_SEARCH_URL, make_response(json_data={
            "levels": [make_level_payload(ID=1), make_level_payload(ID=2)]
        }))
        levels = Gddl.getlevelsbyname("Test Level")
        assert [lv.ID for lv in levels] == [1, 2]
        assert stub_requests.calls[-1]["params"] == {"name": "Test Level"}

    def test_getlevelsbyname_non_200(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get(GDDL_SEARCH_URL, make_response(404, json_data={}))
        assert Gddl.getlevelsbyname("x") == []

    def test_getlevelsbyname_exception(self, stub_requests: Any) -> None:
        import requests as _requests
        stub_requests.get(GDDL_SEARCH_URL, _requests.ConnectionError("boom"))
        assert Gddl.getlevelsbyname("x") == []

    def test_getlevelbyid_with_tags_does_two_round_trips(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        # /tags 必须先登记：路由是子串匹配，".../123" 也能匹配 ".../123/tags"
        stub_requests.get("/api/level/123/tags", make_response(json_data=[
            {"ReactCount": 4, "Tag": {"Name": "Precision"}},
        ]))
        stub_requests.get("/api/level/123", make_response(json_data=make_level_payload(ID=123)))
        level = Gddl.getlevelbyid(123)
        assert level is not None
        assert level.ID == 123
        assert level.Tags == [{"Name": "Precision", "Count": 4}]
        assert len(stub_requests.calls) == 2

    def test_getlevelbyid_without_tags_is_one_round_trip(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        """with_tags=False 时不该顺带去拉 tags，Tags 留空"""
        stub_requests.get("/api/level/123", make_response(json_data=make_level_payload(ID=123)))
        level = Gddl.getlevelbyid(123, with_tags=False)
        assert level is not None
        assert level.Tags == []
        assert len(stub_requests.calls) == 1

    def test_getlevelbyid_non_200(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get("/api/level/123", make_response(404, json_data={}))
        assert Gddl.getlevelbyid(123) is None

    def test_getlevelbyid_exception(self, stub_requests: Any) -> None:
        import requests as _requests
        stub_requests.get("/api/level/123", _requests.Timeout("t"))
        assert Gddl.getlevelbyid(123) is None


class TestGddlSearch:
    def test_searchlevels_params(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get(GDDL_SEARCH_URL, make_response(json_data={"total": 0, "levels": []}))
        Gddl.searchlevels(page=2, limit=5, sort="ID", minRating=3, maxRating=None)
        params = stub_requests.calls[-1]["params"]
        # None 的 filter 直接不发，免得接口把 "None" 当值
        assert params == {"page": 2, "limit": 5, "sort": "ID", "minRating": 3}

    def test_searchlevels_clamps_negative_page_only(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        """page 卡了下限，limit 却一点没卡（和 getsubmissions 不一样）"""
        stub_requests.get(GDDL_SEARCH_URL, make_response(json_data={}))
        Gddl.searchlevels(page=-3, limit=99)
        assert stub_requests.calls[-1]["params"]["page"] == 0
        assert stub_requests.calls[-1]["params"]["limit"] == 99

    def test_searchlevels_non_200(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get(GDDL_SEARCH_URL, make_response(500, json_data={}))
        assert Gddl.searchlevels() is None

    def test_searchlevels_exception(self, stub_requests: Any) -> None:
        import requests as _requests
        stub_requests.get(GDDL_SEARCH_URL, _requests.Timeout("t"))
        assert Gddl.searchlevels() is None

    def test_getlevelbyindex_uses_index_as_page(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        """靠 sort=ID + limit=1 + page=index 定位第 index 关，dailydemon 就指着这个稳"""
        stub_requests.get(GDDL_SEARCH_URL, make_response(
            json_data={"total": 500, "levels": [make_level_payload(ID=777)]}
        ))
        level = Gddl.getlevelbyindex(42, minRating=1)
        assert level is not None
        assert level.ID == 777
        params = stub_requests.calls[-1]["params"]
        assert params == {"page": 42, "limit": 1, "sort": "ID", "minRating": 1}

    def test_getlevelbyindex_none_payload(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get(GDDL_SEARCH_URL, make_response(500, json_data={}))
        assert Gddl.getlevelbyindex(0) is None

    @pytest.mark.parametrize("payload", [{}, {"levels": []}, {"total": 3, "levels": []}])
    def test_getlevelbyindex_empty_levels(
        self, stub_requests: Any, make_response: Any, payload: dict[str, Any]
    ) -> None:
        """超出范围的 index 会拿到空 levels，要返回 None 而不是 IndexError"""
        stub_requests.get(GDDL_SEARCH_URL, make_response(json_data=payload))
        assert Gddl.getlevelbyindex(9999) is None

    @pytest.mark.parametrize(
        ("low", "high", "expected_min", "expected_max"),
        [
            (20, -1, 19.5, 20.5),   # high 不传就是单个 tier，±0.5 展开
            (20, 25, 19.5, 25.5),
            (1, -1, 1.0, 1.5),      # 下限被 1.0 兜住，不会出现 0.5
            (2, -1, 1.5, 2.5),
            (39, -1, 38.5, 39.0),   # 上限被 39.0 兜住，不会出现 39.5
            (38, -1, 37.5, 38.5),
            (1, 39, 1.0, 39.0),     # 全区间，两端都被兜
        ],
    )
    def test_random_by_tier_expands_range(
        self, stub_requests: Any, make_response: Any,
        low: int, high: int, expected_min: float, expected_max: float,
    ) -> None:
        stub_requests.get(GDDL_SEARCH_URL, make_response(
            json_data={"total": 1, "levels": [make_level_payload(ID=9)]}
        ))
        Gddl.getrandomlevelbytier(low, high)
        params = stub_requests.calls[-1]["params"]
        assert params["minRating"] == expected_min
        assert params["maxRating"] == expected_max
        assert params["sort"] == "random"
        assert params["page"] == 0
        assert params["limit"] == 1

    def test_random_by_tier_enjoyment_filters(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get(GDDL_SEARCH_URL, make_response(
            json_data={"total": 1, "levels": [make_level_payload()]}
        ))
        Gddl.getrandomlevelbytier(10, enjoyment_min=7.0, enjoyment_max=9.5)
        params = stub_requests.calls[-1]["params"]
        assert params["minEnjoyment"] == 7.0
        assert params["maxEnjoyment"] == 9.5

    def test_random_by_tier_omits_unset_enjoyment(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get(GDDL_SEARCH_URL, make_response(
            json_data={"total": 1, "levels": [make_level_payload()]}
        ))
        Gddl.getrandomlevelbytier(10)
        params = stub_requests.calls[-1]["params"]
        assert "minEnjoyment" not in params
        assert "maxEnjoyment" not in params

    def test_random_by_tier_empty_result(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get(GDDL_SEARCH_URL, make_response(json_data={"total": 0, "levels": []}))
        assert Gddl.getrandomlevelbytier(20) is None

    def test_random_by_tier_request_failed(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get(GDDL_SEARCH_URL, make_response(500, json_data={}))
        assert Gddl.getrandomlevelbytier(20) is None


# ==========================================================================
# nlwapi
# ==========================================================================
@pytest.fixture
def nlw_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """把 nlwapi 的数据目录指到 tmp_path，收尾时把四张表的内容原样还回去。

    四个 list / dict 是被 _SOURCES 和 _rebuild_dicts 直接引用的对象，
    reload() 是原地 clear + append，所以只能原地还原，不能整个换掉。
    """
    lists = (nlwapi.nlwlevels, nlwapi.idslevels, nlwapi.lwlevels, nlwapi.hdslevels)
    dicts = (
        nlwapi.nlwlevel_dict, nlwapi.idslevel_dict,
        nlwapi.lwlevel_dict, nlwapi.hdslevel_dict,
    )
    list_backup = [list(x) for x in lists]
    dict_backup = [dict(x) for x in dicts]
    monkeypatch.setattr(nlwapi, "WORK_FOLDER", tmp_path)

    def _write(filename: str, payload: Any) -> Path:
        path = tmp_path / filename
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    try:
        yield _write
    finally:
        for lst, backup in zip(lists, list_backup):
            lst[:] = backup
        for dct, backup in zip(dicts, dict_backup):
            dct.clear()
            dct.update(backup)


class TestNlwModels:
    def test_base_level_required_vs_optional_keys(self) -> None:
        """name / creator / video 是硬取的，其余走 .get"""
        level = Level({"name": "N", "creator": "C", "video": "V"})
        assert (level.name, level.creator, level.video) == ("N", "C", "V")
        assert level.length is None
        assert level.checkpoints is None
        assert level.id is None
        assert level.description is None

    @pytest.mark.parametrize("missing", ["name", "creator", "video"])
    def test_base_level_missing_required_key_raises(self, missing: str) -> None:
        row = {"name": "N", "creator": "C", "video": "V"}
        del row[missing]
        with pytest.raises(KeyError):
            Level(row)

    @pytest.mark.parametrize(
        ("cls", "source"),
        [(NLWlevel, "NLW"), (IDSlevel, "IDS"), (LWlevel, "LW"), (HDSlevel, "HDS")],
    )
    def test_subclasses_tag_their_source(self, cls: type, source: str) -> None:
        level = cls(make_nlw_row(tier="Fuck", skillset="wave"))
        assert level.source == source
        assert level.tier == "Fuck"
        assert level.skillset == "wave"

    def test_nlw_and_lw_carry_enjoyment(self) -> None:
        assert NLWlevel(make_nlw_row(enjoyment=62.0)).enjoyment == 62.0
        assert LWlevel(make_nlw_row(enjoyment=None)).enjoyment is None

    @pytest.mark.parametrize("cls", [IDSlevel, HDSlevel])
    def test_ids_hds_do_not_read_enjoyment(self, cls: type) -> None:
        """IDS/HDS 表没有 enjoyment 这一列，缺了也要能构造。

        注意 IDS/HDS 实例上**压根没有** enjoyment 这个属性（既没在 __init__ 里赋值，
        类上也没有默认值），所以 `level.enjoyment` 会 AttributeError 而不是 None。
        四个子类在这点上不一致（NLW 有类默认值、LW 只在 __init__ 里赋值），
        看着像是漏了，只是目前没有调用方直接读它，所以没炸。
        """
        row = make_nlw_row()
        del row["enjoyment"]
        level = cls(row)
        assert not hasattr(level, "enjoyment")
        assert level.tier == "1"

    @pytest.mark.parametrize("cls", [NLWlevel, LWlevel])
    def test_nlw_lw_require_enjoyment(self, cls: type) -> None:
        row = make_nlw_row()
        del row["enjoyment"]
        with pytest.raises(KeyError):
            cls(row)

    def test_to_dict_is_a_copy(self) -> None:
        level = NLWlevel(make_nlw_row())
        dumped = level.to_dict()
        dumped["name"] = "changed"
        assert level.name == "Alpha"


class TestNlwLoading:
    def test_loads_all_four_sources(self, nlw_workspace: Any) -> None:
        nlw_workspace("nlw_levels.json", {"levels": [make_nlw_row(1, "N1")]})
        nlw_workspace("ids_levels.json", {"levels": [make_nlw_row(2, "I1")]})
        nlw_workspace("lw_levels.json", {"levels": [make_nlw_row(3, "L1")]})
        nlw_workspace("hds_levels.json", {"levels": [make_nlw_row(4, "H1")]})
        nlwapi.reload()
        assert [lv.name for lv in nlwapi.nlwlevels] == ["N1"]
        assert [lv.name for lv in nlwapi.idslevels] == ["I1"]
        assert [lv.name for lv in nlwapi.lwlevels] == ["L1"]
        assert [lv.name for lv in nlwapi.hdslevels] == ["H1"]
        assert set(nlwapi.nlwlevel_dict) == {1}
        assert set(nlwapi.hdslevel_dict) == {4}

    def test_missing_files_are_skipped_not_fatal(self, nlw_workspace: Any) -> None:
        """只有 nlw 一个文件在，另外三个缺失也不能抛"""
        nlw_workspace("nlw_levels.json", {"levels": [make_nlw_row(1)]})
        nlwapi.reload()
        assert len(nlwapi.nlwlevels) == 1
        assert nlwapi.idslevels == []
        assert nlwapi.lwlevels == []
        assert nlwapi.hdslevels == []

    def test_broken_json_is_skipped(self, nlw_workspace: Any, tmp_path: Path) -> None:
        (tmp_path / "nlw_levels.json").write_text("{ this is not json", encoding="utf-8")
        nlw_workspace("ids_levels.json", {"levels": [make_nlw_row(2)]})
        nlwapi.reload()
        assert nlwapi.nlwlevels == []
        # 坏文件不能连累别的源
        assert len(nlwapi.idslevels) == 1

    def test_bad_row_is_skipped_but_others_survive(self, nlw_workspace: Any) -> None:
        """一条数据缺 video，只丢这条，前后两条照常进表"""
        bad = make_nlw_row(2, "Bad")
        del bad["video"]
        nlw_workspace("nlw_levels.json", {"levels": [
            make_nlw_row(1, "Good1"), bad, make_nlw_row(3, "Good3"),
        ]})
        nlwapi.reload()
        assert [lv.name for lv in nlwapi.nlwlevels] == ["Good1", "Good3"]

    def test_missing_levels_key(self, nlw_workspace: Any) -> None:
        nlw_workspace("nlw_levels.json", {"timestamp": 0})
        nlwapi.reload()
        assert nlwapi.nlwlevels == []

    def test_reload_is_idempotent(self, nlw_workspace: Any) -> None:
        """reload 是往 list 里 append 的，没先 clear 的话每次都会翻倍"""
        nlw_workspace("nlw_levels.json", {"levels": [make_nlw_row(1), make_nlw_row(2)]})
        nlwapi.reload()
        nlwapi.reload()
        nlwapi.reload()
        assert len(nlwapi.nlwlevels) == 2
        assert len(nlwapi.nlwlevel_dict) == 2

    def test_dict_is_keyed_by_raw_id_type(self, nlw_workspace: Any) -> None:
        """json 里 id 是数字，查询表的键就是 int。

        Nlw.*_query_level 的类型注解写的是 Union[str, int]，但字符串 id
        永远命中不了 int 键 —— 这看起来是个隐患（见返回值里的说明），
        这里先把现状钉住。
        """
        nlw_workspace("nlw_levels.json", {"levels": [make_nlw_row(56916170)]})
        nlwapi.reload()
        assert Nlw.nlw_query_level(56916170) is not None
        assert Nlw.nlw_query_level("56916170") is None

    def test_stale_timestamp_does_not_break_loading(self, nlw_workspace: Any) -> None:
        """时间戳过期只是打个 warning，数据照样要进表"""
        nlw_workspace("nlw_levels.json", {"timestamp": 1, "levels": [make_nlw_row(1)]})
        nlwapi.reload()
        assert len(nlwapi.nlwlevels) == 1


class TestNlwQueries:
    @pytest.fixture(autouse=True)
    def _empty_tables(self, nlw_workspace: Any) -> None:
        """每个用例都从四张空表开始，自己往里塞"""
        nlwapi.reload()

    @staticmethod
    def _put(bucket: list, dct: dict, cls: type, row: dict[str, Any]) -> Any:
        level = cls(row)
        bucket.append(level)
        dct[level.id] = level
        return level

    def test_query_by_id_per_source(self) -> None:
        n = self._put(nlwapi.nlwlevels, nlwapi.nlwlevel_dict, NLWlevel, make_nlw_row(1))
        i = self._put(nlwapi.idslevels, nlwapi.idslevel_dict, IDSlevel, make_nlw_row(2))
        lw = self._put(nlwapi.lwlevels, nlwapi.lwlevel_dict, LWlevel, make_nlw_row(3))
        h = self._put(nlwapi.hdslevels, nlwapi.hdslevel_dict, HDSlevel, make_nlw_row(4))
        assert Nlw.nlw_query_level(1) is n
        assert Nlw.ids_query_level(2) is i
        assert Nlw.lw_query_level(3) is lw
        assert Nlw.hds_query_level(4) is h
        assert Nlw.nlw_query_level(999) is None

    def test_getlevelbyid_prefers_nlw_over_everything(self) -> None:
        n = self._put(nlwapi.nlwlevels, nlwapi.nlwlevel_dict, NLWlevel, make_nlw_row(1))
        self._put(nlwapi.lwlevels, nlwapi.lwlevel_dict, LWlevel, make_nlw_row(1))
        self._put(nlwapi.idslevels, nlwapi.idslevel_dict, IDSlevel, make_nlw_row(1))
        self._put(nlwapi.hdslevels, nlwapi.hdslevel_dict, HDSlevel, make_nlw_row(1))
        assert Nlw.getlevelbyid(1) is n

    def test_getlevelbyid_lw_beats_ids_and_hds(self) -> None:
        lw = self._put(nlwapi.lwlevels, nlwapi.lwlevel_dict, LWlevel, make_nlw_row(1))
        self._put(nlwapi.idslevels, nlwapi.idslevel_dict, IDSlevel, make_nlw_row(1))
        self._put(nlwapi.hdslevels, nlwapi.hdslevel_dict, HDSlevel, make_nlw_row(1))
        assert Nlw.getlevelbyid(1) is lw

    def test_getlevelbyid_only_ids(self) -> None:
        i = self._put(nlwapi.idslevels, nlwapi.idslevel_dict, IDSlevel, make_nlw_row(1))
        assert Nlw.getlevelbyid(1) is i

    def test_getlevelbyid_only_hds(self) -> None:
        h = self._put(nlwapi.hdslevels, nlwapi.hdslevel_dict, HDSlevel, make_nlw_row(1))
        assert Nlw.getlevelbyid(1) is h

    def test_getlevelbyid_nothing(self) -> None:
        assert Nlw.getlevelbyid(1) is None

    # ---- IDS / HDS 同时命中时的取舍表（Legacy 是这里唯一的分界）----
    def test_ids_not_legacy_wins(self) -> None:
        i = self._put(nlwapi.idslevels, nlwapi.idslevel_dict, IDSlevel,
                      make_nlw_row(1, tier="Fuck"))
        self._put(nlwapi.hdslevels, nlwapi.hdslevel_dict, HDSlevel,
                  make_nlw_row(1, tier="Fuck"))
        assert Nlw.getlevelbyid(1) is i

    def test_ids_legacy_falls_back_to_hds_with_description(self) -> None:
        self._put(nlwapi.idslevels, nlwapi.idslevel_dict, IDSlevel,
                  make_nlw_row(1, tier="Legacy"))
        h = self._put(nlwapi.hdslevels, nlwapi.hdslevel_dict, HDSlevel,
                      make_nlw_row(1, tier="Fuck", description="有描述"))
        assert Nlw.getlevelbyid(1) is h

    def test_both_legacy_falls_back_to_ids(self) -> None:
        i = self._put(nlwapi.idslevels, nlwapi.idslevel_dict, IDSlevel,
                      make_nlw_row(1, tier="Legacy"))
        self._put(nlwapi.hdslevels, nlwapi.hdslevel_dict, HDSlevel,
                  make_nlw_row(1, tier="Legacy", description="有描述"))
        assert Nlw.getlevelbyid(1) is i

    @pytest.mark.parametrize("description", [None, ""])
    def test_ids_legacy_but_hds_has_no_description_falls_back_to_ids(
        self, description: Optional[str]
    ) -> None:
        """HDS 没描述就不算「更好的匹配」，还是退回 IDS（哪怕它是 Legacy）"""
        i = self._put(nlwapi.idslevels, nlwapi.idslevel_dict, IDSlevel,
                      make_nlw_row(1, tier="Legacy"))
        self._put(nlwapi.hdslevels, nlwapi.hdslevel_dict, HDSlevel,
                  make_nlw_row(1, tier="Fuck", description=description))
        assert Nlw.getlevelbyid(1) is i

    def test_getlevelbyname_searches_all_tables_in_order(self) -> None:
        n = self._put(nlwapi.nlwlevels, nlwapi.nlwlevel_dict, NLWlevel,
                      make_nlw_row(1, "Same Name"))
        i = self._put(nlwapi.idslevels, nlwapi.idslevel_dict, IDSlevel,
                      make_nlw_row(2, "Same Name"))
        lw = self._put(nlwapi.lwlevels, nlwapi.lwlevel_dict, LWlevel,
                       make_nlw_row(3, "Same Name"))
        h = self._put(nlwapi.hdslevels, nlwapi.hdslevel_dict, HDSlevel,
                      make_nlw_row(4, "Same Name"))
        # 顺序写死在源码里：NLW -> IDS -> LW -> HDS
        assert Nlw.getlevelbyname("Same Name") == [n, i, lw, h]

    @pytest.mark.parametrize("query", ["bloodbath", "BLOODBATH", "  BloodBath  "])
    def test_getlevelbyname_is_case_and_space_insensitive(self, query: str) -> None:
        lv = self._put(nlwapi.nlwlevels, nlwapi.nlwlevel_dict, NLWlevel,
                       make_nlw_row(1, "  Bloodbath "))
        assert Nlw.getlevelbyname(query) == [lv]

    def test_getlevelbyname_no_match(self) -> None:
        self._put(nlwapi.nlwlevels, nlwapi.nlwlevel_dict, NLWlevel, make_nlw_row(1, "A"))
        assert Nlw.getlevelbyname("B") == []

    def test_getlevelbyname_skips_nameless_entries(self) -> None:
        """表里混进 None / 空名字的条目不能把整次查询带崩"""
        lv = self._put(nlwapi.nlwlevels, nlwapi.nlwlevel_dict, NLWlevel, make_nlw_row(1, "A"))
        nlwapi.nlwlevels.append(None)
        nlwapi.nlwlevels.append(NLWlevel(make_nlw_row(2, "")))
        assert Nlw.getlevelbyname("a") == [lv]


# ==========================================================================
# platapi
# ==========================================================================
@pytest.fixture
def plat_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    """给 platapi 的六个模块级全局上个保险。

    fetch() 是用 global 重新赋值的，测完必须还原，否则会污染同进程里
    其他人的用例（Platapi 的静态方法读的就是这些全局）。
    monkeypatch 记录的是当前值，teardown 时原样写回。
    """
    for name in (
        "platdata", "platdata_entries", "platdata_main_entries",
        "platdata_derived_entries", "platdata_by_id", "platdata_by_name",
    ):
        monkeypatch.setattr(platapi, name, getattr(platapi, name))


@pytest.fixture
def write_plat(tmp_path: Path) -> Any:
    """把 plat 数据写进 tmp_path，返回文件路径"""

    def _write(rows: list[dict[str, Any]], name: str = "plat.json") -> Path:
        path = tmp_path / name
        path.write_text(
            json.dumps({"timestamp": 0, "levels": rows}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    return _write


class TestPlatInfoFromDict:
    def test_basic_parse(self) -> None:
        info = PlatInfo.from_dict(make_plat_row())
        assert info.id == "111"
        assert info.name == "Plat A"
        assert info.tier == "9 - CRUEL"
        assert info.tpl == "100"
        assert info.pemonlist == "41"
        assert info.enjoyment == 8.5
        assert info.tags == ["Deathless", "Precision"]
        assert info.is_main is True

    def test_values_are_stringified_and_stripped(self) -> None:
        info = PlatInfo.from_dict(make_plat_row(level_id=222, name="  Spaced  ", weight=100))
        assert info.id == "222"
        assert info.name == "Spaced"
        assert info.weight == "100"

    def test_none_stays_none(self) -> None:
        info = PlatInfo.from_dict(make_plat_row(tier=None, creator=None, video=None, section=None))
        assert info.tier is None
        assert info.creator is None
        assert info.video is None
        assert info.section is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(8.5, 8.5), ("7", 7.0), ("7.25", 7.25), (0, 0.0), (None, None),
         ("", None), ("n/a", None), ([], None)],
    )
    def test_enjoyment_coercion(self, raw: Any, expected: Optional[float]) -> None:
        """能转 float 就转，转不动就 None（不能抛，也不能留下字符串）"""
        info = PlatInfo.from_dict(make_plat_row(enjoyment=raw))
        assert info.enjoyment == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (["A", "B"], ["A", "B"]),
            ([" A ", "B "], ["A", "B"]),
            (["---"], []),              # 表格里的占位符，等于没有 tag
            (["---", "A"], ["---", "A"]),  # 只有整列就是 ["---"] 才当空
            ([], []),
            (["A", None, "B"], ["A", "B"]),
            ("notalist", []),
            (None, []),
        ],
    )
    def test_tags_normalization(self, raw: Any, expected: list[str]) -> None:
        assert PlatInfo.from_dict(make_plat_row(tags=raw)).tags == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(["X"], ["X"]), ([" X "], ["X"]), ([None], []), ("nope", []), (None, [])],
    )
    def test_derived_levels_normalization(self, raw: Any, expected: list[str]) -> None:
        assert PlatInfo.from_dict(make_plat_row(derived_levels=raw)).derived_levels == expected

    @pytest.mark.parametrize("field", ["tpl", "pemonlist"])
    def test_dash_placeholder_becomes_none(self, field: str) -> None:
        """表格里的 "-" 表示没排名，要变成 None"""
        info = PlatInfo.from_dict(make_plat_row(**{field: "-"}))
        assert getattr(info, field) is None

    def test_dash_placeholder_mutates_the_input_dict(self) -> None:
        """注意：from_dict 会就地改调用方传进来的 dict（tpl/pemonlist 的 "-"）。

        看着像是个副作用 bug —— 解析函数不该改输入。现状先钉住。
        """
        row = make_plat_row(tpl="-", pemonlist="-")
        PlatInfo.from_dict(row)
        assert row["tpl"] is None
        assert row["pemonlist"] is None

    def test_derived_entry_is_not_main(self) -> None:
        info = PlatInfo.from_dict(make_plat_row(derived_from="Plat A"))
        assert info.is_main is False
        assert info.derived_from == "Plat A"

    def test_empty_string_derived_from_counts_as_derived(self) -> None:
        """is_main 判的是 `derived_from is None`，空串会被当成「有母关」。

        表格里真出现空串的话，这一条会从 by_id 里消失（只有 main 进 by_id）。
        看起来不太对，这里记录现状。
        """
        info = PlatInfo.from_dict(make_plat_row(derived_from=""))
        assert info.derived_from == ""
        assert info.is_main is False

    def test_missing_id_and_name_become_empty_string(self) -> None:
        info = PlatInfo.from_dict({})
        assert info.id == ""
        assert info.name == ""
        assert info.is_main is True

    def test_to_dict_roundtrip(self) -> None:
        row = make_plat_row()
        info = PlatInfo.from_dict(row)
        dumped = info.to_dict()
        assert dumped["id"] == "111"
        assert set(dumped) == {
            "id", "name", "tier", "tpl", "pemonlist", "creator", "tags",
            "enjoyment", "video", "weight", "section", "derived_from", "derived_levels",
        }
        # is_main 是算出来的，不进 to_dict
        assert "is_main" not in dumped


class TestPlatData:
    def test_load_splits_main_and_derived(self, write_plat: Any) -> None:
        path = write_plat([
            make_plat_row("1", "Main"),
            make_plat_row("2", "Main (Derived)", derived_from="Main"),
        ])
        data = PlatData(cache_file=str(path))
        assert [e.id for e in data.entries] == ["1", "2"]
        assert [e.id for e in data.main_entries] == ["1"]
        assert [e.id for e in data.derived_entries] == ["2"]

    def test_by_id_only_contains_main_entries(self, write_plat: Any) -> None:
        """派生关卡不进 by_id —— 用派生关卡的 id 查是查不到的"""
        path = write_plat([
            make_plat_row("1", "Main"),
            make_plat_row("2", "Derived", derived_from="Main"),
        ])
        data = PlatData(cache_file=str(path))
        assert set(data.by_id) == {"1"}
        assert data.getlevelbyid("2") is None

    def test_by_name_contains_derived_entries_too(self, write_plat: Any) -> None:
        path = write_plat([
            make_plat_row("1", "Main"),
            make_plat_row("2", "Derived", derived_from="Main"),
        ])
        data = PlatData(cache_file=str(path))
        assert data.getlevelbyname("derived") is not None
        assert data.getlevelbyname("derived").id == "2"

    def test_duplicate_id_first_wins(self, write_plat: Any) -> None:
        path = write_plat([
            make_plat_row("1", "First"),
            make_plat_row("1", "Second"),
        ])
        data = PlatData(cache_file=str(path))
        assert data.getlevelbyid("1").name == "First"

    def test_duplicate_name_first_wins(self, write_plat: Any) -> None:
        path = write_plat([make_plat_row("1", "Dup"), make_plat_row("2", "Dup")])
        data = PlatData(cache_file=str(path))
        assert data.getlevelbyname("dup").id == "1"

    def test_name_registered_under_both_exact_and_lowercase(self, write_plat: Any) -> None:
        path = write_plat([make_plat_row("1", "MiXeD")])
        data = PlatData(cache_file=str(path))
        assert set(data.by_name) == {"MiXeD", "mixed"}

    def test_lowercase_collision_shadows_later_entry(self, write_plat: Any) -> None:
        """先来的 "ABC" 顺手占了 "abc" 这个键，后面真叫 "abc" 的那条就一个键都注册不上。

        查 "abc" 拿到的是第一条。看起来是个隐患，先钉住现状。
        """
        path = write_plat([make_plat_row("1", "ABC"), make_plat_row("2", "abc")])
        data = PlatData(cache_file=str(path))
        assert data.getlevelbyname("abc").id == "1"
        assert set(data.by_name) == {"ABC", "abc"}

    def test_entries_without_id_are_dropped(self, write_plat: Any) -> None:
        path = write_plat([make_plat_row("1", "Kept"), make_plat_row("", "NoId")])
        data = PlatData(cache_file=str(path))
        assert [e.name for e in data.entries] == ["Kept"]

    def test_non_dict_rows_are_skipped(self, write_plat: Any) -> None:
        path = write_plat([make_plat_row("1", "Kept"), "junk", 42, None])
        data = PlatData(cache_file=str(path))
        assert [e.name for e in data.entries] == ["Kept"]

    def test_missing_file_gives_empty_data(self, tmp_path: Path) -> None:
        data = PlatData(cache_file=str(tmp_path / "does_not_exist.json"))
        assert data.entries == []
        assert data.by_id == {}
        assert data.getlevelbyid("1") is None

    def test_broken_json_gives_empty_data(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{oops", encoding="utf-8")
        assert PlatData(cache_file=str(path)).entries == []

    def test_missing_levels_key(self, tmp_path: Path) -> None:
        path = tmp_path / "nolevels.json"
        path.write_text(json.dumps({"timestamp": 0}), encoding="utf-8")
        assert PlatData(cache_file=str(path)).entries == []

    def test_getlevelbyid_strips_and_stringifies(self, write_plat: Any) -> None:
        path = write_plat([make_plat_row("12345", "X")])
        data = PlatData(cache_file=str(path))
        assert data.getlevelbyid("  12345  ") is not None
        assert data.getlevelbyid(12345) is not None  # 内部 str() 过

    def test_getlevelbyname_strips_and_lowercases(self, write_plat: Any) -> None:
        path = write_plat([make_plat_row("1", "Some Level")])
        data = PlatData(cache_file=str(path))
        assert data.getlevelbyname("  SOME LEVEL ") is not None
        assert data.getlevelbyname("some level") is not None
        assert data.getlevelbyname("other") is None


class TestPlatapiFacade:
    def test_fetch_refreshes_every_global(
        self, plat_globals: None, write_plat: Any
    ) -> None:
        """fetch 必须把六个全局一起换掉 —— 漏掉 by_name 会让 getderivedlevels 用旧表"""
        path = write_plat([
            make_plat_row("1", "Main", derived_levels=["Main II"]),
            make_plat_row("2", "Main II", derived_from="Main"),
        ])
        entries = platapi.fetch(cache_file=str(path))
        assert [e.id for e in entries] == ["1", "2"]
        assert platapi.platdata_entries is entries
        assert platapi.platdata_entries is platapi.platdata.entries
        assert platapi.platdata_main_entries is platapi.platdata.main_entries
        assert platapi.platdata_derived_entries is platapi.platdata.derived_entries
        assert platapi.platdata_by_id is platapi.platdata.by_id
        assert platapi.platdata_by_name is platapi.platdata.by_name

    def test_facade_lookups(self, plat_globals: None, write_plat: Any) -> None:
        path = write_plat([make_plat_row("999", "Facade Level")])
        platapi.fetch(cache_file=str(path))
        assert Platapi.getlevelbyid("999").name == "Facade Level"
        assert Platapi.getlevelbyid(999).name == "Facade Level"
        assert Platapi.getlevelbyname("facade level").id == "999"
        assert Platapi.getlevelbyname("nope") is None

    @pytest.mark.parametrize("falsy", [None, "", 0])
    def test_getlevelbyid_short_circuits_on_falsy(
        self, plat_globals: None, write_plat: Any, falsy: Any
    ) -> None:
        """`if not level_id` 把 0 也挡了 —— id 真是 "0" 的关卡查不出来。

        现有数据里没有 id 为 0 的关卡，所以只是个隐患，不是当下的故障。
        """
        path = write_plat([make_plat_row("0", "Zero Level")])
        platapi.fetch(cache_file=str(path))
        assert Platapi.getlevelbyid(falsy) is None
        # 但用字符串 "0" 是能查到的，说明数据本身在表里
        assert Platapi.getlevelbyid("0").name == "Zero Level"

    def test_getderivedlevels(self, plat_globals: None, write_plat: Any) -> None:
        path = write_plat([
            make_plat_row("1", "Main", derived_levels=["Main II", "Main III"]),
            make_plat_row("2", "Main II", derived_from="Main"),
            make_plat_row("3", "Main III", derived_from="Main"),
        ])
        platapi.fetch(cache_file=str(path))
        main = Platapi.getlevelbyid("1")
        derived = Platapi.getderivedlevels(main)
        assert [d.id for d in derived] == ["2", "3"]

    def test_getderivedlevels_empty(self, plat_globals: None, write_plat: Any) -> None:
        path = write_plat([make_plat_row("1", "Main")])
        platapi.fetch(cache_file=str(path))
        assert Platapi.getderivedlevels(Platapi.getlevelbyid("1")) == []

    def test_getderivedlevels_raises_on_unknown_name(
        self, plat_globals: None, write_plat: Any
    ) -> None:
        """derived_levels 里写了一个表里没有的名字就直接 KeyError。

        调用方（draw.py:734 `Platapi.getderivedlevels(plat_info)[0]`）没接这个异常，
        数据一旦对不上，整张图就出不来。看起来该用 .get 过滤掉才对。
        """
        path = write_plat([make_plat_row("1", "Main", derived_levels=["Does Not Exist"])])
        platapi.fetch(cache_file=str(path))
        with pytest.raises(KeyError):
            Platapi.getderivedlevels(Platapi.getlevelbyid("1"))

    def test_getderivedlevels_matches_lowercased_name(
        self, plat_globals: None, write_plat: Any
    ) -> None:
        """by_name 里同时存了原名和小写名两个键，所以派生名大小写不一致时也能命中"""
        path = write_plat([
            make_plat_row("1", "Main", derived_levels=["main ii"]),
            make_plat_row("2", "Main II", derived_from="Main"),
        ])
        platapi.fetch(cache_file=str(path))
        assert [d.id for d in Platapi.getderivedlevels(Platapi.getlevelbyid("1"))] == ["2"]

    def test_default_cache_path_is_the_plugin_data_dir(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """不传 cache_file 时默认落在 <插件目录>/data/plat_combined.json

        _fetch 打掉，避免真去读那份 800KB 的缓存（而且它在干净 clone 上并不存在）。
        """
        monkeypatch.setattr(PlatData, "_fetch", lambda self: [])
        data = PlatData()
        assert Path(data.cache_file) == (
            Path(platapi.__file__).parent / "data" / "plat_combined.json"
        )

    def test_reload_refetches_from_the_default_path(
        self, plat_globals: None, write_plat: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """reload() -> fetch() 不带参数 -> PlatData() 不带参数（= 默认路径），并换掉六个全局

        以前这个用例直接 `platapi.reload()` 读仓库里真实的 plat_combined.json，
        但那个文件是 .gitignore 掉的：干净 clone / CI 上根本不存在，
        reload 会静默加载 0 条，两条断言（isinstance list + 身份比较）照样通过 ——
        等于在 CI 上什么都没测。这里把 PlatData 的 __init__ 换成一个记账版：
        既能证明 reload 传下去的确实是 None（默认路径），又能确定性地断言
        六个模块级全局都指向了新对象。
        """
        path = write_plat([make_plat_row("777", "Reloaded Level")])
        seen: list[Optional[str]] = []
        real_init = PlatData.__init__

        def spy_init(self: PlatData, cache_file: Optional[str] = None) -> None:
            seen.append(cache_file)
            real_init(self, cache_file=str(path))

        monkeypatch.setattr(PlatData, "__init__", spy_init)
        platapi.reload()

        assert seen == [None]  # reload 没有透传任何路径 = 走默认那份
        assert [e.id for e in platapi.platdata_entries] == ["777"]
        assert platapi.platdata_entries is platapi.platdata.entries
        assert platapi.platdata_main_entries is platapi.platdata.main_entries
        assert platapi.platdata_derived_entries is platapi.platdata.derived_entries
        assert platapi.platdata_by_id is platapi.platdata.by_id
        assert platapi.platdata_by_name is platapi.platdata.by_name


# ==========================================================================
# dailydemon
# ==========================================================================
FIXED_DAY = date(2026, 7, 26)


class FakeGddl:
    """替掉 dailydemon 里的 Gddl，全部离线。"""

    def __init__(
        self,
        search_payload: Optional[dict[str, Any]] = None,
        by_index: Any = None,
        by_id: Any = None,
    ) -> None:
        self.search_payload = search_payload
        self._by_index = by_index
        self._by_id = by_id
        self.search_calls: list[dict[str, Any]] = []
        self.index_calls: list[int] = []
        self.id_calls: list[Any] = []

    def searchlevels(self, **kwargs: Any) -> Optional[dict[str, Any]]:
        self.search_calls.append(kwargs)
        return self.search_payload

    def getlevelbyindex(self, index: int, **filters: Any) -> Any:
        self.index_calls.append(index)
        if callable(self._by_index):
            return self._by_index(index)
        return self._by_index

    def getlevelbyid(self, level_id: Any, *a: Any, **k: Any) -> Any:
        self.id_calls.append(level_id)
        if callable(self._by_id):
            return self._by_id(level_id)
        return self._by_id


def make_gddl_level(level_id: int, name: str = "Daily") -> GDDLLevel:
    return GDDLLevel(make_level_payload(ID=level_id, meta={"ID": level_id, "Name": name}))


class TestDailyDemonPure:
    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (date(2026, 7, 26), 20260726),
            (date(2000, 1, 1), 20000101),
            (date(1999, 12, 31), 19991231),
            (date(2026, 1, 9), 20260109),
        ],
    )
    def test_today_seed(self, day: date, expected: int) -> None:
        assert dailydemon.today_seed(day) == expected

    def test_key_format(self) -> None:
        assert dailydemon._key(FIXED_DAY) == "dailydemon_2026-07-26"
        assert dailydemon._key(date(2026, 1, 2)) == "dailydemon_2026-01-02"

    def test_today_passthrough(self) -> None:
        assert dailydemon._today(FIXED_DAY) is FIXED_DAY

    def test_pick_index_is_in_range_and_deterministic(self) -> None:
        first = dailydemon.pick_index(2380, FIXED_DAY)
        second = dailydemon.pick_index(2380, FIXED_DAY)
        assert first == second
        assert 0 <= first < 2380

    def test_pick_index_ignores_global_random_state(self, seeded_random: Any) -> None:
        """种子来自日期，不是全局 random —— 别人 seed 过也不能影响结果"""
        seeded_random(1)
        a = dailydemon.pick_index(1000, FIXED_DAY)
        seeded_random(999999)
        b = dailydemon.pick_index(1000, FIXED_DAY)
        assert a == b
        # 和用日期种子手算的一致
        assert a == random.Random(20260726).randrange(1000)

    def test_pick_index_changes_with_the_day(self) -> None:
        days = [date(2026, 7, d) for d in range(1, 21)]
        picks = {dailydemon.pick_index(10000, d) for d in days}
        # 不同日期不保证两两不同，但 20 天全撞成一个值只能是逻辑坏了
        assert len(picks) > 1

    def test_pick_index_total_one(self) -> None:
        assert dailydemon.pick_index(1, FIXED_DAY) == 0

    def test_filters_are_the_documented_conditions(self) -> None:
        assert dailydemon.FILTERS == {
            "minRating": 1, "maxRating": 9, "minEnjoyment": 7, "minSubmissionCount": 10,
        }
        assert dailydemon.describe_conditions() == "tier 1-9、enjoyment 7+、提交数 10+"

    def test_keep_seconds_is_two_days(self) -> None:
        assert dailydemon.KEEP_SECONDS == 2 * 24 * 3600


class TestDailyDemonStorage:
    def test_get_recent_empty(self, patch_storage: Any) -> None:
        patch_storage(dailydemon)
        assert dailydemon.get_recent() == []

    @pytest.mark.parametrize("stored", ["notalist", 5, {"a": 1}, None])
    def test_get_recent_rejects_non_list(self, patch_storage: Any, stored: Any) -> None:
        patch_storage(dailydemon, initial={dailydemon.RECENT_KEY: stored})
        assert dailydemon.get_recent() == []

    def test_get_recent_filters_and_casts(self, patch_storage: Any) -> None:
        """字符串数字要转成 int，负号允许，小数和乱码丢掉"""
        patch_storage(dailydemon, initial={
            dailydemon.RECENT_KEY: [1, "2", "-3", "3.5", "abc", "", None],
        })
        assert dailydemon.get_recent() == [1, 2, -3]

    def test_remember_appends(self, patch_storage: Any) -> None:
        r = patch_storage(dailydemon)
        dailydemon.remember(10)
        dailydemon.remember(20)
        assert r.get(dailydemon.RECENT_KEY) == [10, 20]

    def test_remember_moves_existing_to_the_end(self, patch_storage: Any) -> None:
        r = patch_storage(dailydemon, initial={dailydemon.RECENT_KEY: [1, 2, 3]})
        dailydemon.remember(2)
        assert r.get(dailydemon.RECENT_KEY) == [1, 3, 2]

    def test_remember_keeps_only_last_30(self, patch_storage: Any) -> None:
        r = patch_storage(dailydemon, initial={
            dailydemon.RECENT_KEY: list(range(dailydemon.RECENT_KEEP)),
        })
        dailydemon.remember(1000)
        kept = r.get(dailydemon.RECENT_KEY)
        assert len(kept) == dailydemon.RECENT_KEEP == 30
        assert kept[-1] == 1000
        assert kept[0] == 1  # 最老的那个（0）被挤掉了

    def test_get_cached_id_missing(self, patch_storage: Any) -> None:
        patch_storage(dailydemon)
        assert dailydemon.get_cached_id(FIXED_DAY) is None

    def test_get_cached_id_parses_string(self, patch_storage: Any) -> None:
        patch_storage(dailydemon, initial={"dailydemon_2026-07-26": "12345"})
        assert dailydemon.get_cached_id(FIXED_DAY) == 12345

    @pytest.mark.parametrize("garbage", ["abc", "12.5", [1], {}])
    def test_get_cached_id_garbage_is_treated_as_missing(
        self, patch_storage: Any, garbage: Any
    ) -> None:
        patch_storage(dailydemon, initial={"dailydemon_2026-07-26": garbage})
        assert dailydemon.get_cached_id(FIXED_DAY) is None

    def test_get_cached_id_is_per_day(self, patch_storage: Any) -> None:
        patch_storage(dailydemon, initial={"dailydemon_2026-07-26": "1"})
        assert dailydemon.get_cached_id(date(2026, 7, 27)) is None


class TestDailyDemonFlow:
    def test_cached_day_short_circuits(
        self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """当天已经存过就直接按 id 取，一次搜索都不发"""
        patch_storage(dailydemon, initial={"dailydemon_2026-07-26": "555"})
        level = make_gddl_level(555)
        fake = FakeGddl(by_id=level)
        monkeypatch.setattr(dailydemon, "Gddl", fake)

        got, total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got is level
        assert total == 0
        assert err == ""
        assert fake.id_calls == [555]
        assert fake.search_calls == []

    def test_cached_level_gone_falls_through_to_a_new_pick(
        self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        r = patch_storage(dailydemon, initial={"dailydemon_2026-07-26": "555"})
        picked = make_gddl_level(777)
        fake = FakeGddl(search_payload={"total": 100}, by_index=picked, by_id=None)
        monkeypatch.setattr(dailydemon, "Gddl", fake)

        got, total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got is picked
        assert total == 100
        assert err == ""
        assert r.get("dailydemon_2026-07-26") == "777"

    def test_search_failure(self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_storage(dailydemon)
        monkeypatch.setattr(dailydemon, "Gddl", FakeGddl(search_payload=None))
        assert dailydemon.get_daily_demon(FIXED_DAY) == (None, 0, "GDDL 那边没响应，等会再试试")

    @pytest.mark.parametrize("payload", [{}, {"total": 0}])
    def test_zero_total(
        self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
    ) -> None:
        patch_storage(dailydemon)
        monkeypatch.setattr(dailydemon, "Gddl", FakeGddl(search_payload=payload))
        got, total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got is None
        assert total == 0
        assert "没有符合条件的关卡" in err

    def test_search_uses_the_fixed_filters(
        self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_storage(dailydemon)
        fake = FakeGddl(search_payload={"total": 10}, by_index=make_gddl_level(1))
        monkeypatch.setattr(dailydemon, "Gddl", fake)
        dailydemon.get_daily_demon(FIXED_DAY)
        assert fake.search_calls[0] == {
            "page": 0, "limit": 1, "sort": "ID", **dailydemon.FILTERS
        }

    def test_pick_is_stored_with_ttl_and_remembered(
        self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        r = patch_storage(dailydemon)
        level = make_gddl_level(31415, "Chosen")
        monkeypatch.setattr(
            dailydemon, "Gddl", FakeGddl(search_payload={"total": 500}, by_index=level)
        )
        got, total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got is level
        assert (total, err) == (500, "")
        assert r.get("dailydemon_2026-07-26") == "31415"
        # 存两天，允许几秒误差
        assert dailydemon.KEEP_SECONDS - 5 <= r.ttl("dailydemon_2026-07-26") <= dailydemon.KEEP_SECONDS
        assert r.get(dailydemon.RECENT_KEY) == [31415]

    def test_first_candidate_not_recent_stops_immediately(
        self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_storage(dailydemon)
        fake = FakeGddl(search_payload={"total": 500}, by_index=make_gddl_level(1))
        monkeypatch.setattr(dailydemon, "Gddl", fake)
        dailydemon.get_daily_demon(FIXED_DAY)
        assert len(fake.index_calls) == 1

    def test_recent_collision_rerolls(
        self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """第一次挑到最近推过的就再挑，直到挑出没推过的为止"""
        patch_storage(dailydemon, initial={dailydemon.RECENT_KEY: [100]})
        fresh = make_gddl_level(200)
        calls = {"n": 0}

        def _by_index(_index: int) -> GDDLLevel:
            calls["n"] += 1
            return make_gddl_level(100) if calls["n"] == 1 else fresh

        fake = FakeGddl(search_payload={"total": 500}, by_index=_by_index)
        monkeypatch.setattr(dailydemon, "Gddl", fake)
        got, _total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got is fresh
        assert err == ""
        assert len(fake.index_calls) == 2

    def test_gives_up_after_max_reroll_and_uses_the_last_one(
        self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """池子里只有一关且刚推过：轮满 MAX_REROLL 次之后还是用它，不能死循环"""
        patch_storage(dailydemon, initial={dailydemon.RECENT_KEY: [100]})
        repeated = make_gddl_level(100)
        fake = FakeGddl(search_payload={"total": 1}, by_index=repeated)
        monkeypatch.setattr(dailydemon, "Gddl", fake)
        got, _total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got is repeated
        assert err == ""
        assert len(fake.index_calls) == dailydemon.MAX_REROLL == 5
        # total=1 的时候每次 randrange(1) 都是 0
        assert fake.index_calls == [0] * 5

    def test_all_lookups_fail(
        self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        r = patch_storage(dailydemon)
        fake = FakeGddl(search_payload={"total": 500}, by_index=None)
        monkeypatch.setattr(dailydemon, "Gddl", fake)
        got, total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got is None
        assert total == 500
        assert err == "取今日关卡失败，等会再试试"
        assert len(fake.index_calls) == dailydemon.MAX_REROLL
        # 失败就不该往存储里写
        assert r.get("dailydemon_2026-07-26") is None
        assert r.get(dailydemon.RECENT_KEY) is None

    def test_two_different_days_pick_independently(
        self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """按天存 key，昨天的结果不会顶掉今天的"""
        r = patch_storage(dailydemon)
        monkeypatch.setattr(
            dailydemon, "Gddl",
            FakeGddl(search_payload={"total": 500}, by_index=lambda i: make_gddl_level(i + 1)),
        )
        dailydemon.get_daily_demon(date(2026, 7, 26))
        dailydemon.get_daily_demon(date(2026, 7, 27))
        assert r.get("dailydemon_2026-07-26") is not None
        assert r.get("dailydemon_2026-07-27") is not None

    def test_same_day_twice_is_stable(
        self, patch_storage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """同一天调两次必须是同一关 —— 这就是整个模块存在的理由。

        第二次走的是缓存分支，所以哪怕 GDDL 那边的 total 变了也不受影响。
        """
        patch_storage(dailydemon)
        first_level = make_gddl_level(4242)
        levels = {4242: first_level}
        fake = FakeGddl(
            search_payload={"total": 500},
            by_index=lambda _i: first_level,
            by_id=lambda lid: levels.get(int(lid)),
        )
        monkeypatch.setattr(dailydemon, "Gddl", fake)
        a, _t1, _e1 = dailydemon.get_daily_demon(FIXED_DAY)
        # 换个 total 模拟 GDDL 数据变动
        fake.search_payload = {"total": 999}
        b, total_b, _e2 = dailydemon.get_daily_demon(FIXED_DAY)
        assert a is b
        assert total_b == 0
        assert len(fake.search_calls) == 1


# ==========================================================================
# icons
# ==========================================================================
def make_gduser(**over: Any) -> GDUser:
    """GDUser() 会把所有字段置 None，这里只填 icons 用得到的几个"""
    user = GDUser()
    user.user_name = "TestPlayer"
    user.color = 3
    user.color2 = 12
    user.acc_glow = 0
    for form in icons.FORMS:
        setattr(user, form.attr, 1)
    for key, value in over.items():
        setattr(user, key, value)
    return user


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """把 icons 重试之间的 asyncio.sleep 换成立即返回，记下等了多久。

    真睡 0.5 秒既慢又给不出任何额外信息。
    """
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake(delay: float, *a: Any, **k: Any) -> Any:
        sleeps.append(delay)
        return await real_sleep(0)

    monkeypatch.setattr(icons.asyncio, "sleep", _fake)
    return sleeps


class TestIconForms:
    def test_nine_gamemodes_in_a_fixed_order(self) -> None:
        assert [f.key for f in icons.FORMS] == [
            "cube", "ship", "ball", "ufo", "wave",
            "robot", "spider", "swing", "jetpack",
        ]

    def test_api_type_equals_key(self) -> None:
        assert all(f.api_type == f.key for f in icons.FORMS)

    def test_every_form_attr_is_a_real_gduser_field(self) -> None:
        """Form.attr 得对得上 GDUser 的字段名，拼错了只会静默取默认值 1"""
        gduser_fields = {attr for attr, _ in GDUser.FIELD_MAP.values()}
        for form in icons.FORMS:
            assert form.attr in gduser_fields, form

    def test_form_by_key_is_complete(self) -> None:
        assert set(icons.FORM_BY_KEY) == {f.key for f in icons.FORMS}

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("cube", "cube"), ("CUBE", "cube"), ("CuBe", "cube"),
            ("bird", "ufo"), ("ufo", "ufo"), ("UFO", "ufo"), ("飞碟", "ufo"),
            ("dart", "wave"), ("wave", "wave"), ("波", "wave"),
            ("方块", "cube"), ("船", "ship"), ("球", "ball"),
            ("机器人", "robot"), ("蜘蛛", "spider"),
            ("秋千", "swing"), ("喷气背包", "jetpack"),
            ("jetpack", "jetpack"),
        ],
    )
    def test_resolve_form(self, name: str, expected: str) -> None:
        form = icons.resolve_form(name)
        assert form is not None
        assert form.key == expected

    @pytest.mark.parametrize("name", ["", "nope", "cubes", "cu be", "立方体"])
    def test_resolve_form_unknown(self, name: str) -> None:
        assert icons.resolve_form(name) is None

    def test_default_form_resolves(self) -> None:
        assert icons.resolve_form(icons.DEFAULT_FORM) is icons.FORM_BY_KEY["cube"]

    def test_form_names(self) -> None:
        assert icons.form_names() == (
            "cube / ship / ball / ufo / wave / robot / spider / swing / jetpack"
        )

    def test_every_alias_target_exists(self) -> None:
        for alias, target in icons.ALIASES.items():
            assert target in icons.FORM_BY_KEY, alias


class TestIconFetch:
    async def test_fetch_one_builds_params(self, stub_httpx: Any) -> None:
        stub_httpx.get("gdicon.oat.zone/icon.png", httpx.Response(200, content=png_bytes()))
        user = make_gduser(acc_ship=47, color=9, color2=15, acc_glow=1)
        img = await icons.fetch_one(user, icons.FORM_BY_KEY["ship"])
        assert img is not None
        assert img.mode == "RGBA"
        assert img.size == (60, 60)
        params = dict(stub_httpx.requests[-1].url.params)
        assert params == {"type": "ship", "value": "47", "color1": "9",
                          "color2": "15", "glow": "true"}

    async def test_glow_off_omits_the_param(self, stub_httpx: Any) -> None:
        stub_httpx.get("gdicon.oat.zone/icon.png", httpx.Response(200, content=png_bytes()))
        await icons.fetch_one(make_gduser(acc_glow=0), icons.FORM_BY_KEY["cube"])
        assert "glow" not in dict(stub_httpx.requests[-1].url.params)

    @pytest.mark.parametrize(
        ("icon_id", "expected"),
        [(1, "1"), (2, "2"), (98, "98"), (0, "1"), (None, "1"), (-5, "1")],
    )
    async def test_icon_id_floor_is_1(
        self, stub_httpx: Any, icon_id: Any, expected: str
    ) -> None:
        """0 / None / 负数都得变成 1，接口不认这些值"""
        stub_httpx.get("gdicon.oat.zone/icon.png", httpx.Response(200, content=png_bytes()))
        await icons.fetch_one(make_gduser(acc_icon=icon_id), icons.FORM_BY_KEY["cube"])
        assert dict(stub_httpx.requests[-1].url.params)["value"] == expected

    async def test_none_colors_become_zero(self, stub_httpx: Any) -> None:
        stub_httpx.get("gdicon.oat.zone/icon.png", httpx.Response(200, content=png_bytes()))
        await icons.fetch_one(make_gduser(color=None, color2=None), icons.FORM_BY_KEY["cube"])
        params = dict(stub_httpx.requests[-1].url.params)
        assert params["color1"] == "0"
        assert params["color2"] == "0"

    async def test_missing_attr_falls_back_to_1(self, stub_httpx: Any) -> None:
        """GDUser 上没有这个属性时 getattr 的默认值兜底"""
        stub_httpx.get("gdicon.oat.zone/icon.png", httpx.Response(200, content=png_bytes()))
        user = make_gduser()
        del user.acc_swing
        await icons.fetch_one(user, icons.FORM_BY_KEY["swing"])
        assert dict(stub_httpx.requests[-1].url.params)["value"] == "1"

    async def test_http_error_retries_then_gives_up(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        stub_httpx.get("gdicon.oat.zone/icon.png", httpx.Response(500))
        img = await icons.fetch_one(make_gduser(), icons.FORM_BY_KEY["cube"])
        assert img is None
        assert len(stub_httpx.requests) == icons.ICON_RETRIES == 2
        assert no_sleep == [0.5]  # 只在两次之间睡一次

    async def test_transport_exception_is_swallowed(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        stub_httpx.get("gdicon.oat.zone/icon.png", httpx.ConnectError("down"))
        assert await icons.fetch_one(make_gduser(), icons.FORM_BY_KEY["cube"]) is None
        assert len(stub_httpx.requests) == icons.ICON_RETRIES

    async def test_second_attempt_can_succeed(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        state = {"n": 0}

        def _flaky(_request: httpx.Request) -> httpx.Response:
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(503)
            return httpx.Response(200, content=png_bytes((20, 20)))

        stub_httpx.get("gdicon.oat.zone/icon.png", _flaky)
        img = await icons.fetch_one(make_gduser(), icons.FORM_BY_KEY["cube"])
        assert img is not None
        assert img.size == (20, 20)

    async def test_empty_body_is_treated_as_a_failure(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        """200 但是空 body 也算失败，要重试"""
        stub_httpx.get("gdicon.oat.zone/icon.png", httpx.Response(200, content=b""))
        assert await icons.fetch_one(make_gduser(), icons.FORM_BY_KEY["cube"]) is None
        assert len(stub_httpx.requests) == icons.ICON_RETRIES

    async def test_undecodable_body_gives_up_immediately(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        """拿到了但不是图片：没必要重试，直接放弃"""
        stub_httpx.get("gdicon.oat.zone/icon.png", httpx.Response(200, content=b"not an image"))
        assert await icons.fetch_one(make_gduser(), icons.FORM_BY_KEY["cube"]) is None
        assert len(stub_httpx.requests) == 1
        assert no_sleep == []

    async def test_fetch_all_covers_every_form(self, stub_httpx: Any) -> None:
        """九个 gamemode 一起发；返回的顺序必须和 FORMS 一致。

        响应里用图片宽度编码 form 下标，反过来验证 zip 没错位。
        """
        index_by_type = {f.api_type: i for i, f in enumerate(icons.FORMS)}

        def _by_type(request: httpx.Request) -> httpx.Response:
            idx = index_by_type[request.url.params["type"]]
            return httpx.Response(200, content=png_bytes((10 + idx, 10)))

        stub_httpx.get("gdicon.oat.zone/icon.png", _by_type)
        user = make_gduser()
        for i, form in enumerate(icons.FORMS):
            setattr(user, form.attr, 100 + i)

        items = await icons.fetch_all(user)
        assert len(items) == len(icons.FORMS)
        assert [f for f, _ in items] == list(icons.FORMS)
        for i, (form, img) in enumerate(items):
            assert img is not None, form
            assert img.width == 10 + i
        # 每个 form 的 icon id 都取自它自己的 GDUser 字段
        sent = {
            p["type"]: p["value"]
            for p in (dict(r.url.params) for r in stub_httpx.requests)
        }
        assert sent == {f.api_type: str(100 + i) for i, f in enumerate(icons.FORMS)}

    async def test_fetch_all_keeps_none_for_failures(
        self, stub_httpx: Any, no_sleep: list[float]
    ) -> None:
        """某个 gamemode 挂了就那一格是 None，其余照常返回"""

        def _one_fails(request: httpx.Request) -> httpx.Response:
            if request.url.params["type"] == "spider":
                return httpx.Response(500)
            return httpx.Response(200, content=png_bytes((30, 30)))

        stub_httpx.get("gdicon.oat.zone/icon.png", _one_fails)
        items = await icons.fetch_all(make_gduser())
        by_key = {form.key: img for form, img in items}
        assert by_key["spider"] is None
        assert all(img is not None for key, img in by_key.items() if key != "spider")


class TestIconCompose:
    @pytest.mark.parametrize(
        ("count", "rows"),
        [(0, 0), (1, 1), (3, 1), (4, 2), (6, 2), (7, 3), (9, 3), (10, 4)],
    )
    def test_canvas_geometry(self, count: int, rows: int) -> None:
        """宽度固定三列，高度按行数长；行数是向上取整"""
        items = [(icons.FORMS[i % 9], None) for i in range(count)]
        sheet = icons.compose_sheet(make_gduser(), items)
        assert sheet.mode == "RGBA"
        assert sheet.size == (
            icons.PAD * 2 + icons.CELL * icons.GRID_COLS,
            icons.PAD * 2 + icons.TITLE_H + icons.CELL * rows,
        )

    def test_nine_icons_sheet(self) -> None:
        items = [(f, Image.new("RGBA", (60, 60), (0, 128, 255, 255))) for f in icons.FORMS]
        sheet = icons.compose_sheet(make_gduser(), items)
        assert sheet.size == (432, 486)

    def test_missing_icons_are_skipped(self) -> None:
        items: list[tuple[icons.Form, Optional[Image.Image]]] = [
            (icons.FORMS[0], Image.new("RGBA", (60, 60), (255, 255, 255, 255))),
            (icons.FORMS[1], None),
            (icons.FORMS[2], None),
        ]
        sheet = icons.compose_sheet(make_gduser(), items)
        assert sheet.size == (432, 222)

    def test_nameless_user_gets_placeholder(self) -> None:
        """user_name 是 None 时标题画 "?"，不能抛"""
        sheet = icons.compose_sheet(make_gduser(user_name=None), [])
        assert sheet.size == (432, 90)

    def test_oversized_icons_are_scaled_down(self) -> None:
        big = Image.new("RGBA", (512, 512), (1, 2, 3, 255))
        sheet = icons.compose_sheet(make_gduser(), [(icons.FORMS[0], big)])
        assert sheet.size == (432, 222)

    @pytest.mark.parametrize(
        ("size", "expected"),
        [
            ((60, 60), (60, 60)),        # 比框小，原样返回
            ((112, 112), (112, 112)),    # 正好等于框，也不动
            ((224, 224), (112, 112)),
            ((224, 112), (112, 56)),     # 非正方形按长边缩
            ((112, 224), (56, 112)),
            ((10000, 1), (112, 1)),      # 极端比例，高度被 max(1, ...) 兜住
        ],
    )
    def test_fit_scaling(self, size: tuple[int, int], expected: tuple[int, int]) -> None:
        fitted = icons._fit(Image.new("RGBA", size), icons.ICON_BOX)
        assert fitted.size == expected

    def test_fit_returns_the_same_object_when_no_scaling_needed(self) -> None:
        """不需要缩放时直接返回原对象（不复制）"""
        img = Image.new("RGBA", (50, 50))
        assert icons._fit(img, icons.ICON_BOX) is img
