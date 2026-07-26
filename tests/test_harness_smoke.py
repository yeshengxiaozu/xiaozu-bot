"""验证测试脚手架本身是好的。

这个文件不测业务逻辑，只测「conftest 把环境铺好了没」：
能不能 import 到仓库代码、fixture 能不能用、断网守卫真的会拦。
其他 7 个测试文件都建立在这些前提上，所以这里挂了别的都别看了。
"""

from __future__ import annotations

import http.client
import importlib
import os
import socket
import urllib.request

import httpx
import pytest
import requests

from tests.conftest import (
    COMMAND_START,
    NetworkBlocked,
    SUPERUSER_ID,
)

# ---------------------------------------------------------------------------
# 1. nonebot 初始化 & import
# ---------------------------------------------------------------------------


def test_nonebot_is_initialized_with_none_driver() -> None:
    """conftest 应该已经用 ~none driver init 过了，而且配置是写死的那份"""
    import nonebot

    driver = nonebot.get_driver()
    assert driver.config.command_start == COMMAND_START
    assert SUPERUSER_ID in driver.config.superusers
    # 默认的 fastapi driver 根本没装，能拿到 driver 就说明用的是 ~none
    assert type(driver).__name__ == "Driver"


def test_json_storage_imports_and_exports() -> None:
    """xiaozu_bot.utils.json_storage 能 import，JsonRedis / plugin_storage 都在"""
    mod = importlib.import_module("xiaozu_bot.utils.json_storage")

    assert hasattr(mod, "JsonRedis")
    assert hasattr(mod, "plugin_storage")


@pytest.mark.parametrize(
    "module_name",
    [
        "xiaozu_bot.plugins.game",
        "xiaozu_bot.plugins.jrrp",
        "xiaozu_bot.plugins.roulette",
        "xiaozu_bot.plugins.gdlevelsearch.gdapi",
        "xiaozu_bot.plugins.gdlevelsearch.aredlapi",
    ],
)
def test_plugin_modules_import(module_name: str) -> None:
    """挑几个不同插件的模块，确认 import 期不炸也不联网"""
    module = importlib.import_module(module_name)

    assert module.__name__ == module_name


def test_game_plugin_registers_matchers() -> None:
    """game 插件 import 之后应该真的注册了 matcher（不是空壳）"""
    from nonebot.matcher import Matcher

    game = importlib.import_module("xiaozu_bot.plugins.game")

    assert issubclass(game.setmode, Matcher)
    # 白名单群号是硬编码在插件里的，顺手确认没漂
    assert game.whitelist_rule is not None


def test_gdapi_exposes_official_song_map() -> None:
    """gdlevelsearch.gdapi 的官方曲目表在 import 期就该建好"""
    gdapi = importlib.import_module("xiaozu_bot.plugins.gdlevelsearch.gdapi")

    assert gdapi.OFFICIAL_SONG_MAP[0] == ("Stereo Madness", "Foreverbound")
    assert gdapi.GD_PAGE_SIZE == 10


# ---------------------------------------------------------------------------
# 2. JsonRedis fixture
# ---------------------------------------------------------------------------


def test_json_redis_roundtrip(json_redis, tmp_path) -> None:
    """json_redis fixture 存得进读得出，而且文件落在 tmp_path 里"""
    json_redis.set("hello", "world")

    assert json_redis.get("hello") == "world"
    assert json_redis.exists("hello") is True
    # 没设过期时间的键，ttl 按 redis 语义返回 -1
    assert json_redis.ttl("hello") == -1
    assert json_redis.get("missing") is None
    assert json_redis.ttl("missing") == -2

    # 落盘路径必须在 tmp_path 下，绝不能写进仓库工作区
    assert json_redis.file_path == tmp_path / "storage.json"
    assert json_redis.file_path.exists()

    # 换一个实例重新读同一个文件，数据还在 —— 证明真的写盘了
    reopened = type(json_redis)(json_redis.file_path)
    assert reopened.get("hello") == "world"

    json_redis.delete("hello")
    assert json_redis.exists("hello") is False


