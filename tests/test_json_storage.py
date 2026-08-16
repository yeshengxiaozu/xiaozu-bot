"""`xiaozu_bot/utils/json_storage.py`（JsonRedis + plugin_storage）的测试。

全仓库每个插件的持久化都压在这个模块上，所以这里测得细一点：
类型保真、过期语义、哈希表和「带过期时间的普通键」怎么区分、
坏文件的容错、写盘的原子性、以及类自己声明的线程安全。

外加 `scripts/` 里三个脚本中**不需要网络也不需要真 Redis**的那部分逻辑。

约定：
- 只写 `tmp_path`，一个字节都不往仓库工作区里写；
- 过期相关一律用假时钟（monkeypatch 掉 json_storage 模块里的 `time`），
  不 sleep、不依赖真实时钟；
- 断言的都是源码里真读到的行为。个别地方源码的行为看着不对，
  测试仍然按**实际行为**写，但会在注释里标出来（搜 "看着不对"）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO_ROOT
from xiaozu_bot.utils import json_storage
from xiaozu_bot.utils.json_storage import JsonRedis, plugin_storage, write_json_atomic

SCRIPTS_DIR = REPO_ROOT / "scripts"
_MISSING = object()


# ===========================================================================
# 本文件自用的小工具（公共 fixture 在 tests/conftest.py，这里不重复造）
# ===========================================================================
class _FakeClock:
    """假时钟。装到 json_storage.time 上，过期逻辑就变成完全可控的。

    json_storage 里只用到 `time.time()`，所以实现这一个方法就够。
    """

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    """把 json_storage 模块里的 time 换成假时钟。

    只替换模块全局名，不动真的 time 模块，影响面最小。
    """
    clock = _FakeClock()
    monkeypatch.setattr(json_storage, "time", clock)
    return clock


class _RecordingLogger:
    """替掉 json_storage 里的 loguru logger。

    一是不让坏文件用例往 stderr 刷一堆 traceback，
    二是能直接断言「记没记那条日志」。
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _record(self, level: str) -> Callable[..., None]:
        def _log(message: Any = "", *args: Any, **kwargs: Any) -> None:
            self.records.append((level, str(message)))

        return _log

    def __getattr__(self, name: str) -> Callable[..., None]:
        return self._record(name)

    @property
    def messages(self) -> list[str]:
        return [message for _, message in self.records]


@pytest.fixture
def recording_logger(monkeypatch: pytest.MonkeyPatch) -> _RecordingLogger:
    """接管 json_storage 的日志输出，返回记录器。"""
    logger = _RecordingLogger()
    monkeypatch.setattr(json_storage, "logger", logger)
    return logger


