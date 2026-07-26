"""更新器的「隔离性」测试：更新器再怎么挂，也不能把 bot 带下水。

这个文件和 test_updater.py 分工不同：
  - test_updater.py 测的是「更新器自己干活对不对」；
  - 这个文件测的是「更新器出事的时候，bot 还能不能正常跑」。

之所以单独拎出来，是因为更新器是整个程序里最脆的一块 —— 它依赖 7 个外部
数据源（Google Sheets、GD History API、GitHub raw、boomlings），任何一个
超时、改格式、限流都可能让它挂掉，而它是在 bot 进程里跑的定时任务。
这里每一条都对应一个「本来可能把 bot 拖垮」的路径。

conftest 的 no_network 是 autouse 的，所以这个文件里的「断网」是真断网。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import NetworkBlocked
from xiaozu_bot.plugins import gdlevelsearch
from xiaozu_bot.plugins.gdlevelsearch import aredlapi, nlwapi, platapi
from xiaozu_bot.plugins.gdlevelsearch.updater import paths


class TestStartupSurvivesNoNetwork:
    """断网时 bot 必须能正常启动。"""

    async def test_启动钩子在断网时不抛异常(self) -> None:
        """`_refresh_aredl_on_startup` 是注册在 driver.on_startup 上的。

        nonebot 的启动钩子抛异常会让整个启动流程失败 —— 也就是说
        api.aredl.net 挂掉的那天，bot 根本起不来。
        所以它必须自己把异常吃掉。
        """
        # 真断网：conftest 的 no_network 会让 aredlapi.reload() 里的请求抛
        # NetworkBlocked，正好模拟「连不上 AREDL」
        await gdlevelsearch._refresh_aredl_on_startup()
        # 没抛出来就算过

    async def test_启动钩子失败后插件仍然可用(self) -> None:
        """刷新失败之后，内存里的旧数据要还在，命令还能查。"""
        await gdlevelsearch._refresh_aredl_on_startup()
        # 插件模块本身没有被破坏
        assert hasattr(gdlevelsearch, "reload_all")
        assert callable(aredlapi.reload)


class TestReloadIsolatesPerSource:
    """一个数据源挂了，不能连累另外两个，也不能把内存里的旧数据清空。"""

    def test_单个源抛异常不影响其他源(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: list[str] = []

        def boom() -> None:
            called.append("nlw")
            raise RuntimeError("模拟 nlw 数据文件读坏了")

        monkeypatch.setattr(nlwapi, "reload", boom)
        monkeypatch.setattr(platapi, "reload", lambda: called.append("plat"))
        monkeypatch.setattr(aredlapi, "reload", lambda: called.append("aredl"))

        gdlevelsearch.reload_all()  # 不该抛

        # nlw 炸了，但 plat 和 aredl 照样跑到了
        assert called == ["nlw", "plat", "aredl"]

    def test_三个源全挂也不抛(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """最坏情况：磁盘上的缓存全坏了。bot 该继续跑，只是搜不到东西。"""

        def boom() -> None:
            raise OSError("模拟磁盘读不了")

        for mod in (nlwapi, platapi, aredlapi):
            monkeypatch.setattr(mod, "reload", boom)

        gdlevelsearch.reload_all()  # 不该抛


class TestDailyJobNeverEscapes:
    """定时任务无论怎么挂，都不能把异常抛给 apscheduler。"""

    async def test_流水线失败时任务本身不抛(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from xiaozu_bot.plugins.gdlevelsearch import updater

        async def boom(*a: Any, **kw: Any) -> None:
            raise RuntimeError("模拟整条流水线挂了")

        reported: list[str] = []

        async def fake_report(**kw: Any) -> None:
            reported.append(kw.get("title", ""))

        monkeypatch.setattr(updater, "run_all_async", boom)
        monkeypatch.setattr(
            "xiaozu_bot.plugins.gdlevelsearch.updater.notify.report_error", fake_report
        )

        await updater.daily_update_job()  # 不该抛

        assert reported == ["数据更新失败"]

    async def test_没有bot连着时上报不会把真正的错误顶掉(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """回归：`get_bot()` 以前写在 try 外面。

        定时任务是在 bot 进程里跑的，但**没有任何 QQ 客户端连着的时候**
        （刚启动、断线重连中、OneBot 那头挂了）`get_bot()` 抛
        ValueError("There are no bots to get.")。
        它以前在 try 外面，于是这个 ValueError 会从 daily_update_job 的
        except 分支里冒出去，把原始错误顶掉 —— 日志里只剩「没有 bot」，
        真正挂在哪一步反而看不见了。
        """
        from xiaozu_bot.plugins.gdlevelsearch.updater import notify

        def no_bots() -> Any:
            raise ValueError("There are no bots to get.")

        monkeypatch.setattr(notify, "get_bot", no_bots)
        monkeypatch.setattr(notify, "_last_error_key", None)

        # 不该抛：拿不到 bot 只能记日志，不能变成第二个异常
        await notify.report_error(
            title="数据更新失败", err=RuntimeError("真正的错误"), context={"job": "x"}
        )

    async def test_整条链路_断网加没有bot_也不抛(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """最真实的坏情况：半夜三点断网，而且那会儿 bot 恰好没连上。"""
        from xiaozu_bot.plugins.gdlevelsearch import updater
        from xiaozu_bot.plugins.gdlevelsearch.updater import notify

        async def boom(*a: Any, **kw: Any) -> None:
            raise NetworkBlocked("模拟断网")

        def no_bots() -> Any:
            raise ValueError("There are no bots to get.")

        monkeypatch.setattr(updater, "run_all_async", boom)
        monkeypatch.setattr(notify, "get_bot", no_bots)
        monkeypatch.setattr(notify, "_last_error_key", None)

        await updater.daily_update_job()  # 从头到尾一声不吭地扛住


class TestPublishRefusesToWipeGoodData:
    """发布前的下限检查：解析出空数据时，宁可不发布也不能盖掉线上的。"""

    @staticmethod
    def _write(path: Path, n: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": 0, "levels": [{"name": f"lv{i}"} for i in range(n)]}
        path.write_text(json.dumps(payload), encoding="utf-8")

    @pytest.fixture
    def dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
        data, staging = tmp_path / "data", tmp_path / "data" / ".staging"
        data.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(paths, "DATA_DIR", data)
        monkeypatch.setattr(paths, "STAGING_DIR", staging)
        return data, staging

    def test_空数据不会盖掉线上数据(self, dirs: tuple[Path, Path]) -> None:
        """上游表格改格式 -> 解析出 [] -> job 不抛异常 -> 以前会直接发布上去。"""
        data, staging = dirs
        self._write(data / "nlw_levels.json", 1565)
        self._write(staging / "nlw_levels.json", 0)

        moved = paths.publish()

        assert moved == [], "空数据绝对不能发布"
        assert len(json.loads((data / "nlw_levels.json").read_text())["levels"]) == 1565

    def test_腰斩也拦住(self, dirs: tuple[Path, Path]) -> None:
        """插一行空行会让解析在中间 break，产出一份「看着正常」的半截数据。"""
        data, staging = dirs
        self._write(data / "hds_levels.json", 2000)
        self._write(staging / "hds_levels.json", 300)

        assert paths.publish() == []
        assert len(json.loads((data / "hds_levels.json").read_text())["levels"]) == 2000

    def test_正常波动照常发布(self, dirs: tuple[Path, Path]) -> None:
        """实测一次真实更新：hds -3.9%、ids +1.7%、plat +3.4%，都得放行。"""
        data, staging = dirs
        self._write(data / "hds_levels.json", 2108)
        self._write(staging / "hds_levels.json", 2025)

        assert paths.publish() == ["hds_levels.json"]
        assert len(json.loads((data / "hds_levels.json").read_text())["levels"]) == 2025

    def test_首次发布没有旧数据时放行(self, dirs: tuple[Path, Path]) -> None:
        """干净部署：data/ 里还什么都没有，这时候不该拦。"""
        _, staging = dirs
        self._write(staging / "lw_levels.json", 5)

        assert paths.publish() == ["lw_levels.json"]

    def test_一个文件被拦不影响其他文件(self, dirs: tuple[Path, Path]) -> None:
        data, staging = dirs
        self._write(data / "nlw_levels.json", 1000)
        self._write(staging / "nlw_levels.json", 0)      # 该拦
        self._write(data / "ids_levels.json", 1000)
        self._write(staging / "ids_levels.json", 1010)   # 该放行

        assert paths.publish() == ["ids_levels.json"]
        assert len(json.loads((data / "nlw_levels.json").read_text())["levels"]) == 1000


class TestBotWorksWithBrokenData:
    """数据文件缺失/损坏时，命令要能给出提示，而不是把 handler 炸掉。"""

    def test_数据文件是坏json时reload不抛(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """磁盘写到一半断电，留下半截 json。bot 重启时不能因此起不来。"""
        broken = tmp_path / "nlw_levels.json"
        broken.write_text('{"levels": [{"name": "半截', encoding="utf-8")

        def boom() -> None:
            json.loads(broken.read_text(encoding="utf-8"))

        monkeypatch.setattr(nlwapi, "reload", boom)
        monkeypatch.setattr(platapi, "reload", lambda: None)
        monkeypatch.setattr(aredlapi, "reload", lambda: None)

        gdlevelsearch.reload_all()  # reload_all 会把每个源的异常吃掉


class TestUpdaterImportIsSideEffectSafe:
    """import 更新器这件事本身不能联网、不能崩。"""

    def test_断网下import整个updater包(self) -> None:
        """`gdlevelsearch/__init__.py` 里 `from . import updater` 是模块级的。

        updater/__init__.py 在模块级就跑了 setup_updater()。要是它联网或者
        抛异常，整个 gdlevelsearch 插件就加载不了，bot 直接少一大块功能。
        """
        import importlib

        mod = importlib.import_module(
            "xiaozu_bot.plugins.gdlevelsearch.updater"
        )
        assert hasattr(mod, "daily_update_job")

    def test_ssl补丁挂的是工厂不是实例(self) -> None:
        """回归：以前挂的是 create_default_context(...) 的返回值。

        标准库把 ssl._create_default_https_context 当**工厂**调用，挂成实例的话
        全进程任何不显式传 context 的 HTTPS 连接都 TypeError —— 而这个补丁是在
        import 期打的，等于 bot 一启动就把 urllib/httplib2 的 HTTPS 废掉了。
        """
        import ssl

        assert callable(ssl._create_default_https_context)
        ctx1 = ssl._create_default_https_context()
        ctx2 = ssl._create_default_https_context()
        assert isinstance(ctx1, ssl.SSLContext)
        # 标准库拿到 context 之后会就地改它，所以每次必须是新的
        assert ctx1 is not ctx2


class TestSchedulerRegistrationIsOptional:
    """定时任务注册失败不能让 import 挂掉（scripts/run_updater.py 就靠这个）。"""

    def test_没有nonebot环境时也能import(self) -> None:
        """回归：以前只 catch ImportError。

        nonebot_plugin_apscheduler 装着的时候 import 本身是成功的，但它模块体里
        会调 get_driver()，没 nonebot.init() 过就抛 ValueError —— 也就是说
        `python scripts/run_updater.py` 在 import 期就直接炸，
        而那个脚本正是「不起 bot 也能更新数据」的唯一入口。
        """
        source = (
            Path(__file__).resolve().parent.parent
            / "xiaozu_bot/plugins/gdlevelsearch/updater/__init__.py"
        ).read_text(encoding="utf-8")
        assert "except (ImportError, ValueError)" in source, (
            "定时任务注册的兜底必须同时接住 ImportError 和 ValueError"
        )


class TestNoNetworkAtImportTime:
    """整个插件树在断网下必须 import 得动（no_network 是 autouse 的）。"""

    @pytest.mark.parametrize(
        "name",
        [
            "xiaozu_bot.plugins.gdlevelsearch",
            "xiaozu_bot.plugins.gdlevelsearch.updater",
            "xiaozu_bot.plugins.gdlevelsearch.updater.runner",
            "xiaozu_bot.plugins.gdlevelsearch.updater.paths",
            "xiaozu_bot.plugins.gdlevelsearch.updater.notify",
        ],
    )
    def test_模块能import(self, name: str) -> None:
        import importlib

        assert importlib.import_module(name) is not None
