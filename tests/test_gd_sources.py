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

import json
import random
import zipfile

# datetime 只在这里用来造固定日期，绝不取 now()
from datetime import date

# dailydemon 必须用 import_module 拿：gdlevelsearch/__init__.py:655 有一句
# `dailydemon = on_command("dailydemon")`，把同名子模块在包命名空间里盖掉了，
# `from ... import dailydemon` 拿到的是那个 Matcher，不是模块。
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageChops

from xiaozu_bot.plugins.gdlevelsearch import (
    gddl_store,
    gddlapi,
    iconrender,
    icons,
    nlwapi,
    paths,
    platapi,
)
from xiaozu_bot.plugins.gdlevelsearch.api.gdapi import GDUser
from xiaozu_bot.plugins.gdlevelsearch.api.gddlapi import (
    GDDL_LIMIT_MAX,
    GDDL_LIMIT_MIN,
    GDDL_PLAT_LENGTH,
    GDDL_SUBMISSION_LIMIT,
    Gddl,
    GDDLLevel,
    GDDLSearchEntry,
    LevelMeta,
    SongInfo,
    Submission,
    SubmissionPage,
)
from xiaozu_bot.plugins.gdlevelsearch.api.nlwapi import (
    HDSlevel,
    IDSlevel,
    Level,
    LWlevel,
    Nlw,
    NLWlevel,
)
from xiaozu_bot.plugins.gdlevelsearch.api.platapi import Platapi, PlatData, PlatInfo

dailydemon = import_module("xiaozu_bot.plugins.gdlevelsearch.commands.dailydemon")

# ==========================================================================
# 造数据的小工具
# ==========================================================================

GDDL_LEVEL_URL = "https://gdladder.com/api/levels/"
GDDL_SEARCH_URL = "https://gdladder.com/api/levels"


def make_song_payload(**over: Any) -> dict[str, Any]:
    """SongDTO"""
    payload = {
        "ID": -1,
        "Name": "Stereo Madness",
        "Author": "ForeverBound",
        "Size": 1.5,
    }
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
        "Rarity": 0,
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


def make_search_payload(**over: Any) -> dict[str, Any]:
    """The compact, lowercase DTO returned by the levels search endpoint."""
    payload = {
        "id": 1000,
        "rating": 20.5,
        "enjoyment": 7.5,
        "name": "Test Level",
        "difficulty": "Extreme",
        "rarity": 0,
        "publisherName": "Test Publisher",
        "songName": "Stereo Madness",
    }
    payload.update(over)
    return payload


def make_submission_payload(**over: Any) -> dict[str, Any]:
    """SubmissionDTO。上游改字段名时只改这一处，用例不用一条条跟着改。"""
    payload = {
        "ID": 7,
        "Rating": 21,
        "Enjoyment": 8.5,
        "RefreshRate": 240,
        "Device": "PC",
        "Proof": "https://youtu.be/p",
        "IsSolo": False,
        "Progress": 100,
        "Attempts": 1234,
        "DateAdded": "2026-01-02T03:04:05Z",
        "UserID": 42,
        "User": {"Name": "someone"},
        "SecondaryUser": {"Name": "partner"},
    }
    payload.update(over)
    return payload


# SubmissionDTO 的键 -> Submission 实例上的属性名。
# User / SecondaryUser 是嵌套结构（只取里面的 Name），不在这张表里。
SUBMISSION_FIELDS: dict[str, str] = {
    "ID": "id",
    "Rating": "rating",
    "Enjoyment": "enjoyment",
    "RefreshRate": "refresh_rate",
    "Device": "device",
    "Proof": "proof",
    "IsSolo": "is_solo",
    "Progress": "progress",
    "Attempts": "attempts",
    "DateAdded": "date_added",
    "UserID": "user_id",
}


def make_submission_page_payload(
    ids: tuple[int, ...] = (1, 2, 3), **over: Any
) -> dict[str, Any]:
    """/api/level/{id}/submissions 的一页，ids 决定这一页里有哪几条提交。"""
    payload = {
        "total": len(ids),
        "limit": GDDL_SUBMISSION_LIMIT,
        "page": 0,
        "data": [make_submission_payload(ID=i) for i in ids],
    }
    payload.update(over)
    return payload


def make_nlw_row(
    level_id: int = 1, name: str = "Alpha", **over: Any
) -> dict[str, Any]:
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


def make_plat_row(
    level_id: str = "111", name: str = "Plat A", **over: Any
) -> dict[str, Any]:
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


