"""*gdsearch_manage / *gdsearch_status 的测试。

两个命令都是 SUPERUSER 专用，测试直接调 handler（run_handler 不经过权限检查）。
运行时文件都通过 fixture 指到 tmp_path，绝不碰仓库 data/。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeBot, run_handler, sent_texts
from xiaozu_bot.plugins.gdlevelsearch import (
    gdsearchmanage,
    gdsearchmanage_select,
    gdsearchstatus,
)
from xiaozu_bot.plugins.gdlevelsearch.commands import manage


@pytest.fixture(autouse=True)
def _clean_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例独立的会话状态。"""
    monkeypatch.setattr(manage, "manage_sessions", {})


@pytest.fixture
def manage_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 manage 的两个运行时文件指到 tmp_path。"""
    monkeypatch.setattr(manage, "UNMATCHED_PATH", tmp_path / "metadata_unmatched.json")
    monkeypatch.setattr(manage, "METADATA_CACHE_PATH", tmp_path / "metadata.json")
    return tmp_path


def _write_unmatched(tmp: Path, entries: list[dict]) -> None:
    (tmp / "metadata_unmatched.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )


def _write_cache(tmp: Path, entries: list[dict]) -> None:
    (tmp / "metadata.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sample_entries() -> list[dict]:
    return [
        {"name": "Alpha", "creator": "alice", "reason": "not-found", "added_at": "t"},
        {"name": "Beta", "creator": "bob", "reason": "ambiguous", "added_at": "t"},
        {"name": "Gamma", "creator": "carol", "reason": "not-found", "added_at": "t"},
    ]


class TestGdsearchManage:
    async def test_empty_args_shows_first_page_and_starts_session(
        self, fake_bot: FakeBot, make_group_event: Any, manage_paths: Path
    ) -> None:
        _write_unmatched(manage_paths, _sample_entries())
        await run_handler(
            gdsearchmanage, fake_bot, make_group_event("*gdsearch_manage"), arg=""
        )
        texts = sent_texts(fake_bot)
        assert len(texts) == 1
        for token in (
            "共 3 条",
            "1. Alpha by alice",
            "2. Beta by bob [多条候选]",
            "3. Gamma by carol [查无此关]",
        ):
            assert token in texts[0], token
        assert manage.manage_sessions != {}

    async def test_assign_writes_cache_and_removes_entry(
        self, fake_bot: FakeBot, make_group_event: Any, manage_paths: Path
    ) -> None:
        _write_unmatched(manage_paths, _sample_entries())
        await run_handler(
            gdsearchmanage, fake_bot, make_group_event("*gdsearch_manage 1 123456"),
            arg="1 123456",
        )
        text = sent_texts(fake_bot)[-1]
        assert "已记录：Alpha by alice -> 123456" in text
        assert "剩余 2 条" in text
        cache = _read(manage_paths / "metadata.json")
        assert cache == [{"name": "Alpha", "creator": "alice", "id": 123456}]
        remaining = _read(manage_paths / "metadata_unmatched.json")
        assert [(e["name"], e["creator"]) for e in remaining] == [
            ("Beta", "bob"),
            ("Gamma", "carol"),
        ]

    async def test_last_assign_ends_session(
        self, fake_bot: FakeBot, make_group_event: Any, manage_paths: Path
    ) -> None:
        _write_unmatched(manage_paths, _sample_entries()[:1])
        await run_handler(
            gdsearchmanage, fake_bot, make_group_event("*gdsearch_manage 1 7"), arg="1 7"
        )
        text = sent_texts(fake_bot)[-1]
        assert "没有剩余待匹配关卡了" in text
        assert manage.manage_sessions == {}
        assert _read(manage_paths / "metadata_unmatched.json") == []

    async def test_zero_id_is_rejected(
        self, fake_bot: FakeBot, make_group_event: Any, manage_paths: Path
    ) -> None:
        _write_unmatched(manage_paths, _sample_entries())
        await run_handler(
            gdsearchmanage, fake_bot, make_group_event("*gdsearch_manage 1 0"), arg="1 0"
        )
        assert "正整数" in sent_texts(fake_bot)[-1]

    async def test_out_of_range_index_is_rejected(
        self, fake_bot: FakeBot, make_group_event: Any, manage_paths: Path
    ) -> None:
        _write_unmatched(manage_paths, _sample_entries())
        await run_handler(
            gdsearchmanage, fake_bot, make_group_event("*gdsearch_manage 99 1"), arg="99 1"
        )
        assert "序号超出范围" in sent_texts(fake_bot)[-1]

    async def test_help_subcommand_returns_usage(
        self, fake_bot: FakeBot, make_group_event: Any
    ) -> None:
        await run_handler(
            gdsearchmanage, fake_bot, make_group_event("*gdsearch_manage help"), arg="help"
        )
        text = sent_texts(fake_bot)[-1]
        assert "*gdsearch_manage" in text
        assert "*gdsearch_status" in text

    async def test_paging(
        self, fake_bot: FakeBot, make_group_event: Any, manage_paths: Path
    ) -> None:
        entries = [
            {"name": f"L{i:02d}", "creator": "c", "reason": "not-found", "added_at": "t"}
            for i in range(1, 12)
        ]
        _write_unmatched(manage_paths, entries)
        event = make_group_event("*gdsearch_manage")
        await run_handler(gdsearchmanage, fake_bot, event, arg="")
        first = sent_texts(fake_bot)[-1]
        assert "第 1/2 页" in first and "1. L01 by c" in first and "10. L10 by c" in first
        await run_handler(gdsearchmanage, fake_bot, event, arg="n")
        second = sent_texts(fake_bot)[-1]
        assert "第 2/2 页" in second and "11. L11 by c" in second
        await run_handler(gdsearchmanage, fake_bot, event, arg="p")
        assert "第 1/2 页" in sent_texts(fake_bot)[-1]

    async def test_exit_ends_session(
        self, fake_bot: FakeBot, make_group_event: Any, manage_paths: Path
    ) -> None:
        _write_unmatched(manage_paths, _sample_entries())
        await run_handler(
            gdsearchmanage, fake_bot, make_group_event("*gdsearch_manage 结束"), arg="结束"
        )
        assert "已退出" in sent_texts(fake_bot)[-1]
        assert manage.manage_sessions == {}

    async def test_select_message_assigns_during_session(
        self, fake_bot: FakeBot, make_group_event: Any, manage_paths: Path
    ) -> None:
        _write_unmatched(manage_paths, _sample_entries())
        await run_handler(
            gdsearchmanage, fake_bot, make_group_event("*gdsearch_manage"), arg=""
        )
        assert manage.manage_sessions != {}
        await run_handler(gdsearchmanage_select, fake_bot, make_group_event("1 42"))
        assert "已记录：Alpha by alice -> 42" in sent_texts(fake_bot)[-1]
        cache = _read(manage_paths / "metadata.json")
        assert cache == [{"name": "Alpha", "creator": "alice", "id": 42}]

    async def test_select_ignores_foreign_text(
        self, fake_bot: FakeBot, make_group_event: Any, manage_paths: Path
    ) -> None:
        _write_unmatched(manage_paths, _sample_entries())
        await run_handler(
            gdsearchmanage, fake_bot, make_group_event("*gdsearch_manage"), arg=""
        )
        before = len(sent_texts(fake_bot))
        await run_handler(gdsearchmanage_select, fake_bot, make_group_event("hello world"))
        assert len(sent_texts(fake_bot)) == before


class TestGdsearchStatus:
    async def test_status_reports_counts_and_times(
        self,
        fake_bot: FakeBot,
        make_group_event: Any,
        manage_paths: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_unmatched(manage_paths, _sample_entries())
        _write_cache(manage_paths, [{"name": "A", "creator": "a", "id": 1}])
        # 状态命令读 paths.DATA_DIR 下的榜单/aredl 文件，指到 tmp
        monkeypatch.setattr(manage.paths, "DATA_DIR", manage_paths)
        for name, count in [
            ("nlw_levels.json", 10),
            ("ids_levels.json", 5),
            ("lw_levels.json", 7),
            ("hds_levels.json", 3),
            ("aredl_levels.json", 2),
            ("arepl_levels.json", 4),
        ]:
            (manage_paths / name).write_text(
                json.dumps({"levels": list(range(count))}), encoding="utf-8"
            )

        await run_handler(gdsearchstatus, fake_bot, make_group_event("*gdsearch_status"))

        text = sent_texts(fake_bot)[-1]
        for token in (
            "待手动匹配关卡：3",
            "metadata 缓存：1 条",
            "nlw: 10",
            "ids: 5",
            "lw: 7",
            "hds: 3",
            "aredl 2、arepl 4",
            "榜单最后修改时间（四者最早）：",
            "AREDL 最后修改时间（两者最早）：",
        ):
            assert token in text, token

    async def test_status_tolerates_missing_files(
        self, fake_bot: FakeBot, make_group_event: Any, manage_paths: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """data/ 里什么都没有时也能出状态，不抛。"""
        monkeypatch.setattr(manage.paths, "DATA_DIR", manage_paths)
        await run_handler(gdsearchstatus, fake_bot, make_group_event("*gdsearch_status"))
        text = sent_texts(fake_bot)[-1]
        assert "待手动匹配关卡：0" in text
        assert "无" in text
