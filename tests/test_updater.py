"""gdlevelsearch/updater 子系统的测试。

覆盖三块：

1. `paths.py` —— staging / published 两级目录的划分，以及"跑完才发布"里
   那个 `publish()` 的原子搬运语义；
2. `runner.py` —— 分层并发编排，重点锁住 **任何一个 job 挂了就绝不发布**
   这条不变量（commit 34cace4 就是为了它做的）；
3. `jobs/*` —— 把"纯转换（原始表格列 -> 规整记录）"和"抓取"拆开，
   只测纯转换那一半，一个网络请求都不发。

几处 ⚠️ 开头的用例锁的是**当前实际行为**而不是正确行为，具体见各自的
docstring —— 那几个地方源码有 bug，按约定这里只记录不修。
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from googleapiclient.errors import HttpError

import xiaozu_bot.plugins.gdlevelsearch as gdlevelsearch
import xiaozu_bot.plugins.gdlevelsearch.updater as updater_pkg
from xiaozu_bot.plugins.gdlevelsearch import gdapi
from xiaozu_bot.plugins.gdlevelsearch.updater import notify, paths, runner
from xiaozu_bot.plugins.gdlevelsearch.updater.jobs import (
    constants,
    fetchsfh,
    getmetadata,
    googlesheetapi,
    hds,
    ids,
    lw,
    metadata,
    nlw,
    platbatch,
    platdata,
    platdiff,
    platrank,
)
from xiaozu_bot.plugins.gdlevelsearch.updater.jobs import platapi as jobs_platapi

pytestmark = pytest.mark.updater

# 表格里用到的几个非 ASCII 字符，写成转义避免在别的编辑器里被吃掉
ARROW = "↓"  # ↓，ids / hds 的分档表头前缀
INF = "∞"  # ∞，hds 里"无限存档点"


# ==========================================================================
# 公共 fixture / 小工具
# ==========================================================================
@pytest.fixture
def data_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """把 paths.DATA_DIR / STAGING_DIR 指到 tmp_path，绝不碰仓库里的 data/。

    paths 里那几个函数都是运行时读模块级全局，所以在这里换掉就等于
    所有 `from ..paths import staged` 的 job 也一起换掉了。
    """
    data = tmp_path / "data"
    staging = data / ".staging"
    staging.mkdir(parents=True)
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "STAGING_DIR", staging)
    return SimpleNamespace(data=data, staging=staging)


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class FakeClock:
    """替换 job 模块里的 `time`，把 sleep 记下来而不是真睡。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def time(self) -> float:
        return self.now


