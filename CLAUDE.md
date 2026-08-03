# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

基于 NoneBot 2 的 QQ / OneBot 群机器人，主功能是 Geometry Dash 关卡检索（`gdlevelsearch`），另有 AI 聊天、TTS、猜图、抓图、人品等插件。开发约定、测试与 CI 维护手册见 [AGENTS.md](AGENTS.md) 与 [MAINTAINING.md](MAINTAINING.md)；本文档只讲「要读多个文件才看得懂」的架构。

## 常用命令

全部在仓库根目录执行：

```bash
python -m pip install -e ".[dev]"      # 开发安装（含 pytest / ruff / pyright）
nb run --reload                        # 起 bot（热重载）
python bot.py                          # 起 bot，能暴露插件 import 期错误
python -m pytest                       # 全量测试
python -m pytest tests/test_gdapi.py -q                        # 只跑一个文件
python -m pytest tests/test_gdapi.py::TestDecryptPassword -q   # 只跑一个类
ruff check .                           # lint（CI 门禁）
ruff check --fix .                     # 自动修复（别用 --unsafe-fixes）
python scripts/run_updater.py          # 单独跑 GD 数据更新（不需要 bot / QQ）
python scripts/try_search.py Tartarus  # 走完整搜索+出图，结果写到 temp/ 的 png
```

测试**必须离线**（autouse 的 `no_network` 守卫会拦任何真实出网）、写文件只许写 `tmp_path`。更细的 pytest 用法、覆盖率、`-m "not slow"` 见 MAINTAINING.md 第 6 节。

## 两种运行路径

- **bot.py**：`nonebot.init` → 注册 OneBot V11 / QQ 适配器 → `load_from_toml("pyproject.toml")` 加载 `xiaozu_bot/plugins/` 下所有插件。插件 import 期就有副作用（注册 matcher、建 `data/storage.json`、把缓存读进模块级变量），所以「import 成功」就是「插件加载成功」。
- **独立脚本**：`scripts/run_updater.py` 把 `gdlevelsearch/` 塞进 `sys.path`，让 `updater` 变成顶层包直接 import（绕开会拉适配器、注册 matcher 的 `gdlevelsearch/__init__.py`）；`scripts/try_search.py` 用 `scripts/_bootstrap.py` 往 `sys.modules` 装同名空壳包。两个脚本都校验工作目录，**只能从仓库根目录跑**。

`updater/jobs/*.py` 顶部的 `try: from ..paths import X / except ImportError: from updater.paths import X` 双路径 import 就是为这两种场景准备的（`TID252` 因此在 pyproject 里被 ignore）。改 jobs 时别破坏它。

## 插件模型

- `xiaozu_bot/` 和 `xiaozu_bot/plugins/` 是 PEP 420 命名空间包（无 `__init__.py`）；每个插件子目录**有** `__init__.py`。NoneBot 从 `pyproject.toml` 的 `plugin_dirs` 扫描，新插件放到 `xiaozu_bot/plugins/<名字>/` 即可，无需登记。
- 每个插件导出 `__plugin_meta__` 与 `Config`（`Config` 加必填字段必须给默认值，否则所有插件 import 期崩、CI 收集阶段红）。
- **模块级副作用是常态**：`r = JsonRedis(plugin_storage(__file__))` 在 import 期就建好 `data/storage.json`（`.gitignore` 用 `xiaozu_bot/plugins/*/data/` 通配盖住）；`gdlevelsearch/api/` 各模块在 import 期把 json 缓存读进模块级 list/dict。这直接决定了：抓完新数据必须调 `reload_all()` 才进内存、独立脚本必须用空壳包绕开 import。
- `game` 插件目录只剩空壳（无 `__init__.py`，不会被加载）；roulette 轮盘/蓝莓经济已下线——不要恢复。

## gdlevelsearch 分层

```
api/       数据源客户端：gdapi（GD 官方服务器协议）、gddlapi、aredlapi、nlwapi、platapi
render/    出图：draw（合图，create_image_from_gdlevel）、icons + iconrender（本地图标渲染）
services/  跨命令共享业务：search_by_name、send_result 等
commands/  每个命令一个文件：matcher + handler，import 即注册
updater/   定时数据更新：runner + jobs + notify
```