def _read_raw(path: Path) -> Any:
    """把存储文件按 json 读出来（断言真的落盘了什么用）。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _load_script(name: str, *, restore_syspath: bool = True) -> types.ModuleType:
    """按文件路径加载 scripts/ 下的脚本，不塞进 sys.modules。

    这几个脚本在模块级会改 sys.path（migrate 塞仓库根目录、run_updater 塞
    gdlevelsearch 目录），跑测试的进程是所有测试共用的，所以默认加载完就还原，
    免得污染别人。
    """
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_xiaozu_test_script_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        if restore_syspath:
            sys.path[:] = saved
    return module


# ===========================================================================
# plugin_storage
# ===========================================================================
class TestPluginStorage:
    """路径是相对插件文件算的，不是相对 cwd —— 这就是这个函数存在的理由。"""

    def test_path_is_relative_to_plugin_file(self, tmp_path: Path) -> None:
        """<插件目录>/data/storage.json"""
        plugin_file = tmp_path / "plugins" / "demo" / "__init__.py"

        assert plugin_storage(plugin_file) == (
            tmp_path / "plugins" / "demo" / "data" / "storage.json"
        ).resolve()

    def test_path_does_not_depend_on_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """换个工作目录，同一个插件文件算出来的路径必须一模一样。"""
        plugin_file = tmp_path / "plugins" / "demo" / "__init__.py"
        before = plugin_storage(plugin_file)

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        assert plugin_storage(plugin_file) == before

    def test_relative_plugin_file_is_resolved_against_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """反过来：传相对路径就还是跟着 cwd 走（Path.resolve 的语义）。

        调用方传的都是 `__file__`（3.9+ 是绝对路径），所以生产上不会踩到；
        这条只是把边界钉住。
        """
        monkeypatch.chdir(tmp_path)
        here = Path.cwd()

        assert plugin_storage("demo_plugin.py") == here / "data" / "storage.json"

        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.chdir(sub)
        assert plugin_storage("demo_plugin.py") == Path.cwd() / "data" / "storage.json"
        assert Path.cwd() != here

    def test_accepts_str_and_path_alike(self, tmp_path: Path) -> None:
        """str 和 Path 传进来结果一样。"""
        plugin_file = tmp_path / "plugins" / "demo" / "__init__.py"

        assert plugin_storage(str(plugin_file)) == plugin_storage(plugin_file)

    def test_custom_file_name(self, tmp_path: Path) -> None:
        """第二个参数能换文件名，目录固定还是 data/。"""
        plugin_file = tmp_path / "plugins" / "demo" / "__init__.py"

        assert plugin_storage(plugin_file, "levels.json") == (
            tmp_path / "plugins" / "demo" / "data" / "levels.json"
        ).resolve()

    def test_does_not_create_anything(self, tmp_path: Path) -> None:
        """纯算路径，不建目录也不建文件。"""
        plugin_file = tmp_path / "plugins" / "demo" / "__init__.py"

        result = plugin_storage(plugin_file)

        assert not result.exists()
        assert not result.parent.exists()

    @pytest.mark.parametrize("plugin", ["jrrp", "guess", "zhua"])
    def test_matches_real_plugin_layout(self, plugin: str) -> None:
        """拿仓库里真的插件文件算一遍，落点必须是 plugins/<x>/data/storage.json。

        只算路径，不 import 插件，所以没有任何副作用。
        """
        plugin_file = REPO_ROOT / "xiaozu_bot" / "plugins" / plugin / "__init__.py"
        assert plugin_file.is_file()

        assert plugin_storage(plugin_file) == (
            REPO_ROOT / "xiaozu_bot" / "plugins" / plugin / "data" / "storage.json"
        )


# ===========================================================================
# get / set
# ===========================================================================
class TestGetSet:
    @pytest.mark.parametrize(
        "value",
        [
            "字符串",
            123,
            0,
            -1,
            3.5,
            True,
            False,
            None,
            [1, "a", None],
            {"a": 1, "b": [2, 3]},
            {},
            [],
        ],
    )
    def test_roundtrip_keeps_python_type(self, json_redis: JsonRedis, value: Any) -> None:
        """存什么类型读出来还是什么类型 —— docstring 里明写了不像真 redis 那样都变 str。

        dailydemon.py 依赖这一点（`r.set(RECENT_KEY, recent)` 存的是 list，
        取出来 `isinstance(value, list)` 才成立）。
        """
        json_redis.set("k", value)

        got = json_redis.get("k")
        assert got == value
        assert type(got) is type(value)

    def test_int_stays_int_not_str(self, json_redis: JsonRedis) -> None:
        """特别钉一下：存 int 读出来不是 "1"。"""
        json_redis.set("n", 1)

        assert json_redis.get("n") == 1
        assert json_redis.get("n") != "1"

    def test_missing_key_returns_none(self, json_redis: JsonRedis) -> None:
        assert json_redis.get("没有这个键") is None

    def test_set_overwrites(self, json_redis: JsonRedis) -> None:
        json_redis.set("k", "old")
        json_redis.set("k", "new")

        assert json_redis.get("k") == "new"

    def test_none_value_is_indistinguishable_from_missing_by_get(
        self, json_redis: JsonRedis
    ) -> None:
        """存 None 之后 get 是 None、exists 是 True，但 ttl 会当成不存在返回 -2。

        ttl 里判的是 `value is None`（第 134 行），所以「存了 None 的键」
        和「不存在的键」在 ttl 眼里一样。看着不对，但没有调用方存 None，
        这里只是把现状钉住。
        """
        json_redis.set("k", None)

        assert json_redis.get("k") is None
        assert json_redis.exists("k") is True
        assert json_redis.ttl("k") == -2

    def test_get_returns_the_live_object_not_a_copy(self, json_redis: JsonRedis) -> None:
        """get 返回的是内部那个对象本身，改它会直接改到内存里的数据、且不写盘。

        dailydemon.get_recent() 是重新构造了一个 list 才没踩到。
        """
        json_redis.set("lst", [1, 2])

        json_redis.get("lst").append(3)

        assert json_redis.get("lst") == [1, 2, 3]
        # 但磁盘上还是老样子 —— 没走 set 就没保存
        assert _read_raw(json_redis.file_path)["lst"] == [1, 2]

    def test_json_roundtrip_narrows_types_after_reload(self, tmp_path: Path) -> None:
        """跨进程重载会受 json 本身的限制：tuple 变 list、int 键变 str 键。

        这是 json 的语义，不是 bug，但写调用方的时候得知道。
        """
        path = tmp_path / "storage.json"
        first = JsonRedis(path)
        first.set("tup", (1, 2))
        first.set("intkey", {1: "a"})

        # 同一个实例里还是原来的 python 对象
        assert first.get("tup") == (1, 2)

        second = JsonRedis(path)
        assert second.get("tup") == [1, 2]
        assert second.get("intkey") == {"1": "a"}


# ===========================================================================
# 过期时间 / ttl
# ===========================================================================
class TestExpiry:
    def test_value_visible_before_expiry_and_gone_after(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        json_redis.set("k", "v", ex=60)

        fake_clock.advance(59.999)
        assert json_redis.get("k") == "v"

        # 到点用的是 >=，所以正好等于过期时刻就算过期了
        fake_clock.advance(0.001)
        assert json_redis.get("k") is None

    def test_expiring_value_is_wrapped_on_disk(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        """带 ex 的值在文件里是 {"_val": …, "_exp": …} 这种壳。"""
        json_redis.set("k", "v", ex=60)

        raw = _read_raw(json_redis.file_path)
        assert raw["k"] == {"_val": "v", "_exp": fake_clock.now + 60}

    def test_ttl_positive_minus_one_minus_two(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        """docstring 承诺的三种返回值。"""
        json_redis.set("有过期", "v", ex=60)
        json_redis.set("没过期", "v")

        assert json_redis.ttl("有过期") == 60
        assert json_redis.ttl("没过期") == -1
        assert json_redis.ttl("不存在") == -2

        fake_clock.advance(60)
        assert json_redis.ttl("有过期") == -2

    def test_ttl_counts_down(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        json_redis.set("k", "v", ex=600)

        fake_clock.advance(100)
        assert json_redis.ttl("k") == 500

    def test_ttl_truncates_toward_zero(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        """int() 是截断，所以剩 0.5 秒的活键 ttl 返回 0。

        guess / zhua 判的是 `r.ttl(key) > 0`，也就是最后不到 1 秒的冷却
        会被当成已经结束。差 1 秒无所谓，但这是实打实的边界，钉住。
        """
        json_redis.set("k", "v", ex=10)

        fake_clock.advance(9.5)
        assert json_redis.ttl("k") == 0
        # 键其实还活着，get 拿得到
        assert json_redis.get("k") == "v"

    @pytest.mark.parametrize("ex", [0, -1])
    def test_non_positive_ex_expires_immediately(
        self, json_redis: JsonRedis, fake_clock: _FakeClock, ex: int
    ) -> None:
        """ex<=0 等于立刻过期（`now >= _exp` 当场成立）。"""
        json_redis.set("k", "v", ex=ex)

        assert json_redis.get("k") is None
        assert json_redis.ttl("k") == -2
        assert json_redis.exists("k") is False

    def test_expired_key_disappears_from_keys_exists_hkeys(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        json_redis.set("会过期", "v", ex=30)
        json_redis.set("不会过期", "v")

        assert json_redis.keys() == ["会过期", "不会过期"]

        fake_clock.advance(30)

        assert json_redis.keys() == ["不会过期"]
        assert json_redis.exists("会过期") is False
        assert json_redis.hkeys("会过期") == []
        assert json_redis.hexists("会过期", "_val") is False

    def test_clean_expired_reports_whether_it_deleted(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        """_clean_expired 的返回值：删了东西才是 True。"""
        json_redis.set("k", "v", ex=30)

        assert json_redis._clean_expired() is False

        fake_clock.advance(30)
        assert json_redis._clean_expired() is True
        # 已经删干净了，再清一次就没得删
        assert json_redis._clean_expired() is False

    def test_expired_entry_lingers_on_disk_until_next_write(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        """_clean_expired 只改内存不写盘，过期项要等下一次写操作才从文件里消失。

        对外行为没差别（重新加载后一访问就又被清掉），但排查数据的时候
        会在文件里看到"早该没了"的键。
        """
        json_redis.set("会过期", "v", ex=30)
        fake_clock.advance(30)

        assert json_redis.get("会过期") is None
        assert "会过期" in _read_raw(json_redis.file_path)

        json_redis.set("别的", 1)
        assert "会过期" not in _read_raw(json_redis.file_path)

    def test_ttl_survives_reload(self, tmp_path: Path, fake_clock: _FakeClock) -> None:
        """过期时间存的是绝对时间戳，换个实例读出来剩余秒数照样对。"""
        path = tmp_path / "storage.json"
        first = JsonRedis(path)
        first.set("k", "v", ex=100)

        fake_clock.advance(40)
        second = JsonRedis(path)

        assert second.get("k") == "v"
        assert second.ttl("k") == 60

    def test_expired_key_is_gone_after_reload(
        self, tmp_path: Path, fake_clock: _FakeClock
    ) -> None:
        path = tmp_path / "storage.json"
        first = JsonRedis(path)
        first.set("k", "v", ex=100)

        fake_clock.advance(100)
        second = JsonRedis(path)

        assert second.get("k") is None
        assert second.keys() == []

    def test_set_without_ex_clears_previous_expiry(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        """重新 set 不带 ex，整个值被替换掉，过期时间也就没了（同 redis SET）。"""
        json_redis.set("k", "v", ex=10)
        json_redis.set("k", "v2")

        assert json_redis.ttl("k") == -1
        fake_clock.advance(100)
        assert json_redis.get("k") == "v2"


# ===========================================================================
# 哈希表
# ===========================================================================
class TestHash:
    def test_basic_hash_ops(self, json_redis: JsonRedis) -> None:
        json_redis.hset("preferences", "12345", "1")
        json_redis.hset("preferences", "999", "0")

        assert json_redis.hget("preferences", "12345") == "1"
        assert sorted(json_redis.hkeys("preferences")) == ["12345", "999"]
        assert json_redis.hexists("preferences", "999") is True
        assert json_redis.hexists("preferences", "别的") is False

    def test_hash_values_keep_type(self, json_redis: JsonRedis) -> None:
        json_redis.hset("h", "n", 7)

        assert json_redis.hget("h", "n") == 7

    def test_hget_on_missing_name_or_field(self, json_redis: JsonRedis) -> None:
        assert json_redis.hget("没这张表", "f") is None

        json_redis.hset("h", "a", 1)
        assert json_redis.hget("h", "没这个字段") is None

    def test_hkeys_hexists_on_missing_name(self, json_redis: JsonRedis) -> None:
        assert json_redis.hkeys("没这张表") == []
        assert json_redis.hexists("没这张表", "f") is False

    def test_hset_replaces_non_dict_value(self, json_redis: JsonRedis) -> None:
        """原来是普通值的键，hset 之后直接变成一张新的空表再塞字段。"""
        json_redis.set("k", "我是字符串")

        json_redis.hset("k", "f", "v")

        assert json_redis.hget("k", "f") == "v"
        assert json_redis.get("k") == {"f": "v"}

    def test_hset_on_list_value_also_replaces(self, json_redis: JsonRedis) -> None:
        json_redis.set("k", [1, 2, 3])

        json_redis.hset("k", "f", "v")

        assert json_redis.get("k") == {"f": "v"}

    def test_hash_survives_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "storage.json"
        JsonRedis(path).hset("h", "f", "v")

        assert JsonRedis(path).hget("h", "f") == "v"

    def test_field_named_val_is_not_confused(self, json_redis: JsonRedis) -> None:
        """字段名叫 `_val` 没问题 —— 判定看的是有没有 `_exp`。"""
        json_redis.hset("h", "_val", "x")

        assert json_redis.hkeys("h") == ["_val"]
        assert json_redis.hexists("h", "_val") is True
        assert json_redis.hget("h", "_val") == "x"
        # 没有 _exp，get 就把整张表原样返回
        assert json_redis.get("h") == {"_val": "x"}

    def test_field_named_exp_in_the_past_deletes_the_whole_hash(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        """**看着不对**：字段名恰好叫 `_exp` 且值是个过去的时间戳，整张表会被静默删掉。

        _clean_expired 只看 "有没有 _exp 这个 key"，分不清哈希表和过期壳。
        现有调用方的字段名都是 QQ 号 / 群号，踩不到；但这是个真的坑。
        """
        json_redis.hset("h", "_exp", 1)

        assert json_redis.exists("h") is False
        assert json_redis.hget("h", "_exp") is None

    def test_field_named_exp_in_the_future_breaks_get(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        """**看着不对**：`_exp` 字段值在未来的话，表不会被删，但一系列 API 互相打架。"""
        json_redis.hset("h", "_exp", fake_clock.now + 10_000)

        # 键还在
        assert json_redis.exists("h") is True
        # hget 不走 _as_hash，直接把字段给了
        assert json_redis.hget("h", "_exp") == fake_clock.now + 10_000
        # 但 hkeys / hexists 走 _as_hash，把它当成"过期壳"排掉了
        assert json_redis.hkeys("h") == []
        assert json_redis.hexists("h", "_exp") is False
        # get 更惨：当成过期壳去取 _val，直接 KeyError
        with pytest.raises(KeyError):
            json_redis.get("h")

    def test_non_numeric_exp_field_poisons_every_operation(
        self, json_redis: JsonRedis
    ) -> None:
        """**看着不对**：`_exp` 字段是个字符串的话，_clean_expired 里 float >= str 抛 TypeError，
        整个实例上所有读操作全废（get/keys/exists/hget… 都要先 _clean_expired）。

        没有校验，也没有兜底 try。
        """
        json_redis.hset("h", "_exp", "马上")

        for call in (
            lambda: json_redis.get("h"),
            json_redis.keys,
            lambda: json_redis.exists("别的键"),
            lambda: json_redis.hkeys("h"),
            lambda: json_redis.ttl("别的键"),
        ):
            with pytest.raises(TypeError):
                call()

    def test_hash_apis_on_an_expiring_scalar_key(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        """带 ex 的普通键在哈希 API 下的表现：hkeys/hexists 认得出来，hget 认不出来。

        _as_hash 明确把 {_val,_exp} 排掉了，所以 hkeys/hexists 返回空；
        但 hget 根本没调 _as_hash，于是能把内部字段 `_val` / `_exp` 直接读出来。
        **看着不对**，不过没有调用方会对同一个键混用两套 API。
        """
        json_redis.set("k", "v", ex=60)

        assert json_redis.hkeys("k") == []
        assert json_redis.hexists("k", "_val") is False
        # 内部实现细节从 hget 漏出来了
        assert json_redis.hget("k", "_val") == "v"
        assert json_redis.hget("k", "_exp") == fake_clock.now + 60

    def test_hset_on_an_expiring_key_pollutes_the_wrapper(
        self, json_redis: JsonRedis, fake_clock: _FakeClock
    ) -> None:
        """**看着不对**：对带 ex 的键 hset，字段会被塞进过期壳里，而不是把壳换掉。

        hset 只判了 isinstance(dict)，过期壳也是 dict，于是留下一个
        {_val, _exp, 业务字段} 的四不像：get 还能用，hkeys 说没有字段，
        hget 又说有 —— 三个 API 各说各话。
        """
        json_redis.set("k", "v", ex=60)

        json_redis.hset("k", "f", "x")

        assert json_redis.get("k") == "v"
        assert json_redis.hget("k", "f") == "x"
        assert json_redis.hkeys("k") == []
        assert json_redis.hexists("k", "f") is False
        # 过期时间也还在，到点整条（连同业务字段）一起没
        fake_clock.advance(60)
        assert json_redis.exists("k") is False


# ===========================================================================
# keys / exists / delete
# ===========================================================================
class TestKeysExistsDelete:
    def test_keys_defaults_to_everything(self, json_redis: JsonRedis) -> None:
        json_redis.set("a", 1)
        json_redis.hset("h", "f", 1)

        result = json_redis.keys()

        assert isinstance(result, list)
        assert sorted(result) == ["a", "h"]

    def test_keys_accepts_pattern_as_keyword(self, json_redis: JsonRedis) -> None:
        """参数名必须叫 pattern —— docstring 说有调用方是按关键字传的。"""
        json_redis.set("roulette_status_1", "x")
        json_redis.set("别的", "x")

        assert json_redis.keys(pattern="roulette_status*") == ["roulette_status_1"]

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            ("jrrp_*", ["jrrp_1", "jrrp_22"]),
            ("jrrp_?", ["jrrp_1"]),
            ("jrrp_[12]", ["jrrp_1"]),
            ("*_1", ["jrrp_1", "zhua_1"]),
            ("jrrp_1", ["jrrp_1"]),
            ("nope*", []),
        ],
    )
    def test_keys_glob_semantics(
        self, json_redis: JsonRedis, pattern: str, expected: list[str]
    ) -> None:
        """跟 redis KEYS 一样的 glob：* / ? / [seq]。"""
        for key in ("jrrp_1", "jrrp_22", "zhua_1"):
            json_redis.set(key, 1)

        assert sorted(json_redis.keys(pattern)) == sorted(expected)

    def test_keys_case_sensitivity_is_platform_dependent(
        self, json_redis: JsonRedis
    ) -> None:
        """**看着不对**：用的是 `fnmatch.filter`，它会先 os.path.normcase。

        Windows 上 normcase 会转小写，于是 keys() 变成大小写不敏感、还会把
        "/" 当 "\\"；Linux 上则是敏感的。真 redis 的 KEYS 任何平台都区分大小写。
        也就是说同一份数据在开发机（Windows）和服务器（Linux）上匹配结果不一样。
        想要平台一致应该用 fnmatch.fnmatchcase。
        """
        json_redis.set("JRRP_1", 1)
        json_redis.set("jrrp_2", 2)

        matched = sorted(json_redis.keys("jrrp_*"))

        if os.name == "nt":
            assert matched == ["JRRP_1", "jrrp_2"]
        else:
            assert matched == ["jrrp_2"]

    def test_exists(self, json_redis: JsonRedis) -> None:
        json_redis.set("a", 1)
        json_redis.hset("h", "f", 1)

        assert json_redis.exists("a") is True
        assert json_redis.exists("h") is True
        assert json_redis.exists("没有") is False

    def test_delete(self, json_redis: JsonRedis) -> None:
        json_redis.set("a", 1)
        json_redis.hset("h", "f", 1)

        json_redis.delete("a")
        json_redis.delete("h")

        assert json_redis.exists("a") is False
        assert json_redis.get("a") is None
        assert json_redis.hkeys("h") == []
        assert _read_raw(json_redis.file_path) == {}

    def test_delete_missing_key_is_a_noop_and_does_not_write(
        self, json_redis: JsonRedis
    ) -> None:
        """删不存在的键：不抛异常，也不会重写文件（源码里 save 在 if 里面）。"""
        # 直接往文件里塞个哨兵，只要没被重写它就还在
        json_redis.file_path.write_text('{"哨兵": 1}', encoding="utf-8")

        json_redis.delete("没有这个键")
        assert _read_raw(json_redis.file_path) == {"哨兵": 1}

        # 真写一次就会按内存里的数据整体覆盖，哨兵随之消失
        json_redis.set("a", 1)
        assert _read_raw(json_redis.file_path) == {"a": 1}


# ===========================================================================
# 加载 / 保存 / 崩溃安全
# ===========================================================================
class TestLoadAndSave:
    def test_creates_file_and_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "深" / "一点" / "storage.json"

        redis = JsonRedis(path)

        assert path.is_file()
        assert _read_raw(path) == {}
        assert redis.data == {}

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        """migrate 脚本传的是 str(target)。"""
        path = tmp_path / "storage.json"

        redis = JsonRedis(str(path))

        assert isinstance(redis.file_path, Path)
        assert redis.file_path == path

    def test_data_survives_a_new_instance(self, tmp_path: Path) -> None:
        path = tmp_path / "storage.json"
        first = JsonRedis(path)
        first.set("a", 1)
        first.hset("h", "f", "v")

        second = JsonRedis(path)

        assert second.get("a") == 1
        assert second.hget("h", "f") == "v"

    def test_auto_save_false_never_touches_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "storage.json"

        redis = JsonRedis(path, auto_save=False)
        redis.set("a", 1)
        redis.hset("h", "f", 1)
        redis.delete("a")

        assert not path.exists()
        assert redis.hget("h", "f") == 1

    def test_auto_save_false_still_loads_and_can_save_explicitly(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "storage.json"
        JsonRedis(path).set("旧的", 1)

        redis = JsonRedis(path, auto_save=False)
        assert redis.get("旧的") == 1

        redis.set("新的", 2)
        # 还没手动 save，文件里只有旧的
        assert _read_raw(path) == {"旧的": 1}

        redis._save()
        assert JsonRedis(path).get("新的") == 2

    def test_empty_file_is_not_treated_as_corrupt(
        self, tmp_path: Path, recording_logger: _RecordingLogger
    ) -> None:
        """0 字节的文件走的是"当空的"分支，不算坏文件，不会留 .broken。"""
        path = tmp_path / "storage.json"
        path.write_bytes(b"")

        redis = JsonRedis(path)

        assert redis.data == {}
        assert not path.with_suffix(".json.broken").exists()
        assert recording_logger.records == []
        # auto_save 的话顺手把它写成了合法的空 json
        assert _read_raw(path) == {}

    def test_corrupt_file_is_quarantined_instead_of_raising(
        self, tmp_path: Path, recording_logger: _RecordingLogger
    ) -> None:
        """半截 json 不能让插件 import 失败：改名留档 + 从空的开始。"""
        path = tmp_path / "storage.json"
        broken_text = '{"a": 1, "b": [1, 2'
        path.write_text(broken_text, encoding="utf-8")

        redis = JsonRedis(path)

        assert redis.data == {}
        assert redis.get("a") is None

        quarantined = tmp_path / "storage.json.broken"
        assert quarantined.read_text(encoding="utf-8") == broken_text
        # 原文件是被 os.replace 挪走的，这一步之后并没有立刻重建
        assert not path.exists()
        assert any(".broken" in m for m in recording_logger.messages)

        # 后续写一次就能正常用了
        redis.set("a", 2)
        assert _read_raw(path) == {"a": 2}

    def test_non_utf8_file_is_not_quarantined(
        self, tmp_path: Path, recording_logger: _RecordingLogger
    ) -> None:
        """**看着不对**：编码坏掉的文件不走隔离逻辑，异常直接抛出去。

        _load 只接了 OSError 和 json.JSONDecodeError；解码是在 json.load 里
        read() 的时候做的，抛的是 UnicodeDecodeError（ValueError 的子类，
        既不是 OSError 也不是 JSONDecodeError），于是漏了出去 ——
        插件在 import 期构造 JsonRedis，这条异常会让整个插件加载不了，
        正好是这段容错想避免的事。
        """
        path = tmp_path / "storage.json"
        path.write_bytes(b"\xff\xfe\x00garbage")

        with pytest.raises(UnicodeDecodeError):
            JsonRedis(path)

        assert not (tmp_path / "storage.json.broken").exists()
        assert recording_logger.records == []

    def test_valid_json_that_is_not_an_object_breaks_later_calls(
        self, tmp_path: Path
    ) -> None:
        """**看着不对**：顶层不是 object 的合法 json（比如一个数组）不会被隔离。

        json.load 成功 -> self.data 变成 list -> 之后任何一次
        _clean_expired 里的 self.data.items() 直接 AttributeError。
        坏 json 有兜底，"合法但类型不对"的 json 没有。
        """
        path = tmp_path / "storage.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")

        redis = JsonRedis(path)
        assert redis.data == [1, 2, 3]

        with pytest.raises(AttributeError):
            redis.get("a")

    def test_save_writes_tmp_then_atomically_replaces(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """崩溃安全的核心：先写 <name>.tmp，内容完整之后再 os.replace 换过去。"""
        path = tmp_path / "storage.json"
        redis = JsonRedis(path)

        seen: list[tuple[Path, Path, str]] = []
        real_replace = os.replace

        def spy_replace(src: Any, dst: Any) -> Any:
            # replace 之前临时文件里必须已经是完整的 json
            seen.append((Path(src), Path(dst), Path(src).read_text(encoding="utf-8")))
            return real_replace(src, dst)

        monkeypatch.setattr(json_storage.os, "replace", spy_replace)
        redis.set("k", "v")

        assert len(seen) == 1
        src, dst, content = seen[0]
        assert src == tmp_path / "storage.json.tmp"
        assert dst == path
        assert json.loads(content) == {"k": "v"}
        # 换完之后临时文件不该留下
        assert not src.exists()
        assert _read_raw(path) == {"k": "v"}

    def test_failed_replace_keeps_old_file_and_cleans_tmp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        recording_logger: _RecordingLogger,
    ) -> None:
        """写盘失败不抛给调用方：旧文件原样保留，临时文件删掉，只记日志。"""
        path = tmp_path / "storage.json"
        redis = JsonRedis(path)
        redis.set("k", "旧值")

        def boom(src: Any, dst: Any) -> Any:
            raise OSError("磁盘满了")

        monkeypatch.setattr(json_storage.os, "replace", boom)

        redis.set("k", "新值")  # 不抛

        assert _read_raw(path) == {"k": "旧值"}
        assert not (tmp_path / "storage.json.tmp").exists()
        assert redis.get("k") == "新值"  # 内存里是改了的
        assert any("写入失败" in m for m in recording_logger.messages)

    def test_saved_file_is_readable_utf8_json(self, tmp_path: Path) -> None:
        """ensure_ascii=False + indent=4：中文直接可读，不是 \\uXXXX。"""
        path = tmp_path / "storage.json"
        redis = JsonRedis(path)

        redis.set("名字", "小组")

        text = path.read_text(encoding="utf-8")
        assert "名字" in text
        assert "小组" in text
        assert "\\u" not in text
        assert "\n    " in text  # indent=4


# ===========================================================================
# write_json_atomic（updater 落盘共用）
# ===========================================================================
class TestWriteJsonAtomic:
    def test_writes_complete_payload_then_replaces(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "snapshot.json"
        seen: list[tuple[Path, Path, str]] = []
        real_replace = os.replace

        def spy_replace(src: Any, dst: Any) -> Any:
            seen.append((Path(src), Path(dst), Path(src).read_text(encoding="utf-8")))
            return real_replace(src, dst)

        monkeypatch.setattr(json_storage.os, "replace", spy_replace)
        write_json_atomic(path, {"levels": [1, 2], "中文": "值"}, indent=2)

        assert len(seen) == 1
        src, dst, content = seen[0]
        assert src.name.startswith(".snapshot.json.") and src.name.endswith(".tmp")
        assert dst == path
        assert json.loads(content) == {"levels": [1, 2], "中文": "值"}
        assert not src.exists()
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "levels": [1, 2],
            "中文": "值",
        }

    def test_failed_replace_preserves_old_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "snapshot.json"
        path.write_text('{"levels": [1]}', encoding="utf-8")

        def boom(src: Any, dst: Any) -> Any:
            raise OSError("磁盘满了")

        monkeypatch.setattr(json_storage.os, "replace", boom)
        with pytest.raises(OSError, match="磁盘满了"):
            write_json_atomic(path, {"levels": [1, 2]})

        assert json.loads(path.read_text(encoding="utf-8")) == {"levels": [1]}
        assert list(tmp_path.glob("*.tmp")) == []

    def test_serialization_failure_keeps_old_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "snapshot.json"
        path.write_text('{"levels": [1]}', encoding="utf-8")
        monkeypatch.setattr(
            json_storage.os, "replace", lambda *a: pytest.fail("不应 replace")
        )

        with pytest.raises(TypeError):
            write_json_atomic(path, {"levels": [object()]})

        assert json.loads(path.read_text(encoding="utf-8")) == {"levels": [1]}
        assert list(tmp_path.glob("*.tmp")) == []

    def test_creates_missing_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "data" / "snapshot.json"
        write_json_atomic(path, {"ok": True})
        assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}


# ===========================================================================
# 线程安全
# ===========================================================================
class TestThreadSafety:
    """类里自带 threading.Lock，这里验证并发下不丢数据、文件不写坏。"""

    @staticmethod
    def _run(workers: list[threading.Thread]) -> None:
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join(timeout=30)
        assert not any(thread.is_alive() for thread in workers), "有线程卡住了"

    def test_concurrent_set_loses_nothing(self, tmp_path: Path) -> None:
        redis = JsonRedis(tmp_path / "storage.json", auto_save=False)
        n_threads, per_thread = 8, 25
        barrier = threading.Barrier(n_threads, timeout=30)
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                barrier.wait()  # 尽量让它们真的撞在一起
                for j in range(per_thread):
                    redis.set(f"k_{index}_{j}", index * 1000 + j)
            except BaseException as exc:
                errors.append(exc)

        self._run([
            threading.Thread(target=worker, args=(i,)) for i in range(n_threads)
        ])

        assert errors == []
        assert len(redis.data) == n_threads * per_thread
        for i in range(n_threads):
            for j in range(per_thread):
                assert redis.get(f"k_{i}_{j}") == i * 1000 + j

    def test_concurrent_set_with_auto_save_keeps_file_valid(
        self, tmp_path: Path
    ) -> None:
        """每次 set 都写盘的情况下，文件不能被写成半截。"""
        path = tmp_path / "storage.json"
        redis = JsonRedis(path)
        n_threads, per_thread = 4, 15
        barrier = threading.Barrier(n_threads, timeout=30)
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                barrier.wait()
                for j in range(per_thread):
                    redis.set(f"k_{index}_{j}", index * 1000 + j)
            except BaseException as exc:
                errors.append(exc)

        self._run([
            threading.Thread(target=worker, args=(i,)) for i in range(n_threads)
        ])

        assert errors == []
        raw = _read_raw(path)  # 读不出来就说明写坏了
        assert len(raw) == n_threads * per_thread
        assert raw == JsonRedis(path).data
        assert not (tmp_path / "storage.json.tmp").exists()

    def test_concurrent_hset_on_one_hash_loses_no_field(self, tmp_path: Path) -> None:
        """hset 是"读-改-写"，没有锁的话并发下会丢字段。"""
        redis = JsonRedis(tmp_path / "storage.json", auto_save=False)
        n_threads, per_thread = 6, 20
        barrier = threading.Barrier(n_threads, timeout=30)
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                barrier.wait()
                for j in range(per_thread):
                    redis.hset("preferences", f"f_{index}_{j}", index)
            except BaseException as exc:
                errors.append(exc)

        self._run([
            threading.Thread(target=worker, args=(i,)) for i in range(n_threads)
        ])

        assert errors == []
        assert len(redis.hkeys("preferences")) == n_threads * per_thread


# ===========================================================================
# scripts/_bootstrap.py
# ===========================================================================
class TestBootstrapScript:
    @pytest.fixture
    def bootstrap(self) -> types.ModuleType:
        return _load_script("_bootstrap")

    def test_constants_point_at_the_real_tree(
        self, bootstrap: types.ModuleType
    ) -> None:
        assert bootstrap.REPO_ROOT == REPO_ROOT
        assert bootstrap.PKG == "xiaozu_bot.plugins.gdlevelsearch"
        assert bootstrap.PLUGIN_DIR == (
            REPO_ROOT / "xiaozu_bot" / "plugins" / "gdlevelsearch"
        )
        assert bootstrap.PLUGIN_DIR.is_dir()

    def test_load_gdlevelsearch_installs_shell_packages(
        self, bootstrap: types.ModuleType
    ) -> None:
        """往 sys.modules 里塞空壳包，__path__ 指对，但不执行真的 __init__.py。

        这个用例会临时动 sys.modules（整个测试进程共用），所以退出时严格还原，
        并且最后断言确实还原干净了 —— 不能影响别的测试文件。
        """
        names = ("xiaozu_bot", "xiaozu_bot.plugins", bootstrap.PKG)
        saved_modules = {name: sys.modules.get(name, _MISSING) for name in names}
        saved_path = list(sys.path)

        try:
            for name in names:
                sys.modules.pop(name, None)

            package = bootstrap.load_gdlevelsearch()

            assert package is sys.modules[bootstrap.PKG]
            assert package.__path__ == [str(bootstrap.PLUGIN_DIR)]
            # 空壳：不是真 import 出来的，没有 spec / 文件
            assert package.__spec__ is None
            assert not hasattr(package, "__file__")
            # 父包也被塞成了空壳
            assert sys.modules["xiaozu_bot"].__path__ == [
                str(REPO_ROOT / "xiaozu_bot")
            ]
            # 幂等：已经在 sys.modules 里就不再替换
            assert bootstrap.load_gdlevelsearch() is package
            assert str(REPO_ROOT) in sys.path
        finally:
            for name, module in saved_modules.items():
                if module is _MISSING:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
            sys.path[:] = saved_path

        for name, module in saved_modules.items():
            assert sys.modules.get(name, _MISSING) is module


# ===========================================================================
# scripts/run_updater.py
# ===========================================================================
class TestRunUpdaterScript:
    def test_constants_point_at_the_real_tree(self) -> None:
        module = _load_script("run_updater")

        assert module.REPO_ROOT == REPO_ROOT
        assert module.PLUGIN_DIR == REPO_ROOT / "xiaozu_bot" / "plugins" / "gdlevelsearch"
        assert (module.PLUGIN_DIR / "updater").is_dir()

    def test_import_puts_plugin_dir_first_on_syspath(self) -> None:
        """import 期就把 gdlevelsearch/ 塞到 sys.path[0]，让 `updater` 成为顶层包。

        （测完立刻还原，别的测试还要用这个进程。）
        """
        saved = list(sys.path)
        try:
            module = _load_script("run_updater", restore_syspath=False)
            assert sys.path[0] == str(module.PLUGIN_DIR)
        finally:
            sys.path[:] = saved

    def test_refuses_to_run_outside_repo_root(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """不在仓库根目录直接返回 2 —— 这一步在 import updater 之前，所以不联网。"""
        module = _load_script("run_updater")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["run_updater.py"])

        assert module.main() == 2

        assert "请在仓库根目录运行" in capsys.readouterr().err


# ===========================================================================
# scripts/migrate_redis_to_json.py
# ===========================================================================
class _FakeRedis:
    """够 migrate 脚本用的假 redis 客户端。

    脚本只用到 ping / keys / type / ttl / get / exists / hgetall 这几个方法。
    keys 用 fnmatchcase 匹配，跟真 redis 一样区分大小写（也就跟平台无关）。
    """

    def __init__(
        self,
        strings: dict[str, str] | None = None,
        hashes: dict[str, dict[str, str]] | None = None,
        ttls: dict[str, int] | None = None,
        odd_types: dict[str, str] | None = None,
        ping_error: BaseException | None = None,
    ) -> None:
        self.strings = dict(strings or {})
        self.hashes = dict(hashes or {})
        self.ttls = dict(ttls or {})
        self.odd_types = dict(odd_types or {})  # 键 -> 假装的类型
        self.ping_error = ping_error

    def ping(self) -> bool:
        if self.ping_error is not None:
            raise self.ping_error
        return True

    def _all_keys(self) -> list[str]:
        return [*self.strings, *self.hashes, *self.odd_types]

    def keys(self, pattern: str = "*") -> list[str]:
        import fnmatch

        return [k for k in self._all_keys() if fnmatch.fnmatchcase(k, pattern)]

    def type(self, key: str) -> str:
        if key in self.odd_types:
            return self.odd_types[key]
        if key in self.hashes:
            return "hash"
        if key in self.strings:
            return "string"
        return "none"

    def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)

    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    def exists(self, name: str) -> int:
        return int(name in self._all_keys())

    def hgetall(self, name: str) -> dict[str, str]:
        return dict(self.hashes.get(name, {}))


def _default_fake_redis() -> _FakeRedis:
    """一份覆盖了 PLANS 里全部插件的假数据，外加两个不在迁移范围内的键。"""
    return _FakeRedis(
        strings={
            "jrrp_12345": "66",
            "jrrp_999": "1",
            "guess_total_tries": "42",
            "guess_total_right": "7",
            "guess_cooldown_group_1": "SomeLevel",
            "zhua_cd_12345": "waiting",
            "blueberry_12345": "999",  # 不在 PLANS 里
            "roulette_status_1": "idle",  # 不在 PLANS 里
        },
        hashes={
            "guess_answer": {"group_1": "SomeLevel"},
            "guess_answer_position": {"group_1": "10 20"},
            "guess_ori": {"group_1": "img.png"},
        },
        ttls={
            "jrrp_12345": 3600,
            "jrrp_999": -1,
            "guess_cooldown_group_1": 30,
            "zhua_cd_12345": 600,
        },
        odd_types={"jrrp_listy": "list"},  # 匹配 jrrp_* 但类型不对，应该被跳过
    )


class _MigrateEnv:
    """把 migrate 脚本跑起来所需要的一整套替身。"""

    def __init__(
        self, module: types.ModuleType, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.module = module
        self.root = root
        self.monkeypatch = monkeypatch
        self.client = _default_fake_redis()
        self.ctor_kwargs: dict[str, Any] = {}

    def target(self, plugin: str) -> Path:
        return self.root / "xiaozu_bot" / "plugins" / plugin / "data" / "storage.json"

    def run(self, *argv: str) -> int:
        self.monkeypatch.setattr(sys, "argv", ["migrate_redis_to_json.py", *argv])
        return self.module.main()


@pytest.fixture
def migrate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _MigrateEnv:
    """加载 migrate 脚本，并把「仓库根目录」挪到 tmp_path，redis 换成假的。

    脚本会往 REPO_ROOT/xiaozu_bot/plugins/<x>/data/ 里写文件，
    所以必须先把 REPO_ROOT 指到临时目录，绝不能碰真仓库。

    redis 是可选依赖（pyproject 里的 [migrate] extra），只有这个搬家脚本要它。
    以前这里是裸 `import redis`，没装的机器上依赖它的用例会 ERROR 而不是 SKIP ——
    照 README 主路径 `pip install -e ".[dev]"` 装的人一上来就是红的一片。
    所以第一件事就是 importorskip。
    """
    # exc_type=ImportError 是有意写的：pytest 9.1 起 importorskip 默认只接
    # ModuleNotFoundError（「压根没装」），装了但 import 到一半炸掉（比如
    # 缺 C 扩展、版本冲突）抛的是别的 ImportError，默认会直接 ERROR。
    # 对测试套件来说这两种都该是 SKIP。
    redis = pytest.importorskip(
        "redis",
        reason='需要 pip install -e ".[migrate]" 才有 redis',
        exc_type=ImportError,
    )

    module = _load_script("migrate_redis_to_json")
    monkeypatch.chdir(tmp_path)
    root = Path.cwd()  # chdir 之后再取，规避 Windows 短路径名不一致
    monkeypatch.setattr(module, "REPO_ROOT", root)

    env = _MigrateEnv(module, root, monkeypatch)

    def _fake_ctor(**kwargs: Any) -> _FakeRedis:
        env.ctor_kwargs = kwargs
        return env.client

    monkeypatch.setattr(redis, "Redis", _fake_ctor)
    return env


class TestMigrateScriptPlans:
    """PLANS 是张数据表，它得和插件里真正用的键名对得上。"""

    @pytest.fixture
    def plans(self) -> dict[str, dict[str, list[str]]]:
        return _load_script("migrate_redis_to_json").PLANS

    def test_plan_shape(self, plans: dict[str, dict[str, list[str]]]) -> None:
        assert set(plans) == {"jrrp", "guess", "zhua"}
        for plan in plans.values():
            assert set(plan) == {"patterns", "hashes"}
            assert isinstance(plan["patterns"], list)
            assert isinstance(plan["hashes"], list)

    # 这里以前还有一条 test_every_plugin_in_plans_exists_and_uses_json_redis，
    # 它 grep 生产源码里有没有 "JsonRedis" 这个字符串。改个 import 别名、换个
    # 封装名就会红，但插件其实一点没坏；反过来真出问题（键名对不上）它又抓不到。
    # 下面那条按键名比对的才是有用的那半，插件文件不存在时它会直接 FileNotFoundError。
    def test_every_key_in_plans_appears_in_plugin_source(
        self, plans: dict[str, dict[str, list[str]]]
    ) -> None:
        """迁移表里的键名/前缀必须在对应插件源码里真出现过，否则就是搬了个没人读的键。"""
        for plugin, plan in plans.items():
            source = (
                REPO_ROOT / "xiaozu_bot" / "plugins" / plugin / "__init__.py"
            ).read_text(encoding="utf-8")
            for pattern in plan["patterns"]:
                assert pattern.rstrip("*") in source, (plugin, pattern)
            for name in plan["hashes"]:
                assert name in source, (plugin, name)


class TestMigrateScriptRun:
    def test_refuses_to_run_outside_repo_root(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cwd 不是仓库根目录就返回 2（而且是在 import redis 之前）。"""
        module = _load_script("migrate_redis_to_json")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(module, "REPO_ROOT", tmp_path / "别的地方")
        monkeypatch.setattr(sys, "argv", ["migrate_redis_to_json.py"])

        assert module.main() == 2
        assert "请在仓库根目录运行" in capsys.readouterr().err

    def test_returns_1_when_redis_is_unreachable(
        self, migrate_env: _MigrateEnv, capsys: pytest.CaptureFixture[str]
    ) -> None:
        migrate_env.client = _FakeRedis(ping_error=OSError("连接被拒绝"))

        assert migrate_env.run("--write") == 1

        err = capsys.readouterr().err
        assert "连不上 Redis" in err

    def test_connection_arguments_are_passed_through(
        self, migrate_env: _MigrateEnv
    ) -> None:
        migrate_env.client = _FakeRedis(ping_error=OSError("x"))

        migrate_env.run("--host", "10.0.0.1", "--port", "6380", "--db", "3")

        assert migrate_env.ctor_kwargs == {
            "host": "10.0.0.1",
            "port": 6380,
            "db": 3,
            "decode_responses": True,
        }

    def test_dry_run_writes_nothing(
        self, migrate_env: _MigrateEnv, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert migrate_env.run() == 0

        out = capsys.readouterr().out
        assert "预演模式" in out
        assert not (migrate_env.root / "xiaozu_bot").exists()

    def test_write_moves_everything_in_the_plan(
        self, migrate_env: _MigrateEnv, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert migrate_env.run("--write") == 0

        jrrp = JsonRedis(migrate_env.target("jrrp"))
        assert jrrp.get("jrrp_12345") == "66"
        assert 3590 <= jrrp.ttl("jrrp_12345") <= 3600  # 带着剩余 ttl 一起搬
        assert jrrp.get("jrrp_999") == "1"
        assert jrrp.ttl("jrrp_999") == -1  # redis 的 -1（永不过期）不写 ex
        # 类型不是 string 的键被跳过
        assert jrrp.exists("jrrp_listy") is False
        assert "不是 string" in capsys.readouterr().out

        guess = JsonRedis(migrate_env.target("guess"))
        assert guess.get("guess_total_tries") == "42"
        assert guess.get("guess_total_right") == "7"
        assert guess.ttl("guess_cooldown_group_1") > 0
        assert guess.hget("guess_answer", "group_1") == "SomeLevel"
        assert guess.hget("guess_answer_position", "group_1") == "10 20"
        assert guess.hget("guess_ori", "group_1") == "img.png"

        zhua = JsonRedis(migrate_env.target("zhua"))
        assert zhua.get("zhua_cd_12345") == "waiting"
        assert zhua.ttl("zhua_cd_12345") > 0

    def test_totals_and_leftover_report(
        self, migrate_env: _MigrateEnv, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """合计项数 = 普通键 + 各哈希表字段数；不在计划里的键要被明确列出来。"""
        migrate_env.run("--write")

        out = capsys.readouterr().out
        # jrrp 2 个键（jrrp_listy 类型不对，没进 moved）
        # + guess 3 个键 3 个字段 + zhua 1 个键 = 9
        assert "合计 9 项" in out
        assert "blueberry_12345" in out
        assert "roulette_status_1" in out
        assert "2 个键不在迁移范围内" in out

    def test_existing_target_is_skipped_without_force(
        self, migrate_env: _MigrateEnv, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = migrate_env.target("jrrp")
        target.parent.mkdir(parents=True)
        target.write_text('{"哨兵": 1}', encoding="utf-8")

        migrate_env.run("--write")

        assert _read_raw(target) == {"哨兵": 1}
        out = capsys.readouterr().out
        assert "已存在" in out
        # 跳过的插件的键没被 claim，于是出现在"不在迁移范围内"里 —— 措辞有点误导
        assert "jrrp_12345" in out.split("不在迁移范围内")[-1]

    def test_force_merges_into_the_existing_file(
        self, migrate_env: _MigrateEnv
    ) -> None:
        """--force 实际是"合并"而不是"覆盖"：JsonRedis 会先把老文件读进来。

        脚本 docstring 写的是"覆盖"，措辞和行为对不上（数据不会丢，只是没清空）。
        """
        target = migrate_env.target("jrrp")
        target.parent.mkdir(parents=True)
        target.write_text('{"哨兵": 1}', encoding="utf-8")

        migrate_env.run("--write", "--force")

        after = JsonRedis(target)
        assert after.get("jrrp_12345") == "66"
        assert after.get("哨兵") == 1  # 老数据还在

    def test_hash_with_wrong_type_is_skipped(
        self, migrate_env: _MigrateEnv, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """计划里当哈希表搬的键，在 redis 里类型不对就跳过，而且不建文件。"""
        migrate_env.client = _FakeRedis(
            strings={}, hashes={}, odd_types={"guess_answer": "string"}
        )

        migrate_env.run("--write")

        assert "不是 hash" in capsys.readouterr().out
        assert not migrate_env.target("guess").exists()
