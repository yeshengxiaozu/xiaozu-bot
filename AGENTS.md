# AGENTS.md

## 项目概览

这是一个基于 NoneBot 2 的 Python 机器人项目。`bot.py` 初始化 NoneBot，注册
OneBot V11 和 QQ 适配器，并从 `pyproject.toml` 加载 `xiaozu_bot/plugins/` 下的插件。

主要目录：

- `xiaozu_bot/plugins/`：机器人插件。`gdlevelsearch` 包含 Geometry Dash 数据源、
  搜索、渲染和更新器；其他目录提供 AI、TTS、猜图、抓图、人品和娱乐命令。
- `xiaozu_bot/utils/`：适配器兼容层和基于 JSON 的持久化存储。
- `scripts/`：不依赖 QQ 连接的本地检查、GD 数据更新和旧 Redis 数据迁移脚本。
- `tests/`：使用 NoneBot `~none` driver 的单元测试和测试替身。
- `MAINTAINING.md`：测试与 CI 的维护手册；修改测试或工作流前先阅读它。

## 环境与安装

- 支持 Python 3.10 及以上；CI 的常规测试版本是 3.10 和 3.13，每周定时任务会覆盖
  3.10 到 3.13。
- 安装项目依赖：`python -m pip install -e .`
- 安装开发依赖：`python -m pip install -e ".[dev]"`
- 需要迁移旧 Redis 数据时再安装：`python -m pip install -e ".[migrate]"`
- `tts` extra 中的 `mlx-audio` 面向 Apple Silicon；不要把它加入 Linux CI 的安装步骤。
- `.env`、插件 `data/`、临时音频和缓存都属于本地运行产物，不要提交凭据或生成数据。

## 运行与本地脚本

命令默认从项目根目录执行：

```bash
nb run --reload
python bot.py
python scripts/run_updater.py
python scripts/try_search.py Tartarus
```

`python bot.py` 会加载所有插件并启动 bot；加载失败会在启动阶段暴露。`run_updater.py`
需要网络，并将关卡缓存写入被忽略的 `gdlevelsearch/data/`；`try_search.py` 需要已有缓存，
并把图片写入 `temp/`。两个脚本当前都会检查工作目录，因此不要从其他目录调用它们。

创建新插件时使用 `xiaozu_bot/plugins/<plugin_name>/`，保持现有的
`__plugin_meta__`、`Config` 和 NoneBot matcher 注册模式。跨 OneBot/QQ 的消息、媒体和
事件逻辑优先复用 `xiaozu_bot/utils/adapter_compat.py`。

## 测试与质量检查

```bash
python -m pytest tests -q
python -m pytest tests --cov=xiaozu_bot --cov-report=term-missing
ruff check .
```

测试必须离线运行，不得访问真实网络；需要网络响应时使用 `stub_requests` 或
`stub_httpx`。文件写入使用 pytest 的 `tmp_path`，插件的模块级 `JsonRedis` 使用
`patch_storage` 替换，避免污染工作区。涉及随机数、时间或外部响应时使用测试提供的
替身和固定输入。

CI 中 `ruff check .` 是阻塞合并的门禁；大多数自动修复可以使用 `ruff check --fix .`，
不要使用 `--unsafe-fixes`。当前 CI 不运行 `ruff format --check` 或 pyright。

## 数据与持久化约定

- 运行时持久化使用 `xiaozu_bot/utils/json_storage.py` 的 `JsonRedis`，不把 Redis
  作为 bot 的运行时依赖重新引入。
- 插件数据放在对应插件的 `data/` 下；GD 更新器先写 `data/.staging/`，完整成功后
  才发布到 `data/`。
- `guess/data/` 和 `zhua/data/` 是需要部署者自行准备的运行时资源；猜图临时裁图位于
  `guess/pictures/`。这些目录都被 `.gitignore` 忽略。
- 不要在测试中改写仓库内的生产数据；发现生产代码问题时修改生产代码并补测试，
  不要让测试悄悄绕过问题。

## 修改原则

- 保持改动聚焦，避免顺手重排或重命名无关文件。
- 修改命令、插件、适配器或数据更新流程时，同时更新相关测试和 README/维护文档。
- 不恢复已删除的 `game` 插件、旧的 Redis 运行时方案或已下线的 roulette/blueberry
  功能，除非需求明确要求恢复。
- 提交前检查 `git status`，确认没有凭据、缓存、`data/` 文件或临时输出。
