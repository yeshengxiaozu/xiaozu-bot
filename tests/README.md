# 测试说明

## 怎么跑

## 文件一览

| 文件 | 测什么 |
| --- | --- |
| `test_harness_smoke.py` | 脚手架本身：nonebot 初始化、fixture、断网守卫 |
| `test_json_storage.py` | `utils/json_storage.py` + `scripts/migrate_redis_to_json.py` |
| `test_gdapi.py` | `gdlevelsearch/gdapi.py`（GD 官方服务器协议解析） |
| `test_gd_sources.py` | GDDL / NLW / plat / icons / dailydemon 几个数据源 |
| `test_search_and_ratings.py` | `fullsearch.py` / `ratings.py` 两个分页会话 |
| `test_gdlevelsearch_entry.py` | `gdlevelsearch/__init__.py`：`search_by_name`、`reload_all`、各命令 handler |
| `test_draw.py` | `gdlevelsearch/draw.py` 里的纯函数与 `_fetch_thumbnail` |
| `test_updater.py` | `gdlevelsearch/updater/` 整条抓取流水线 |
| `test_small_plugins.py` | jrrp / joy / roulette / zhua / say / ai / guess |
| `test_game_and_help.py` | game 与 xiaozubot_help |

## 怎么跑

从**仓库根目录**跑（pytest 的 rootdir 就是那里）：

```bash
python -m pytest tests -q          # 全跑
python -m pytest tests -q -x       # 第一个失败就停
python -m pytest tests/test_harness_smoke.py -q
python -m pytest tests -q -m "not slow"
python -m pytest tests --cov=xiaozu_bot --cov-report=term-missing
```

不需要 venv，也不需要装 fastapi driver —— `tests/conftest.py` 会用 `~none` driver 初始化 nonebot。

## conftest 在 import 期干了什么

test 模块被收集之前，`tests/conftest.py` 在**模块级**（不是 fixture 里）按顺序做了三件事：

1. 从 `__file__` 推出仓库根目录塞进 `sys.path`；
2. 调 `nonebot.init(driver="~none", ...)`，只调一次（拿 `get_driver()` 抛不抛 `ValueError` 当探针）。
   插件在 import 期就会调 `get_plugin_config()` / `on_command()` / `require()`，不先 init 直接炸；
3. 摘掉 nonebot 默认那个 INFO 级 loguru handler，只留 WARNING 以上。

**配置是写死的，不读仓库里的 `.env`**，换台机器结果一样：

| 常量 | 值 |
| --- | --- |
| `COMMAND_START` | `{"*"}` |
| `COMMAND_SEP` | `{"."}` |
| `SUPERUSER_ID` | `"10000"` |
| `BOT_SELF_ID` | `"10001"` |
| `DEFAULT_USER_ID` | `12345` |
| `DEFAULT_GROUP_ID` | `67890` |
| `DEFAULT_TIME` | `1700000000` |
| `GAME_WHITELIST_GROUP_IDS` | `(1035708051, 870217476)` |

要用就 `from tests.conftest import SUPERUSER_ID`。

## fixture 一览

### 存储

| 名字 | 干什么 |
| --- | --- |
| `json_redis` | 一个干净的 `JsonRedis`，落在 `tmp_path/storage.json` |
| `make_json_redis` | 工厂，`make_json_redis("a.json", initial={...})`，要几个独立实例就造几个 |
| `patch_storage` | 把插件模块级的 `r` 换成临时实例：`patch_storage(jrrp, initial={...})`，测完自动还原 |

插件是在 import 期就 `r = JsonRedis(plugin_storage(__file__))` 的。**碰插件存储一律用 `patch_storage`**，
不然会读写 `xiaozu_bot/plugins/<x>/data/storage.json`。

### 事件 / Bot

| 名字 | 干什么 |
| --- | --- |
| `make_group_event` | 造 `GroupMessageEvent`：`make_group_event("*jrrp", user_id=1, group_id=2, to_me=True)` |
| `make_private_event` | 造 `PrivateMessageEvent`，`to_me` 默认 `True` |
| `onebot_adapter` | 挂在 `~none` driver 上的 OneBot V11 `Adapter` |
| `fake_bot` | `FakeBot`，继承真 `Bot`，`call_api` 全记进 `.calls`，返回值用 `.api_results` 预设 |

```python
fake_bot.api_results["get_group_member_info"] = {"role": "admin"}
await fake_bot.send_group_msg(group_id=1, message="hi")
assert fake_bot.called_apis == ["send_group_msg"]
```

### 网络

| 名字 | 干什么 |
| --- | --- |
| `no_network` | **autouse**，不用写在参数里。任何真实出网都抛 `NetworkBlocked` |
| `stub_requests` | 可编程的 `requests` 桩，按 URL 分发，记录调用 |
| `stub_httpx` | 可编程的 `httpx` 桩（同步 + 异步 transport 都接管） |
| `make_response` | 造假的 requests 响应（`FakeResponse`，有 `status_code/text/content/json()/raise_for_status()`） |
| `make_httpx_response` | 造真的 `httpx.Response`，`.request` 已经挂好，能直接 `raise_for_status()` |