@pytest.fixture
def gddl_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use an isolated in-memory GDDL snapshot for fallback tests."""
    monkeypatch.setattr(gddl_store, "levels", [])
    monkeypatch.setattr(gddl_store, "by_id", {})
    monkeypatch.setattr(gddl_store, "_by_name", {})
    monkeypatch.setattr(gddl_store, "fetched_at", None)
    gddl_store._rebuild_indexes(
        [
            make_level_payload(ID=123, Meta=make_meta_payload(Name="Cached Level")),
            make_level_payload(ID=456, Rating=30, Enjoyment=9, SubmissionCount=20),
        ],
        "test",
    )


@pytest.fixture
def empty_gddl_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """提供一个完全空的 GDDL 本地快照，避免 local fallback 污染纯 HTTP 测试。"""
    monkeypatch.setattr(gddl_store, "levels", [])
    monkeypatch.setattr(gddl_store, "by_id", {})
    monkeypatch.setattr(gddl_store, "_by_name", {})
    monkeypatch.setattr(gddl_store, "fetched_at", None)


# ==========================================================================
# gddlapi —— 常量
# ==========================================================================
class TestGddlConstants:
    def test_constants_match_the_web_api(self) -> None:
        """这几个常量就是接口契约，改一个都要有人来改测试。

        网页上一页 10 条；limit 只认 1-30（超了直接 400）；
        Length 枚举里 6 = platformer；三张白名单是照着接口文档抄的。
        """
        assert GDDL_SUBMISSION_LIMIT == 10
        assert (GDDL_LIMIT_MIN, GDDL_LIMIT_MAX) == (1, 30)
        assert GDDL_PLAT_LENGTH == 6
        assert {
            "attempts",
            "dateAdded",
            "enjoyment",
            "rating",
            "progress",
            "refreshRate",
            "username",
        } == gddlapi.SUBMISSION_SORTS
        assert {"asc", "desc"} == gddlapi.SORT_DIRECTIONS
        assert {"all", "victors", "incomplete"} == gddlapi.PROGRESS_FILTERS


# ==========================================================================
# gddlapi —— 数据类
# ==========================================================================
class TestGddlModels:
    def test_song_info_only_keeps_three_fields(self) -> None:
        """SongDTO 有 Size，但 SongInfo 不收，to_dict 里不该冒出来"""
        song = SongInfo(make_song_payload(Size=99.0))
        assert (song.ID, song.Name, song.Author) == (
            -1,
            "Stereo Madness",
            "ForeverBound",
        )
        assert set(song.to_dict()) == {"ID", "Name", "Author"}

    def test_song_info_str(self) -> None:
        song = SongInfo(make_song_payload(ID=5, Name="N", Author="A"))
        assert str(song) == "ID: 5\nName: N\nAuthor: A"

    def test_song_info_requires_all_three_keys(self) -> None:
        """三个键全是硬取，缺任何一个都是 KeyError"""
        for missing in ("ID", "Name", "Author"):
            payload = make_song_payload()
            del payload[missing]
            with pytest.raises(KeyError, match=missing):
                SongInfo(payload)

    def test_level_meta_is_pemon_boundary(self) -> None:
        """只有 Length == 6 才算 plat，5（XL）和 7（不存在的值）都不算"""
        for length, expected in [
            (1, False),
            (4, False),
            (5, False),
            (GDDL_PLAT_LENGTH, True),
            (7, False),
        ]:
            meta = LevelMeta(make_meta_payload(Length=length))
            assert meta.is_pemon() is expected, length

    def test_level_meta_drops_publisher_and_uploaded_at(self) -> None:
        """__init__ 压根没读 PublisherID / UploadedAt，实例上就不该有 PublisherID。

        UploadedAt 因为类上有默认值 None 还能取到，PublisherID 只有类型注解，
        取它会 AttributeError —— 这是源码现状，不是笔误。
        """
        meta = LevelMeta(make_meta_payload())
        assert set(meta.to_dict()) == {
            "ID",
            "Name",
            "Description",
            "SongID",
            "Length",
            "IsTwoPlayer",
            "Difficulty",
            "Rarity",
            "seconds",
            "Song",
        }
        assert meta.UploadedAt is None
        assert meta.seconds is None
        assert not hasattr(meta, "PublisherID")

    def test_level_meta_preserves_rarity(self) -> None:
        meta = LevelMeta(make_meta_payload(Rarity=4))
        assert meta.Rarity == 4
        assert isinstance(meta.Rarity, int)

    def test_gddl_level_every_key_lands_on_the_attribute_of_the_same_name(self) -> None:
        """LevelDTO 的键名和属性名一一对应；number|null 的字段要原样保留 null 而不是变 0。

        照着 make_level_payload 的键走，DTO 加字段时这里自动跟着校验。
        """
        for payload in (
            make_level_payload(),
            make_level_payload(Rating=None, Enjoyment=None, Deviation=None),
        ):
            level = GDDLLevel(payload)
            for key, value in payload.items():
                if key == "Meta":
                    continue
                assert getattr(level, key) == value, key
            assert level.Meta.Song.Name == payload["Meta"]["Song"]["Name"]
            assert level.Tags == []

    def test_gddl_level_tags_default_and_passthrough(self) -> None:
        tags = [{"Name": "Timings", "Count": 3}]
        assert GDDLLevel(make_level_payload(), tags).Tags == tags
        assert GDDLLevel(make_level_payload(), []).Tags == []

    def test_gddl_level_is_pemon_delegates_to_meta(self) -> None:
        assert GDDLLevel(make_level_payload(meta={"Length": 6})).is_pemon() is True
        assert GDDLLevel(make_level_payload(meta={"Length": 5})).is_pemon() is False

    def test_gddl_level_missing_key_raises(self) -> None:
        """所有字段都是硬取，少任何一个都是 KeyError（调用方得自己接住）"""
        for missing in make_level_payload():
            payload = make_level_payload()
            del payload[missing]
            with pytest.raises(KeyError):
                GDDLLevel(payload)

    def test_search_entry_uses_the_compact_search_dto(self) -> None:
        entry = GDDLSearchEntry(make_search_payload(id=42, name="Compact"))
        assert entry.ID == 42
        assert entry.Name == "Compact"
        assert entry.Rating == 20.5
        assert entry.Enjoyment == 7.5
        assert entry.Difficulty == "Extreme"
        assert entry.Rarity == 0
        assert entry.PublisherName == "Test Publisher"
        assert entry.SongName == "Stereo Madness"
        assert entry.is_pemon() is False

    def test_search_entry_requires_lowercase_api_keys(self) -> None:
        payload = make_search_payload()
        del payload["name"]
        with pytest.raises(KeyError, match="name"):
            GDDLSearchEntry(payload)


class TestSubmission:
    def test_every_key_maps_to_its_attribute(self) -> None:
        """SubmissionDTO 的键按 SUBMISSION_FIELDS 落到属性上，嵌套的 User 只取 Name。

        IsSolo 在样例里是 False：`.get(key, True)` 的坑就在这，
        显式的 False 必须留住，不能被默认值顶掉。
        """
        payload = make_submission_payload()
        sub = Submission(payload)
        for key, attr in SUBMISSION_FIELDS.items():
            assert getattr(sub, attr) == payload[key], key
        assert sub.is_solo is False
        assert sub.user_name == payload["User"]["Name"]
        assert sub.second_user_name == payload["SecondaryUser"]["Name"]

    def test_empty_payload_is_all_none_except_is_solo(self) -> None:
        """Submission 全用 .get，空 dict 也能构造出来，is_solo 默认 True"""
        sub = Submission({})
        for attr in SUBMISSION_FIELDS.values():
            if attr == "is_solo":
                continue
            assert getattr(sub, attr) is None, attr
        assert sub.user_name is None
        assert sub.second_user_name is None
        assert sub.is_solo is True

    def test_null_user_objects_do_not_explode(self) -> None:
        """User / SecondaryUser 是 null 的时候要退化成空 dict，不能 AttributeError"""
        sub = Submission(make_submission_payload(User=None, SecondaryUser=None))
        assert sub.user_name is None
        assert sub.second_user_name is None

    def test_to_dict_is_a_copy(self) -> None:
        sub = Submission(make_submission_payload(ID=1))
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
        page = SubmissionPage(make_submission_page_payload())
        assert [s.id for s in page.submissions] == [1, 2, 3]
        assert all(isinstance(s, Submission) for s in page.submissions)

    def test_total_pages_boundaries(self) -> None:
        """向上取整，且至少 1 页。整除的边界两侧都要对。"""
        for total, limit, expected in [
            (0, 10, 1),
            (1, 10, 1),
            (9, 10, 1),
            (10, 10, 1),
            (11, 10, 2),
            (20, 10, 2),
            (21, 10, 3),
            (5, 1, 5),
            (30, 30, 1),
            (31, 30, 2),
        ]:
            page = SubmissionPage({"total": total, "limit": limit})
            assert page.total_pages == expected, (total, limit)

    def test_total_pages_guards_against_bad_limit(self) -> None:
        """limit <= 0 直接返回 1，不能除零也不能出负数页"""
        for limit in (0, -1, -30):
            assert SubmissionPage({"total": 100, "limit": limit}).total_pages == 1, limit

    def test_page_number_is_taken_as_is(self) -> None:
        """page 是接口回什么就是什么（0 起），这里不做任何换算"""
        assert SubmissionPage(make_submission_page_payload(page=3)).page == 3


# ==========================================================================
# gddlapi —— HTTP
# ==========================================================================
class TestGddlGetSubmissions:
    def test_happy_path(self, stub_requests: Any, make_response: Any) -> None:
        stub_requests.get(
            "https://gdladder.com/api/levels/1000/submissions",
            make_response(json_data=make_submission_page_payload(ids=(1, 2))),
        )
        page = Gddl.getsubmissions(1000)
        assert page is not None
        assert page.total == 2
        assert [s.id for s in page.submissions] == [1, 2]
        call = stub_requests.calls[-1]
        assert call["url"] == "https://gdladder.com/api/levels/1000/submissions"
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
            "/submissions",
            make_response(json_data=make_submission_page_payload(ids=())),
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
            "https://gdladder.com/api/levels/1000/submissions/spread"
        )

    def test_spread_non_200_returns_none(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get("/submissions/spread", make_response(503, json_data={}))
        assert Gddl.getspread(1000) is None

    def test_spread_exception_returns_none(self, stub_requests: Any) -> None:
        import requests as _requests

        stub_requests.get("/submissions/spread", _requests.ConnectionError("nope"))
        assert Gddl.getspread(1000) is None

    def test_tags_are_flattened(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        """接口回的是嵌套的 GetLevelTagsResponseDTO，这里压成 Name/Count 两个键"""
        stub_requests.get(
            "/tags",
            make_response(
                json_data=[
                    {"TagID": 1, "ReactCount": 9},
                    {"TagID": 2, "ReactCount": 0},
                ]
            ),
        )
        assert Gddl.getleveltags(500) == [
            {"Name": "Cube", "Count": 9},
            {"Name": "Ship", "Count": 0},
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

    def test_tags_uses_gddl_timeout(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get("/tags", make_response(json_data=[]))
        Gddl.getleveltags(500)
        assert stub_requests.calls[-1]["timeout"] == gddlapi.GDDL_TIMEOUT


class TestGddlLevelLookup:
    def test_getlevelsbyname(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(
                json_data={
                    "data": [
                        make_search_payload(id=1),
                        make_search_payload(id=2),
                    ]
                }
            ),
        )
        levels = Gddl.getlevelsbyname("Test Level")
        assert [lv.ID for lv in levels] == [1, 2]
        assert all(isinstance(level, GDDLSearchEntry) for level in levels)
        assert [lv.Name for lv in levels] == ["Test Level", "Test Level"]
        assert stub_requests.calls[-1]["params"] == {"name": "Test Level"}

    def test_getlevelsbyname_non_200(
        self,
        empty_gddl_snapshot: None,
        stub_requests: Any,
        make_response: Any,
    ) -> None:
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(404, json_data={}),
        )
        assert Gddl.getlevelsbyname("__definitely_not_cached__") == []

    def test_getlevelsbyname_exception(
        self,
        empty_gddl_snapshot: None,
        stub_requests: Any,
    ) -> None:
        import requests as _requests

        stub_requests.get(
            GDDL_SEARCH_URL,
            _requests.ConnectionError("boom"),
        )
        assert Gddl.getlevelsbyname("__definitely_not_cached__") == []

    def test_getlevelbyid_with_tags_does_two_round_trips(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        # /tags 必须先登记：路由是子串匹配，
        # ".../123" 也能匹配 ".../123/tags"
        stub_requests.get(
            "/api/levels/123/tags",
            make_response(
                json_data=[
                    {"TagID": 1, "ReactCount": 4},
                ]
            ),
        )
        stub_requests.get(
            "/api/levels/123",
            make_response(json_data=make_level_payload(ID=123)),
        )
        level = Gddl.getlevelbyid(123)
        assert level is not None
        assert level.ID == 123
        assert level.Tags == [{"Name": "Cube", "Count": 4}]
        assert len(stub_requests.calls) == 2

    def test_getlevelbyid_without_tags_is_one_round_trip(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        """with_tags=False 时不该顺带去拉 tags，Tags 留空"""
        stub_requests.get(
            "/api/levels/123",
            make_response(json_data=make_level_payload(ID=123)),
        )
        level = Gddl.getlevelbyid(123, with_tags=False)
        assert level is not None
        assert level.Tags == []
        assert len(stub_requests.calls) == 1

    def test_getlevelbyid_non_200(
        self,
        empty_gddl_snapshot: None,
        stub_requests: Any,
        make_response: Any,
    ) -> None:
        unknown_id = 999999999
        stub_requests.get(
            f"/api/levels/{unknown_id}",
            make_response(404, json_data={}),
        )
        assert Gddl.getlevelbyid(unknown_id) is None

    def test_getlevelbyid_exception(
        self,
        empty_gddl_snapshot: None,
        stub_requests: Any,
    ) -> None:
        import requests as _requests

        unknown_id = 999999999
        stub_requests.get(
            f"/api/levels/{unknown_id}",
            _requests.Timeout("t"),
        )
        assert Gddl.getlevelbyid(unknown_id) is None


class TestGddlLocalFallback:

    def test_name_lookup_uses_snapshot_after_remote_failure(
        self,
        gddl_snapshot: None,
        stub_requests: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import requests as _requests

        stub_requests.get(
            GDDL_SEARCH_URL,
            _requests.ConnectionError("offline"),
        )
        monkeypatch.setattr(
            gddl_store,
            "get_by_name",
            lambda _name: [make_search_payload(id=123, name="Cached Level")],
        )
        levels = Gddl.getlevelsbyname("cached level")
        assert [level.ID for level in levels] == [123]
        assert levels[0].Name == "Cached Level"


class TestGddlSearch:
    def test_searchlevels_params(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(json_data={"total": 0, "data": []}),
        )
        Gddl.searchlevels(
            page=2,
            limit=5,
            sort="ID",
            minRating=3,
            maxRating=None,
        )
        params = stub_requests.calls[-1]["params"]
        assert params == {
            "page": 2,
            "limit": 5,
            "sort": "ID",
            "minRating": 3,
        }

    def test_searchlevels_clamps_negative_page_only(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        """page 卡了下限，limit 却一点没卡（和 getsubmissions 不一样）"""
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(json_data={}),
        )
        Gddl.searchlevels(page=-3, limit=99)
        params = stub_requests.calls[-1]["params"]
        assert params["page"] == 0
        assert params["limit"] == 99

    def test_searchlevels_non_200(
        self,
        empty_gddl_snapshot: None,
        stub_requests: Any,
        make_response: Any,
    ) -> None:
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(500, json_data={}),
        )
        assert Gddl.searchlevels(minRating=999) is None

    def test_searchlevels_exception(
        self,
        empty_gddl_snapshot: None,
        stub_requests: Any,
    ) -> None:
        import requests as _requests

        stub_requests.get(
            GDDL_SEARCH_URL,
            _requests.Timeout("t"),
        )
        assert Gddl.searchlevels(minRating=999) is None

    def test_getlevelbyindex_uses_index_as_page(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        """靠 sort=ID + limit=1 + page=index 定位第 index 关，dailydemon 就指着这个稳"""
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(
                json_data={
                    "total": 500,
                    "data": [make_search_payload(id=777)],
                }
            ),
        )
        level = Gddl.getlevelbyindex(42, minRating=1)
        assert level is not None
        assert level.ID == 777
        params = stub_requests.calls[-1]["params"]
        assert params == {
            "page": 42,
            "limit": 1,
            "sort": "ID",
            "minRating": 1,
        }

    def test_getlevelbyindex_none_payload(
        self,
        empty_gddl_snapshot: None,
        stub_requests: Any,
        make_response: Any,
    ) -> None:
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(500, json_data={}),
        )
        assert Gddl.getlevelbyindex(999999) is None

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"data": []},
            {"total": 3, "data": []},
        ],
    )
    def test_getlevelbyindex_empty_levels(
        self,
        empty_gddl_snapshot: None,
        stub_requests: Any,
        make_response: Any,
        payload: dict[str, Any],
    ) -> None:
        """超出范围的 index 会拿到空 levels，要返回 None 而不是 IndexError"""
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(json_data=payload),
        )
        assert Gddl.getlevelbyindex(9999) is None

    @pytest.mark.parametrize(
        ("low", "high", "expected_min", "expected_max"),
        [
            (20, -1, 19.5, 20.5),
            (20, 25, 19.5, 25.5),
            (1, -1, 1.0, 1.5),
            (2, -1, 1.5, 2.5),
            (39, -1, 38.5, 39.0),
            (38, -1, 37.5, 38.5),
            (1, 39, 1.0, 39.0),
        ],
    )
    def test_random_by_tier_expands_range(
        self,
        stub_requests: Any,
        make_response: Any,
        low: int,
        high: int,
        expected_min: float,
        expected_max: float,
    ) -> None:
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(
                json_data={
                    "total": 1,
                    "data": [make_search_payload(id=9)],
                }
            ),
        )
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
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(
                json_data={
                    "total": 1,
                    "data": [make_search_payload()],
                }
            ),
        )
        Gddl.getrandomlevelbytier(
            10,
            enjoyment_min=7.0,
            enjoyment_max=9.5,
        )
        params = stub_requests.calls[-1]["params"]
        assert params["minEnjoyment"] == 7.0
        assert params["maxEnjoyment"] == 9.5

    def test_random_by_tier_omits_unset_enjoyment(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(
                json_data={
                    "total": 1,
                    "data": [make_search_payload()],
                }
            ),
        )
        Gddl.getrandomlevelbytier(10)
        params = stub_requests.calls[-1]["params"]
        assert "minEnjoyment" not in params
        assert "maxEnjoyment" not in params

    def test_random_by_tier_empty_result(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(json_data={"total": 0, "data": []}),
        )
        assert Gddl.getrandomlevelbytier(20) is None

    def test_random_by_tier_request_failed(
        self,
        empty_gddl_snapshot: None,
        stub_requests: Any,
        make_response: Any,
    ) -> None:
        stub_requests.get(
            GDDL_SEARCH_URL,
            make_response(500, json_data={}),
        )
        assert Gddl.getrandomlevelbytier(39, high=39) is None


# ==========================================================================
# nlwapi
# ==========================================================================
@pytest.fixture
def nlw_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """把 nlwapi 的数据目录指到 tmp_path，收尾时把四张表的内容原样还回去。

    四个 list / dict 是被 _SOURCES 和 _rebuild_dicts 直接引用的对象，
    reload() 是原地 clear + append，所以只能原地还原，不能整个换掉。
    """
    lists = (
        nlwapi.nlwlevels,
        nlwapi.idslevels,
        nlwapi.lwlevels,
        nlwapi.hdslevels,
    )
    dicts = (
        nlwapi.nlwlevel_dict,
        nlwapi.idslevel_dict,
        nlwapi.lwlevel_dict,
        nlwapi.hdslevel_dict,
    )
    list_backup = [list(x) for x in lists]
    dict_backup = [dict(x) for x in dicts]
    monkeypatch.setattr(nlwapi, "WORK_FOLDER", tmp_path)

    def _write(filename: str, payload: Any) -> Path:
        path = tmp_path / filename
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    try:
        yield _write
    finally:
        for lst, backup in zip(lists, list_backup):
            lst[:] = backup
        for dct, backup in zip(dicts, dict_backup):
            dct.clear()
            dct.update(backup)


NLW_SOURCES: list[tuple[type, str]] = [
    (NLWlevel, "NLW"),
    (IDSlevel, "IDS"),
    (LWlevel, "LW"),
    (HDSlevel, "HDS"),
]

NLW_REQUIRED_KEYS = ("name", "creator", "video")


class TestNlwModels:
    def test_base_level_required_vs_optional_keys(self) -> None:
        """name / creator / video 是硬取的，其余走 .get"""
        row = make_nlw_row()
        level = Level({key: row[key] for key in NLW_REQUIRED_KEYS})
        assert (level.name, level.creator, level.video) == (
            row["name"],
            row["creator"],
            row["video"],
        )
        assert level.length is None
        assert level.checkpoints is None
        assert level.id is None
        assert level.description is None

    def test_base_level_missing_required_key_raises(self) -> None:
        for missing in NLW_REQUIRED_KEYS:
            row = make_nlw_row()
            del row[missing]
            with pytest.raises(KeyError):
                Level(row)

    def test_subclasses_tag_their_source(self) -> None:
        for cls, source in NLW_SOURCES:
            level = cls(make_nlw_row(tier="Fuck", skillset="wave"))
            assert level.source == source
            assert level.tier == "Fuck"
            assert level.skillset == "wave"

    def test_nlw_and_lw_carry_enjoyment(self) -> None:
        assert NLWlevel(make_nlw_row(enjoyment=62.0)).enjoyment == 62.0
        assert LWlevel(make_nlw_row(enjoyment=None)).enjoyment is None

    def test_ids_hds_do_not_read_enjoyment(self) -> None:
        """IDS/HDS 表没有 enjoyment 这一列，缺了也要能构造。"""
        for cls in (IDSlevel, HDSlevel):
            row = make_nlw_row()
            del row["enjoyment"]
            level = cls(row)
            assert not hasattr(level, "enjoyment"), cls
            assert level.tier == "1"

    def test_nlw_lw_require_enjoyment(self) -> None:
        for cls in (NLWlevel, LWlevel):
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
        nlw_workspace("nlw_levels.json", {"levels": [make_nlw_row(1)]})
        nlwapi.reload()
        assert len(nlwapi.nlwlevels) == 1
        assert nlwapi.idslevels == []
        assert nlwapi.lwlevels == []
        assert nlwapi.hdslevels == []

    def test_broken_json_is_skipped(
        self, nlw_workspace: Any, tmp_path: Path
    ) -> None:
        (tmp_path / "nlw_levels.json").write_text(
            "{ this is not json",
            encoding="utf-8",
        )
        nlw_workspace("ids_levels.json", {"levels": [make_nlw_row(2)]})
        nlwapi.reload()
        assert nlwapi.nlwlevels == []
        assert len(nlwapi.idslevels) == 1

    def test_bad_row_is_skipped_but_others_survive(
        self, nlw_workspace: Any
    ) -> None:
        bad = make_nlw_row(2, "Bad")
        del bad["video"]
        nlw_workspace(
            "nlw_levels.json",
            {
                "levels": [
                    make_nlw_row(1, "Good1"),
                    bad,
                    make_nlw_row(3, "Good3"),
                ]
            },
        )
        nlwapi.reload()
        assert [lv.name for lv in nlwapi.nlwlevels] == ["Good1", "Good3"]

    def test_missing_levels_key(self, nlw_workspace: Any) -> None:
        nlw_workspace("nlw_levels.json", {"timestamp": 0})
        nlwapi.reload()
        assert nlwapi.nlwlevels == []

    def test_reload_is_idempotent(self, nlw_workspace: Any) -> None:
        """reload 是往 list 里 append 的，没先 clear 的话每次都会翻倍"""
        nlw_workspace(
            "nlw_levels.json",
            {"levels": [make_nlw_row(1), make_nlw_row(2)]},
        )
        nlwapi.reload()
        nlwapi.reload()
        nlwapi.reload()
        assert len(nlwapi.nlwlevels) == 2
        assert len(nlwapi.nlwlevel_dict) == 2

    def test_dict_is_keyed_by_raw_id_type(self, nlw_workspace: Any) -> None:
        """json 里 id 是数字，查询表的键就是 int。"""
        nlw_workspace(
            "nlw_levels.json",
            {"levels": [make_nlw_row(56916170)]},
        )
        nlwapi.reload()
        assert Nlw.nlw_query_level(56916170) is not None
        assert Nlw.nlw_query_level("56916170") is None

    def test_stale_timestamp_does_not_break_loading(
        self, nlw_workspace: Any
    ) -> None:
        """时间戳过期只是打个 warning，数据照样要进表"""
        nlw_workspace(
            "nlw_levels.json",
            {"timestamp": 1, "levels": [make_nlw_row(1)]},
        )
        nlwapi.reload()
        assert len(nlwapi.nlwlevels) == 1


class TestNlwQueries:
    @pytest.fixture(autouse=True)
    def _empty_tables(self, nlw_workspace: Any) -> None:
        """每个用例都从四张空表开始，自己往里塞"""
        nlwapi.reload()

    @staticmethod
    def _put(
        bucket: list,
        dct: dict,
        cls: type,
        row: dict[str, Any],
    ) -> Any:
        level = cls(row)
        bucket.append(level)
        dct[level.id] = level
        return level

    def test_query_by_id_per_source(self) -> None:
        n = self._put(
            nlwapi.nlwlevels,
            nlwapi.nlwlevel_dict,
            NLWlevel,
            make_nlw_row(1),
        )
        i = self._put(
            nlwapi.idslevels,
            nlwapi.idslevel_dict,
            IDSlevel,
            make_nlw_row(2),
        )
        lw = self._put(
            nlwapi.lwlevels,
            nlwapi.lwlevel_dict,
            LWlevel,
            make_nlw_row(3),
        )
        h = self._put(
            nlwapi.hdslevels,
            nlwapi.hdslevel_dict,
            HDSlevel,
            make_nlw_row(4),
        )
        assert Nlw.nlw_query_level(1) is n
        assert Nlw.ids_query_level(2) is i
        assert Nlw.lw_query_level(3) is lw
        assert Nlw.hds_query_level(4) is h
        assert Nlw.nlw_query_level(999) is None

    def test_getlevelbyid_prefers_nlw_over_everything(self) -> None:
        n = self._put(
            nlwapi.nlwlevels,
            nlwapi.nlwlevel_dict,
            NLWlevel,
            make_nlw_row(1),
        )
        self._put(
            nlwapi.lwlevels,
            nlwapi.lwlevel_dict,
            LWlevel,
            make_nlw_row(1),
        )
        self._put(
            nlwapi.idslevels,
            nlwapi.idslevel_dict,
            IDSlevel,
            make_nlw_row(1),
        )
        self._put(
            nlwapi.hdslevels,
            nlwapi.hdslevel_dict,
            HDSlevel,
            make_nlw_row(1),
        )
        assert Nlw.getlevelbyid(1) is n

    def test_getlevelbyid_lw_beats_ids_and_hds(self) -> None:
        lw = self._put(
            nlwapi.lwlevels,
            nlwapi.lwlevel_dict,
            LWlevel,
            make_nlw_row(1),
        )
        self._put(
            nlwapi.idslevels,
            nlwapi.idslevel_dict,
            IDSlevel,
            make_nlw_row(1),
        )
        self._put(
            nlwapi.hdslevels,
            nlwapi.hdslevel_dict,
            HDSlevel,
            make_nlw_row(1),
        )
        assert Nlw.getlevelbyid(1) is lw

    def test_getlevelbyid_only_ids(self) -> None:
        i = self._put(
            nlwapi.idslevels,
            nlwapi.idslevel_dict,
            IDSlevel,
            make_nlw_row(1),
        )
        assert Nlw.getlevelbyid(1) is i

    def test_getlevelbyid_only_hds(self) -> None:
        h = self._put(
            nlwapi.hdslevels,
            nlwapi.hdslevel_dict,
            HDSlevel,
            make_nlw_row(1),
        )
        assert Nlw.getlevelbyid(1) is h

    def test_getlevelbyid_nothing(self) -> None:
        assert Nlw.getlevelbyid(1) is None

    def test_ids_not_legacy_wins(self) -> None:
        i = self._put(
            nlwapi.idslevels,
            nlwapi.idslevel_dict,
            IDSlevel,
            make_nlw_row(1, tier="Fuck"),
        )
        self._put(
            nlwapi.hdslevels,
            nlwapi.hdslevel_dict,
            HDSlevel,
            make_nlw_row(1, tier="Fuck"),
        )
        assert Nlw.getlevelbyid(1) is i

    def test_ids_legacy_falls_back_to_hds_with_description(self) -> None:
        self._put(
            nlwapi.idslevels,
            nlwapi.idslevel_dict,
            IDSlevel,
            make_nlw_row(1, tier="Legacy"),
        )
        h = self._put(
            nlwapi.hdslevels,
            nlwapi.hdslevel_dict,
            HDSlevel,
            make_nlw_row(1, tier="Fuck", description="有描述"),
        )
        assert Nlw.getlevelbyid(1) is h

    def test_both_legacy_falls_back_to_ids(self) -> None:
        i = self._put(
            nlwapi.idslevels,
            nlwapi.idslevel_dict,
            IDSlevel,
            make_nlw_row(1, tier="Legacy"),
        )
        self._put(
            nlwapi.hdslevels,
            nlwapi.hdslevel_dict,
            HDSlevel,
            make_nlw_row(1, tier="Legacy", description="有描述"),
        )
        assert Nlw.getlevelbyid(1) is i

    @pytest.mark.parametrize("description", [None, ""])
    def test_ids_legacy_but_hds_has_no_description_falls_back_to_ids(
        self, description: str | None
    ) -> None:
        i = self._put(
            nlwapi.idslevels,
            nlwapi.idslevel_dict,
            IDSlevel,
            make_nlw_row(1, tier="Legacy"),
        )
        self._put(
            nlwapi.hdslevels,
            nlwapi.hdslevel_dict,
            HDSlevel,
            make_nlw_row(1, tier="Fuck", description=description),
        )
        assert Nlw.getlevelbyid(1) is i

    def test_getlevelbyname_searches_all_tables_in_order(self) -> None:
        n = self._put(
            nlwapi.nlwlevels,
            nlwapi.nlwlevel_dict,
            NLWlevel,
            make_nlw_row(1, "Same Name"),
        )
        i = self._put(
            nlwapi.idslevels,
            nlwapi.idslevel_dict,
            IDSlevel,
            make_nlw_row(2, "Same Name"),
        )
        lw = self._put(
            nlwapi.lwlevels,
            nlwapi.lwlevel_dict,
            LWlevel,
            make_nlw_row(3, "Same Name"),
        )
        h = self._put(
            nlwapi.hdslevels,
            nlwapi.hdslevel_dict,
            HDSlevel,
            make_nlw_row(4, "Same Name"),
        )
        assert Nlw.getlevelbyname("Same Name") == [n, i, lw, h]

    @pytest.mark.parametrize(
        "query",
        ["bloodbath", "BLOODBATH", "  BloodBath  "],
    )
    def test_getlevelbyname_is_case_and_space_insensitive(
        self, query: str
    ) -> None:
        lv = self._put(
            nlwapi.nlwlevels,
            nlwapi.nlwlevel_dict,
            NLWlevel,
            make_nlw_row(1, "  Bloodbath "),
        )
        assert Nlw.getlevelbyname(query) == [lv]

    def test_getlevelbyname_no_match(self) -> None:
        self._put(
            nlwapi.nlwlevels,
            nlwapi.nlwlevel_dict,
            NLWlevel,
            make_nlw_row(1, "A"),
        )
        assert Nlw.getlevelbyname("B") == []

    def test_getlevelbyname_skips_nameless_entries(self) -> None:
        lv = self._put(
            nlwapi.nlwlevels,
            nlwapi.nlwlevel_dict,
            NLWlevel,
            make_nlw_row(1, "A"),
        )
        nlwapi.nlwlevels.append(None)
        nlwapi.nlwlevels.append(NLWlevel(make_nlw_row(2, "")))
        assert Nlw.getlevelbyname("a") == [lv]


# ==========================================================================
# platapi
# ==========================================================================
@pytest.fixture
def plat_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    """给 platapi 的六个模块级全局上个保险。"""
    for name in (
        "platdata",
        "platdata_entries",
        "platdata_main_entries",
        "platdata_derived_entries",
        "platdata_by_id",
        "platdata_by_name",
    ):
        monkeypatch.setattr(platapi, name, getattr(platapi, name))


@pytest.fixture
def write_plat(tmp_path: Path) -> Any:
    """把 plat 数据写进 tmp_path，返回文件路径"""

    def _write(
        rows: list[dict[str, Any]],
        name: str = "plat.json",
    ) -> Path:
        path = tmp_path / name
        path.write_text(
            json.dumps(
                {"timestamp": 0, "levels": rows},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    return _write


class TestPlatInfoFromDict:
    def test_clean_row_round_trips_unchanged(self) -> None:
        dumped = PlatInfo.from_dict(make_plat_row()).to_dict()
        assert dumped == make_plat_row()
        assert "is_main" not in dumped
        assert PlatInfo.from_dict(make_plat_row()).is_main is True

    def test_values_are_stringified_and_stripped(self) -> None:
        info = PlatInfo.from_dict(
            make_plat_row(level_id="222", name="  Spaced  ", weight=100)
        )
        assert info.id == "222"
        assert info.name == "Spaced"
        assert info.weight == "100"

    def test_none_stays_none(self) -> None:
        info = PlatInfo.from_dict(
            make_plat_row(
                tier=None,
                creator=None,
                video=None,
                section=None,
            )
        )
        assert info.tier is None
        assert info.creator is None
        assert info.video is None
        assert info.section is None

    def test_enjoyment_coercion(self) -> None:
        for raw, expected in [
            (8.5, 8.5),
            ("7", 7.0),
            ("7.25", 7.25),
            (0, 0.0),
            (None, None),
            ("", None),
            ("n/a", None),
            ([], None),
        ]:
            assert (
                PlatInfo.from_dict(
                    make_plat_row(enjoyment=raw)
                ).enjoyment
                == expected
            ), raw

    def test_tags_normalization(self) -> None:
        for raw, expected in [
            (["A", "B"], ["A", "B"]),
            ([" A ", "B "], ["A", "B"]),
            (["---"], []),
            (["---", "A"], ["---", "A"]),
            ([], []),
            (["A", None, "B"], ["A", "B"]),
            ("notalist", []),
            (None, []),
        ]:
            assert (
                PlatInfo.from_dict(make_plat_row(tags=raw)).tags == expected
            ), raw

    def test_derived_levels_normalization(self) -> None:
        for raw, expected in [
            (["X"], ["X"]),
            ([" X "], ["X"]),
            ([None], []),
            ("nope", []),
            (None, []),
        ]:
            info = PlatInfo.from_dict(
                make_plat_row(derived_levels=raw)
            )
            assert info.derived_levels == expected, raw

    def test_dash_placeholder_becomes_none(self) -> None:
        for field in ("tpl", "pemonlist"):
            info = PlatInfo.from_dict(
                make_plat_row(**{field: "-"})
            )
            assert getattr(info, field) is None, field

    def test_dash_placeholder_does_not_mutate_the_input_dict(self) -> None:
        row = make_plat_row(tpl="-", pemonlist="-")
        PlatInfo.from_dict(row)
        assert row["tpl"] == "-"
        assert row["pemonlist"] == "-"

    def test_derived_entry_is_not_main(self) -> None:
        info = PlatInfo.from_dict(
            make_plat_row(derived_from="Plat A")
        )
        assert info.is_main is False
        assert info.derived_from == "Plat A"

    def test_empty_string_derived_from_counts_as_derived(self) -> None:
        info = PlatInfo.from_dict(
            make_plat_row(derived_from="")
        )
        assert info.derived_from == ""
        assert info.is_main is False

    def test_missing_id_and_name_become_empty_string(self) -> None:
        info = PlatInfo.from_dict({})
        assert info.id == ""
        assert info.name == ""
        assert info.is_main is True


class TestPlatData:
    def test_random_by_tier_uses_numeric_prefix_and_main_entries(
        self,
        plat_globals: None,
        write_plat: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = write_plat(
            [
                make_plat_row("1", "Tier One", tier="1 - EASY"),
                make_plat_row("2", "Tier Ten", tier="10 - INSANE"),
                make_plat_row(
                    "3", "Derived", tier="1 - EASY", derived_from="Tier One"
                ),
                make_plat_row("---", "Placeholder", tier="1 - EASY"),
            ]
        )
        platapi.fetch(cache_file=str(path))
        seen: list[list[str]] = []

        def pick(candidates: list[PlatInfo]) -> PlatInfo:
            seen.append([entry.id for entry in candidates])
            return candidates[0]

        monkeypatch.setattr(platapi.random, "choice", pick)

        result = Platapi.getrandomlevelbytier(1)

        assert result.id == "1"
        assert seen == [["1"]]

    def test_random_by_tier_returns_none_without_matching_entries(
        self,
        plat_globals: None,
        write_plat: Any,
    ) -> None:
        path = write_plat([make_plat_row("1", "Tier One", tier="1 - EASY")])
        platapi.fetch(cache_file=str(path))

        assert Platapi.getrandomlevelbytier(13) is None

    def test_derived_entries_go_into_by_name_but_not_by_id(
        self, write_plat: Any
    ) -> None:
        path = write_plat(
            [
                make_plat_row("1", "Main"),
                make_plat_row("2", "Derived", derived_from="Main"),
            ]
        )
        data = PlatData(cache_file=str(path))
        assert [e.id for e in data.entries] == ["1", "2"]
        assert [e.id for e in data.main_entries] == ["1"]
        assert [e.id for e in data.derived_entries] == ["2"]
        assert set(data.by_id) == {"1"}
        assert data.getlevelbyid("2") is None
        assert data.getlevelbyname("derived").id == "2"

    def test_duplicate_id_or_name_first_wins(
        self, write_plat: Any
    ) -> None:
        path = write_plat(
            [
                make_plat_row("1", "First"),
                make_plat_row("1", "Second"),
                make_plat_row("2", "Dup"),
                make_plat_row("3", "Dup"),
            ]
        )
        data = PlatData(cache_file=str(path))
        assert data.getlevelbyid("1").name == "First"
        assert data.getlevelbyname("dup").id == "2"

    def test_name_registered_under_lowercase_key(
        self, write_plat: Any
    ) -> None:
        path = write_plat([make_plat_row("1", "MiXeD")])
        data = PlatData(cache_file=str(path))
        assert set(data.by_name) == {"mixed"}

    def test_lowercase_collision_keeps_first_entry(
        self, write_plat: Any
    ) -> None:
        path = write_plat(
            [
                make_plat_row("1", "ABC"),
                make_plat_row("2", "abc"),
            ]
        )
        data = PlatData(cache_file=str(path))
        assert data.getlevelbyname("abc").id == "1"
        assert set(data.by_name) == {"abc"}

    def test_bad_rows_are_dropped(self, write_plat: Any) -> None:
        path = write_plat(
            [
                make_plat_row("1", "Kept"),
                make_plat_row("", "NoId"),
                "junk",
                42,
                None,
            ]
        )
        data = PlatData(cache_file=str(path))
        assert [e.name for e in data.entries] == ["Kept"]

    def test_unusable_cache_file_gives_empty_data(
        self, tmp_path: Path
    ) -> None:
        broken = tmp_path / "broken.json"
        broken.write_text("{oops", encoding="utf-8")
        nolevels = tmp_path / "nolevels.json"
        nolevels.write_text(
            json.dumps({"timestamp": 0}),
            encoding="utf-8",
        )
        for path in (
            tmp_path / "does_not_exist.json",
            broken,
            nolevels,
        ):
            data = PlatData(cache_file=str(path))
            assert data.entries == [], path
            assert data.by_id == {}, path
            assert data.getlevelbyid("1") is None, path

    def test_lookups_strip_stringify_and_lowercase(
        self, write_plat: Any
    ) -> None:
        path = write_plat(
            [make_plat_row("12345", "Some Level")]
        )
        data = PlatData(cache_file=str(path))
        assert data.getlevelbyid("  12345  ") is not None
        assert data.getlevelbyid(12345) is not None
        assert data.getlevelbyname("  SOME LEVEL ") is not None
        assert data.getlevelbyname("some level") is not None
        assert data.getlevelbyname("other") is None


class TestPlatapiFacade:
    def test_fetch_refreshes_every_global(
        self,
        plat_globals: None,
        write_plat: Any,
    ) -> None:
        path = write_plat(
            [
                make_plat_row(
                    "1",
                    "Main",
                    derived_levels=["Main II"],
                ),
                make_plat_row(
                    "2",
                    "Main II",
                    derived_from="Main",
                ),
            ]
        )
        entries = platapi.fetch(cache_file=str(path))
        assert [e.id for e in entries] == ["1", "2"]
        assert platapi.platdata_entries is entries
        assert platapi.platdata_entries is platapi.platdata.entries
        assert platapi.platdata_main_entries is platapi.platdata.main_entries
        assert (
            platapi.platdata_derived_entries
            is platapi.platdata.derived_entries
        )
        assert platapi.platdata_by_id is platapi.platdata.by_id
        assert platapi.platdata_by_name is platapi.platdata.by_name

    def test_facade_lookups(
        self,
        plat_globals: None,
        write_plat: Any,
    ) -> None:
        path = write_plat(
            [make_plat_row("999", "Facade Level")]
        )
        platapi.fetch(cache_file=str(path))
        assert (
            Platapi.getlevelbyid("999").name
            == "Facade Level"
        )
        assert (
            Platapi.getlevelbyid(999).name
            == "Facade Level"
        )
        assert (
            Platapi.getlevelbyname("facade level").id
            == "999"
        )
        assert Platapi.getlevelbyname("nope") is None

    @pytest.mark.parametrize("falsy", [None, "", 0])
    def test_getlevelbyid_short_circuits_on_falsy(
        self,
        plat_globals: None,
        write_plat: Any,
        falsy: Any,
    ) -> None:
        path = write_plat(
            [make_plat_row("0", "Zero Level")]
        )
        platapi.fetch(cache_file=str(path))
        assert Platapi.getlevelbyid(falsy) is None
        assert (
            Platapi.getlevelbyid("0").name
            == "Zero Level"
        )

    def test_getderivedlevels(
        self,
        plat_globals: None,
        write_plat: Any,
    ) -> None:
        path = write_plat(
            [
                make_plat_row(
                    "1",
                    "Main",
                    derived_levels=["Main II", "Main III"],
                ),
                make_plat_row(
                    "2",
                    "Main II",
                    derived_from="Main",
                ),
                make_plat_row(
                    "3",
                    "Main III",
                    derived_from="Main",
                ),
            ]
        )
        platapi.fetch(cache_file=str(path))
        main = Platapi.getlevelbyid("1")
        derived = Platapi.getderivedlevels(main)
        assert [d.id for d in derived] == ["2", "3"]

    def test_getderivedlevels_empty(
        self,
        plat_globals: None,
        write_plat: Any,
    ) -> None:
        path = write_plat(
            [make_plat_row("1", "Main")]
        )
        platapi.fetch(cache_file=str(path))
        assert (
            Platapi.getderivedlevels(
                Platapi.getlevelbyid("1")
            )
            == []
        )

    def test_getderivedlevels_skips_unknown_name(
        self,
        plat_globals: None,
        write_plat: Any,
    ) -> None:
        path = write_plat(
            [
                make_plat_row(
                    "1",
                    "Main",
                    derived_levels=["Does Not Exist"],
                )
            ]
        )
        platapi.fetch(cache_file=str(path))
        assert Platapi.getderivedlevels(Platapi.getlevelbyid("1")) == []

    def test_getderivedlevels_matches_lowercased_name(
        self,
        plat_globals: None,
        write_plat: Any,
    ) -> None:
        path = write_plat(
            [
                make_plat_row(
                    "1",
                    "Main",
                    derived_levels=["main ii"],
                ),
                make_plat_row(
                    "2",
                    "Main II",
                    derived_from="Main",
                ),
            ]
        )
        platapi.fetch(cache_file=str(path))
        assert [
            d.id
            for d in Platapi.getderivedlevels(
                Platapi.getlevelbyid("1")
            )
        ] == ["2"]

    def test_default_cache_path_is_the_plugin_data_dir(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            PlatData,
            "_fetch",
            lambda self: [],
        )
        data = PlatData()
        assert Path(data.cache_file) == (
            paths.DATA_DIR / "plat_combined.json"
        )

    def test_reload_refetches_from_the_default_path(
        self,
        plat_globals: None,
        write_plat: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = write_plat(
            [make_plat_row("777", "Reloaded Level")]
        )
        seen: list[str | None] = []
        real_init = PlatData.__init__

        def spy_init(
            self: PlatData,
            cache_file: str | None = None,
        ) -> None:
            seen.append(cache_file)
            real_init(self, cache_file=str(path))

        monkeypatch.setattr(
            PlatData,
            "__init__",
            spy_init,
        )
        platapi.reload()

        assert seen == [None]
        assert [
            e.id for e in platapi.platdata_entries
        ] == ["777"]
        assert (
            platapi.platdata_entries
            is platapi.platdata.entries
        )
        assert (
            platapi.platdata_main_entries
            is platapi.platdata.main_entries
        )
        assert (
            platapi.platdata_derived_entries
            is platapi.platdata.derived_entries
        )
        assert (
            platapi.platdata_by_id
            is platapi.platdata.by_id
        )
        assert (
            platapi.platdata_by_name
            is platapi.platdata.by_name
        )


# ==========================================================================
# dailydemon
# ==========================================================================
FIXED_DAY = date(2026, 7, 26)


class FakeGddl:
    """替掉 dailydemon 里的 Gddl，全部离线。"""

    def __init__(
        self,
        search_payload: dict[str, Any] | None = None,
        by_index: Any = None,
        by_id: Any = None,
    ) -> None:
        self.search_payload = search_payload
        self._by_index = by_index
        self._by_id = by_id
        self.search_calls: list[dict[str, Any]] = []
        self.index_calls: list[int] = []
        self.id_calls: list[Any] = []

    def searchlevels(
        self,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        self.search_calls.append(kwargs)
        return self.search_payload

    def getlevelbyindex(
        self,
        index: int,
        **filters: Any,
    ) -> Any:
        self.index_calls.append(index)
        if callable(self._by_index):
            return self._by_index(index)
        return self._by_index

    def getlevelbyid(
        self,
        level_id: Any,
        *a: Any,
        **k: Any,
    ) -> Any:
        self.id_calls.append(level_id)
        if callable(self._by_id):
            return self._by_id(level_id)
        return self._by_id


def make_gddl_level(
    level_id: int,
    name: str = "Daily",
) -> GDDLSearchEntry:
    return GDDLSearchEntry(make_search_payload(id=level_id, name=name))


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
    def test_today_seed(
        self,
        day: date,
        expected: int,
    ) -> None:
        assert dailydemon.today_seed(day) == expected

    def test_key_format(self) -> None:
        assert (
            dailydemon._key(FIXED_DAY)
            == "dailydemon_2026-07-26"
        )
        assert (
            dailydemon._key(date(2026, 1, 2))
            == "dailydemon_2026-01-02"
        )

    def test_today_passthrough(self) -> None:
        assert dailydemon._today(FIXED_DAY) is FIXED_DAY

    def test_pick_index_is_in_range_and_deterministic(self) -> None:
        first = dailydemon.pick_index(2380, FIXED_DAY)
        second = dailydemon.pick_index(2380, FIXED_DAY)
        assert first == second
        assert 0 <= first < 2380

    def test_pick_index_ignores_global_random_state(
        self,
        seeded_random: Any,
    ) -> None:
        seeded_random(1)
        a = dailydemon.pick_index(1000, FIXED_DAY)
        seeded_random(999999)
        b = dailydemon.pick_index(1000, FIXED_DAY)
        assert a == b
        assert a == random.Random(20260726).randrange(1000)

    def test_pick_index_changes_with_the_day(self) -> None:
        days = [
            date(2026, 7, d)
            for d in range(1, 21)
        ]
        picks = {
            dailydemon.pick_index(10000, d)
            for d in days
        }
        assert len(picks) > 1

    def test_pick_index_total_one(self) -> None:
        assert dailydemon.pick_index(1, FIXED_DAY) == 0

    def test_filters_are_the_documented_conditions(self) -> None:
        assert dailydemon.FILTERS == {
            "minRating": 1,
            "maxRating": 9,
            "minEnjoyment": 7,
            "minSubmissionCount": 10,
        }
        assert dailydemon.describe_conditions().strip()

    def test_keep_seconds_is_two_days(self) -> None:
        assert dailydemon.KEEP_SECONDS == 2 * 24 * 3600


class TestDailyDemonStorage:
    def test_get_recent_empty(
        self,
        patch_storage: Any,
    ) -> None:
        patch_storage(dailydemon)
        assert dailydemon.get_recent() == []

    @pytest.mark.parametrize(
        "stored",
        ["notalist", 5, {"a": 1}, None],
    )
    def test_get_recent_rejects_non_list(
        self,
        patch_storage: Any,
        stored: Any,
    ) -> None:
        patch_storage(
            dailydemon,
            initial={dailydemon.RECENT_KEY: stored},
        )
        assert dailydemon.get_recent() == []

    def test_get_recent_filters_and_casts(
        self,
        patch_storage: Any,
    ) -> None:
        patch_storage(
            dailydemon,
            initial={
                dailydemon.RECENT_KEY: [
                    1,
                    "2",
                    "-3",
                    "3.5",
                    "abc",
                    "",
                    None,
                ]
            },
        )
        assert dailydemon.get_recent() == [
            1,
            2,
            -3,
        ]

    def test_remember_appends(
        self,
        patch_storage: Any,
    ) -> None:
        r = patch_storage(dailydemon)
        dailydemon.remember(10)
        dailydemon.remember(20)
        assert r.get(dailydemon.RECENT_KEY) == [10, 20]

    def test_remember_moves_existing_to_the_end(
        self,
        patch_storage: Any,
    ) -> None:
        r = patch_storage(
            dailydemon,
            initial={
                dailydemon.RECENT_KEY: [1, 2, 3]
            },
        )
        dailydemon.remember(2)
        assert r.get(dailydemon.RECENT_KEY) == [
            1,
            3,
            2,
        ]

    def test_remember_keeps_only_the_last_batch(
        self,
        patch_storage: Any,
    ) -> None:
        r = patch_storage(
            dailydemon,
            initial={
                dailydemon.RECENT_KEY: list(
                    range(dailydemon.RECENT_KEEP)
                )
            },
        )
        dailydemon.remember(1000)
        kept = r.get(dailydemon.RECENT_KEY)
        assert len(kept) == dailydemon.RECENT_KEEP
        assert kept[-1] == 1000
        assert kept[0] == 1

    def test_get_cached_id_missing(
        self,
        patch_storage: Any,
    ) -> None:
        patch_storage(dailydemon)
        assert dailydemon.get_cached_id(FIXED_DAY) is None

    def test_get_cached_id_parses_string(
        self,
        patch_storage: Any,
    ) -> None:
        patch_storage(
            dailydemon,
            initial={
                "dailydemon_2026-07-26": "12345"
            },
        )
        assert dailydemon.get_cached_id(FIXED_DAY) == 12345

    @pytest.mark.parametrize(
        "garbage",
        ["abc", "12.5", [1], {}],
    )
    def test_get_cached_id_garbage_is_treated_as_missing(
        self,
        patch_storage: Any,
        garbage: Any,
    ) -> None:
        patch_storage(
            dailydemon,
            initial={
                "dailydemon_2026-07-26": garbage
            },
        )
        assert dailydemon.get_cached_id(FIXED_DAY) is None

    def test_get_cached_id_is_per_day(
        self,
        patch_storage: Any,
    ) -> None:
        patch_storage(
            dailydemon,
            initial={
                "dailydemon_2026-07-26": "1"
            },
        )
        assert (
            dailydemon.get_cached_id(
                date(2026, 7, 27)
            )
            is None
        )


class TestDailyDemonFlow:
    def test_cached_day_short_circuits(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        patch_storage(
            dailydemon,
            initial={
                "dailydemon_2026-07-26": "555"
            },
        )
        fake = FakeGddl()
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            fake,
        )

        got, total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got == 555
        assert total == 0
        assert err == ""
        assert fake.id_calls == []
        assert fake.search_calls == []

    def test_cached_day_does_not_requery_the_level(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        r = patch_storage(
            dailydemon,
            initial={
                "dailydemon_2026-07-26": "555"
            },
        )
        fake = FakeGddl(search_payload={"total": 100})
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            fake,
        )

        got, total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got == 555
        assert total == 0
        assert err == ""
        assert r.get("dailydemon_2026-07-26") == "555"
        assert fake.search_calls == []
        assert fake.index_calls == []

    def test_search_failure(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        patch_storage(dailydemon)
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            FakeGddl(search_payload=None),
        )
        got, total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert (got, total) == (None, 0)
        assert err

    @pytest.mark.parametrize(
        "payload",
        [{}, {"total": 0}],
    )
    def test_zero_total(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
        payload: dict[str, Any],
    ) -> None:
        patch_storage(dailydemon)
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            FakeGddl(search_payload=payload),
        )
        got, total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got is None
        assert total == 0
        assert err

    def test_search_uses_the_fixed_filters(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        patch_storage(dailydemon)
        fake = FakeGddl(
            search_payload={"total": 10},
            by_index=make_gddl_level(1),
        )
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            fake,
        )
        dailydemon.get_daily_demon(FIXED_DAY)
        assert fake.search_calls[0] == {
            "page": 0,
            "limit": 1,
            "sort": "ID",
            **dailydemon.FILTERS,
        }

    def test_pick_is_stored_with_ttl_and_remembered(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        r = patch_storage(dailydemon)
        level = make_gddl_level(31415, "Chosen")
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            FakeGddl(
                search_payload={"total": 500},
                by_index=level,
            ),
        )
        got, total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got == level.ID
        assert (total, err) == (500, "")
        assert (
            r.get("dailydemon_2026-07-26")
            == "31415"
        )
        assert (
            dailydemon.KEEP_SECONDS - 5
            <= r.ttl("dailydemon_2026-07-26")
            <= dailydemon.KEEP_SECONDS
        )
        assert r.get(dailydemon.RECENT_KEY) == [31415]

    def test_first_candidate_not_recent_stops_immediately(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        patch_storage(dailydemon)
        fake = FakeGddl(
            search_payload={"total": 500},
            by_index=make_gddl_level(1),
        )
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            fake,
        )
        dailydemon.get_daily_demon(FIXED_DAY)
        assert len(fake.index_calls) == 1

    def test_recent_collision_rerolls(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        patch_storage(
            dailydemon,
            initial={
                dailydemon.RECENT_KEY: [100]
            },
        )
        fresh = make_gddl_level(200)
        calls = {"n": 0}

        def _by_index(_index: int) -> GDDLSearchEntry:
            calls["n"] += 1
            return (
                make_gddl_level(100)
                if calls["n"] == 1
                else fresh
            )

        fake = FakeGddl(
            search_payload={"total": 500},
            by_index=_by_index,
        )
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            fake,
        )
        got, _total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got == fresh.ID
        assert err == ""
        assert len(fake.index_calls) == 2

    def test_gives_up_after_max_reroll_and_uses_the_last_one(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        patch_storage(
            dailydemon,
            initial={
                dailydemon.RECENT_KEY: [100]
            },
        )
        repeated = make_gddl_level(100)
        fake = FakeGddl(
            search_payload={"total": 1},
            by_index=repeated,
        )
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            fake,
        )
        got, _total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got == repeated.ID
        assert err == ""
        assert len(fake.index_calls) == dailydemon.MAX_REROLL
        assert fake.index_calls == [
            0
        ] * dailydemon.MAX_REROLL

    def test_all_lookups_fail(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        r = patch_storage(dailydemon)
        fake = FakeGddl(
            search_payload={"total": 500},
            by_index=None,
        )
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            fake,
        )
        got, total, err = dailydemon.get_daily_demon(FIXED_DAY)
        assert got is None
        assert total == 500
        assert err
        assert len(fake.index_calls) == dailydemon.MAX_REROLL
        assert (
            r.get("dailydemon_2026-07-26")
            is None
        )
        assert (
            r.get(dailydemon.RECENT_KEY)
            is None
        )

    def test_two_different_days_pick_independently(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        r = patch_storage(dailydemon)
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            FakeGddl(
                search_payload={"total": 500},
                by_index=lambda i: make_gddl_level(i + 1),
            ),
        )
        dailydemon.get_daily_demon(
            date(2026, 7, 26)
        )
        dailydemon.get_daily_demon(
            date(2026, 7, 27)
        )
        assert (
            r.get("dailydemon_2026-07-26")
            is not None
        )
        assert (
            r.get("dailydemon_2026-07-27")
            is not None
        )

    def test_same_day_twice_is_stable(
        self,
        patch_storage: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        patch_storage(dailydemon)
        first_level = make_gddl_level(4242)
        levels = {4242: first_level}
        fake = FakeGddl(
            search_payload={"total": 500},
            by_index=lambda _i: first_level,
            by_id=lambda lid: levels.get(int(lid)),
        )
        monkeypatch.setattr(
            dailydemon,
            "Gddl",
            fake,
        )
        a, _t1, _e1 = dailydemon.get_daily_demon(FIXED_DAY)
        fake.search_payload = {"total": 999}
        b, total_b, _e2 = dailydemon.get_daily_demon(FIXED_DAY)
        assert a == b
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


class TestIconForms:
    def test_nine_gamemodes_in_a_fixed_order(self) -> None:
        assert [f.key for f in icons.FORMS] == [
            "cube",
            "ship",
            "ball",
            "ufo",
            "wave",
            "robot",
            "spider",
            "swing",
            "jetpack",
        ]
        assert (
            icons.form_names()
            == " / ".join(f.key for f in icons.FORMS)
        )

    def test_forms_table_is_self_consistent(self) -> None:
        gduser_fields = {
            attr
            for attr, _ in GDUser.FIELD_MAP.values()
        }
        for form in icons.FORMS:
            assert form.api_type == form.key, form
            assert form.attr in gduser_fields, form
        assert set(icons.FORM_BY_KEY) == {
            f.key for f in icons.FORMS
        }
        with zipfile.ZipFile(
            iconrender.ICONS_ZIP
        ) as zf:
            names = set(zf.namelist())
        for form in icons.FORMS:
            assert (
                f"{form.resource}_01-uhd.plist"
                in names
            ), form

    def test_resolve_form_accepts_every_key_and_alias(self) -> None:
        for form in icons.FORMS:
            assert (
                icons.resolve_form(form.key)
                is form
            ), form
            assert (
                icons.resolve_form(form.key.upper())
                is form
            ), form
        for alias, target in icons.ALIASES.items():
            assert (
                target in icons.FORM_BY_KEY
            ), alias
            assert (
                icons.resolve_form(alias)
                is icons.FORM_BY_KEY[target]
            ), alias
        assert (
            icons.resolve_form(icons.DEFAULT_FORM)
            is icons.FORM_BY_KEY["cube"]
        )

    def test_resolve_form_unknown(self) -> None:
        for name in (
            "",
            "nope",
            "cubes",
            "cu be",
            "立方体",
        ):
            assert icons.resolve_form(name) is None, name


class TestIconFetch:
    async def test_fetch_one_renders_the_requested_icon(self) -> None:
        user = make_gduser(
            acc_ship=47,
            color=9,
            color2=15,
            acc_glow=1,
        )
        img = await icons.fetch_one(
            user,
            icons.FORM_BY_KEY["ship"],
        )
        assert img is not None
        assert img.mode == "RGBA"
        assert img.width > 0 and img.height > 0

    @pytest.mark.parametrize(
        "icon_id",
        [1, 2, 98, 0, None, -5],
    )
    async def test_icon_id_floor_is_1(
        self,
        icon_id: Any,
    ) -> None:
        img = await icons.fetch_one(
            make_gduser(acc_icon=icon_id),
            icons.FORM_BY_KEY["cube"],
        )
        assert img is not None

    async def test_missing_attr_falls_back_to_1(self) -> None:
        user = make_gduser()
        del user.acc_swing
        img = await icons.fetch_one(
            user,
            icons.FORM_BY_KEY["swing"],
        )
        assert img is not None

    async def test_icon_not_in_local_atlas_returns_none(self) -> None:
        img = await icons.fetch_one(
            make_gduser(acc_icon=99999),
            icons.FORM_BY_KEY["cube"],
        )
        assert img is None

    async def test_fetch_all_covers_every_form(self) -> None:
        user = make_gduser()
        for i, form in enumerate(icons.FORMS):
            setattr(
                user,
                form.attr,
                1 + i % 8,
            )
        items = await icons.fetch_all(user)
        assert len(items) == len(icons.FORMS)
        assert [f for f, _ in items] == list(
            icons.FORMS
        )
        for form, img in items:
            assert img is not None, form
            assert img.mode == "RGBA"

    async def test_fetch_all_keeps_none_for_missing_resources(self) -> None:
        user = make_gduser()
        user.acc_spider = 99999
        items = await icons.fetch_all(user)
        by_key = {
            form.key: img
            for form, img in items
        }
        assert by_key["spider"] is None
        assert all(
            img is not None
            for key, img in by_key.items()
            if key != "spider"
        )


def _has_white_pixel(img: Image.Image) -> bool:
    """图里是否存在不透明且接近纯白的像素（UFO 圆顶是白色层）。"""
    px = img.convert("RGBA").load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if (
                a > 0
                and r > 240
                and g > 240
                and b > 240
            ):
                return True
    return False


class TestIconLayoutData:
    """robot/spider 的部件布局数据要自洽。"""

    def test_robot_spider_part_layouts_are_sane(self) -> None:
        assert set(iconrender.ROBOT_PARTS) == {
            "robot",
            "spider",
        }
        for form, layout in iconrender.ROBOT_PARTS.items():
            assert len(layout["slots"]) == len(
                layout["names"]
            ), form
            assert all(
                1 <= slot["part"] <= 4
                for slot in layout["slots"]
            ), form
            assert [
                slot["z"]
                for slot in layout["slots"]
            ] == list(range(len(layout["slots"]))), form
            for idx, tint in layout["tints"].items():
                assert (
                    0 <= int(idx) < len(layout["slots"])
                ), form
                assert 0 <= tint <= 255, form

    async def test_robot_and_spider_render_multi_part(self) -> None:
        for form, attr in (
            ("robot", "acc_robot"),
            ("spider", "acc_spider"),
        ):
            img = await icons.fetch_one(
                make_gduser(**{attr: 5}),
                icons.FORM_BY_KEY[form],
            )
            assert img is not None, form
            assert img.width >= 100 and img.height >= 100, form

    async def test_primary_color_covers_secondary(self) -> None:
        for form, attr, icon_id in (
            ("robot", "acc_robot", 59),
            ("spider", "acc_spider", 19),
        ):
            img = await icons.fetch_one(
                make_gduser(
                    **{
                        attr: icon_id,
                        "color": 15,
                        "color2": 12,
                        "acc_glow": 1,
                        "color3": 51,
                    }
                ),
                icons.FORM_BY_KEY[form],
            )
            assert img is not None, form
            px = img.convert("RGBA").load()
            white = 0
            black = 0
            for y in range(img.height):
                for x in range(img.width):
                    r, g, b, a = px[x, y]
                    if a == 0:
                        continue
                    if (
                        r > 240
                        and g > 240
                        and b > 240
                    ):
                        white += 1
                    elif (
                        r < 40
                        and g < 40
                        and b < 40
                    ):
                        black += 1
            assert black > white, form

    async def test_glow_color_applies_to_robot_and_spider(self) -> None:
        for form, attr in (
            ("robot", "acc_robot"),
            ("spider", "acc_spider"),
        ):
            with_color3 = await icons.fetch_one(
                make_gduser(
                    **{
                        attr: 5,
                        "color": 40,
                        "color2": 11,
                        "acc_glow": 1,
                        "color3": 3,
                    }
                ),
                icons.FORM_BY_KEY[form],
            )
            fallback = await icons.fetch_one(
                make_gduser(
                    **{
                        attr: 5,
                        "color": 40,
                        "color2": 11,
                        "acc_glow": 1,
                    }
                ),
                icons.FORM_BY_KEY[form],
            )
            assert (
                with_color3 is not None
                and fallback is not None
            ), form
            assert not _same_image(
                with_color3,
                fallback,
            ), form


class TestBakedColors:
    async def test_ball_75_keeps_red_tongue(self) -> None:
        img = await icons.fetch_one(
            make_gduser(
                acc_ball=75,
                color=1,
                color2=1,
                acc_glow=0,
            ),
            icons.FORM_BY_KEY["ball"],
        )
        assert img is not None
        px = img.convert("RGBA").load()
        red = 0
        for y in range(img.height):
            for x in range(img.width):
                r, g, b, a = px[x, y]
                if (
                    a > 0
                    and r > 120
                    and r > g + 60
                    and r > b + 60
                ):
                    red += 1
        assert red >= 100


class TestUfoDome:
    async def test_ufo_has_white_dome_but_cube_does_not(self) -> None:
        ufo = await icons.fetch_one(
            make_gduser(
                acc_bird=7,
                color=3,
                color2=1,
                acc_glow=0,
            ),
            icons.FORM_BY_KEY["ufo"],
        )
        cube = await icons.fetch_one(
            make_gduser(
                acc_icon=7,
                color=3,
                color2=1,
                acc_glow=0,
            ),
            icons.FORM_BY_KEY["cube"],
        )
        assert ufo is not None and cube is not None
        assert _has_white_pixel(ufo)
        assert not _has_white_pixel(cube)


def _same_image(
    a: Image.Image,
    b: Image.Image,
) -> bool:
    """两张图逐像素一致。注意 RGBA 的 getbbox 只认 alpha，得先转 RGB 再比。"""
    return (
        ImageChops.difference(
            a.convert("RGB"),
            b.convert("RGB"),
        ).getbbox()
        is None
    )


class TestIconGlowColor:
    """辉光颜色要跟 GD 一致：color3 优先，没设置就回退 color2。"""

    def test_glowc_none_falls_back_to_color2(self) -> None:
        default = iconrender.get_icon_from_cols(
            "player_ball",
            97,
            40,
            11,
            True,
            None,
        )
        explicit = iconrender.get_icon_from_cols(
            "player_ball",
            97,
            40,
            11,
            True,
            11,
        )
        assert _same_image(default, explicit)

    def test_glowc_overrides_color2(self) -> None:
        with_color2 = iconrender.get_icon_from_cols(
            "player_ball",
            97,
            40,
            11,
            True,
            11,
        )
        with_color3 = iconrender.get_icon_from_cols(
            "player_ball",
            97,
            40,
            11,
            True,
            3,
        )
        assert not _same_image(
            with_color2,
            with_color3,
        )

    def test_glow_off_ignores_glowc(self) -> None:
        a = iconrender.get_icon_from_cols(
            "player_ball",
            97,
            40,
            11,
            False,
            3,
        )
        b = iconrender.get_icon_from_cols(
            "player_ball",
            97,
            40,
            11,
            False,
            11,
        )
        assert _same_image(a, b)

    async def test_fetch_one_passes_color3_as_glow_color(self) -> None:
        with_color3 = await icons.fetch_one(
            make_gduser(
                acc_ball=97,
                color=40,
                color2=11,
                acc_glow=1,
                color3=3,
            ),
            icons.FORM_BY_KEY["ball"],
        )
        fallback = await icons.fetch_one(
            make_gduser(
                acc_ball=97,
                color=40,
                color2=11,
                acc_glow=1,
            ),
            icons.FORM_BY_KEY["ball"],
        )
        assert (
            with_color3 is not None
            and fallback is not None
        )
        assert not _same_image(
            with_color3,
            fallback,
        )


def sheet_size(rows: int) -> tuple[int, int]:
    """按 icons 的几何常量算出 rows 行时画布该有多大。"""
    return (
        icons.PAD * 2
        + icons.CELL * icons.GRID_COLS,
        icons.PAD * 2
        + icons.TITLE_H
        + icons.CELL * rows,
    )


class TestIconCompose:
    pytestmark = pytest.mark.slow

    def test_canvas_geometry(self) -> None:
        for count, rows in [
            (0, 0),
            (1, 1),
            (3, 1),
            (4, 2),
            (6, 2),
            (7, 3),
            (9, 3),
            (10, 4),
        ]:
            items = [
                (
                    icons.FORMS[i % len(icons.FORMS)],
                    None,
                )
                for i in range(count)
            ]
            sheet = icons.compose_sheet(
                make_gduser(),
                items,
            )
            assert sheet.mode == "RGBA", count
            assert sheet.size == sheet_size(rows), count

    def test_nine_icons_sheet(self) -> None:
        items = [
            (
                f,
                Image.new(
                    "RGBA",
                    (60, 60),
                    (0, 128, 255, 255),
                ),
            )
            for f in icons.FORMS
        ]
        sheet = icons.compose_sheet(
            make_gduser(),
            items,
        )
        assert sheet.size == sheet_size(3)

    def test_missing_icons_are_skipped(self) -> None:
        items: list[
            tuple[icons.Form, Image.Image | None]
        ] = [
            (
                icons.FORMS[0],
                Image.new(
                    "RGBA",
                    (60, 60),
                    (255, 255, 255, 255),
                ),
            ),
            (icons.FORMS[1], None),
            (icons.FORMS[2], None),
        ]
        sheet = icons.compose_sheet(
            make_gduser(),
            items,
        )
        assert sheet.size == sheet_size(1)

    def test_nameless_user_gets_placeholder(self) -> None:
        sheet = icons.compose_sheet(
            make_gduser(user_name=None),
            [],
        )
        assert sheet.size == sheet_size(0)

    def test_oversized_icons_are_scaled_down(self) -> None:
        big = Image.new(
            "RGBA",
            (512, 512),
            (1, 2, 3, 255),
        )
        sheet = icons.compose_sheet(
            make_gduser(),
            [(icons.FORMS[0], big)],
        )
        assert sheet.size == sheet_size(1)

    def test_fit_scaling(self) -> None:
        box = icons.ICON_BOX
        for size, expected in [
            ((60, 60), (60, 60)),
            ((box, box), (box, box)),
            ((box * 2, box * 2), (box, box)),
            ((box * 2, box), (box, box // 2)),
            ((box, box * 2), (box // 2, box)),
            ((10000, 1), (box, 1)),
        ]:
            fitted = icons._fit(
                Image.new("RGBA", size),
                box,
            )
            assert fitted.size == expected, size

    def test_fit_returns_the_same_object_when_no_scaling_needed(
        self,
    ) -> None:
        img = Image.new("RGBA", (50, 50))
        assert (
            icons._fit(
                img,
                icons.ICON_BOX,
            )
            is img
        )
