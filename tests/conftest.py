"""pytest 全局配置与公共 fixture。

这个文件里有三件事必须在任何 test 模块 import `xiaozu_bot` 之前做完：

1. 把仓库根目录塞进 sys.path（否则 `import xiaozu_bot` 找不到）；
2. 调 `nonebot.init(driver="~none")`（插件在 import 期就会调
   `get_plugin_config()` / `on_command()` / `require()`，没 init 过直接炸；
   默认的 fastapi driver 也没装，必须显式用 `~none`）；
3. 把 nonebot 的 loguru 输出压下去，免得 pytest 的输出被日志淹没。

conftest.py 是在收集 test 模块之前 import 的，所以上面这些写在模块级
（而不是 fixture 里）才是可靠的时机。
"""

from __future__ import annotations

import contextlib
import http.client as _http_client
import ipaddress
import json as _json
import random as _random
import socket as _socket
import sys
import urllib.request as _urllib_request
from pathlib import Path
from typing import Any, Callable, Optional, Union

import pytest

# --------------------------------------------------------------------------
# 1. sys.path：从 __file__ 推仓库根目录，不要写死绝对路径
# --------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# --------------------------------------------------------------------------
# 2. nonebot 初始化（全进程只做一次）+ 日志静音
# --------------------------------------------------------------------------
import nonebot  # noqa: E402
from nonebot.log import logger as _nb_logger  # noqa: E402
from nonebot.log import logger_id as _nb_logger_id  # noqa: E402

# 测试里用到的固定配置。**注意：这些是测试写死的，不读仓库里的 .env**，
# 这样换台机器、换个 .env 结果也一样。
# command_start 跟生产约定一致（xiaozubot_help/commands.py 里 Cmd.prefix 默认 "*"）。
COMMAND_START: set[str] = {"*"}
COMMAND_SEP: set[str] = {"."}
SUPERUSER_ID = "10000"
BOT_SELF_ID = "10001"

# 事件工厂的默认值，写死是为了不依赖真实时钟 / 随机数
DEFAULT_USER_ID = 12345
DEFAULT_GROUP_ID = 67890
DEFAULT_TIME = 1700000000

# game 插件里硬编码的白名单群，测 game 的人直接用这个
GAME_WHITELIST_GROUP_IDS = (1035708051, 870217476)


def _quiet_nonebot_logging() -> None:
    """摘掉 nonebot 默认那个 INFO 级 loguru handler，只留 WARNING 以上。

    nonebot 一启动就往 stderr 刷一堆 SUCCESS/INFO，pytest 的输出会很难看。
    保留 WARNING 是为了插件真出问题的时候还能看见。
    """
    try:
        _nb_logger.remove(_nb_logger_id)
    except ValueError:
        # 已经被摘过了（比如 conftest 被重复 import），忽略
        pass
    _nb_logger.add(sys.stderr, level="WARNING", format="{level} | {name} | {message}")


def _init_nonebot_once() -> None:
    """幂等地初始化 nonebot。

    `get_driver()` 在没 init 过的时候抛 ValueError，拿它当"初始化了没"的探针。
    """
    try:
        nonebot.get_driver()
    except ValueError:
        _quiet_nonebot_logging()
        nonebot.init(
            driver="~none",
            command_start=COMMAND_START,
            command_sep=COMMAND_SEP,
            superusers={SUPERUSER_ID},
            log_level="WARNING",
        )


_init_nonebot_once()


# --------------------------------------------------------------------------
# 3. 到这里才能安全地 import 适配器和仓库自己的代码
# --------------------------------------------------------------------------
import httpx  # noqa: E402
import requests  # noqa: E402
from nonebot.adapters.onebot.v11 import Adapter, Bot, Message  # noqa: E402
from nonebot.adapters.onebot.v11 import (  # noqa: E402
    GroupMessageEvent,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Sender  # noqa: E402

from xiaozu_bot.utils.json_storage import JsonRedis  # noqa: E402


# --------------------------------------------------------------------------
# 断网守卫
# --------------------------------------------------------------------------
class NetworkBlocked(RuntimeError):
    """测试里发起了真实网络请求时抛的异常。

    看到它说明某个测试没把 requests / httpx 桩掉。测试必须在飞机上也能跑过。
    """


_LOOPBACK_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "", "::1"})