- `commands/` 只 import 就完成 matcher 注册；包 `__init__.py` re-export 各 matcher 和会话状态，保持 `gdlevelsearch.gdsearch` 这类旧引用（含测试）可用。改被 re-export 的符号时同步改 `__init__.py`。
- **命令会话模式**：`*gdsearch` / `*gdfullsearch` / `*gdratings` / `*gdsearch_manage` 都是「`on_command` 把结果存进按 user/session 分键的模块级 dict + 30s 超时任务，再用 `on_message` 选择器 matcher 接后续输入（选序号 / 翻页 / 取消）」。各命令的 `_drop_*` 清理函数由 `services/search.py::_clear_all_sessions` 统一调用。
- 同步阻塞调用（requests、Pillow 合图）一律 `await asyncio.to_thread(...)`，别直接 await。

## updater 流水线

`updater/runner.py` 的 `JOBS` / `STAGES` 定义两层并发任务图：第 1 层纯抓取（nlw/ids/lw/hds/plat*/sfh），第 2 层 platbatch + getmetadata 依赖第 1 层产出。产物先写 `data/.staging/`，**整条流水线全绿才原子发布**（`updater/paths.py::publish`，逐文件有 50% 下限检查，防止上游表格改格式导致空文件盖掉好数据），中途失败 `data/` 保持原样。触发路径：每天凌晨 3 点 apscheduler 定时任务 + `*gdsearch_update` 命令，跑完都调 `gdlevelsearch.reload_all()`（api 模块 import 期只读一次，少了这步新数据要等重启才生效）。

## 适配器兼容层（xiaozu_bot/utils/adapter_compat.py）

跨 OneBot V11 / QQ 官方机器人的统一入口：事件类型别名（`GroupMessageEvent` / `PrivateMessageEvent` 等）、`send_image` / `send_audio` / `send_forward` / `react` / `send_group` 系列，以及 `install_qq_rich_media_compat()`（`bot.py` 里注册适配器前调用，给 QQ Bot 打媒体上传补丁）。插件里发消息 / 媒体一律走这里，别直接分支写 `bot.send`。

## 持久化（xiaozu_bot/utils/json_storage.py）

`JsonRedis` 是 JSON 文件的 Redis 模拟（get/set 带过期、hset/hget、keys glob），写盘用临时文件 + `os.replace` 保证原子；坏文件改名 `.broken` 后从空开始、**绝不抛异常**（否则插件 import 期就崩）。不再用真 Redis。存储路径用 `plugin_storage(__file__)` 算，别写相对 CWD 的路径。

## 测试要点

- `tests/conftest.py` 在 import 期用 `~none` driver 初始化 nonebot，配置常量写死（`*` 命令前缀、SUPERUSER 10000 等），不读 `.env`。
- autouse 的 `no_network` 守卫拦一切真实出网；模拟响应用 `stub_requests` / `stub_httpx` + `make_response` / `make_httpx_response`。
- 插件模块级 `JsonRedis` 用 `patch_storage(模块, initial=...)` 替换，别真读写 `data/`；要断言随机结果先 `seeded_random(...)`。
- `asyncio_mode = "auto"`：`async def test_xxx` 直接写，不用 `@pytest.mark.asyncio`。
- **测试故意不测文案**（改回复措辞不该让用例红），断言盯行为 / 数字 / 分支。哪些测试永远别删、怎么安全删测试、CI 红了怎么查，都在 MAINTAINING.md。

## 别踩的坑

- 别跑 `ruff check --unsafe-fixes`（会破坏 `updater/__init__.py` 的相对 import，定时任务运行时才炸）。
- `gdlevelsearch/__init__.py` 里 `reload_all` 定义在 `import updater` 之前——updater 的定时任务要回头调它，别挪。
- 给 `Config` 加必填字段必须给默认值，否则所有插件 import 期崩、CI 收集阶段红。
- 新增命令想让 `*help` 显示，往 `xiaozubot_help/commands.py` 的 `COMMANDS` 加一条即可；超级用户专用、已下线的命令不要加。
