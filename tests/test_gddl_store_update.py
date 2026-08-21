from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

import xiaozu_bot.plugins.gdlevelsearch.updater as updater_pkg
from tests.conftest import FakeBot, run_handler, sent_texts
from xiaozu_bot.plugins import gdlevelsearch
from xiaozu_bot.plugins.gdlevelsearch import gddl_store, gddlapi


def test_fetch_all_levels_uses_five_request_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    max_active = 0
    lock = threading.Lock()
    first_batch = threading.Barrier(5)
    calls: list[int] = []
    sleeps: list[float] = []

    def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def fake_page(page: int = 0, **_: Any) -> dict[str, Any]:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            calls.append(page)
        try:
            if 1 <= page <= 5:
                first_batch.wait(timeout=2)
            start = page * gddl_store.PAGE_SIZE
            return {
                "total": 275,
                "data": [
                    {"id": start + offset}
                    for offset in range(gddl_store.PAGE_SIZE)
                ],
            }
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(
        gddlapi.Gddl,
        "_searchlevels_online",
        staticmethod(fake_page),
    )
    monkeypatch.setattr(
        gddl_store.time,
        "sleep",
        record_sleep,
    )

    levels = gddl_store.fetch_all_levels()

    assert levels is not None
    assert len(levels) == 275
    assert max_active == 5
    assert sorted(calls) == list(range(11))
    assert sleeps == [gddl_store.FETCH_INTERVAL, gddl_store.FETCH_INTERVAL]


async def test_gddl_store_update_job_refreshes_under_shared_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    monkeypatch.setattr(updater_pkg, "_gddl_lock", asyncio.Lock())
    monkeypatch.setattr(
        gddl_store,
        "refresh",
        lambda: called.append("refresh") or True,
    )

    assert await updater_pkg.gddl_store_update_job() is True
    assert called == ["refresh"]


async def test_gdsearch_store_update_command_reports_success(
    monkeypatch: pytest.MonkeyPatch,
    fake_bot: FakeBot,
    make_group_event: Any,
) -> None:
    monkeypatch.setattr(
        updater_pkg,
        "gddl_store_update_job",
        _successful_update,
    )

    await run_handler(
        gdlevelsearch.gdsearch_store_update,
        fake_bot,
        make_group_event("*gdsearch_store_update", user_id=10000),
    )

    messages = sent_texts(fake_bot)
    assert messages[0].endswith("GDDL store...")
    assert messages[1].startswith("✅ GDDL store")


async def _successful_update() -> bool:
    return True