#: 各种库认的代理环境变量。测试进程里必须一个都不剩，
#: 否则「连到 127.0.0.1 的代理端口」会被下面的环回豁免放行，等于开了个出网后门。
_PROXY_ENV_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "no_proxy",
)


def _is_loopback(host: Optional[str]) -> bool:
    """判断一个主机名/地址是不是环回地址。

    Windows 上 asyncio 的 ProactorEventLoop 自建 self-pipe 时会往
    127.0.0.1 connect 一次，一刀切封 socket 会让所有 async 测试直接崩，
    所以环回必须放行。真正拦外网 HTTP 的是下面那几个库级别的补丁。

    注意这个豁免本身是有洞的：本机开着 HTTP 代理的时候（开发机上很常见），
    任何认代理设置的库都会把 socket 目标变成 127.0.0.1:<代理端口>，
    于是照样能出网。所以 no_network 里除了这个豁免，还额外做了两件事：
    清掉代理环境变量 + 把 http.client 的 connect 整个封死。
    """
    if host is None:
        return True
    if host in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """autouse：任何真实的 requests / httpx / 裸 socket 出网都抛 NetworkBlocked。

    分四层拦：

    - 库级别：`requests.adapters.HTTPAdapter.send`（所有 requests.get/post 和
      模块级复用的 Session 最终都走这里）、`httpx.HTTPTransport.handle_request`、
      `httpx.AsyncHTTPTransport.handle_async_request`。连 127.0.0.1 的本地
      大模型也一起拦掉。
    - `http.client.HTTPConnection.connect` / `HTTPSConnection.connect`：
      urllib.request、urllib3 直连、httplib2（google-api-python-client 用的就是它）
      全部走这两个方法，一刀切封死。**这一层不放行环回**，因为环回正是代理的地址。
    - 代理设置：清掉 HTTP_PROXY/HTTPS_PROXY/… 环境变量，并把
      `urllib.request.getproxies` 换成返回空 dict（Windows 上它读的是注册表，
      光清环境变量不够）。不然「出网请求 -> 本机代理 -> 外网」这条路
      在 socket 层看起来就是一次合法的环回连接。
    - socket 级别：`getaddrinfo` / `create_connection` / `socket.connect`，
      兜住上面三层都没盖到的裸 socket，环回地址放行（见 _is_loopback，
      Windows 的 ProactorEventLoop self-pipe 必须放行，否则 async 用例全崩）。

    想放行某次调用，就 monkeypatch 掉更上层的入口（`requests.get`、
    `httpx.MockTransport`，或者直接用下面的 stub_requests / stub_httpx），
    不要去动这个 fixture。
    """

    def _blocked_requests(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        raise NetworkBlocked(
            f"测试里发起了真实 HTTP 请求：{request.method} {request.url}。"
            "请用 stub_requests fixture 或 monkeypatch 把它桩掉。"
        )

    def _blocked_httpx(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        raise NetworkBlocked(
            f"测试里发起了真实 httpx 请求：{request.method} {request.url}。"
            "请用 stub_httpx fixture 或 httpx.MockTransport 把它桩掉。"
        )

    def _blocked_http_client(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise NetworkBlocked(
            f"测试里用 http.client 建了真实连接：{self.host}:{self.port}。"
            "urllib / urllib3 / httplib2 都走这里，请把调用方桩掉。"
        )

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _blocked_requests)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked_httpx)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _blocked_httpx
    )

    # http.client 这层**不看环回**：本机代理就在环回上，放行等于没拦。
    # （实测过：开发机上 HTTP_PROXY=http://127.0.0.1:7897 时，只在 socket 层拦、
    #  放行环回的话，urlopen("http://example.com/") 会经代理真的出网拿回 200。）
    # HTTPSConnection.connect 是自己覆写过的，必须单独打一遍。
    #
    # 副作用，将来踩到的话看这里：**真的想连本机 HTTP 服务的用例也会被这一层拦掉** ——
    # 比如起个 pytest-httpserver、或者拿 uvicorn/ASGI 真跑一次 nonebot.get_asgi()。
    # 那种情况下报错里那句「请把调用方桩掉」是不对的建议。
    # 要放行的话在那个用例里单独把这两行 setattr 撤掉（monkeypatch 的 undo 是按
    # 用例回滚的），别去改这个 fixture —— 它现在的严格程度是有原因的。
    monkeypatch.setattr(_http_client.HTTPConnection, "connect", _blocked_http_client)
    monkeypatch.setattr(_http_client.HTTPSConnection, "connect", _blocked_http_client)

    # 代理配置清干净，免得出网请求伪装成环回连接溜过 _is_loopback。
    for var in _PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_urllib_request, "getproxies", dict)

    real_getaddrinfo = _socket.getaddrinfo
    real_create_connection = _socket.create_connection
    real_connect = _socket.socket.connect
    real_connect_ex = _socket.socket.connect_ex

    def _guard_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_loopback(host):
            return real_getaddrinfo(host, *args, **kwargs)
        raise NetworkBlocked(f"测试里做了真实 DNS 解析：{host!r}")

    def _guard_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else None
        if _is_loopback(host):
            return real_create_connection(address, *args, **kwargs)
        raise NetworkBlocked(f"测试里建了真实 TCP 连接：{address!r}")

    def _guard_connect(self: Any, address: Any, *args: Any, **kwargs: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else None
        if _is_loopback(host):
            return real_connect(self, address, *args, **kwargs)
        raise NetworkBlocked(f"测试里建了真实 TCP 连接：{address!r}")

    def _guard_connect_ex(self: Any, address: Any, *args: Any, **kwargs: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else None
        if _is_loopback(host):
            return real_connect_ex(self, address, *args, **kwargs)
        raise NetworkBlocked(f"测试里建了真实 TCP 连接：{address!r}")

    monkeypatch.setattr(_socket, "getaddrinfo", _guard_getaddrinfo)
    monkeypatch.setattr(_socket, "create_connection", _guard_create_connection)
    monkeypatch.setattr(_socket.socket, "connect", _guard_connect)
    monkeypatch.setattr(_socket.socket, "connect_ex", _guard_connect_ex)


# --------------------------------------------------------------------------
# 假 HTTP 响应
# --------------------------------------------------------------------------
_UNSET = object()


class FakeResponse:
    """伪造的 requests.Response，够 gdapi / gddlapi / aredlapi 那几处调用用。

    调用方实际会摸到的属性只有 status_code / text / content / json() /
    raise_for_status()，这里就实现这些。text 没给的话从 json 推，
    content 没给的话从 text 推。
    """

    def __init__(
        self,
        status_code: int = 200,
        json_data: Any = _UNSET,
        text: Optional[str] = None,
        content: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        url: str = "https://example.invalid/",
        encoding: str = "utf-8",
    ) -> None:
        self.status_code = status_code
        self._json = json_data
        self.url = url
        self.encoding = encoding
        self.headers = dict(headers or {})
        if text is None:
            if json_data is not _UNSET:
                text = _json.dumps(json_data, ensure_ascii=False)
            elif content is not None:
                text = content.decode(encoding, errors="replace")
            else:
                text = ""
        self.text = text
        self.content = content if content is not None else text.encode(encoding)

    def json(self, **_: Any) -> Any:
        """跟真 Response 一样：不是合法 json 就抛。"""
        if self._json is not _UNSET:
            return self._json
        return _json.loads(self.text)

    @property
    def ok(self) -> bool:
        return self.status_code < 400  # noqa: PLR2004

    def raise_for_status(self) -> None:
        if self.status_code >= 400:  # noqa: PLR2004
            raise requests.HTTPError(f"{self.status_code} for {self.url}", response=self)

    def __repr__(self) -> str:
        return f"<FakeResponse [{self.status_code}] {self.url}>"


@pytest.fixture
def make_response() -> Callable[..., FakeResponse]:
    """工厂：造一个假的 requests 响应。

        resp = make_response(json_data={"a": 1})
        resp = make_response(200, text="1:123:2:name#...")
        resp = make_response(404)
    """
    return FakeResponse


@pytest.fixture
def make_httpx_response() -> Callable[..., httpx.Response]:
    """工厂：造一个真的 httpx.Response（已经把 .request 挂好，能 raise_for_status）。

        resp = make_httpx_response(200, json={"choices": [...]})
    """

    def _make(
        status_code: int = 200,
        *,
        url: str = "https://example.invalid/",
        method: str = "GET",
        **kwargs: Any,
    ) -> httpx.Response:
        request = httpx.Request(method, url)
        response = httpx.Response(status_code, request=request, **kwargs)
        return response

    return _make


# --------------------------------------------------------------------------
# 可编程的 HTTP 桩
# --------------------------------------------------------------------------
class RequestsRouter:
    """按 URL 分发的 requests 桩。没登记过的 URL 直接抛 NetworkBlocked。

    URL 匹配规则：先全等，再看登记的串是不是请求 URL 的子串（方便忽略 query）。
    """

    def __init__(self) -> None:
        self.routes: list[tuple[Optional[str], str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def add(
        self,
        url: str,
        response: Any = None,
        *,
        method: Optional[str] = None,
        **response_kwargs: Any,
    ) -> "RequestsRouter":
        """登记一条路由。

        response 可以是 FakeResponse、可调用对象（收 **call 信息，返回响应），
        或者异常实例（会被 raise，用来测超时分支）。都不给就用
        response_kwargs 现造一个 FakeResponse。
        """
        if response is None:
            response = FakeResponse(**response_kwargs)
        self.routes.append((method.upper() if method else None, url, response))
        return self

    def get(self, url: str, response: Any = None, **kw: Any) -> "RequestsRouter":
        """登记一条 GET 路由"""
        return self.add(url, response, method="GET", **kw)

    def post(self, url: str, response: Any = None, **kw: Any) -> "RequestsRouter":
        """登记一条 POST 路由"""
        return self.add(url, response, method="POST", **kw)

    def _dispatch(self, method: str, url: str, **kwargs: Any) -> Any:
        """挑一条登记过的路由。

        匹配顺序：先精确相等，再按**登记串从长到短**做子串匹配。
        顺序很要紧，别改回「按登记顺序扫一遍」：那样先登记的短串会把后登记的
        长串吃掉 —— 比如先登记了 ".../aredl/levels"、后登记 ".../aredl/levels/123"，
        请求 .../levels/123 会命中第一条，静默返回错的那份 payload，
        既不报错也不警告，只有断言莫名其妙对不上。
        """
        call = {"method": method.upper(), "url": url, **kwargs}
        self.calls.append(call)

        def _usable(route_method: Optional[str]) -> bool:
            return route_method is None or route_method == method.upper()

        exact = [r for r in self.routes if _usable(r[0]) and r[1] == url]
        partial = sorted(
            (r for r in self.routes if _usable(r[0]) and r[1] != url and r[1] in url),
            key=lambda r: len(r[1]),
            reverse=True,
        )
        for _, _, response in [*exact, *partial]:
            if isinstance(response, BaseException):
                raise response
            if callable(response) and not isinstance(response, FakeResponse):
                return response(**call)
            return response
        raise NetworkBlocked(f"没登记过的请求：{method.upper()} {url}")

    @property
    def urls(self) -> list[str]:
        """按顺序列出被请求过的 URL，方便断言调用了什么"""
        return [c["url"] for c in self.calls]


@pytest.fixture
def stub_requests(monkeypatch: pytest.MonkeyPatch, no_network: None) -> RequestsRouter:
    """把 requests 的 get/post/request 和 Session 上的同名方法换成可编程的桩。

    显式依赖 no_network 是**必须的**，不是装饰：这个 fixture 会覆盖掉
    no_network 打在同一批属性上的补丁，所以必须保证 no_network 先跑完。
    以前不写也能work，靠的是「autouse fixture 恰好排在同作用域的非 autouse 前面」
    这个实现细节 —— 那不是契约，写出来才是。

    仓库里各处都是 `import requests` 再 `requests.get(...)`，共用同一个模块对象，
    所以在这里补一次就等于全仓库都补上了。

        def test_x(stub_requests):
            stub_requests.get("api.aredl.net/v2/api/aredl/levels", json_data=[])
            ...
            assert stub_requests.urls == [...]
    """
    router = RequestsRouter()

    def _make(method: str) -> Callable[..., Any]:
        def _call(url: str, **kwargs: Any) -> Any:
            return router._dispatch(method, url, **kwargs)

        return _call

    def _request(method: str, url: str, **kwargs: Any) -> Any:
        return router._dispatch(method, url, **kwargs)

    def _session_request(self: Any, method: str, url: str, **kwargs: Any) -> Any:
        return router._dispatch(method, url, **kwargs)

    def _session_method(method: str) -> Callable[..., Any]:
        def _call(self: Any, url: str, **kwargs: Any) -> Any:
            return router._dispatch(method, url, **kwargs)

        return _call

    for name in ("get", "post", "put", "delete", "head", "patch"):
        monkeypatch.setattr(requests, name, _make(name))
        monkeypatch.setattr(requests.Session, name, _session_method(name))
    monkeypatch.setattr(requests, "request", _request)
    monkeypatch.setattr(requests.Session, "request", _session_request)
    return router


class HttpxRouter:
    """按 URL 分发的 httpx 桩，同步/异步都走它。没登记过的 URL 抛 NetworkBlocked。"""

    def __init__(self) -> None:
        self.routes: list[tuple[Optional[str], str, Any]] = []
        self.requests: list[httpx.Request] = []

    def add(
        self,
        url: str,
        response: Any = None,
        *,
        method: Optional[str] = None,
        status_code: int = 200,
        **response_kwargs: Any,
    ) -> "HttpxRouter":
        """登记一条路由。response 可以是 httpx.Response、可调用对象或异常实例。"""
        if response is None:
            response = httpx.Response(status_code, **response_kwargs)
        self.routes.append((method.upper() if method else None, url, response))
        return self

    def get(self, url: str, response: Any = None, **kw: Any) -> "HttpxRouter":
        """登记一条 GET 路由"""
        return self.add(url, response, method="GET", **kw)

    def post(self, url: str, response: Any = None, **kw: Any) -> "HttpxRouter":
        """登记一条 POST 路由"""
        return self.add(url, response, method="POST", **kw)

    def handle(self, request: httpx.Request) -> httpx.Response:
        """匹配规则和 RequestsRouter._dispatch 一样：先精确，再长串优先。

        理由见那边的注释 —— 按登记顺序扫的话，先登记的短 URL 会静默吃掉
        后登记的长 URL 的请求。
        """
        self.requests.append(request)
        url = str(request.url)

        def _usable(route_method: Optional[str]) -> bool:
            return route_method is None or route_method == request.method.upper()

        exact = [r for r in self.routes if _usable(r[0]) and r[1] == url]
        partial = sorted(
            (r for r in self.routes if _usable(r[0]) and r[1] != url and r[1] in url),
            key=lambda r: len(r[1]),
            reverse=True,
        )
        for _, _, response in [*exact, *partial]:
            if isinstance(response, BaseException):
                raise response
            result = response(request) if callable(response) else response
            result.request = request
            return result
        raise NetworkBlocked(f"没登记过的 httpx 请求：{request.method} {url}")

    @property
    def urls(self) -> list[str]:
        """按顺序列出被请求过的 URL"""
        return [str(r.url) for r in self.requests]


@pytest.fixture
def stub_httpx(monkeypatch: pytest.MonkeyPatch, no_network: None) -> HttpxRouter:
    """把 httpx 默认的同步/异步 transport 换成可编程的桩。

    和 stub_requests 一样显式依赖 no_network，理由见那边。

    ai 插件和 draw.py / icons.py 都是在函数里现 new `httpx.AsyncClient()`，
    没法从外面塞 transport，所以只能补 transport 类上的方法。

        async def test_ai(stub_httpx, make_httpx_response):
            stub_httpx.post("/v1/chat/completions", json={"choices": [...]})
    """
    router = HttpxRouter()

    def _sync(self: Any, request: httpx.Request, *a: Any, **k: Any) -> httpx.Response:
        return router.handle(request)

    async def _async(
        self: Any, request: httpx.Request, *a: Any, **k: Any
    ) -> httpx.Response:
        return router.handle(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _async)
    return router


# --------------------------------------------------------------------------
# JsonRedis 存储
# --------------------------------------------------------------------------
@pytest.fixture
def json_redis(tmp_path: Path) -> JsonRedis:
    """一个干净的 JsonRedis，落在 tmp_path/storage.json，绝不碰仓库工作区。"""
    return JsonRedis(tmp_path / "storage.json")


@pytest.fixture
def make_json_redis(tmp_path: Path) -> Callable[..., JsonRedis]:
    """工厂：要几个独立的 JsonRedis 就造几个。

        r1 = make_json_redis("a.json")
        r2 = make_json_redis("b.json", initial={"k": "v"})
    """
    counter = {"n": 0}

    def _make(
        name: Optional[str] = None,
        *,
        initial: Optional[dict[str, Any]] = None,
        auto_save: bool = True,
    ) -> JsonRedis:
        if name is None:
            counter["n"] += 1
            name = f"storage_{counter['n']}.json"
        redis = JsonRedis(tmp_path / name, auto_save=auto_save)
        for key, value in (initial or {}).items():
            redis.set(key, value)
        return redis

    return _make


@pytest.fixture
def patch_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., JsonRedis]:
    """把插件模块级的那个 `r = JsonRedis(plugin_storage(__file__))` 换成临时实例。

    插件是在 import 期就把 JsonRedis 建好绑在模块上的，测试必须替换掉，
    不然会读写 xiaozu_bot/plugins/<x>/data/storage.json。

        from xiaozu_bot.plugins import jrrp
        def test_jrrp(patch_storage):
            r = patch_storage(jrrp, initial={"jrrp_12345": "66"})
            ...
    """

    def _patch(
        module: Any,
        *,
        initial: Optional[dict[str, Any]] = None,
        attr: str = "r",
    ) -> JsonRedis:
        filename = f"{module.__name__.replace('.', '_')}_{attr}.json"
        redis = JsonRedis(tmp_path / filename)
        for key, value in (initial or {}).items():
            redis.set(key, value)
        monkeypatch.setattr(module, attr, redis)
        return redis

    return _patch


# --------------------------------------------------------------------------
# OneBot V11 事件 / Bot
# --------------------------------------------------------------------------
def _as_message(message: Union[str, Message]) -> Message:
    return message if isinstance(message, Message) else Message(message)


@pytest.fixture
def make_group_event() -> Callable[..., GroupMessageEvent]:
    """工厂：造一个 OneBot V11 群消息事件。

    时间戳、消息 id 之类都写死了默认值，不依赖真实时钟。

        event = make_group_event("*jrrp")
        event = make_group_event("*zhua", user_id=1, group_id=2, to_me=True)
        event = make_group_event("*reload", user_id=int(SUPERUSER_ID))
    """

    def _make(
        message: Union[str, Message] = "",
        *,
        user_id: int = DEFAULT_USER_ID,
        group_id: int = DEFAULT_GROUP_ID,
        self_id: int = int(BOT_SELF_ID),
        message_id: int = 1,
        timestamp: int = DEFAULT_TIME,
        sub_type: str = "normal",
        to_me: bool = False,
        nickname: str = "测试用户",
        card: str = "",
        role: str = "member",
        **extra: Any,
    ) -> GroupMessageEvent:
        msg = _as_message(message)
        return GroupMessageEvent(
            time=timestamp,
            self_id=self_id,
            post_type="message",
            sub_type=sub_type,
            user_id=user_id,
            message_type="group",
            message_id=message_id,
            message=msg,
            original_message=msg.copy(),
            raw_message=str(msg),
            font=0,
            sender=Sender(user_id=user_id, nickname=nickname, card=card, role=role),
            group_id=group_id,
            to_me=to_me,
            **extra,
        )

    return _make


@pytest.fixture
def make_private_event() -> Callable[..., PrivateMessageEvent]:
    """工厂：造一个 OneBot V11 私聊消息事件。

    私聊的 to_me 默认就是 True（真适配器也是这么标的）。

        event = make_private_event("*help")
    """

    def _make(
        message: Union[str, Message] = "",
        *,
        user_id: int = DEFAULT_USER_ID,
        self_id: int = int(BOT_SELF_ID),
        message_id: int = 1,
        timestamp: int = DEFAULT_TIME,
        sub_type: str = "friend",
        to_me: bool = True,
        nickname: str = "测试用户",
        **extra: Any,
    ) -> PrivateMessageEvent:
        msg = _as_message(message)
        return PrivateMessageEvent(
            time=timestamp,
            self_id=self_id,
            post_type="message",
            sub_type=sub_type,
            user_id=user_id,
            message_type="private",
            message_id=message_id,
            message=msg,
            original_message=msg.copy(),
            raw_message=str(msg),
            font=0,
            sender=Sender(user_id=user_id, nickname=nickname),
            to_me=to_me,
            **extra,
        )

    return _make


class FakeBot(Bot):
    """记录所有 call_api 调用的假 Bot，一个真请求都不会发。

    OneBot 的 Bot 用 __getattr__ 把 send_group_msg 之类都路由到 call_api，
    所以只覆盖 call_api 就够了。

        bot.api_results["get_group_member_info"] = {"role": "admin"}
        await bot.send_group_msg(group_id=1, message="hi")
        assert bot.calls[-1][0] == "send_group_msg"
    """

    def __init__(self, adapter: Adapter, self_id: str = BOT_SELF_ID) -> None:
        super().__init__(adapter, self_id)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.api_results: dict[str, Any] = {}

    async def call_api(self, api: str, **data: Any) -> Any:
        self.calls.append((api, data))
        result = self.api_results.get(api)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return result(**data)
        return result

    @property
    def called_apis(self) -> list[str]:
        """按顺序列出被调过的 api 名"""
        return [name for name, _ in self.calls]


@pytest.fixture
def onebot_adapter() -> Adapter:
    """一个挂在 ~none driver 上的 OneBot V11 适配器实例，给 FakeBot 用。"""
    return Adapter(nonebot.get_driver())


@pytest.fixture
def fake_bot(onebot_adapter: Adapter) -> FakeBot:
    """一个 FakeBot：所有 call_api 都被记进 .calls，返回值可以用 .api_results 预设。"""
    return FakeBot(onebot_adapter)


# --------------------------------------------------------------------------
# 其他
# --------------------------------------------------------------------------
@pytest.fixture
def seeded_random() -> Any:
    """给全局 random 播固定种子，测完把原来的状态还回去。

    好几个插件在 import 期调了 `random.seed()`（无参 = 用系统熵），
    要断言随机结果就必须显式播种。

        def test_map(seeded_random):
            seeded_random(0)
            ...
    """
    state = _random.getstate()

    def _seed(seed: Any = 0) -> Any:
        _random.seed(seed)
        return _random

    try:
        yield _seed
    finally:
        _random.setstate(state)


@pytest.fixture
def repo_root() -> Path:
    """仓库根目录的 Path，读示例数据文件的时候用。"""
    return REPO_ROOT


# ==========================================================================
# 直接驱动 matcher handler 的工具
#
# 插件的处理函数都是 nonebot 的 matcher handler，最后一句几乎都是
# `await xxx.finish(...)`。`Matcher.finish` 是 classmethod，内部从
# `current_bot` / `current_event` 两个 ContextVar 拿 bot 和事件，
# 调完 `bot.send(...)` 再抛 `FinishedException`。
#
# 所以这里的做法是：**不打桩 finish/send，而是把 ContextVar 设成 FakeBot 和
# 真事件，直接 await handler 本体，再捕获 FinishedException**。这样连 OneBot
# 适配器里 at_sender 拼 @ 段、message_type 推断那一圈都是真跑的。
#
# 这几个函数原来定义在 tests/test_small_plugins.py 里，别的测试文件靠
# `from tests.test_small_plugins import ...` 拿过去用 —— 那样 test_small_plugins.py
# 就删不掉了，而「不想要的测试可以整个文件删掉」是这套测试的基本约定
# （见 MAINTAINING.md 第 5 节）。放在 conftest 里就没这个问题。
#
# 另外注意：nonebot 要是改了内部 API，全仓库的 handler 测试都是从这里进去的，
# 改这一处就能让几百个用例一起恢复。
# ==========================================================================


def _as_message(value: Union[None, str, "Message"]) -> "Message":
    from nonebot.adapters.onebot.v11 import Message

    if value is None:
        return Message("")
    return value if isinstance(value, Message) else Message(value)


@contextlib.contextmanager
def matcher_context(bot: "FakeBot", event: Any) -> Any:
    """把 nonebot 的 current_bot / current_event 临时设成给定的 bot 和事件。

    `Matcher.send` 就是从这两个 ContextVar 里取东西的，不设的话直接 LookupError。
    """
    from nonebot.matcher import current_bot, current_event

    token_bot = current_bot.set(bot)
    token_event = current_event.set(event)
    try:
        yield
    finally:
        current_bot.reset(token_bot)
        current_event.reset(token_event)


async def run_handler(
    matcher: Any,
    bot: "FakeBot",
    event: Any = None,
    *,
    arg: Union[None, str, "Message"] = None,
    index: int = 0,
) -> bool:
    """直接调用某个 matcher 的第 index 个 handler。

    返回 True 表示 handler 以 `finish()` 收尾（抛了 FinishedException），
    返回 False 表示它自己 return 掉了。发出去的东西都在 `bot.calls` 里。

    handler 的形参默认值是 `CommandArg()` 这类依赖注入对象，直接调必须显式把
    `arg=Message(...)` 传进去，否则 `str(arg)` 拿到的是依赖对象本身 ——
    下面按签名挑参数就是干这个的。
    """
    import inspect

    from nonebot.exception import FinishedException

    handler = matcher.handlers[index].call
    message = _as_message(arg)
    pool: dict[str, Any] = {
        "bot": bot,
        "event": event,
        "matcher": matcher,
        "arg": message,
        "args": message,
    }
    signature = inspect.signature(handler)
    kwargs = {
        name: value for name, value in pool.items() if name in signature.parameters
    }
    with matcher_context(bot, event):
        try:
            await handler(**kwargs)
        except FinishedException:
            return True
    return False


async def run_coro(bot: "FakeBot", event: Any, factory: Callable[[], Any]) -> bool:
    """跑一个不是 handler 的协程（比如 guess.can_start），语义同 run_handler。"""
    from nonebot.exception import FinishedException

    with matcher_context(bot, event):
        try:
            await factory()
        except FinishedException:
            return True
    return False


def sent_messages(bot: "FakeBot") -> list[Any]:
    """FakeBot 收到的所有 send_msg 调用里的消息体"""
    return [data["message"] for api, data in bot.calls if api == "send_msg"]


def sent_texts(bot: "FakeBot") -> list[str]:
    """FakeBot 发出去的纯文本（@ 段之类会被 extract_plain_text 滤掉）"""
    return [msg.extract_plain_text().strip() for msg in sent_messages(bot)]


def only_text(bot: "FakeBot") -> str:
    """断言「只发了一条消息」，并把那条消息的纯文本还回来。

    比 `assert sent_texts(bot) == ["一整句原文"]` 松一档：
    多发一条消息照样红（发几条是行为），但把回复改几个字不会红（措辞不是行为）。
    用法是 `assert "57" in only_text(bot)` —— 断言的是「回了 57」这件事。
    """
    texts = sent_texts(bot)
    assert len(texts) == 1, f"应该只发一条消息，实际发了 {len(texts)} 条：{texts}"
    return texts[0]
