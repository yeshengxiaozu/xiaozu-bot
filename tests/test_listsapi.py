"""gdlevelsearch/api/listsapi.py 的内存缓存与容错测试。

锁的是这几条行为：
1. 四个榜单首次查询时加载一次，之后常驻内存，draw 时不再重复读文件；
2. `reload()` 重新加载，updater 跑完后由 `reload_all()` 调用；
3. 缺文件 / 坏 JSON / BOM / 个别坏条目都不能让 `search_level` 崩掉。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xiaozu_bot.plugins.gdlevelsearch.api import listsapi


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    listsapi._cache = None
    yield
    listsapi._cache = None


def _make_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    idl = tmp_path / "idl.json"
    idl.write_text(
        json.dumps(
            {
                "timestamp": 123.0,
                "levels": [
                    {"position": "1", "id": 100, "name": "A", "creator": "x"},
                    {"position": "2", "id": "101", "name": "B", "creator": "y"},
                ],
            }
        ),
        encoding="utf-8",
    )
    hdl = tmp_path / "hdl.json"
    hdl.write_text(
        json.dumps(
            [
                {"id": "200", "position": "1", "name": "C", "creator": "z"},
                {"id": 201, "position": "2", "name": "D", "creator": "w"},
            ]
        ),
        encoding="utf-8",
    )
    mdl = tmp_path / "mdl.json"
    mdl.write_text(json.dumps([{"id": 300}]), encoding="utf-8")
    edl = tmp_path / "edl.json"
    edl.write_text(json.dumps([{"id": 400}]), encoding="utf-8")
    paths = {
        "IDL": idl,
        "HDL": hdl,
        "MDL": mdl,
        "EDL": edl,
    }
    monkeypatch.setattr(listsapi, "DATA_PATHS", paths)
    return paths


class TestListsSearch:
    def test_混合结构按排名返回(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_files(tmp_path, monkeypatch)

        assert listsapi.Lists.search_level(100) == "IDL #1"
        assert listsapi.Lists.search_level("101") == "IDL #2"
        assert listsapi.Lists.search_level(200) == "HDL #1"
        assert listsapi.Lists.search_level(201) == "HDL #2"
        assert listsapi.Lists.search_level(999999) is None

    def test_非法或零id返回None(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_files(tmp_path, monkeypatch)

        assert listsapi.Lists.search_level("abc") is None
        assert listsapi.Lists.search_level(0) is None
        assert listsapi.Lists.search_level(None) is None

    def test_文件全缺失不报错(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            listsapi,
            "DATA_PATHS",
            {
                "IDL": tmp_path / "missing1.json",
                "HDL": tmp_path / "missing2.json",
                "MDL": tmp_path / "missing3.json",
                "EDL": tmp_path / "missing4.json",
            },
        )

        assert listsapi.Lists.search_level(123) is None

    def test_IDL纯列表结构也兼容(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        paths = _make_files(tmp_path, monkeypatch)
        paths["IDL"].write_text(json.dumps([{"id": 300}]), encoding="utf-8")

        assert listsapi.Lists.search_level(300) == "IDL #1"


class TestMemoryCache:
    def test_首次查询后不再读文件(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_files(tmp_path, monkeypatch)
        calls: list[str] = []
        original = listsapi._load_level_ids

        def counting(name: str, path: Path) -> list[int]:
            calls.append(name)
            return original(name, path)

        monkeypatch.setattr(listsapi, "_load_level_ids", counting)

        assert listsapi.Lists.search_level(100) == "IDL #1"
        assert listsapi.Lists.search_level(200) == "HDL #1"
        assert calls == ["IDL", "HDL", "MDL", "EDL"]

        # 再查一次：命中缓存，不再解析文件
        assert listsapi.Lists.search_level(100) == "IDL #1"
        assert listsapi.Lists.search_level(200) == "HDL #1"
        assert calls == ["IDL", "HDL", "MDL", "EDL"]

    def test_文件改动reload前不生效(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        paths = _make_files(tmp_path, monkeypatch)
        assert listsapi.Lists.search_level(100) == "IDL #1"

        data = json.loads(paths["IDL"].read_text(encoding="utf-8"))
        data["levels"][0]["id"] = 999
        paths["IDL"].write_text(json.dumps(data), encoding="utf-8")

        # 没 reload：还是旧缓存
        assert listsapi.Lists.search_level(100) == "IDL #1"
        assert listsapi.Lists.search_level(999) is None

        listsapi.reload()
        assert listsapi.Lists.search_level(999) == "IDL #1"


class TestRobustness:
    def test_带BOM的文件能解析(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        paths = _make_files(tmp_path, monkeypatch)
        paths["IDL"].write_bytes(b"\xef\xbb\xbf" + paths["IDL"].read_bytes())

        assert listsapi.Lists.search_level(100) == "IDL #1"

    def test_坏JSON只影响自己(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        paths = _make_files(tmp_path, monkeypatch)
        paths["MDL"].write_text("{broken json", encoding="utf-8")

        assert listsapi.Lists.search_level(100) == "IDL #1"
        assert listsapi.Lists.search_level(200) == "HDL #1"

    def test_个别坏条目被跳过(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        paths = _make_files(tmp_path, monkeypatch)
        paths["MDL"].write_text(
            json.dumps(
                [
                    {"id": 500},
                    {"id": None},
                    {"name": "no id at all"},
                    "garbage entry",
                    {"id": "501"},
                ]
            ),
            encoding="utf-8",
        )

        assert listsapi.Lists.search_level(500) == "MDL #1"
        assert listsapi.Lists.search_level(501) == "MDL #2"
        assert listsapi.Lists.search_level(None) is None

    def test_reload失败时保留旧数据(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        paths = _make_files(tmp_path, monkeypatch)
        assert listsapi.Lists.search_level(100) == "IDL #1"

        paths["IDL"].write_text("{broken json", encoding="utf-8")
        listsapi.reload()

        assert listsapi.Lists.search_level(100) == "IDL #1"