`no_network` 分四层拦：

- **库级别**：`requests.adapters.HTTPAdapter.send`（`requests.get/post` 和模块级复用的 `Session`
  最后都走这里）、`httpx.HTTPTransport.handle_request`、`httpx.AsyncHTTPTransport.handle_async_request`。
  连 `127.0.0.1` 的本地大模型也一起拦。
- **`http.client` 级别**：`HTTPConnection.connect` 和 `HTTPSConnection.connect` 一刀切封死。
  `urllib.request`、urllib3 直连、httplib2（`google-api-python-client` 用的就是它）全走这两个方法。
  **这一层不放行环回**，因为环回正是代理监听的地址。
- **代理设置**：清掉 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 等环境变量，
  并把 `urllib.request.getproxies` 换成返回空 dict（Windows 上它读注册表，光清环境变量不够）。
- **socket 级别**：`getaddrinfo` / `create_connection` / `socket.connect`，兜住上面没盖到的裸 socket。
  **环回地址放行** —— Windows 上 asyncio 的 ProactorEventLoop 建 self-pipe 时要往 127.0.0.1 connect，
  一刀切封死所有 async 用例都会崩。

> 为什么要有中间那两层：只靠 socket 层的环回豁免是有洞的。开发机上如果有本机
> HTTP 代理（`HTTP_PROXY=http://127.0.0.1:7897` 这种很常见），任何认代理设置的库
> 都会把连接目标变成环回，守卫直接放行，请求照样打到外网 —— 实测
> `urllib.request.urlopen("http://example.com/")` 在旧版守卫下能拿回 200。
> `tests/test_harness_smoke.py::test_no_network_blocks_urllib` 就是盯这个的回归用例。

想放行某次调用，用 `stub_requests` / `stub_httpx`，或者自己 monkeypatch 更上层的入口，
**别去改 `no_network`**。

```python
def test_aredl(stub_requests, make_response):
    stub_requests.get(
        "https://api.aredl.net/v2/api/aredl/levels",
        make_response(json_data=[...]),
    )
    ...
    assert stub_requests.urls == ["https://api.aredl.net/v2/api/aredl/levels"]

async def test_ai(stub_httpx):
    stub_httpx.post("/v1/chat/completions", httpx.Response(200, json={...}))
```

URL 匹配：先全等，再看登记的串是不是请求 URL 的子串（方便忽略 query）。
没登记过的 URL 直接抛 `NetworkBlocked`。
登记的位置放异常实例（比如 `requests.Timeout("boom")`）就能测调用方的错误分支。

### 其他

| 名字 | 干什么 |
| --- | --- |
| `seeded_random` | `seeded_random(0)` 给全局 `random` 播固定种子，测完还原原状态 |
| `repo_root` | 仓库根目录的 `Path` |

好几个插件在 import 期调了 `random.seed()`（无参 = 用系统熵），
**要断言随机结果就必须先 `seeded_random(...)`**。

## 写测试的规矩

- **不许联网**。飞机上跑不过的测试就是 bug。
- **不许往仓库工作区写东西**，只写 `tmp_path`。
- **不许依赖真实时钟、执行顺序、没播种的 random、dict/set 迭代顺序**。
- **不许改 `xiaozu_bot/`、`scripts/`、`bot.py`**。发现生产代码有 bug 就报出来，别在这边顺手改。
- 断言必须是**你真读过源码**的行为。

## 目录约定

`tests/` 下有 `__init__.py`，是个包。新建子目录时**请顺手补一个 `__init__.py`**：

有 `__init__.py` 时模块以 `tests.plugins.test_x` 这种完整包名 import，不同子目录里的同名
`test_*.py` 互不干扰；没有的话 pytest 默认的 prepend 模式会按 basename import，
两个 `tests/a/test_foo.py` 和 `tests/b/test_foo.py` 直接 `import file mismatch` 报错，
整个收集阶段都跑不下去。

有它还有个好处：pytest 装的 conftest 就是 `tests.conftest`，
所以 `from tests.conftest import NetworkBlocked` 拿到的和 pytest 用的是同一个模块对象。

## pytest 配置（在 `pyproject.toml` 的 `[tool.pytest.ini_options]`）

- `testpaths = ["tests"]`
- `pythonpath = ["."]`
- `asyncio_mode = "auto"` —— **`async def test_xxx` 不用加 `@pytest.mark.asyncio`**，直接写就行
- `asyncio_default_fixture_loop_scope = "function"` —— 每个用例一个新事件循环
- `addopts = "-ra --strict-config"`
- `filterwarnings`：基线是 `default`（显示但不炸）；`xiaozu_bot` 自己发出的
  `DeprecationWarning` 会当错误。**没设成全局 `error`** 是因为模块级 import 的 warning
  每进程只触发一次，全局 error 会变成「谁先 import 谁挂」的顺序依赖。
- `markers`：已登记 `slow` / `integration` / `updater`。没登记的 marker 只警告不报错
  （没开 `--strict-markers`，就是为了不卡住并行加测试的人）。