def test_json_redis_hash_and_keys(json_redis) -> None:
    """哈希操作和 keys 的 glob 匹配都对得上 JsonRedis 的实现"""
    json_redis.hset("bag", "a", 1)
    json_redis.hset("bag", "b", 2)

    assert json_redis.hget("bag", "a") == 1
    assert json_redis.hexists("bag", "b") is True
    assert json_redis.hexists("bag", "zzz") is False
    assert sorted(json_redis.hkeys("bag")) == ["a", "b"]

    json_redis.set("roulette_status_1", "x")
    json_redis.set("roulette_status_2", "y")
    # 调用方是用关键字传的，参数名必须是 pattern
    assert sorted(json_redis.keys(pattern="roulette_status*")) == [
        "roulette_status_1",
        "roulette_status_2",
    ]


def test_make_json_redis_gives_independent_stores(make_json_redis) -> None:
    """make_json_redis 每次给的是互不干扰的实例，initial 也能预填"""
    a = make_json_redis("a.json", initial={"k": "va"})
    b = make_json_redis("b.json")

    assert a.get("k") == "va"
    assert b.get("k") is None
    assert a.file_path != b.file_path


def test_patch_storage_swaps_plugin_module_storage(patch_storage, tmp_path) -> None:
    """patch_storage 能把插件模块级的 r 换掉，测完自动还原"""
    jrrp = importlib.import_module("xiaozu_bot.plugins.jrrp")
    original = jrrp.r

    patched = patch_storage(jrrp, initial={"jrrp_12345": "66"})

    assert jrrp.r is patched
    assert jrrp.r is not original
    assert jrrp.r.get("jrrp_12345") == "66"
    assert tmp_path in jrrp.r.file_path.parents


# ---------------------------------------------------------------------------
# 3. 事件 / Bot 工厂
# ---------------------------------------------------------------------------