# ==========================================================================
# paths.py
# ==========================================================================
class TestPaths:
    """staging / published 的目录划分与原子发布"""

    def test_目录常量由_file_推出来且不随_cwd_变化(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = (paths.PLUGIN_DIR, paths.DATA_DIR, paths.STAGING_DIR)
        assert all(p.is_absolute() for p in before)

        monkeypatch.chdir(tmp_path)

        assert (paths.PLUGIN_DIR, paths.DATA_DIR, paths.STAGING_DIR) == before
        assert paths.PLUGIN_DIR == Path(paths.__file__).resolve().parent.parent
        assert paths.PLUGIN_DIR.name == "gdlevelsearch"
        assert paths.DATA_DIR == paths.PLUGIN_DIR / "data"
        assert paths.STAGING_DIR == paths.DATA_DIR / ".staging"

    def test_发布清单与中间产物清单不重叠(self) -> None:
        # bot 只读 PUBLISHED_FILES 里这几个，INTERMEDIATE_FILES 是 platbatch 的输入
        assert paths.PUBLISHED_FILES == (
            "nlw_levels.json",
            "ids_levels.json",
            "lw_levels.json",
            "hds_levels.json",
            "plat_combined.json",
            "nong_index.json",
        )
        assert paths.INTERMEDIATE_FILES == (
            "platdata.json",
            "platdiff.json",
            "platrank_weights.json",
        )
        assert not set(paths.PUBLISHED_FILES) & set(paths.INTERMEDIATE_FILES)

    def test_ensure_dirs_两级目录都建出来(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = tmp_path / "nested" / "data"
        monkeypatch.setattr(paths, "DATA_DIR", data)
        monkeypatch.setattr(paths, "STAGING_DIR", data / ".staging")

        assert not data.exists()
        paths.ensure_dirs()

        assert data.is_dir()
        assert (data / ".staging").is_dir()

    def test_ensure_dirs_可以重复调(self, data_dirs: SimpleNamespace) -> None:
        paths.ensure_dirs()
        paths.ensure_dirs()
        assert data_dirs.staging.is_dir()

    def test_staged_落在_staging_并顺手建目录(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = tmp_path / "data"
        staging = data / ".staging"
        monkeypatch.setattr(paths, "DATA_DIR", data)
        monkeypatch.setattr(paths, "STAGING_DIR", staging)

        assert not staging.exists()
        p = paths.staged("nlw_levels.json")

        assert p == staging / "nlw_levels.json"
        assert staging.is_dir()  # staged() 自己会把目录建出来

    def test_staged_不受_cwd_影响(
        self, data_dirs: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        p = paths.staged("nlw_levels.json")
        assert p.is_absolute()
        assert p == data_dirs.staging / "nlw_levels.json"

    def test_staged_or_published_优先本次抓到的(
        self, data_dirs: SimpleNamespace
    ) -> None:
        (data_dirs.staging / "nlw_levels.json").write_text("new", encoding="utf-8")
        (data_dirs.data / "nlw_levels.json").write_text("old", encoding="utf-8")

        assert (
            paths.staged_or_published("nlw_levels.json")
            == data_dirs.staging / "nlw_levels.json"
        )

    def test_staged_or_published_没抓到就退回上一次发布的(
        self, data_dirs: SimpleNamespace
    ) -> None:
        (data_dirs.data / "nlw_levels.json").write_text("old", encoding="utf-8")

        assert (
            paths.staged_or_published("nlw_levels.json")
            == data_dirs.data / "nlw_levels.json"
        )

    def test_staged_or_published_两边都没有也返回_data_下的路径(
        self, data_dirs: SimpleNamespace
    ) -> None:
        # 不存在时不抛，返回 DATA_DIR 下的路径，交给调用方自己 FileNotFoundError
        p = paths.staged_or_published("missing.json")
        assert p == data_dirs.data / "missing.json"
        assert not p.exists()

    def test_publish_按清单顺序搬运并留下中间产物(
        self, data_dirs: SimpleNamespace
    ) -> None:
        # 故意乱序写入，验证返回值是按 PUBLISHED_FILES 的顺序而不是写入顺序
        (data_dirs.staging / "nong_index.json").write_text("n", encoding="utf-8")
        (data_dirs.staging / "nlw_levels.json").write_text("a", encoding="utf-8")
        (data_dirs.staging / "platdata.json").write_text("mid", encoding="utf-8")

        moved = paths.publish()

        assert moved == ["nlw_levels.json", "nong_index.json"]
        assert (data_dirs.data / "nlw_levels.json").read_text(encoding="utf-8") == "a"
        assert (data_dirs.data / "nong_index.json").read_text(encoding="utf-8") == "n"
        # 搬走之后 staging 里不该再有这两个
        assert not (data_dirs.staging / "nlw_levels.json").exists()
        # 中间产物不发布，留在 staging
        assert (data_dirs.staging / "platdata.json").exists()
        assert not (data_dirs.data / "platdata.json").exists()

    def test_publish_覆盖上一次的同名文件(self, data_dirs: SimpleNamespace) -> None:
        (data_dirs.data / "lw_levels.json").write_text("old", encoding="utf-8")
        (data_dirs.staging / "lw_levels.json").write_text("new", encoding="utf-8")

        assert paths.publish() == ["lw_levels.json"]
        assert (data_dirs.data / "lw_levels.json").read_text(encoding="utf-8") == "new"

    def test_publish_staging_为空时返回空列表(
        self, data_dirs: SimpleNamespace
    ) -> None:
        assert paths.publish() == []

    def test_clear_staging_只删文件不删子目录(
        self, data_dirs: SimpleNamespace
    ) -> None:
        (data_dirs.staging / "a.json").write_text("x", encoding="utf-8")
        (data_dirs.staging / "b.json").write_text("y", encoding="utf-8")
        sub = data_dirs.staging / "sub"
        sub.mkdir()
        (sub / "keep.json").write_text("z", encoding="utf-8")
        (data_dirs.data / "published.json").write_text("keep", encoding="utf-8")

        paths.clear_staging()

        assert list(data_dirs.staging.iterdir()) == [sub]
        assert (sub / "keep.json").exists()  # 子目录里的东西不动
        assert (data_dirs.data / "published.json").exists()  # 已发布的绝不能动

    def test_clear_staging_目录不存在时什么都不做(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(paths, "STAGING_DIR", tmp_path / "nope")
        paths.clear_staging()  # 不该抛


# ==========================================================================
# runner.py —— 分层并发 + "跑完才发布"
# ==========================================================================
@pytest.fixture
def fresh_lock(monkeypatch: pytest.MonkeyPatch) -> asyncio.Lock:
    """给每个用例换一把新的 asyncio.Lock。

    模块级那把是 import 期建的，asyncio.Lock 一旦在某个事件循环上用过就绑死了，
    而 pytest-asyncio 每个用例一个新循环 —— 不换的话用例之间会互相污染。
    """
    lock = asyncio.Lock()
    monkeypatch.setattr(runner, "_lock", lock)
    return lock


def _install_jobs(
    monkeypatch: pytest.MonkeyPatch, jobs: dict, stages: tuple
) -> None:
    monkeypatch.setattr(runner, "JOBS", jobs)
    monkeypatch.setattr(runner, "STAGES", stages)


class TestRunnerTables:
    """JOBS / STAGES 这两张表本身"""

    def test_jobs_表就是这十个且指向对的函数(self) -> None:
        assert runner.JOBS == {
            "nlw": nlw.fetch,
            "ids": ids.fetch,
            "lw": lw.fetch,
            "hds": hds.fetch,
            "platdiff": platdiff.fetch,
            "platrank": platrank.fetch,
            "platdata": platdata.fetch,
            "platbatch": platbatch.batch,
            "sfh": fetchsfh.main,
            "getmetadata": getmetadata.main,
        }

    def test_stages_两层且不重不漏(self) -> None:
        assert runner.STAGES == (
            ("nlw", "ids", "lw", "hds", "platdiff", "platrank", "platdata", "sfh"),
            ("platbatch", "getmetadata"),
        )
        flat = [name for stage in runner.STAGES for name in stage]
        assert len(flat) == len(set(flat)), "同一个 job 不该出现在两层里"
        assert set(flat) == set(runner.JOBS), "STAGES 必须正好覆盖 JOBS"

    def test_有依赖的_job_排在第二层(self) -> None:
        # platbatch 要读 platdata/platdiff/platrank_weights，
        # getmetadata 要读 nlw/ids/lw/hds —— 它们的上游全在第一层
        assert set(runner.STAGES[1]) == {"platbatch", "getmetadata"}
        assert {"platdata", "platdiff", "platrank"} <= set(runner.STAGES[0])
        assert {"nlw", "ids", "lw", "hds"} <= set(runner.STAGES[0])


class TestRunnerRunAll:
    """run_all_async 的编排语义"""

    async def test_全部成功才发布(
        self,
        data_dirs: SimpleNamespace,
        fresh_lock: asyncio.Lock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def job_nlw() -> None:
            paths.staged("nlw_levels.json").write_text("NEW", encoding="utf-8")

        def job_batch() -> None:
            paths.staged("plat_combined.json").write_text("COMBINED", encoding="utf-8")

        _install_jobs(
            monkeypatch,
            {"nlw": job_nlw, "batch": job_batch},
            (("nlw",), ("batch",)),
        )

        result = await runner.run_all_async()

        assert result["failed"] == []
        assert sorted(result["success"]) == ["batch", "nlw"]
        assert result["published"] == ["nlw_levels.json", "plat_combined.json"]
        assert (data_dirs.data / "nlw_levels.json").read_text(encoding="utf-8") == "NEW"
        assert (
            data_dirs.data / "plat_combined.json"
        ).read_text(encoding="utf-8") == "COMBINED"
        # 发布完 staging 里就空了
        assert list(data_dirs.staging.iterdir()) == []

    async def test_某个_job_挂了就绝不发布_线上数据一动不动(
        self,
        data_dirs: SimpleNamespace,
        fresh_lock: asyncio.Lock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """这条是整个 updater 最重要的不变量（commit 34cace4）。"""
        (data_dirs.data / "nlw_levels.json").write_text("OLD", encoding="utf-8")
        ran: list[str] = []

        def job_ok() -> None:
            ran.append("ok")
            paths.staged("nlw_levels.json").write_text("NEW", encoding="utf-8")

        def job_bad() -> None:
            ran.append("bad")
            raise ValueError("表格挂了")

        def job_second_layer() -> None:  # pragma: no cover - 不该被跑到
            ran.append("second")

        _install_jobs(
            monkeypatch,
            {"ok": job_ok, "bad": job_bad, "second": job_second_layer},
            (("ok", "bad"), ("second",)),
        )

        with pytest.raises(RuntimeError) as excinfo:
            await runner.run_all_async()

        assert "Updater failed" in str(excinfo.value)
        assert "表格挂了" in str(excinfo.value)
        assert "ValueError" in str(excinfo.value)
        # 第二层根本没起来
        assert "second" not in ran
        # 线上那份还是老的
        assert (data_dirs.data / "nlw_levels.json").read_text(encoding="utf-8") == "OLD"
        # staging 留着方便查问题
        assert (
            data_dirs.staging / "nlw_levels.json"
        ).read_text(encoding="utf-8") == "NEW"

    async def test_stop_on_error_false_会跑完所有层但依然不发布(
        self,
        data_dirs: SimpleNamespace,
        fresh_lock: asyncio.Lock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ran: list[str] = []

        def job_bad() -> None:
            ran.append("bad")
            raise ValueError("boom")

        def job_second() -> None:
            ran.append("second")
            paths.staged("hds_levels.json").write_text("NEW", encoding="utf-8")

        _install_jobs(
            monkeypatch,
            {"bad": job_bad, "second": job_second},
            (("bad",), ("second",)),
        )

        with pytest.raises(RuntimeError):
            await runner.run_all_async(stop_on_error=False)

        assert ran == ["bad", "second"]  # 第二层照跑
        assert not (data_dirs.data / "hds_levels.json").exists()  # 但还是不发布

    async def test_跑之前会清掉上次留下的残渣(
        self,
        data_dirs: SimpleNamespace,
        fresh_lock: asyncio.Lock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 上一次失败留在 staging 里的半成品
        (data_dirs.staging / "hds_levels.json").write_text("STALE", encoding="utf-8")

        def job_nlw() -> None:
            paths.staged("nlw_levels.json").write_text("NEW", encoding="utf-8")

        _install_jobs(monkeypatch, {"nlw": job_nlw}, (("nlw",),))

        result = await runner.run_all_async()

        # 残渣既没被发布，也不在 staging 了
        assert result["published"] == ["nlw_levels.json"]
        assert not (data_dirs.data / "hds_levels.json").exists()
        assert not (data_dirs.staging / "hds_levels.json").exists()

    async def test_同一层的_job_是并发跑的(
        self,
        data_dirs: SimpleNamespace,
        fresh_lock: asyncio.Lock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """三个 job 在同一个 barrier 上会合 —— 串行跑的话必然超时。"""
        barrier = threading.Barrier(3)

        def make(name: str):
            def _job() -> None:
                barrier.wait(timeout=10)

            _job.__name__ = name
            return _job

        _install_jobs(
            monkeypatch,
            {"a": make("a"), "b": make("b"), "c": make("c")},
            (("a", "b", "c"),),
        )

        result = await runner.run_all_async()

        assert result["failed"] == [], "barrier 没凑齐说明这一层其实是串行跑的"
        assert sorted(result["success"]) == ["a", "b", "c"]

    async def test_层与层之间是串行的(
        self,
        data_dirs: SimpleNamespace,
        fresh_lock: asyncio.Lock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events: list[str] = []
        guard = threading.Lock()

        def make(name: str):
            def _job() -> None:
                with guard:
                    events.append(name)

            return _job

        _install_jobs(
            monkeypatch,
            {"a": make("a"), "b": make("b"), "c": make("c")},
            (("a", "b"), ("c",)),
        )

        await runner.run_all_async()

        assert set(events[:2]) == {"a", "b"}
        assert events[2] == "c"  # 第二层一定在第一层全部结束之后

    async def test_已经有一个在跑的时候直接拒绝(
        self, fresh_lock: asyncio.Lock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[str] = []
        _install_jobs(monkeypatch, {"a": lambda: called.append("a")}, (("a",),))

        async with fresh_lock:
            with pytest.raises(RuntimeError, match="已经有一个更新任务在跑了"):
                await runner.run_all_async()

        assert called == []  # 一个 job 都没起

    async def test_失败结果里带上_job_名和异常类型(
        self,
        data_dirs: SimpleNamespace,
        fresh_lock: asyncio.Lock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def job_bad() -> None:
            raise KeyError("missing-column")

        _install_jobs(monkeypatch, {"bad": job_bad}, (("bad",),))

        with pytest.raises(RuntimeError) as excinfo:
            await runner.run_all_async()

        text = str(excinfo.value)
        assert "'job': 'bad'" in text
        assert "'type': 'KeyError'" in text

    def test_run_all_同步版就是把_async_版跑一遍(
        self,
        data_dirs: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 同步入口自己 asyncio.run，所以这个用例必须是同步的
        monkeypatch.setattr(runner, "_lock", asyncio.Lock())

        def job_nlw() -> None:
            paths.staged("nlw_levels.json").write_text("NEW", encoding="utf-8")

        _install_jobs(monkeypatch, {"nlw": job_nlw}, (("nlw",),))

        result = runner.run_all()

        assert result["success"] == ["nlw"]
        assert result["published"] == ["nlw_levels.json"]


# ==========================================================================
# jobs/constants.py
# ==========================================================================
class TestConstants:
    """常量表整表断言 —— 这些 ID / 表名写错了就是整个数据源静默错位"""

    def test_各表的_spreadsheet_id(self) -> None:
        assert constants.NLW_ID == "1YxUE2kkvhT2E6AjnkvTf-o8iu_shSLbuFkEFcZOvieA"
        assert constants.HDS_ID == "1M7C58CG_5cLGsJEXTLQBtO6nzbpA-1zxCb8ZV8ux3zg"
        assert constants.IDS_ID == "15ehtAIpCR8s04qIb8zij9sTpUdGJbmAE_LDcfVA3tcU"
        assert constants.LW_ID == "15YvW2rRQKlkNpdFMTaRt9CWefDkng6BSh6xRDXSw9r8"
        assert constants.PLAT_RANK_ID == "1uicngbhpej4PEmtYYeGmYlFsA28PwTzzouWb4EWQkTY"
        assert constants.PLAT_DIFF_ID == "1ApwiAVAcBmfyoPW3wvDzc8JvY4Lfg5tFsPlYg3DNWhc"
        assert constants.PLAT_DATA_ID == "13rpmCGCC8NKvRJhVcUuxixUdEuc_I6rm9LlwgB2HAsM"
        # 六张表来自六个不同的文档
        assert len({
            constants.NLW_ID,
            constants.HDS_ID,
            constants.IDS_ID,
            constants.LW_ID,
            constants.PLAT_RANK_ID,
            constants.PLAT_DIFF_ID,
            constants.PLAT_DATA_ID,
        }) == 7

    def test_各表的_sheet_名(self) -> None:
        # 原表就是这么拼错的（Levles / Plevles），不要"顺手修好"
        assert constants.NLW_REGULAR_LEVELS_NAME == "Tha Levles"
        assert constants.NLW_PENDING_LEVELS_NAME == "Pending Levles"
        assert constants.NLW_REGULAR_PLATFORMER_LEVELS_NAME == "Tha Plevles"
        assert constants.NLW_PENDING_PLATFORMER_LEVELS_NAME == "Plending Plevles"
        assert constants.HDS_LEVELS_NAME == "THE List"
        assert constants.HDS_PLATFORMER_LEVELS_NAME == "THE Plat List"
        assert constants.IDS_LEVELS_NAME == "Tha Levels"
        assert constants.IDS_PLATFORMER_LEVELS_NAME == "Tha Platformer Levels"
        assert constants.LW_LEVELS_NAME == "Tha Levles"
        assert constants.LW_PENDING_LEVELS_NAME == "Pending Levles"
        assert constants.PLAT_DIFF_NAME == "The Chart"
        assert constants.PLAT_DATA_SHEET_NAME == "Levels"

    def test_改名表的规模与几个代表项(self) -> None:
        # 注意 FRUITY_LEVELS_NLW 的字面量里 'Collect All Pets' 写了两遍，
        # 所以 19 而不是 20
        assert len(constants.FRUITY_LEVELS_NLW) == 19
        assert len(constants.FRUITY_CREATORS_NLW) == 16
        assert len(constants.FRUITY_LEVELS_IDS) == 8
        assert len(constants.FRUITY_CREATORS_IDS) == 2
        assert len(constants.FRUITY_LEVELS_HDS) == 3
        assert constants.FRUITY_CREATORS_HDS == {}

        assert constants.FRUITY_LEVELS_NLW["Graphite Wordle"] == "Graphite World"
        assert constants.FRUITY_LEVELS_NLW["troll levle"] == "troll level"
        assert constants.FRUITY_CREATORS_NLW["Meloo Meroo"] == "Meroo"
        assert constants.FRUITY_CREATORS_NLW[""] == ""
        assert constants.FRUITY_LEVELS_IDS["'10"] == "10"
        assert constants.FRUITY_CREATORS_IDS["Shocksidan"] == "Shocksidian"
        assert constants.FRUITY_LEVELS_HDS["ABLAZE (New Info)"] == "ABLAZE"

    def test_moonthly_前缀是两个带尾空格的月亮(self) -> None:
        assert constants.MOONTHLIES_PREFIX == ["\U0001f31a ", "\U0001f31d "]
        assert all(p.endswith(" ") for p in constants.MOONTHLIES_PREFIX)


# ==========================================================================
# jobs/platdiff.py
# ==========================================================================
def _diff_cols(names: list[str], **over: Any) -> dict:
    n = len(names)
    cols: dict = {
        "names": list(names),
        "ids": [""] * n,
        "creators": [""] * n,
        "tags": [""] * n,
        "enjoyments": [""] * n,
        "videos": [None] * n,
    }
    cols.update(over)
    return cols


class TestPlatDiffModel:
    def test_to_dict_字段齐全(self) -> None:
        entry = platdiff.PlatDiff(
            name="Null",
            id="123",
            creator="Someone",
            tags="Coin, Deathless",
            enjoyment=8.5,
            video="https://y/1",
            tier="5",
            sheet_index=7,
        )
        assert entry.to_dict() == {
            "sheetIndex": 7,
            "tier": "5",
            "name": "Null",
            "id": "123",
            "creator": "Someone",
            "tags": "Coin, Deathless",
            "enjoyment": 8.5,
            "video": "https://y/1",
        }

    def test_from_dict_to_dict_往返(self) -> None:
        raw = {
            "sheetIndex": 3,
            "tier": "2",
            "name": "Foo",
            "id": "1",
            "creator": "Bar",
            "tags": "",
            "enjoyment": None,
            "video": None,
        }
        assert platdiff.PlatDiff.from_dict(raw).to_dict() == raw

    def test_from_dict_缺字段时用默认值(self) -> None:
        entry = platdiff.PlatDiff.from_dict({})
        assert entry.name == ""
        assert entry.id == ""
        assert entry.creator == ""
        assert entry.tags == ""
        assert entry.enjoyment is None
        assert entry.video is None
        assert entry.tier is None
        assert entry.sheet_index == -1

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("8.5", 8.5),
            (" 7 ", 7.0),
            ("0", 0.0),
            ("-1.5", -1.5),
            ("N/A", None),
            ("8,5", None),
        ],
    )
    def test_parse_enjoyment(self, raw: Optional[str], expected: Any) -> None:
        assert platdiff.PlatDiff._parse_enjoyment(raw) == expected


class TestPlatDiffBuild:
    """build_plat_diff_list：把 The Chart 的列还原成分档条目"""

    def test_按_TIER_表头分档(self) -> None:
        cols = _diff_cols(
            ["TIER 1", "Alpha", "Beta", "TIER 2", "Gamma"],
            ids=["", "1", "2", "", "3"],
            creators=["", "A", "B", "", "C"],
        )
        entries = platdiff.build_plat_diff_list(cols)

        assert [(e.name, e.tier, e.id, e.sheet_index) for e in entries] == [
            ("Alpha", "1", "1", 1),
            ("Beta", "1", "2", 2),
            ("Gamma", "2", "3", 4),
        ]

    def test_第一个表头之前的行全丢掉(self) -> None:
        cols = _diff_cols(["Level Name", "Header Junk", "TIER 1", "Alpha"])
        entries = platdiff.build_plat_diff_list(cols)
        assert [e.name for e in entries] == ["Alpha"]

    def test_空行跳过但不中断且_sheet_index_继续走(self) -> None:
        cols = _diff_cols(["TIER 1", "Alpha", "", "   ", "Beta"])
        entries = platdiff.build_plat_diff_list(cols)
        assert [(e.name, e.sheet_index) for e in entries] == [("Alpha", 1), ("Beta", 4)]

    def test_小写的_tier_1_是关卡名不是表头(self) -> None:
        # 源码注释就点了这件事：真有个关卡叫 "tier 1"
        cols = _diff_cols(["TIER 1", "tier 1"])
        entries = platdiff.build_plat_diff_list(cols)
        assert [(e.name, e.tier) for e in entries] == [("tier 1", "1")]

    def test_光秃秃一个_TIER_会把当前档位清空(self) -> None:
        # name[4:] 为空 -> last_tier 变回 None -> 后面的行全被丢掉
        cols = _diff_cols(["TIER 1", "Alpha", "TIER", "Beta"])
        entries = platdiff.build_plat_diff_list(cols)
        assert [e.name for e in entries] == ["Alpha"]

    def test_名字两端空白被裁掉(self) -> None:
        cols = _diff_cols(["TIER 1", "  Alpha  "])
        assert platdiff.build_plat_diff_list(cols)[0].name == "Alpha"

    def test_列长度不齐时用空串和_None_兜底(self) -> None:
        cols = {
            "names": ["TIER 1", "Alpha", "Beta"],
            "ids": ["", "1"],  # 短一截
            "creators": [],
            "tags": [],
            "enjoyments": [],
            "videos": [None, "https://v/1"],
        }
        entries = platdiff.build_plat_diff_list(cols)

        assert [(e.name, e.id, e.creator, e.tags, e.video) for e in entries] == [
            ("Alpha", "1", "", "", "https://v/1"),
            ("Beta", "", "", "", None),
        ]

    def test_enjoyment_解析失败的落成_None(self) -> None:
        cols = _diff_cols(
            ["TIER 1", "Alpha", "Beta", "Gamma"],
            enjoyments=["", "9.25", "??", ""],
        )
        entries = platdiff.build_plat_diff_list(cols)
        assert [e.enjoyment for e in entries] == [9.25, None, None]

    def test_全空输入产出空列表(self) -> None:
        assert platdiff.build_plat_diff_list({}) == []
        assert platdiff.build_plat_diff_list(_diff_cols([])) == []


class TestPlatDiffCache:
    def test_存读往返(self, data_dirs: SimpleNamespace) -> None:
        entries = platdiff.build_plat_diff_list(
            _diff_cols(["TIER 1", "Alpha"], ids=["", "42"], enjoyments=["", "7"])
        )
        platdiff.save_plat_diff_cache(entries)

        payload = _read_json(data_dirs.staging / "platdiff.json")
        assert isinstance(payload["timestamp"], float)
        assert payload["entries"] == [
            {
                "sheetIndex": 1,
                "tier": "1",
                "name": "Alpha",
                "id": "42",
                "creator": "",
                "tags": "",
                "enjoyment": 7.0,
                "video": None,
            }
        ]

        loaded = platdiff.load_plat_diff_cache()
        assert [e.to_dict() for e in loaded] == payload["entries"]

    def test_缓存不存在时返回空列表(self, data_dirs: SimpleNamespace) -> None:
        assert platdiff.load_plat_diff_cache() == []

    def test_staging_没有就读上一次发布的(self, data_dirs: SimpleNamespace) -> None:
        _write_json(
            data_dirs.data / "platdiff.json",
            {"timestamp": 1.0, "entries": [{"name": "Old", "sheetIndex": 0}]},
        )
        loaded = platdiff.load_plat_diff_cache()
        assert [e.name for e in loaded] == ["Old"]


# ==========================================================================
# jobs/platbatch.py
# ==========================================================================
class TestExtractBaseName:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Null (Deathless)", ("Null", "Deathless")),
            ("Moongrinder (Coin)", ("Moongrinder", "Coin")),
            ("Normal Level", ("Normal Level", None)),
            ("A (B) (C)", ("A (B)", "C")),  # rsplit：只拆最后一对
            ("(Coin)", ("(Coin)", None)),  # 主名为空 -> 不算附属词条
            ("Foo ()", ("Foo ()", None)),  # 后缀为空 -> 不算
            ("Foo (", ("Foo (", None)),  # 没有右括号
            ("Foo )", ("Foo )", None)),  # 没有左括号
            ("", ("", None)),
        ],
    )
    def test_提取主名与后缀(self, name: str, expected: tuple) -> None:
        assert platbatch.extract_base_name(name) == expected


class TestMergePlatData:
    def _write_sources(
        self,
        dirs: SimpleNamespace,
        *,
        platdata_rows: Optional[list] = None,
        platdiff_rows: Optional[list] = None,
        platrank_rows: Optional[list] = None,
    ) -> None:
        if platdata_rows is not None:
            _write_json(
                dirs.staging / "platdata.json", {"timestamp": 0, "data": platdata_rows}
            )
        if platdiff_rows is not None:
            _write_json(
                dirs.staging / "platdiff.json",
                {"timestamp": 0, "entries": platdiff_rows},
            )
        if platrank_rows is not None:
            _write_json(
                dirs.staging / "platrank_weights.json",
                {"timestamp": 0, "levels": platrank_rows},
            )

    def test_三个来源按_name_合并(self, data_dirs: SimpleNamespace) -> None:
        self._write_sources(
            data_dirs,
            platdata_rows=[{"name": "Null", "id": "1", "tpl": "3", "pemonlist": "-"}],
            platdiff_rows=[
                {
                    "name": "Null",
                    "id": "999",
                    "tier": "5",
                    "creator": "Someone",
                    "tags": "Coin, Deathless",
                    "enjoyment": 8.0,
                    "video": "https://v/1",
                }
            ],
            platrank_rows=[{"name": "Null", "weight": "12", "section": "Top"}],
        )

        merged = platbatch.merge_plat_data()

        assert set(merged) == {"Null"}
        level = merged["Null"]
        assert level.id == "1", "platdata 的 id 优先级高于 platdiff"
        assert level.tpl == "3"
        assert level.pemonlist == "-"  # 清理是 clean_level_data 的事
        assert level.tier == "5"
        assert level.creator == "Someone"
        assert level.tags == ["Coin", "Deathless"]
        assert level.enjoyment == 8.0
        assert level.video == "https://v/1"
        assert level.weight == "12"
        assert level.section == "Top"

    def test_platdata_没有_id_时用_platdiff_的(
        self, data_dirs: SimpleNamespace
    ) -> None:
        self._write_sources(
            data_dirs,
            platdata_rows=[{"name": "Null", "tpl": "3"}],
            platdiff_rows=[{"name": "Null", "id": "999"}],
        )
        assert platbatch.merge_plat_data()["Null"].id == "999"

    def test_只在_platdiff_出现的关卡也会被建出来(
        self, data_dirs: SimpleNamespace
    ) -> None:
        self._write_sources(
            data_dirs,
            platdata_rows=[],
            platdiff_rows=[{"name": "OnlyDiff", "tier": "1"}],
            platrank_rows=[{"name": "OnlyRank", "weight": "5", "section": "Low"}],
        )
        merged = platbatch.merge_plat_data()
        assert set(merged) == {"OnlyDiff", "OnlyRank"}
        assert merged["OnlyDiff"].tier == "1"
        assert merged["OnlyRank"].weight == "5"

    def test_名字为空或全空白的行被丢掉(self, data_dirs: SimpleNamespace) -> None:
        self._write_sources(
            data_dirs,
            platdata_rows=[{"name": "", "id": "1"}, {"name": "   ", "id": "2"}],
            platdiff_rows=[{"name": "", "tier": "1"}],
            platrank_rows=[{"name": "", "weight": "1"}],
        )
        assert platbatch.merge_plat_data() == {}

    def test_名字两端空白被裁掉后再合并(self, data_dirs: SimpleNamespace) -> None:
        self._write_sources(
            data_dirs,
            platdata_rows=[{"name": " Null ", "id": "1"}],
            platdiff_rows=[{"name": "Null", "tier": "5"}],
        )
        merged = platbatch.merge_plat_data()
        assert set(merged) == {"Null"}
        assert (merged["Null"].id, merged["Null"].tier) == ("1", "5")

    @pytest.mark.parametrize(
        ("tags_str", "expected"),
        [
            ("Coin, Deathless", ["Coin", "Deathless"]),
            ("  Coin ,, Deathless  ", ["Coin", "Deathless"]),
            ("Coin", ["Coin"]),
            ("", []),
            ("   ", []),
            (",,,", []),
        ],
    )
    def test_tags_按逗号拆并去空(
        self, data_dirs: SimpleNamespace, tags_str: str, expected: list
    ) -> None:
        self._write_sources(
            data_dirs, platdiff_rows=[{"name": "Null", "tags": tags_str}]
        )
        assert platbatch.merge_plat_data()["Null"].tags == expected

    def test_源文件缺失时跳过不报错(self, data_dirs: SimpleNamespace) -> None:
        # 一个文件都没有
        assert platbatch.merge_plat_data() == {}

    def test_部分源文件缺失时其余照常(self, data_dirs: SimpleNamespace) -> None:
        self._write_sources(data_dirs, platdiff_rows=[{"name": "Null", "tier": "3"}])
        merged = platbatch.merge_plat_data()
        assert set(merged) == {"Null"}
        assert merged["Null"].weight is None


class TestProcessDerivedLevels:
    def test_附属词条继承主词条的_id_并被反向引用(self) -> None:
        merged = {
            "Null": platbatch.PlatLevel(name="Null", id="123"),
            "Null (Deathless)": platbatch.PlatLevel(name="Null (Deathless)"),
        }
        result = platbatch.process_derived_levels(merged)

        assert result["Null (Deathless)"].derived_from == "Null"
        assert result["Null (Deathless)"].id == "123"
        assert result["Null"].derived_levels == ["Null (Deathless)"]
        assert result["Null"].derived_from is None

    def test_附属词条排在主词条前面也照样认(self) -> None:
        merged = {
            "Null (Coin)": platbatch.PlatLevel(name="Null (Coin)"),
            "Null": platbatch.PlatLevel(name="Null", id="7"),
        }
        result = platbatch.process_derived_levels(merged)
        assert result["Null (Coin)"].id == "7"
        assert result["Null"].derived_levels == ["Null (Coin)"]

    def test_找不到主词条就当普通词条(self) -> None:
        merged = {"Orphan (Coin)": platbatch.PlatLevel(name="Orphan (Coin)", id="9")}
        result = platbatch.process_derived_levels(merged)
        assert result["Orphan (Coin)"].derived_from is None
        assert result["Orphan (Coin)"].id == "9"

    def test_主词条没有_id_时不覆盖附属词条已有的_id(self) -> None:
        merged = {
            "Null": platbatch.PlatLevel(name="Null", id=None),
            "Null (Coin)": platbatch.PlatLevel(name="Null (Coin)", id="55"),
        }
        result = platbatch.process_derived_levels(merged)
        assert result["Null (Coin)"].id == "55"
        assert result["Null"].derived_levels == ["Null (Coin)"]

    def test_重复调用不会把引用塞两遍(self) -> None:
        merged = {
            "Null": platbatch.PlatLevel(name="Null", id="1"),
            "Null (Coin)": platbatch.PlatLevel(name="Null (Coin)"),
        }
        platbatch.process_derived_levels(merged)
        platbatch.process_derived_levels(merged)
        assert merged["Null"].derived_levels == ["Null (Coin)"]

    def test_一个主词条挂多个附属词条(self) -> None:
        merged = {
            "Null": platbatch.PlatLevel(name="Null", id="1"),
            "Null (Coin)": platbatch.PlatLevel(name="Null (Coin)"),
            "Null (Deathless)": platbatch.PlatLevel(name="Null (Deathless)"),
        }
        platbatch.process_derived_levels(merged)
        assert merged["Null"].derived_levels == ["Null (Coin)", "Null (Deathless)"]


class TestCleanLevelData:
    def test_横杠占位统一清成_None(self) -> None:
        level = platbatch.PlatLevel(name="X", tpl="-", pemonlist="-")
        platbatch.clean_level_data(level)
        assert level.tpl is None
        assert level.pemonlist is None

    def test_正常值不动(self) -> None:
        level = platbatch.PlatLevel(name="X", tpl="12", pemonlist="34")
        platbatch.clean_level_data(level)
        assert (level.tpl, level.pemonlist) == ("12", "34")

    def test_tags_里的三横杠占位被剔掉但仍是列表(self) -> None:
        level = platbatch.PlatLevel(name="X", tags=["---"])
        platbatch.clean_level_data(level)
        assert level.tags == []

        level2 = platbatch.PlatLevel(name="X", tags=["Coin", "---", "Deathless"])
        platbatch.clean_level_data(level2)
        assert level2.tags == ["Coin", "Deathless"]

    @pytest.mark.parametrize(
        ("raw_id", "fixed"),
        [
            ("112363390", "112603907"),
            ("104683046", "0"),
            ("127566338", "0"),
            ("Pending Removal", "0"),
            ("999", "999"),
            (None, None),
        ],
    )
    def test_ID_FIX_修正表(self, raw_id: Optional[str], fixed: Optional[str]) -> None:
        level = platbatch.PlatLevel(name="X", id=raw_id)
        platbatch.clean_level_data(level)
        assert level.id == fixed

    def test_ID_FIX_表本身(self) -> None:
        assert platbatch.ID_FIX == {
            "112363390": "112603907",
            "104683046": "0",
            "127566338": "0",
            "Pending Removal": "0",
        }


class TestBatchProcess:
    def test_端到端_合并_派生_清理_排序_落盘(
        self, data_dirs: SimpleNamespace
    ) -> None:
        _write_json(
            data_dirs.staging / "platdata.json",
            {
                "data": [
                    {"name": "Null", "id": "100", "tpl": "-", "pemonlist": "5"},
                    {"name": "Alpha", "id": "104683046", "tpl": "2"},
                    {"name": "Zeta", "id": "300", "tpl": "1"},
                ]
            },
        )
        _write_json(
            data_dirs.staging / "platdiff.json",
            {
                "entries": [
                    {"name": "Null", "tier": "5", "tags": "Coin, ---", "creator": "A"},
                    {"name": "Null (Deathless)", "tier": "6", "tags": ""},
                ]
            },
        )
        _write_json(
            data_dirs.staging / "platrank_weights.json",
            {"levels": [{"name": "Zeta", "weight": "9", "section": "Top"}]},
        )

        merged = platbatch.batch_process()

        payload = _read_json(data_dirs.staging / "plat_combined.json")
        assert isinstance(payload["timestamp"], float)
        names = [item["name"] for item in payload["levels"]]
        # id 被 ID_FIX 打成 "0" 的 Alpha 已被剔除，其余按 name 排序
        assert names == ["Null", "Null (Deathless)", "Zeta"]

        null = payload["levels"][0]
        assert null["id"] == "100"
        assert null["tpl"] is None  # "-" -> None
        assert null["pemonlist"] == "5"
        assert null["tags"] == ["Coin"]  # "---" 被剔掉
        assert null["derived_levels"] == ["Null (Deathless)"]

        derived = payload["levels"][1]
        assert derived["derived_from"] == "Null"
        assert derived["id"] == "100"  # 从主词条继承
        assert derived["tier"] == "6"

        # 返回值是清理/过滤之后的 dict
        assert set(merged) == {"Null", "Null (Deathless)", "Zeta"}

    def test_batch_就是_batch_process(
        self, data_dirs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[int] = []
        monkeypatch.setattr(platbatch, "batch_process", lambda: called.append(1))
        platbatch.batch()
        assert called == [1]

    def test_没有任何源文件时也能产出空的_plat_combined(
        self, data_dirs: SimpleNamespace
    ) -> None:
        platbatch.batch_process()
        payload = _read_json(data_dirs.staging / "plat_combined.json")
        assert payload["levels"] == []


# ==========================================================================
# jobs/platrank.py
# ==========================================================================
class TestPlatRankBuild:
    def test_weight_为空的行当作分区表头(self) -> None:
        cols = {
            "names": ["Top Placements", "Alpha", "Beta", "Low Placements", "Gamma"],
            "weights": ["", "10", "20", "", "1"],
        }
        entries = platrank.build_level_list(cols)

        assert entries == [
            {"sheetIndex": 1, "name": "Alpha", "weight": "10", "section": "Top"},
            {"sheetIndex": 2, "name": "Beta", "weight": "20", "section": "Top"},
            {"sheetIndex": 4, "name": "Gamma", "weight": "1", "section": "Low"},
        ]

    def test_分区名去掉_Placements_后缀并裁空白(self) -> None:
        cols = {"names": ["Extreme Placements", "Alpha"], "weights": ["", "3"]}
        assert platrank.build_level_list(cols)[0]["section"] == "Extreme"

    def test_没有_Placements_后缀就整名当分区(self) -> None:
        cols = {"names": ["Weird Header", "Alpha"], "weights": ["", "3"]}
        assert platrank.build_level_list(cols)[0]["section"] == "Weird Header"

    def test_第一个分区之前的行丢掉(self) -> None:
        cols = {"names": ["Alpha", "Top Placements", "Beta"], "weights": ["5", "", "6"]}
        entries = platrank.build_level_list(cols)
        assert [e["name"] for e in entries] == ["Beta"]

    def test_空输入产出空列表(self) -> None:
        assert platrank.build_level_list({"names": [], "weights": []}) == []

    def test_weights_列比_names_短会直接_IndexError(self) -> None:
        """⚠️ 生产问题存档：build_level_list 是唯一一个不做越界兜底的 builder。

        Sheets API 返回的每列长度只到该列最后一个非空格，所以 B 列（weight）
        天然可能比 A 列（name）短 —— 只要表尾有一行只填了名字没填权重，
        `weights[i]` 就会 IndexError，platrank 这个 job 整个挂掉。
        别的 job（nlw/ids/lw/hds/platdiff）都写了 `if i < len(x) else ''`。
        这里锁的是当前实际行为，不是期望行为。
        """
        cols = {"names": ["Top Placements", "Alpha", "Beta"], "weights": ["", "10"]}
        with pytest.raises(IndexError):
            platrank.build_level_list(cols)


# ==========================================================================
# jobs/platdata.py
# ==========================================================================
class TestPlatDataBuild:
    def test_跳过表头并逐行组装(self) -> None:
        cols = {
            "id": ["ID", "100", "200"],
            "name": ["Name", "Alpha", "Beta"],
            "tier": ["Tier", "5", "6"],
            "tpl": ["TPL", "1", "-"],
            "pemonlist": ["Pemonlist", "-", "2"],
        }
        assert platdata.build_data_objects(cols) == [
            {"id": "100", "name": "Alpha", "tier": "5", "tpl": "1", "pemonlist": "-"},
            {"id": "200", "name": "Beta", "tier": "6", "tpl": "-", "pemonlist": "2"},
        ]

    def test_遇到空_ID_就停(self) -> None:
        cols = {
            "id": ["ID", "100", "", "300"],
            "name": ["Name", "Alpha", "Beta", "Gamma"],
            "tier": ["Tier", "", "", ""],
            "tpl": ["TPL", "", "", ""],
            "pemonlist": ["P", "", "", ""],
        }
        assert [row["name"] for row in platdata.build_data_objects(cols)] == ["Alpha"]

    def test_其余列比_ID_列短时用空串兜底(self) -> None:
        cols = {
            "id": ["ID", "100", "200"],
            "name": ["Name", "Alpha"],
            "tier": ["Tier"],
            "tpl": [],
            "pemonlist": [],
        }
        assert platdata.build_data_objects(cols) == [
            {"id": "100", "name": "Alpha", "tier": "", "tpl": "", "pemonlist": ""},
            {"id": "200", "name": "", "tier": "", "tpl": "", "pemonlist": ""},
        ]

    def test_只有表头时产出空列表(self) -> None:
        cols = {k: ["header"] for k in ("id", "name", "tier", "tpl", "pemonlist")}
        assert platdata.build_data_objects(cols) == []


# ==========================================================================
# jobs/nlw.py
# ==========================================================================
def _nlw_cols(levels: list[str], **over: Any) -> dict:
    n = len(levels)
    cols: dict = {
        "levels": list(levels),
        "creators": [""] * n,
        "checkpoints": [None] * n,
        "lengths": [""] * n,
        "skillsets": [""] * n,
        "enjoyments": [""] * n,
        "descriptions": [""] * n,
        "videos": [None] * n,
    }
    cols.update(over)
    return cols


class TestNlwBuild:
    def test_竖线表头分档且去掉_Tier_字样(self) -> None:
        cols = _nlw_cols(
            ["| Extreme Tier", "Alpha", "| Insane Tier", "Beta"],
            creators=["", "AuthorA", "", "AuthorB"],
        )
        entries = nlw.build_level_list(cols)

        assert [(e["name"], e["tier"], e["creator"], e["sheetIndex"]) for e in entries] == [
            ("Alpha", "Extreme", "AuthorA", 1),
            ("Beta", "Insane", "AuthorB", 3),
        ]

    def test_第一个表头之前的行丢掉(self) -> None:
        cols = _nlw_cols(["Junk", "| A Tier", "Alpha"])
        assert [e["name"] for e in nlw.build_level_list(cols)] == ["Alpha"]

    def test_Shortcuts_档整档跳过(self) -> None:
        cols = _nlw_cols(["| Shortcuts", "Jump To X", "| A Tier", "Alpha"])
        assert [e["name"] for e in nlw.build_level_list(cols)] == ["Alpha"]

    def test_遇到空关卡名直接停(self) -> None:
        cols = _nlw_cols(["| A Tier", "Alpha", "", "Beta"])
        assert [e["name"] for e in nlw.build_level_list(cols)] == ["Alpha"]

    def test_没关卡了那档直接停(self) -> None:
        cols = _nlw_cols(
            ["| A Tier", "Alpha", "| Not enough levels for you? Tier", "Beta"]
        )
        assert [e["name"] for e in nlw.build_level_list(cols)] == ["Alpha"]

    def test_找不到极端难度的提示行也停(self) -> None:
        cols = _nlw_cols(
            ["| A Tier", "Alpha", "Can't find an extreme you like?", "Beta"]
        )
        assert [e["name"] for e in nlw.build_level_list(cols)] == ["Alpha"]

    def test_None_Yet_占位跳过但不中断(self) -> None:
        cols = _nlw_cols(["| A Tier", "None Yet!", "Alpha"])
        assert [e["name"] for e in nlw.build_level_list(cols)] == ["Alpha"]

    def test_关卡名和作者名走改名表(self) -> None:
        cols = _nlw_cols(
            ["| A Tier", "troll levle", "Graphite Wordle"],
            creators=["", "Meloo Meroo", "Normal Guy"],
        )
        entries = nlw.build_level_list(cols)
        assert [(e["name"], e["creator"]) for e in entries] == [
            ("troll level", "Meroo"),
            ("Graphite World", "Normal Guy"),
        ]

    def test_月更前缀被剥掉(self) -> None:
        prefix = constants.MOONTHLIES_PREFIX[0]
        cols = _nlw_cols(["| A Tier", prefix + "Some Level"])
        assert nlw.build_level_list(cols)[0]["name"] == "Some Level"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("9.1", 9.1), ("", None), ("n/a", None), ("0", 0.0)],
    )
    def test_enjoyment_解析(self, raw: str, expected: Any) -> None:
        cols = _nlw_cols(["| A Tier", "Alpha"], enjoyments=["", raw])
        assert nlw.build_level_list(cols)[0]["enjoyment"] == expected

    def test_存档点空值落成_None_非空则裁空白(self) -> None:
        cols = _nlw_cols(["| A Tier", "Alpha"], checkpoints=[None, "  3  "])
        assert nlw.build_level_list(cols)[0]["checkpoints"] == "3"

        cols2 = _nlw_cols(["| A Tier", "Alpha"], checkpoints=[None, ""])
        assert nlw.build_level_list(cols2)[0]["checkpoints"] is None

    def test_技能点和描述两端空白被裁掉(self) -> None:
        cols = _nlw_cols(
            ["| A Tier", "Alpha"],
            skillsets=["", "  memory  "],
            descriptions=["", "  长得像  "],
        )
        entry = nlw.build_level_list(cols)[0]
        assert entry["skillset"] == "memory"
        assert entry["description"] == "长得像"

    def test_产出字段齐全(self) -> None:
        cols = _nlw_cols(
            ["| A Tier", "Alpha"],
            lengths=["", "2m"],
            videos=[None, "https://v/1"],
        )
        assert set(nlw.build_level_list(cols)[0]) == {
            "sheetIndex",
            "tier",
            "name",
            "creator",
            "length",
            "skillset",
            "enjoyment",
            "description",
            "checkpoints",
            "video",
        }

    def test_空输入产出空列表(self) -> None:
        assert nlw.build_level_list(_nlw_cols([])) == []


# ==========================================================================
# jobs/ids.py
# ==========================================================================
def _ids_cols(levels: list[str], **over: Any) -> dict:
    n = len(levels)
    cols: dict = {
        "levels": list(levels),
        "creators": [""] * n,
        "lengths": [""] * n,
        "checkpoints": [None] * n,
        "skillsets": [""] * n,
        "descriptions": [""] * n,
        "videos": [None] * n,
    }
    cols.update(over)
    return cols


class TestIdsBuild:
    def test_箭头表头分档(self) -> None:
        cols = _ids_cols(
            [f"{ARROW} Extreme {ARROW}", "Alpha", f"{ARROW} Insane {ARROW}", "Beta"]
        )
        entries = ids.build_level_list(cols)
        assert [(e["name"], e["tier"], e["sheetIndex"]) for e in entries] == [
            ("Alpha", "Extreme", 1),
            ("Beta", "Insane", 3),
        ]

    def test_Other_和_Spreadsheet_Fakes_整档跳过(self) -> None:
        cols = _ids_cols(
            [
                f"{ARROW} Other {ARROW}",
                "Junk",
                f"{ARROW} Spreadsheet Fakes (Legacy) {ARROW}",
                "Fake",
                f"{ARROW} Real {ARROW}",
                "Alpha",
            ]
        )
        assert [e["name"] for e in ids.build_level_list(cols)] == ["Alpha"]

    def test_Rerates_档被改写成_Legacy(self) -> None:
        cols = _ids_cols(
            [f"{ARROW} Hard Demon/Extreme Demon Rerates {ARROW}", "Alpha", "Beta"]
        )
        entries = ids.build_level_list(cols)
        assert [(e["name"], e["tier"]) for e in entries] == [
            ("Alpha", "Legacy"),
            ("Beta", "Legacy"),
        ]

    def test_遇到空关卡名直接停(self) -> None:
        cols = _ids_cols([f"{ARROW} A {ARROW}", "Alpha", "", "Beta"])
        assert [e["name"] for e in ids.build_level_list(cols)] == ["Alpha"]

    def test_改名表生效(self) -> None:
        cols = _ids_cols([f"{ARROW} A {ARROW}", "'10"], creators=["", "Shocksidan"])
        entry = ids.build_level_list(cols)[0]
        assert (entry["name"], entry["creator"]) == ("10", "Shocksidian")

    def test_存档点与描述的处理(self) -> None:
        cols = _ids_cols(
            [f"{ARROW} A {ARROW}", "Alpha"],
            checkpoints=[None, " 12 "],
            descriptions=["", "  desc  "],
        )
        entry = ids.build_level_list(cols)[0]
        assert entry["checkpoints"] == "12"
        assert entry["description"] == "desc"

    def test_产出字段齐全且没有_enjoyment(self) -> None:
        cols = _ids_cols([f"{ARROW} A {ARROW}", "Alpha"])
        assert set(ids.build_level_list(cols)[0]) == {
            "sheetIndex",
            "tier",
            "name",
            "creator",
            "length",
            "skillset",
            "description",
            "checkpoints",
            "video",
        }


# ==========================================================================
# jobs/hds.py
# ==========================================================================
def _hds_cols(levels: list[str], **over: Any) -> dict:
    return _ids_cols(levels, **over)


class TestHdsBuild:
    def test_箭头表头分档(self) -> None:
        cols = _hds_cols([f"{ARROW} Tier 1 {ARROW}", "Alpha"])
        entry = hds.build_level_list(cols)[0]
        assert (entry["name"], entry["tier"]) == ("Alpha", "Tier 1")

    def test_Demoted_Promoted_都归到_Legacy(self) -> None:
        cols = _hds_cols([f"{ARROW} A {ARROW}", "Alpha", "Demoted", "Beta", "Promoted", "Gamma"])
        entries = hds.build_level_list(cols)
        assert [(e["name"], e["tier"]) for e in entries] == [
            ("Alpha", "A"),
            ("Beta", "Legacy"),
            ("Gamma", "Legacy"),
        ]

    def test_Plending_档整档跳过(self) -> None:
        cols = _hds_cols([f"{ARROW} Plending {ARROW}", "Junk", f"{ARROW} A {ARROW}", "Alpha"])
        assert [e["name"] for e in hds.build_level_list(cols)] == ["Alpha"]

    def test_Plegacy_档改写成_Legacy(self) -> None:
        cols = _hds_cols([f"{ARROW} Plegacy {ARROW}", "Alpha"])
        assert hds.build_level_list(cols)[0]["tier"] == "Legacy"

    def test_无限存档点写成_INF(self) -> None:
        cols = _hds_cols([f"{ARROW} A {ARROW}", "Alpha"], checkpoints=[None, INF])
        assert hds.build_level_list(cols)[0]["checkpoints"] == "INF"

    def test_一串小于号的技能点写成_NERVE_CONTROL(self) -> None:
        cols = _hds_cols([f"{ARROW} A {ARROW}", "Alpha"], skillsets=["", "<" * 28])
        assert hds.build_level_list(cols)[0]["skillset"] == "NERVE CONTROL"

    def test_小于号数量不对就原样保留(self) -> None:
        cols = _hds_cols([f"{ARROW} A {ARROW}", "Alpha"], skillsets=["", "<" * 27])
        assert hds.build_level_list(cols)[0]["skillset"] == "<" * 27

    def test_改名表生效(self) -> None:
        cols = _hds_cols([f"{ARROW} A {ARROW}", "ABLAZE (New Info)"])
        assert hds.build_level_list(cols)[0]["name"] == "ABLAZE"

    def test_遇到空关卡名直接停(self) -> None:
        cols = _hds_cols([f"{ARROW} A {ARROW}", "Alpha", "", "Beta"])
        assert [e["name"] for e in hds.build_level_list(cols)] == ["Alpha"]


# ==========================================================================
# jobs/lw.py
# ==========================================================================
def _lw_cols(levels: list[str], **over: Any) -> dict:
    n = len(levels)
    cols: dict = {
        "levels": list(levels),
        "creators": [""] * n,
        "lengths": [""] * n,
        "skillsets": [""] * n,
        "enjoyments": [""] * n,
        "descriptions": [""] * n,
        "videos": [None] * n,
    }
    cols.update(over)
    return cols


class TestLwBuild:
    def test_竖线表头分档(self) -> None:
        cols = _lw_cols(["| Easy Tier", "Alpha", "| Hard Tier", "Beta"])
        entries = lw.build_level_list(cols)
        assert [(e["name"], e["tier"], e["sheetIndex"]) for e in entries] == [
            ("Alpha", "Easy", 1),
            ("Beta", "Hard", 3),
        ]

    @pytest.mark.parametrize(
        "section",
        ["Low End", "Low-Mid Range", "Mid Range", "Mid-High Range", "High End", "Ouchie", "Unknown"],
    )
    def test_区间名整行也能当分档表头(self, section: str) -> None:
        cols = _lw_cols([section, "Alpha"])
        entry = lw.build_level_list(cols)[0]
        assert (entry["name"], entry["tier"]) == ("Alpha", section)

    def test_Shortcuts_档整档跳过(self) -> None:
        cols = _lw_cols(["| Shortcuts", "Jump", "| Easy Tier", "Alpha"])
        assert [e["name"] for e in lw.build_level_list(cols)] == ["Alpha"]

    def test_没关卡了那档直接停(self) -> None:
        cols = _lw_cols(
            ["| Easy Tier", "Alpha", "| Not enough levels for you? Tier", "Beta"]
        )
        assert [e["name"] for e in lw.build_level_list(cols)] == ["Alpha"]

    def test_pending_说明行也停(self) -> None:
        cols = _lw_cols(
            ["| Easy Tier", "Alpha", "More info on pending levels here.", "Beta"]
        )
        assert [e["name"] for e in lw.build_level_list(cols)] == ["Alpha"]

    def test_None_Yet_占位跳过(self) -> None:
        cols = _lw_cols(["| Easy Tier", "None Yet!", "Alpha"])
        assert [e["name"] for e in lw.build_level_list(cols)] == ["Alpha"]

    def test_enjoyment_解析(self) -> None:
        cols = _lw_cols(["| Easy Tier", "Alpha", "Beta"], enjoyments=["", "6.5", "??"])
        assert [e["enjoyment"] for e in lw.build_level_list(cols)] == [6.5, None]

    def test_作者名不走改名表(self) -> None:
        # LW 没有 FRUITY 表，作者名原样透传（连 NLW 那边会改的名字也不改）
        cols = _lw_cols(["| Easy Tier", "Alpha"], creators=["", "Meloo Meroo"])
        assert lw.build_level_list(cols)[0]["creator"] == "Meloo Meroo"

    def test_第五十一行之后的空行才会中断(self) -> None:
        names = ["| Easy Tier"] + [f"Lv{i}" for i in range(1, 52)] + ["", "After"]
        entries = lw.build_level_list(_lw_cols(names))
        assert len(entries) == 51
        assert entries[-1]["name"] == "Lv51"
        assert "After" not in [e["name"] for e in entries]

    def test_前五十行里的空行会产出一条空名字记录(self) -> None:
        """⚠️ 生产问题存档：i <= 50 的空行不会被 break 也不会被 continue 掉，

        最后落成一条 name == "" 的记录。别的表（nlw/ids/hds）都是 `if not lvl: break`。
        这里锁的是当前实际行为，不是期望行为。
        """
        cols = _lw_cols(["| Easy Tier", "Alpha", "", "Beta"])
        entries = lw.build_level_list(cols)
        assert [e["name"] for e in entries] == ["Alpha", "", "Beta"]

    def test_产出字段齐全且没有_checkpoints(self) -> None:
        cols = _lw_cols(["| Easy Tier", "Alpha"])
        assert set(lw.build_level_list(cols)[0]) == {
            "sheetIndex",
            "tier",
            "name",
            "creator",
            "length",
            "skillset",
            "enjoyment",
            "description",
            "video",
        }


# ==========================================================================
# jobs/googlesheetapi.py —— 假的 googleapiclient，不碰凭据也不联网
# ==========================================================================
class _FakeExecutable:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _FakeValuesEndpoint:
    def __init__(self, owner: "FakeSheetsService") -> None:
        self._owner = owner

    def get(self, **kwargs: Any) -> _FakeExecutable:
        self._owner.values_calls.append(kwargs)
        return _FakeExecutable(self._owner.values_result)


class _FakeSpreadsheets:
    def __init__(self, owner: "FakeSheetsService") -> None:
        self._owner = owner

    def values(self) -> _FakeValuesEndpoint:
        return _FakeValuesEndpoint(self._owner)

    def get(self, **kwargs: Any) -> _FakeExecutable:
        self._owner.get_calls.append(kwargs)
        if self._owner.get_results:
            return _FakeExecutable(self._owner.get_results.pop(0))
        return _FakeExecutable({})


class FakeSheetsService:
    """假的 sheets service，够 SheetAPI 那三个静态方法用。"""

    def __init__(
        self, values_result: Any = None, get_results: Optional[list] = None
    ) -> None:
        self.values_result = {} if values_result is None else values_result
        self.get_results = list(get_results or [])
        self.values_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def spreadsheets(self) -> _FakeSpreadsheets:
        return _FakeSpreadsheets(self)


def _http_error(status: int) -> HttpError:
    class _Resp:
        def __init__(self, code: int) -> None:
            self.status = code
            self.reason = "fake"

    return HttpError(_Resp(status), b"{}")


class TestPersistently:
    def test_一次成功就不重试(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock()
        monkeypatch.setattr(googlesheetapi, "time", clock)
        calls: list[int] = []

        @googlesheetapi.persistently
        def f() -> str:
            calls.append(1)
            return "ok"

        assert f() == "ok"
        assert len(calls) == 1
        assert clock.slept == []

    def test_保留原函数元信息(self) -> None:
        @googlesheetapi.persistently
        def some_name() -> None:
            """文档串"""

        assert some_name.__name__ == "some_name"
        assert some_name.__doc__ == "文档串"

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_可重试的_HttpError_退避后重试(
        self, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        clock = FakeClock()
        monkeypatch.setattr(googlesheetapi, "time", clock)
        calls: list[int] = []

        @googlesheetapi.persistently
        def f() -> str:
            calls.append(1)
            if len(calls) < 3:
                raise _http_error(status)
            return "ok"

        assert f() == "ok"
        assert len(calls) == 3
        assert clock.slept == [2, 4]  # 2**1, 2**2

    @pytest.mark.parametrize("status", [400, 403, 404])
    def test_不可重试的_HttpError_直接抛(
        self, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        clock = FakeClock()
        monkeypatch.setattr(googlesheetapi, "time", clock)
        calls: list[int] = []

        @googlesheetapi.persistently
        def f() -> None:
            calls.append(1)
            raise _http_error(status)

        with pytest.raises(HttpError):
            f()
        assert len(calls) == 1
        assert clock.slept == []

    def test_其他异常固定睡两秒再试(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock()
        monkeypatch.setattr(googlesheetapi, "time", clock)
        calls: list[int] = []

        @googlesheetapi.persistently
        def f() -> str:
            calls.append(1)
            if len(calls) < 4:
                raise ValueError("boom")
            return "ok"

        assert f() == "ok"
        assert len(calls) == 4
        assert clock.slept == [2, 2, 2]

    def test_五次都失败会再补一次调用(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock()
        monkeypatch.setattr(googlesheetapi, "time", clock)
        calls: list[int] = []

        @googlesheetapi.persistently
        def f() -> str:
            calls.append(1)
            if len(calls) <= 5:
                raise ValueError("boom")
            return "ok"

        assert f() == "ok"
        assert len(calls) == 6  # 循环 5 次 + 循环外那次
        assert clock.slept == [2] * 5

    def test_第六次还失败就把异常抛出去(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock()
        monkeypatch.setattr(googlesheetapi, "time", clock)
        calls: list[int] = []

        @googlesheetapi.persistently
        def f() -> None:
            calls.append(1)
            raise ValueError("always")

        with pytest.raises(ValueError, match="always"):
            f()
        assert len(calls) == 6

    def test_参数原样透传(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(googlesheetapi, "time", FakeClock())

        @googlesheetapi.persistently
        def f(a: int, b: int = 0) -> int:
            return a + b

        assert f(1, b=2) == 3


class TestSheetAPI:
    def test_get_service_优先读环境变量(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        def fake_build(name: str, version: str, **kwargs: Any) -> str:
            captured.update({"name": name, "version": version, **kwargs})
            return "SERVICE"

        monkeypatch.setattr(googlesheetapi, "build", fake_build)
        monkeypatch.setenv("GOOGLE_SHEETS_API_KEY", "MY-KEY")

        assert googlesheetapi.SheetAPI.get_service() == "SERVICE"
        assert captured == {
            "name": "sheets",
            "version": "v4",
            "developerKey": "MY-KEY",
        }

    def test_get_service_没设环境变量就用内置那把(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            googlesheetapi,
            "build",
            lambda name, version, **kw: captured.update(kw) or "SERVICE",
        )
        monkeypatch.delenv("GOOGLE_SHEETS_API_KEY", raising=False)

        googlesheetapi.SheetAPI.get_service()
        assert captured["developerKey"].startswith("AIza")

    def test_get_column_values_整列取值(self) -> None:
        service = FakeSheetsService(
            values_result={"values": [["a"], [], ["c"], ["d", "ignored"]]}
        )
        out = googlesheetapi.SheetAPI.get_column_values(service, "SID", "Tha Levles", "B")

        assert out == ["a", "", "c", "d"]
        assert service.values_calls == [
            {"spreadsheetId": "SID", "range": "'Tha Levles'!B:B"}
        ]

    def test_get_column_values_空表返回空列表(self) -> None:
        service = FakeSheetsService(values_result={})
        assert googlesheetapi.SheetAPI.get_column_values(service, "SID", "S", "A") == []

    def test_get_column_values_出错时原样抛(self) -> None:
        service = FakeSheetsService(values_result=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            googlesheetapi.SheetAPI.get_column_values(service, "SID", "S", "A")

    def test_get_hyperlink_column_取超链接(self) -> None:
        service = FakeSheetsService(
            get_results=[
                {
                    "sheets": [
                        {
                            "data": [
                                {
                                    "rowData": [
                                        {"values": [{"hyperlink": "https://v/1"}]},
                                        {"values": [{}]},
                                        {},
                                        {"values": []},
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        )
        out = googlesheetapi.SheetAPI.get_hyperlink_column(service, "SID", "S", "A")

        assert out == ["https://v/1", None, None, None]
        assert service.get_calls[0] == {
            "spreadsheetId": "SID",
            "ranges": ["'S'!A:A"],
            "fields": "sheets/data/rowData/values/hyperlink",
        }

    def test_get_hyperlink_column_没有_rowData_时返回空列表(self) -> None:
        service = FakeSheetsService(get_results=[{"sheets": [{"data": [{}]}]}])
        assert googlesheetapi.SheetAPI.get_hyperlink_column(service, "S", "S", "A") == []

    def test_get_hyperlink_column_结构不对时抛出去(self) -> None:
        service = FakeSheetsService(get_results=[{}])
        with pytest.raises(KeyError):
            googlesheetapi.SheetAPI.get_hyperlink_column(service, "S", "S", "A")

    def test_get_column_values_with_note_把备注拼进内容(self) -> None:
        service = FakeSheetsService(
            values_result={"values": [["a"], ["b"], ["c"]]},
            get_results=[
                # 第一次 get：拿 sheetId
                {"sheets": [{"properties": {"title": "S", "sheetId": 7}}]},
                # 第二次 get：带 gridData 的备注
                {
                    "sheets": [
                        {
                            "data": [
                                {
                                    "rowData": [
                                        {"values": [{"note": "n1"}]},
                                        {"values": [{}]},
                                        {"values": [{"note": "n3"}]},
                                    ]
                                }
                            ]
                        }
                    ]
                },
            ],
        )
        out = googlesheetapi.SheetAPI.get_column_values_with_note(
            service, "SID", "S", "E"
        )

        assert out == ["a[n1]", "b", "c[n3]"]
        assert service.get_calls[1]["includeGridData"] is True
        assert service.get_calls[1]["ranges"] == ["'S'!E:E"]

    def test_get_column_values_with_note_找不到工作表时抛_ValueError(self) -> None:
        service = FakeSheetsService(
            values_result={"values": [["a"]]},
            get_results=[{"sheets": [{"properties": {"title": "别的表", "sheetId": 1}}]}],
        )
        with pytest.raises(ValueError, match="未找到工作表"):
            googlesheetapi.SheetAPI.get_column_values_with_note(service, "SID", "S", "E")

    def test_get_column_values_with_note_没有备注时原样返回(self) -> None:
        service = FakeSheetsService(
            values_result={"values": [["a"], ["b"]]},
            get_results=[
                {"sheets": [{"properties": {"title": "S", "sheetId": 1}}]},
                {"sheets": [{"data": [{}]}]},
            ],
        )
        assert googlesheetapi.SheetAPI.get_column_values_with_note(
            service, "SID", "S", "A"
        ) == ["a", "b"]


# ==========================================================================
# jobs/fetchsfh.py
# ==========================================================================
class TestFetchSfhMapping:
    def test_按_verifiedLevelIDs_展开映射(self) -> None:
        data = {
            "nongs": {
                "hosted": {
                    "song-1": {
                        "name": "Song A",
                        "artist": "Artist A",
                        "url": "https://s/a",
                        "verifiedLevelIDs": [111, 222],
                    }
                }
            }
        }
        assert fetchsfh.build_level_to_song_mapping(data) == {
            111: {"name": "Song A", "artist": "Artist A", "url": "https://s/a"},
            222: {"name": "Song A", "artist": "Artist A", "url": "https://s/a"},
        }

    @pytest.mark.parametrize("missing", ["name", "artist", "url"])
    def test_缺关键字段的歌跳过(self, missing: str) -> None:
        song = {
            "name": "A",
            "artist": "B",
            "url": "C",
            "verifiedLevelIDs": [1],
        }
        song[missing] = None
        data = {"nongs": {"hosted": {"s": song}}}
        assert fetchsfh.build_level_to_song_mapping(data) == {}

    @pytest.mark.parametrize("verified", [[], None, "123", {}])
    def test_verifiedLevelIDs_不是非空列表就跳过(self, verified: Any) -> None:
        data = {
            "nongs": {
                "hosted": {
                    "s": {
                        "name": "A",
                        "artist": "B",
                        "url": "C",
                        "verifiedLevelIDs": verified,
                    }
                }
            }
        }
        assert fetchsfh.build_level_to_song_mapping(data) == {}

    def test_字符串_id_转成_int_不能转的跳过(self) -> None:
        data = {
            "nongs": {
                "hosted": {
                    "s": {
                        "name": "A",
                        "artist": "B",
                        "url": "C",
                        "verifiedLevelIDs": ["123", "abc", None, 456],
                    }
                }
            }
        }
        assert sorted(fetchsfh.build_level_to_song_mapping(data)) == [123, 456]

    def test_同一个关卡被多首歌认领时后面的覆盖前面的(self) -> None:
        data = {
            "nongs": {
                "hosted": {
                    "s1": {"name": "A", "artist": "x", "url": "u", "verifiedLevelIDs": [1]},
                    "s2": {"name": "B", "artist": "y", "url": "v", "verifiedLevelIDs": [1]},
                }
            }
        }
        assert fetchsfh.build_level_to_song_mapping(data)[1]["name"] == "B"

    def test_没有_nongs_节点时返回空映射(self) -> None:
        assert fetchsfh.build_level_to_song_mapping({}) == {}
        assert fetchsfh.build_level_to_song_mapping({"nongs": {}}) == {}


class TestFetchSfhMain:
    def test_抓到之后写进_staging(
        self,
        data_dirs: SimpleNamespace,
        stub_requests: Any,
        make_response: Any,
    ) -> None:
        payload = {
            "nongs": {
                "hosted": {
                    "s": {
                        "name": "Song",
                        "artist": "Artist",
                        "url": "https://s/1",
                        "verifiedLevelIDs": [42],
                    }
                }
            }
        }
        stub_requests.get("sfh-index.min.json", make_response(200, json_data=payload))

        fetchsfh.main()

        out = data_dirs.staging / "nong_index.json"
        # json 的 key 只能是字符串，落盘之后 42 -> "42"
        assert _read_json(out) == {
            "42": {"name": "Song", "artist": "Artist", "url": "https://s/1"}
        }
        assert stub_requests.calls[0]["timeout"] == 30

    def test_非_200_直接返回不落盘(
        self,
        data_dirs: SimpleNamespace,
        stub_requests: Any,
        make_response: Any,
    ) -> None:
        stub_requests.get("sfh-index.min.json", make_response(503))
        assert fetchsfh.main() is None
        assert not (data_dirs.staging / "nong_index.json").exists()


# ==========================================================================
# jobs/metadata.py
# ==========================================================================
class TestMetadataHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Someone", "someone"),
            ("  Someone  ", "someone"),
            ("A & B", "a and b"),
            ("A&B", "aandb"),
            ("", ""),
        ],
    )
    def test_normalize_creator_name(self, raw: str, expected: str) -> None:
        assert metadata.normalize_creator_name(raw) == expected

    def test_get_cache_key_去掉_and_more_并规范作者(self) -> None:
        assert metadata.get_cache_key("Null and more", "Rob & Bob") == (
            "Null",
            "rob and bob",
        )
        assert metadata.get_cache_key("  Null  ", "Rob") == ("Null", "rob")

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (7, "Unknown"),
            (8, "Easy"),
            (9, "Medium"),
            (10, "Hard"),
            (11, "Insane"),
            (12, "Extreme"),
        ],
    )
    def test_demon_type(self, code: int, expected: str) -> None:
        assert metadata.demon_type(code) == expected

    def test_缓存存读往返(self, tmp_path: Path) -> None:
        cache = [{"name": "Null", "creator": "someone", "id": 123}]
        metadata.save_metadata_cache(cache, str(tmp_path))

        assert (tmp_path / "metadata.json").exists()
        assert metadata.load_metadata_cache(tmp_path) == cache

    def test_缓存不存在时返回空列表(self, tmp_path: Path) -> None:
        assert metadata.load_metadata_cache(tmp_path / "nope") == []

    def test_缓存损坏时返回空列表而不是炸(self, tmp_path: Path) -> None:
        (tmp_path / "metadata.json").write_text("{ 不是 json", encoding="utf-8")
        assert metadata.load_metadata_cache(tmp_path) == []


class TestRateLimiter:
    def test_间隔不够时补睡(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock(start=100.0)
        monkeypatch.setattr(metadata, "time", clock)

        limiter = metadata.RateLimiter(0.5)
        limiter.wait()  # 第一次：_last=0，elapsed 巨大，不睡
        assert clock.slept == []

        limiter.wait()  # 紧接着再来一次：elapsed=0，补睡 0.5
        assert clock.slept == [0.5]

    def test_已经过了间隔就不睡(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock(start=100.0)
        monkeypatch.setattr(metadata, "time", clock)

        limiter = metadata.RateLimiter(0.5)
        limiter.wait()
        clock.now += 10.0
        limiter.wait()

        assert clock.slept == []


class FakeHttp:
    """替换 metadata 模块级那个 requests.Session。"""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class TestFetchLevelData:
    def _resp(self, make_response: Any, hits: list) -> Any:
        return make_response(200, json_data={"hits": hits})

    def test_只有一条结果直接返回(
        self, monkeypatch: pytest.MonkeyPatch, make_response: Any
    ) -> None:
        hit = {"online_id": 1, "cache_username": "Whoever", "cache_level_name": "Null"}
        http = FakeHttp(self._resp(make_response, [hit]))
        monkeypatch.setattr(metadata, "http", http)

        assert metadata.fetch_level_data("Null", "Someone") is hit
        assert http.calls[0]["params"] == {
            "query": "Null",
            "limit": 100,
            "filter": "cache_stars = 10",
        }

    def test_查询名去掉_and_more(
        self, monkeypatch: pytest.MonkeyPatch, make_response: Any
    ) -> None:
        http = FakeHttp(self._resp(make_response, [{"online_id": 1}]))
        monkeypatch.setattr(metadata, "http", http)

        metadata.fetch_level_data("Null and more", "Someone")
        assert http.calls[0]["params"]["query"] == "Null"

    def test_按作者名唯一匹配(
        self, monkeypatch: pytest.MonkeyPatch, make_response: Any
    ) -> None:
        hits = [
            {"online_id": 1, "cache_username": "Alice", "cache_level_name": "Null"},
            {"online_id": 2, "cache_username": "Bob", "cache_level_name": "Null"},
        ]
        monkeypatch.setattr(metadata, "http", FakeHttp(self._resp(make_response, hits)))

        assert metadata.fetch_level_data("Null", "Bob")["online_id"] == 2

    def test_作者匹配不出来就按关卡名精确过滤(
        self, monkeypatch: pytest.MonkeyPatch, make_response: Any
    ) -> None:
        hits = [
            {"online_id": 1, "cache_username": "Alice", "cache_level_name": "Null"},
            {"online_id": 2, "cache_username": "Bob", "cache_level_name": "Null 2"},
        ]
        monkeypatch.setattr(metadata, "http", FakeHttp(self._resp(make_response, hits)))

        assert metadata.fetch_level_data("Null", "Zed")["online_id"] == 1

    def test_过滤完还剩多条就放弃(
        self, monkeypatch: pytest.MonkeyPatch, make_response: Any
    ) -> None:
        hits = [
            {"online_id": 1, "cache_username": "Alice", "cache_level_name": "Null"},
            {"online_id": 2, "cache_username": "Bob", "cache_level_name": "Null"},
        ]
        monkeypatch.setattr(metadata, "http", FakeHttp(self._resp(make_response, hits)))

        assert metadata.fetch_level_data("Null", "Zed") is None

    def test_一条结果都没有返回_None(
        self, monkeypatch: pytest.MonkeyPatch, make_response: Any
    ) -> None:
        monkeypatch.setattr(metadata, "http", FakeHttp(self._resp(make_response, [])))
        assert metadata.fetch_level_data("Null", "Someone") is None

    def test_超时被吞成_None(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import requests

        monkeypatch.setattr(
            metadata, "http", FakeHttp(requests.exceptions.Timeout("timed out"))
        )
        assert metadata.fetch_level_data("Null", "Someone") is None

    def test_连接失败被吞成_None(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import requests

        monkeypatch.setattr(
            metadata, "http", FakeHttp(requests.exceptions.ConnectionError("no route"))
        )
        assert metadata.fetch_level_data("Null", "Someone") is None

    def test_HTTP_错误被吞成_None(
        self, monkeypatch: pytest.MonkeyPatch, make_response: Any
    ) -> None:
        monkeypatch.setattr(metadata, "http", FakeHttp(make_response(500)))
        assert metadata.fetch_level_data("Null", "Someone") is None

    def test_MANUAL_FALLBACK_默认关闭(self) -> None:
        # 打开的话 fetch_level_data 会走 input()，在 bot 里会把线程挂死
        assert metadata.MANUAL_FALLBACK is False


class TestEnrichLevelsWithIds:
    def test_全都命中缓存时一次请求都不发(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        metadata.save_metadata_cache(
            [{"name": "Null", "creator": "someone", "id": 111}], str(tmp_path)
        )
        called: list[str] = []
        monkeypatch.setattr(
            metadata, "fetch_level_data", lambda *a, **k: called.append("x")
        )

        levels = [{"name": "Null", "creator": "  Someone  "}]
        metadata.enrich_levels_with_ids(levels, cache_dir=tmp_path, interval=0)

        assert levels[0]["id"] == 111
        assert called == []

    def test_没缓存就去抓并写回缓存(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            metadata, "fetch_level_data", lambda name, creator, loose=False: {"online_id": 999}
        )

        levels = [{"name": "Null", "creator": "A & B"}]
        metadata.enrich_levels_with_ids(
            levels, cache_dir=tmp_path, max_workers=2, interval=0
        )

        assert levels[0]["id"] == 999
        assert metadata.load_metadata_cache(tmp_path) == [
            {"name": "Null", "creator": "a and b", "id": 999}
        ]

    def test_抓不到_id_时退回_gdapi(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            metadata, "fetch_level_data", lambda name, creator, loose=False: None
        )
        fake_level = SimpleNamespace(stars=10, level_name="Null", level_id=777)
        monkeypatch.setattr(gdapi, "search_levels_by_name", lambda name: [fake_level])

        levels = [{"name": "Null", "creator": "Someone"}]
        metadata.enrich_levels_with_ids(
            levels, cache_dir=tmp_path, max_workers=1, interval=0
        )

        assert levels[0]["id"] == 777

    def test_gdapi_也匹配不上时该关卡没有_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            metadata, "fetch_level_data", lambda name, creator, loose=False: None
        )
        monkeypatch.setattr(gdapi, "search_levels_by_name", lambda name: [])

        levels = [{"name": "Null", "creator": "Someone"}]
        metadata.enrich_levels_with_ids(
            levels, cache_dir=tmp_path, max_workers=1, interval=0
        )

        assert "id" not in levels[0]
        assert metadata.load_metadata_cache(tmp_path) == []

    def test_gdapi_返回多条时也放弃(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            metadata, "fetch_level_data", lambda name, creator, loose=False: None
        )
        two = [
            SimpleNamespace(stars=10, level_name="Null", level_id=1),
            SimpleNamespace(stars=10, level_name="null", level_id=2),
        ]
        monkeypatch.setattr(gdapi, "search_levels_by_name", lambda name: two)

        levels = [{"name": "Null", "creator": "Someone"}]
        metadata.enrich_levels_with_ids(
            levels, cache_dir=tmp_path, max_workers=1, interval=0
        )
        assert "id" not in levels[0]

    def test_单个关卡抓取抛异常不影响整体(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def flaky(name: str, creator: str, loose: bool = False) -> dict:
            if name == "Bad":
                raise RuntimeError("boom")
            return {"online_id": 1}

        monkeypatch.setattr(metadata, "fetch_level_data", flaky)

        levels = [{"name": "Bad", "creator": "X"}, {"name": "Good", "creator": "Y"}]
        metadata.enrich_levels_with_ids(
            levels, cache_dir=tmp_path, max_workers=2, interval=0
        )

        assert "id" not in levels[0]
        assert levels[1]["id"] == 1

    def test_同名同作者只抓一次(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def counting(name: str, creator: str, loose: bool = False) -> dict:
            calls.append(name)
            return {"online_id": 5}

        monkeypatch.setattr(metadata, "fetch_level_data", counting)

        levels = [
            {"name": "Null", "creator": "Someone"},
            {"name": "Null", "creator": "Someone"},
        ]
        # max_workers=1 让第二个必然在第一个写完 cache_map 之后才跑
        metadata.enrich_levels_with_ids(
            levels, cache_dir=tmp_path, max_workers=1, interval=0
        )

        assert calls == ["Null"]
        assert levels[0]["id"] == levels[1]["id"] == 5


# ==========================================================================
# jobs/getmetadata.py
# ==========================================================================
@pytest.fixture
def gm_data_dir(
    data_dirs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """在 data_dirs 之上，把 getmetadata.DATA_DIR 也指到 tmp_path。

    getmetadata 是 `from ..paths import DATA_DIR` 直接把**值**拿过来的，
    import 期就绑死了 —— 和 staged / staged_or_published 那种"运行时读
    paths 模块全局"的函数不一样，data_dirs 里改 paths.DATA_DIR 到不了它这。
    不单独指过来的话，enrich 的 cache_dir 会是仓库里真的 data/，
    测试会往仓库写 metadata.json。（jobs/platapi.py 也是同样的写法。）
    """
    monkeypatch.setattr(getmetadata, "DATA_DIR", data_dirs.data)
    return data_dirs


class TestGetMetadata:
    def test_load_json_file_读得回来(self, tmp_path: Path) -> None:
        p = _write_json(tmp_path / "a.json", {"levels": [1, 2]})
        assert getmetadata.load_json_file(p) == {"levels": [1, 2]}

    def test_load_json_file_文件不存在返回_None(self, tmp_path: Path) -> None:
        assert getmetadata.load_json_file(tmp_path / "nope.json") is None

    def test_load_json_file_内容不是_json_时抛出去(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{ 不是 json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            getmetadata.load_json_file(p)

    def test_save_json_file_保留非_ascii(self, tmp_path: Path) -> None:
        p = tmp_path / "out.json"
        getmetadata.save_json_file(p, {"name": "中文关卡"})
        raw = p.read_text(encoding="utf-8")
        assert "中文关卡" in raw  # ensure_ascii=False
        assert json.loads(raw) == {"name": "中文关卡"}

    def test_DATA_DIR_确实从_paths_import_进来了(self) -> None:
        """回归守卫：以前只 import 了 staged / staged_or_published，

        main() 里却用 DATA_DIR，第一行 logger.info 就 NameError。而
        getmetadata 是 runner STAGES 第二层的 job，`stop_on_error=True` 下
        它一挂 -> results['failed'] 非空 -> run_all_async 抛 RuntimeError
        -> publish() 根本轮不到，整条流水线一次都发布不出去。
        """
        assert getmetadata.DATA_DIR == paths.DATA_DIR

    def test_main_补完_metadata_写回_staging(
        self, gm_data_dir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """四个数据源都补上 id，并且写回的是 staging 而不是 data/。"""
        seen: list[tuple[int, Path]] = []

        def fake_enrich(levels: list[dict], cache_dir: Path, **kw: Any) -> None:
            seen.append((len(levels), Path(cache_dir)))
            for i, lv in enumerate(levels):
                lv["id"] = 1000 + i

        monkeypatch.setattr(getmetadata, "enrich_levels_with_ids", fake_enrich)

        sources = ["nlw_levels.json", "ids_levels.json", "lw_levels.json", "hds_levels.json"]
        for name in sources:
            _write_json(
                gm_data_dir.staging / name,
                {"levels": [{"name": name, "creator": "Someone"}]},
            )

        getmetadata.main()

        # 每个数据源各补一次，cache_dir 一律是 DATA_DIR（metadata.json 跨次复用，不发布）
        assert seen == [(1, gm_data_dir.data)] * 4
        for name in sources:
            assert _read_json(gm_data_dir.staging / name)["levels"][0]["id"] == 1000
            # 绝不能直接写进 data/ —— 那是 runner publish() 的活
            assert not (gm_data_dir.data / name).exists()

    def test_main_读不到_staging_就退回上一次发布的(
        self, gm_data_dir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """staging 里没有的那份，从 data/ 读，补完之后写到 staging。"""
        monkeypatch.setattr(
            getmetadata,
            "enrich_levels_with_ids",
            lambda levels, cache_dir, **kw: [lv.__setitem__("id", 7) for lv in levels],
        )
        _write_json(
            gm_data_dir.data / "ids_levels.json",
            {"levels": [{"name": "Acheron", "creator": "Someone"}]},
        )

        getmetadata.main()

        assert _read_json(gm_data_dir.staging / "ids_levels.json")["levels"][0]["id"] == 7
        # 上一次发布的那份原地没动
        assert "id" not in _read_json(gm_data_dir.data / "ids_levels.json")["levels"][0]

    def test_main_一个数据源都没读到就直接返回(
        self, gm_data_dir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[int] = []
        monkeypatch.setattr(
            getmetadata,
            "enrich_levels_with_ids",
            lambda *a, **kw: called.append(1),
        )

        getmetadata.main()  # 不该抛

        assert called == []
        assert list(gm_data_dir.staging.iterdir()) == []

    def test_test_函数也能跑起来(
        self, gm_data_dir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """同文件里的 test() 用的是同一批名字，以前也一样 NameError。"""
        seen: list[Path] = []
        monkeypatch.setattr(
            getmetadata,
            "enrich_levels_with_ids",
            lambda levels, cache_dir, **kw: seen.append(Path(cache_dir)),
        )
        _write_json(
            gm_data_dir.staging / "ids_levels.json",
            {"levels": [{"name": "Acheron", "creator": "Someone"}]},
        )

        getmetadata.test()

        assert seen == [gm_data_dir.data]


# ==========================================================================
# jobs/platapi.py —— 只测纯解析部分（这个模块目前没人 import，见返回说明）
# ==========================================================================
class TestJobsPlatApiParsing:
    def test_from_dict_把各字段规整成字符串(self) -> None:
        entry = jobs_platapi.PlatInfo.from_dict(
            {
                "id": 123,
                "name": "  Null  ",
                "tier": " 5 ",
                "tpl": None,
                "tags": ["a ", None, 7],
                "enjoyment": "8.5",
                "derived_levels": "不是列表",
                "derived_from": None,
            }
        )
        assert entry.id == "123"
        assert entry.name == "Null"
        assert entry.tier == "5"
        assert entry.tpl is None
        assert entry.tags == ["a", "7"]
        assert entry.enjoyment == 8.5
        assert entry.derived_levels == []
        assert entry.is_main is True

    def test_enjoyment_不能转数时落成_None(self) -> None:
        entry = jobs_platapi.PlatInfo.from_dict({"id": "1", "enjoyment": "??"})
        assert entry.enjoyment is None

    def test_有_derived_from_的不是主词条(self) -> None:
        entry = jobs_platapi.PlatInfo.from_dict({"id": "1", "derived_from": "Null"})
        assert entry.is_main is False

    def test_to_dict_from_dict_往返(self) -> None:
        raw = {
            "id": "1",
            "name": "Null",
            "tier": "5",
            "tpl": "3",
            "pemonlist": "9",
            "creator": "A",
            "tags": ["Coin"],
            "enjoyment": 8.0,
            "video": "https://v",
            "weight": "2",
            "section": "Top",
            "derived_from": None,
            "derived_levels": ["Null (Coin)"],
        }
        assert jobs_platapi.PlatInfo.from_dict(raw).to_dict() == raw

    def test_PlatData_按_id_索引且只收主词条(self, tmp_path: Path) -> None:
        cache = _write_json(
            tmp_path / "plat_combined.json",
            {
                "levels": [
                    {"id": "1", "name": "Null"},
                    {"id": "1", "name": "Null (Coin)", "derived_from": "Null"},
                    {"id": "", "name": "NoId"},
                    "不是 dict",
                ]
            },
        )
        data = jobs_platapi.PlatData(cache_file=str(cache))

        assert [e.name for e in data.entries] == ["Null", "Null (Coin)"]
        assert [e.name for e in data.main_entries] == ["Null"]
        assert [e.name for e in data.derived_entries] == ["Null (Coin)"]
        assert data.getlevelbyid("1").name == "Null"
        assert data.getlevelbyid(" 1 ").name == "Null"
        assert data.getlevelbyid("404") is None

    def test_PlatData_文件缺失或损坏时是空的(self, tmp_path: Path) -> None:
        assert jobs_platapi.PlatData(cache_file=str(tmp_path / "nope.json")).entries == []

        bad = tmp_path / "bad.json"
        bad.write_text("{ 不是 json", encoding="utf-8")
        assert jobs_platapi.PlatData(cache_file=str(bad)).entries == []


# ==========================================================================
# updater/notify.py —— 只测消息拼装，一条消息都不真发
# ==========================================================================
@pytest.fixture
def notify_env(monkeypatch: pytest.MonkeyPatch, fake_bot: Any) -> Any:
    """把 notify.get_bot 换成假 Bot，并清掉去重用的模块级状态。"""
    monkeypatch.setattr(notify, "get_bot", lambda *a, **k: fake_bot)
    monkeypatch.setattr(notify, "_last_error_key", None)
    return fake_bot


class TestNotify:
    def test_error_key_是类型加消息(self) -> None:
        assert notify._error_key(ValueError("boom")) == "ValueError:boom"
        assert notify._error_key(KeyError("k")) == "KeyError:'k'"

    def test_ADMIN_ID(self) -> None:
        assert notify.ADMIN_ID == 3251605531

    async def test_把错误私聊给管理员(self, notify_env: Any) -> None:
        await notify.report_error("数据更新失败", ValueError("boom"), {"job": "nlw"})

        assert notify_env.called_apis == ["send_private_msg"]
        api, payload = notify_env.calls[0]
        assert payload["user_id"] == notify.ADMIN_ID
        msg = payload["message"]
        assert "标题: 数据更新失败" in msg
        assert "类型: ValueError" in msg
        assert "错误: boom" in msg
        assert "job: nlw" in msg
        assert "--- traceback ---" in msg

    async def test_没有上下文时写_None(self, notify_env: Any) -> None:
        await notify.report_error("t", ValueError("boom"))
        assert "上下文:\nNone" in notify_env.calls[0][1]["message"]

    async def test_多个上下文键逐行列出(self, notify_env: Any) -> None:
        await notify.report_error("t", ValueError("x"), {"job": "a", "stage": "b"})
        msg = notify_env.calls[0][1]["message"]
        assert "job: a" in msg
        assert "stage: b" in msg

    async def test_同一个错误不重复刷屏(self, notify_env: Any) -> None:
        await notify.report_error("t", ValueError("boom"))
        await notify.report_error("t", ValueError("boom"))
        assert len(notify_env.calls) == 1

    async def test_换了错误就会再报一次(self, notify_env: Any) -> None:
        await notify.report_error("t", ValueError("boom"))
        await notify.report_error("t", ValueError("另一个"))
        assert len(notify_env.calls) == 2

    async def test_发送失败也不往外抛(self, notify_env: Any) -> None:
        notify_env.api_results["send_private_msg"] = RuntimeError("连接断了")
        await notify.report_error("t", ValueError("boom"))  # 不该抛
        assert notify_env.called_apis == ["send_private_msg"]

    async def test_在_except_里调用时带上真实_traceback(
        self, notify_env: Any
    ) -> None:
        try:
            raise ValueError("boom")
        except ValueError as e:
            await notify.report_error("t", e)

        msg = notify_env.calls[0][1]["message"]
        assert "ValueError: boom" in msg.split("--- traceback ---")[1]


# ==========================================================================
# updater/__init__.py —— 每日任务入口
# ==========================================================================
class TestDailyUpdateJob:
    async def test_跑完之后重载内存缓存(self, monkeypatch: pytest.MonkeyPatch) -> None:
        order: list[str] = []

        async def fake_run_all_async() -> dict:
            order.append("run")
            return {"success": ["nlw"], "failed": [], "published": ["nlw_levels.json"]}

        monkeypatch.setattr(updater_pkg, "run_all_async", fake_run_all_async)
        monkeypatch.setattr(gdlevelsearch, "reload_all", lambda: order.append("reload"))

        await updater_pkg.daily_update_job()

        assert order == ["run", "reload"]

    async def test_失败时上报且不重载(self, monkeypatch: pytest.MonkeyPatch) -> None:
        order: list[str] = []
        reported: dict = {}

        async def boom() -> dict:
            raise RuntimeError("Updater failed: [...]")

        async def fake_report(title: str, err: Exception, context: Any = None) -> None:
            reported.update({"title": title, "err": err, "context": context})

        monkeypatch.setattr(updater_pkg, "run_all_async", boom)
        monkeypatch.setattr(gdlevelsearch, "reload_all", lambda: order.append("reload"))
        monkeypatch.setattr(notify, "report_error", fake_report)

        await updater_pkg.daily_update_job()  # 不该往外抛

        assert order == []
        assert reported["title"] == "数据更新失败"
        assert isinstance(reported["err"], RuntimeError)
        assert reported["context"] == {"job": "daily_update", "stage": "run_all"}


class TestSetupUpdaterSslPatch:
    """setup_updater() 往 ssl._create_default_https_context 上挂 certifi 的 CA。

    标准库把这个名字当**工厂函数**用：http.client.HTTPSConnection.__init__ 里是
    `context = ssl._create_default_https_context()`。所以只能挂可调用对象。
    以前这里挂的是 `ssl.create_default_context(cafile=...)` 的**返回值**（一个
    SSLContext 实例），于是这个模块一被 import（bot 启动时 gdlevelsearch/__init__.py
    就会 import 它），整个进程里任何不显式传 context 的 HTTPS 连接都炸 TypeError。

    requests/urllib3 自己建 context，所以没受影响 —— 这也是它一直没被发现的原因。
    真正会踩到的是 urllib.request.urlopen("https://...")、httplib2
    （google-api-python-client 用的就是它）这类走标准库默认 context 的调用方。
    """

    def test_挂上去的是工厂而不是_SSLContext_实例(self) -> None:
        import ssl

        # import updater_pkg 时 setup_updater() 已经跑过了
        assert callable(ssl._create_default_https_context)
        assert not isinstance(ssl._create_default_https_context, ssl.SSLContext)
        assert isinstance(ssl._create_default_https_context(), ssl.SSLContext)

    def test_每次调用给一个新的_context(self) -> None:
        """标准库拿到 context 之后会就地改它（set_alpn_protocols /

        post_handshake_auth），共用同一个实例的话连接之间会互相串。
        """
        import ssl

        assert (
            ssl._create_default_https_context()
            is not ssl._create_default_https_context()
        )

    def test_建出来的_context_用的是_certifi_的_CA_且开着校验(self) -> None:
        import ssl

        ctx = ssl._create_default_https_context()

        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
        # certifi 那份 CA bundle 确实装进去了
        assert ctx.cert_store_stats()["x509_ca"] > 0

    def test_标准库建_HTTPS_连接不再_TypeError(self) -> None:
        import http.client
        import ssl

        # 只构造不连接：conftest 的守卫封的是 connect，构造本身不出网
        conn = http.client.HTTPSConnection("example.invalid", 443)

        assert conn._context.verify_mode == ssl.CERT_REQUIRED

    def test_setup_updater_可以重复调用(
        self, data_dirs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """import 期已经调过一次；再调一次不该把工厂降级成实例。

        带上 data_dirs：setup_updater 里会 ensure_dirs()，不重定向的话
        会在仓库里真的建出 data/.staging。
        """
        import ssl

        monkeypatch.setattr(ssl, "_create_default_https_context", ssl.create_default_context)

        updater_pkg.setup_updater()

        assert callable(ssl._create_default_https_context)
        assert isinstance(ssl._create_default_https_context(), ssl.SSLContext)