def test_make_group_event(make_group_event) -> None:
    """群消息事件工厂造出来的是真 GroupMessageEvent，字段都对"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent

    event = make_group_event("*jrrp", user_id=111, group_id=222)

    assert isinstance(event, GroupMessageEvent)
    assert event.get_plaintext() == "*jrrp"
    assert event.user_id == 111
    assert event.group_id == 222
    assert event.get_session_id() == "group_222_111"
    assert event.get_type() == "message"
    # original_message 是拷贝，改 message 不该串到它上面
    assert str(event.original_message) == "*jrrp"


def test_make_private_event(make_private_event) -> None:
    """私聊事件工厂：默认 to_me 为 True"""
    from nonebot.adapters.onebot.v11 import PrivateMessageEvent

    event = make_private_event("*help")

    assert isinstance(event, PrivateMessageEvent)
    assert event.to_me is True
    assert event.get_session_id() == "12345"
    assert event.get_plaintext() == "*help"


async def test_fake_bot_records_api_calls(fake_bot) -> None:
    """FakeBot 把 call_api 全记下来，返回值可以预设，一个真请求都不发"""
    fake_bot.api_results["get_group_member_info"] = {"role": "admin"}

    sent = await fake_bot.send_group_msg(group_id=1, message="hi")
    info = await fake_bot.call_api("get_group_member_info", group_id=1, user_id=2)

    assert sent is None
    assert info == {"role": "admin"}
    assert fake_bot.called_apis == ["send_group_msg", "get_group_member_info"]
    assert fake_bot.calls[0][1] == {"group_id": 1, "message": "hi"}


# ---------------------------------------------------------------------------
# 4. 断网守卫
# ---------------------------------------------------------------------------


def test_no_network_blocks_requests() -> None:
    """真 requests 出网会抛 NetworkBlocked，不是超时也不是连接错误"""
    with pytest.raises(NetworkBlocked):
        requests.get("https://api.aredl.net/v2/api/aredl/levels", timeout=1)

    with pytest.raises(NetworkBlocked):
        requests.post("http://www.boomlings.com/database/getGJLevels21.php", timeout=1)


def test_no_network_blocks_module_level_session() -> None:
    """updater/jobs/metadata.py 里那个 import 期就建好的 Session 也拦得住"""
    metadata = importlib.import_module(
        "xiaozu_bot.plugins.gdlevelsearch.updater.jobs.metadata"
    )

    with pytest.raises(NetworkBlocked):
        metadata.http.get(metadata.GD_HISTORY_API, timeout=1)


async def test_no_network_blocks_httpx_async() -> None:
    """httpx 的异步 client 也拦得住（ai 插件和 draw.py 用的就是它）"""
    with pytest.raises(NetworkBlocked):
        async with httpx.AsyncClient() as client:
            await client.post("http://127.0.0.1:1234/v1/chat/completions", json={})


def test_no_network_blocks_httpx_sync() -> None:
    """httpx 的同步 client 同样拦住"""
    with pytest.raises(NetworkBlocked):
        httpx.get("https://gdbrowser.com/api/level/128")


def test_no_network_blocks_raw_socket() -> None:
    """裸 socket / DNS 也拦住，防止有人绕过 requests 直接连"""
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("example.com", 443)

    with pytest.raises(NetworkBlocked):
        socket.create_connection(("93.184.216.34", 80), timeout=1)


def test_no_network_blocks_urllib() -> None:
    """urllib.request 也拦得住

    这条是补一个真实存在过的洞：socket 层的环回豁免（asyncio self-pipe 要用）
    曾经能被本机 HTTP 代理当后门 —— 开发机上 HTTP_PROXY=http://127.0.0.1:7897
    的时候，urlopen 的 socket 目标就是环回，守卫直接放行，请求真的打到了
    example.com 并拿回 200。现在守卫会清代理设置并封死 http.client.connect。

    只用 http:// 试：https 那条路会先撞上 updater 改坏的
    `ssl._create_default_https_context`（见 test_updater.py 里的说明），
    抛的是 TypeError 而不是 NetworkBlocked，而且它到底改没改过取决于
    这个进程里有没有 import 过 updater —— 拿它做断言就成了顺序依赖。
    """
    with pytest.raises(NetworkBlocked):
        urllib.request.urlopen("http://example.com/", timeout=1)  # noqa: S310

    with pytest.raises(NetworkBlocked):
        urllib.request.urlopen("http://api.aredl.net/v2/api/aredl/levels", timeout=1)  # noqa: S310


def test_no_network_blocks_raw_http_client() -> None:
    """http.client 直连拦得住，环回也不放行 —— 环回正是代理监听的地方"""
    with pytest.raises(NetworkBlocked):
        http.client.HTTPConnection("example.com", 80, timeout=1).request("GET", "/")

    with pytest.raises(NetworkBlocked):
        http.client.HTTPConnection("127.0.0.1", 7897, timeout=1).request("GET", "/")


def test_proxy_env_is_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    """守卫跑完之后进程里不该还留着代理环境变量，getproxies 也得是空的"""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        assert os.environ.get(var) is None, f"{var} 没被清掉"
    assert urllib.request.getproxies() == {}


def test_loopback_socket_still_allowed() -> None:
    """环回不能一刀切封死 —— Windows 上 asyncio 的 self-pipe 要用它"""
    server = socket.socket()
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client = socket.socket()
        try:
            client.connect(server.getsockname())
        finally:
            client.close()
    finally:
        server.close()


async def test_asyncio_still_works_under_guard() -> None:
    """断网守卫开着的时候 async 用例照样能跑（这条本身就是证据）"""
    import asyncio

    await asyncio.sleep(0)
    assert asyncio.get_running_loop() is not None


# ---------------------------------------------------------------------------
# 5. HTTP 桩
# ---------------------------------------------------------------------------


def test_make_response_shapes(make_response) -> None:
    """假响应把 status_code / text / content / json() 都补齐了"""
    resp = make_response(json_data={"a": 1})

    assert resp.status_code == 200
    assert resp.json() == {"a": 1}
    assert resp.text == '{"a": 1}'
    assert resp.content == b'{"a": 1}'
    assert resp.ok is True
    resp.raise_for_status()

    bad = make_response(404, text="nope")
    assert bad.ok is False
    with pytest.raises(requests.HTTPError):
        bad.raise_for_status()


def test_stub_requests_routes_and_records(stub_requests, make_response) -> None:
    """stub_requests 按 URL 分发，调用被记下来，没登记的 URL 直接抛"""
    stub_requests.get(
        "https://api.aredl.net/v2/api/aredl/levels",
        make_response(json_data=[{"name": "Bloodbath"}]),
    )

    resp = requests.get("https://api.aredl.net/v2/api/aredl/levels", timeout=1)

    assert resp.json() == [{"name": "Bloodbath"}]
    assert stub_requests.urls == ["https://api.aredl.net/v2/api/aredl/levels"]

    with pytest.raises(NetworkBlocked):
        requests.get("https://gdladder.com/api/level/128", timeout=1)


def test_stub_requests_can_raise_to_exercise_error_paths(stub_requests) -> None:
    """登记异常实例可以测调用方的 RequestException 分支"""
    stub_requests.get("https://api.aredl.net/", requests.Timeout("boom"))

    with pytest.raises(requests.Timeout):
        requests.get("https://api.aredl.net/", timeout=1)


def test_stub_requests_drives_real_plugin_code(stub_requests) -> None:
    """真拿它去驱动 aredlapi.fetch_aredl_levels，证明桩接在了正确的位置"""
    aredlapi = importlib.import_module("xiaozu_bot.plugins.gdlevelsearch.aredlapi")
    stub_requests.get(
        "https://api.aredl.net/v2/api/aredl/levels",
        FakeLevelsResponse(),
    )

    levels = aredlapi.fetch_aredl_levels()

    assert len(levels) == 1
    assert levels[0].name == "Bloodbath"
    assert stub_requests.urls == ["https://api.aredl.net/v2/api/aredl/levels"]


class FakeLevelsResponse:
    """AREDL levels 接口的响应。

    14 个字段一个都不能少 —— AREDLLevel.__init__ 是 jsondict["xxx"] 硬取的，
    缺一个就 KeyError（和线上缓存 aredl_levels.json 里的字段一致）。
    """

    status_code = 200

    def json(self) -> list[dict[str, object]]:
        return [
            {
                "id": "uuid-1",
                "name": "Bloodbath",
                "position": 1,
                "points": 100,
                "legacy": False,
                "level_id": 10565740,
                "two_player": False,
                "tags": [],
                "description": None,
                "song": None,
                "edel_enjoyment": None,
                "is_edel_pending": False,
                "gddl_tier": None,
                "nlw_tier": None,
            }
        ]


async def test_stub_httpx_routes_async(stub_httpx) -> None:
    """stub_httpx 接管异步 transport，没登记的 URL 一样抛"""
    stub_httpx.post(
        "/v1/chat/completions",
        httpx.Response(200, json={"choices": [{"message": {"content": "喵"}}]}),
    )

    async with httpx.AsyncClient() as client:
        resp = await client.post("http://127.0.0.1:1234/v1/chat/completions", json={})

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "喵"
    assert stub_httpx.urls == ["http://127.0.0.1:1234/v1/chat/completions"]

    with pytest.raises(NetworkBlocked):
        async with httpx.AsyncClient() as client:
            await client.get("https://gdbrowser.com/api/level/128")


def test_make_httpx_response_has_request_attached(make_httpx_response) -> None:
    """make_httpx_response 造出来的响应能直接 raise_for_status（.request 挂好了）"""
    resp = make_httpx_response(500, url="https://example.invalid/x")

    with pytest.raises(httpx.HTTPStatusError):
        resp.raise_for_status()


# ---------------------------------------------------------------------------
# 6. 其他
# ---------------------------------------------------------------------------


def test_seeded_random_is_deterministic(seeded_random) -> None:
    """播了固定种子之后结果可复现，测完全局 random 状态会被还原"""
    import random

    seeded_random(0)
    first = [random.randint(1, 100) for _ in range(5)]
    seeded_random(0)
    second = [random.randint(1, 100) for _ in range(5)]

    assert first == second


def test_repo_root_points_at_the_repo(repo_root) -> None:
    """repo_root 指向仓库根，pyproject.toml 和 xiaozu_bot/ 都在下面"""
    assert (repo_root / "pyproject.toml").is_file()
    assert (repo_root / "xiaozu_bot").is_dir()
    assert (repo_root / "bot.py").is_file()
