# xiaozu-bot

[![CI](https://github.com/yeshengxiaozu/xiaozu-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/yeshengxiaozu/xiaozu-bot/actions/workflows/ci.yml)

xiaozu-bot 是一个基于 NoneBot 的模块化机器人仓库示例，包含 AI 聊天、TTS、Geometry Dash 关卡检索、猜图小游戏、人品、抓图等插件。项目支持 OneBot V11 和 QQ 适配器，也适合用于插件开发与测试。

## 主要功能

- **AI 聊天（`ai`）**：对接本地 LLM（LM Studio 风格 API），支持多轮上下文对话与简单会话管理。
- **语音合成 / TTS（`say`）**：使用 `mlx_audio` 生成语音，并通过当前消息适配器发送群/私聊语音消息。
- **Geometry Dash 关卡检索（`gdlevelsearch`）**：整合 AREDL、GDDL、NLW、IDS、LW、HDS、plat chart 等数据源，结果渲染成一张图片发出；带本地缓存和每日自动更新（见下面「数据更新」）。
- **猜图 / 猜关卡（`guess`）**：带题库与图片资源的互动小游戏。
- **抓图 / 表情包（`zhua`）**：从本地图库随机/指定发送图片与描述。
- **随机小工具（`roulette`）**：`*map` 随机来张地图、`*random` 在给的选项里随机挑一个。轮盘和蓝莓经济已下线。
- **每日人品（`jrrp`）**：每日一次的人品查询。
- **娱乐指令（`joy`）**：杂七杂八的小指令合集。
- **图片/文本渲染（GD 插件内的 `imageinfo` 模块）**：将 HTML/Markdown/文本渲染为图片并发送（依赖 `nonebot_plugin_htmlkit`）。
- **帮助命令（`xiaozubot_help`）**：内置简单帮助信息。

## 适用场景

- 游戏社区（例如 Geometry Dash 玩家群）：快速查询关卡、分享讨论、举办小游戏。
- 群聊娱乐：猜图、抓图、人品等互动功能活跃群气氛。
- 语音互动与直播辅助：将文本转语音推送到群中或语音通道。
- 本地私有化 AI：在本地部署 LLM，为群组或个人提供私有化问答服务。
- 插件开发与学习：仓库提供插件示例，便于学习 NoneBot 插件开发模式。

## 快速开始

要求：Python 3.10+。使用 `ai` 插件时需要一个兼容 OpenAI API 的本地 LLM 服务；默认地址是 `http://127.0.0.1:1234`。

开发约定、测试规则和常见维护操作见 [AGENTS.md](AGENTS.md) 与 [MAINTAINING.md](MAINTAINING.md)。

不再需要 Redis —— 所有需要持久化的插件都改用 `xiaozu_bot/utils/json_storage.py`
里的 `JsonRedis`，数据落在各插件自己的 `data/storage.json`。

安装依赖：

```bash
python -m pip install -e .
```

可选的两组额外依赖：

```bash
python -m pip install -e ".[tts]"      # say 插件的 mlx_audio，只能装在 Apple Silicon 上
python -m pip install -e ".[migrate]"  # 只有从旧 Redis 迁移数据时才要
```

运行机器人（使用 NoneBot CLI）：

```bash
nb run --reload
```

创建/开发插件（可选）：

```bash
nb create
nb plugin create
# 插件文件放在 xiaozu_bot/plugins 下
```

## 本地检查与调试

以下命令都请在项目根目录执行。它们不需要连接 QQ；其中 `python bot.py` 会在插件加载完成后启动 bot。

```bash
python bot.py
```

启动前的插件 import 错误、缺少依赖和注册失败都会在这一步暴露出来；成功启动后可以按 Ctrl-C 停止。

```bash
python scripts/run_updater.py            # 跑一遍抓取流水线，逐个任务报成败
python scripts/run_updater.py nlw ids    # 只跑指定的几个
python scripts/run_updater.py --continue # 中间挂了也继续跑后面的
```

```bash
python scripts/try_search.py Tartarus          # 走完整搜索 + 出图，结果写成 png
python scripts/try_search.py 51657783          # 按 id 查
python scripts/try_search.py --reload Tartarus # 顺便验 reload_all() 有没有生效
```

`try_search.py` 走的是和 bot 里一样的 `search_by_name` / `create_image_from_gdlevel`，
只是最后一步 `bot.send` 换成写文件，所以出来的图应该和群里收到的一样。

`run_updater.py` 和 `try_search.py` 会绕开完整 bot 进程加载，只运行 GD 更新或查询流程；
前者直接导入 updater，后者通过 `scripts/_bootstrap.py` 加载 GD 子模块。
插件资源路径按源文件定位，但这两个脚本仍会检查当前目录，必须从项目根目录运行。

## 测试与 CI

> 日常维护看 **[MAINTAINING.md](MAINTAINING.md)** —— CI 红了怎么查、
> 改了什么会红几个用例、哪些测试可以直接删、哪些永远别删，都在那里。

本地跑测试（在仓库根目录）：

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

`[dev]` 里带了 pytest / pytest-asyncio / pytest-cov / ruff / pyright。
用例全在 `tests/` 下，配置在 `pyproject.toml` 的 `[tool.pytest.ini_options]`，
写测试的规矩和 fixture 一览看 `tests/README.md`。三条硬要求：
**不许联网**（有个 autouse 的 fixture 拦着，真出网直接抛异常）、
只许往 `tmp_path` 写、不许依赖真实时钟和没播种的 random。

测试 `scripts/migrate_redis_to_json.py` 的用例需要 `redis` 这个可选依赖。
不装的话它们会 SKIP（`pytest.importorskip`），不会让套件变红；想让它们真跑起来：

```bash
python -m pip install -e ".[dev,migrate]"   # 安装迁移测试需要的 redis
python -m pytest --cov=xiaozu_bot --cov-report=term-missing   # 看覆盖率
```

push 到 `main`、开 PR、以及手动触发时，`.github/workflows/ci.yml` 会在
ubuntu-latest 上测试 Python 3.10 和 3.13；每周定时任务还会补跑 3.11 和 3.12：

- **ruff check** —— 阻塞合并的门禁。存量违规已经清零，CI 失败表示当前改动引入了问题。
- **干净检出守卫** —— 插件在 import 期就会建出 `data/storage.json`，
  这一步确认全新检出上新生成的文件都被 `.gitignore` 覆盖了。
- **全量测试 + 覆盖率** —— 覆盖率在 CI 日志中以 `term-missing` 输出，同时生成 `coverage.xml`。
- **测试跑完工作区还得是干净的** —— 有用例往仓库里写东西就红。

CI 上不装 `[tts]`（mlx-audio 只有 Apple Silicon 的包，Linux runner 装不上），
也不跑 `ruff format --check` 或 pyright；开发依赖仍然保留 pyright，方便本地按需使用。

## 新克隆之后还缺什么

出图用的字体和素材（`xiaozu_bot/plugins/gdlevelsearch/resources/`：
`PUSAB.TTF`、`ARIAL.TTF`、背景图、难度图标、tiers 和 skillsets，共 114 个文件、约 4 MB）
已经在仓库里，clone 后无需另行下载。

真正需要自己补的只有下面这些，它们都在 `.gitignore` 里：

**`xiaozu_bot/plugins/gdlevelsearch/data/*.json`** —— 关卡数据缓存，
跑一次 `python scripts/run_updater.py` 就有了（见下）。没有这些 json 的话
搜索不会报错，只是什么都搜不到。

**`xiaozu_bot/plugins/guess/data/`** —— 猜图题库（各地图目录下的截图）。
题库不在仓库里，也不在 `guess/dist.zip` 里；后者只是旧的 Windows 打包产物，包含
`setup.txt` / `icon.ico` / `main.exe`。题库需要自行补进 `data/<地图目录>/`。
没有题库时 `*guess_start` 会回一句「题库是空的」就结束（`_pick_random_shot`
无放回地把 122 条地图全试一遍才下这个结论），不会像以前那样死循环。

**`xiaozu_bot/plugins/zhua/data/`** —— 抓图图库，没有的话 `*zhua` 抓不到东西。

这些都不影响跑测试：`tests/` 里所有用到它们的用例都把目录指向了
pytest 的 `tmp_path`，干净 checkout 上照样全绿。

## 数据更新

`gdlevelsearch` 的关卡数据来自 `xiaozu_bot/plugins/gdlevelsearch/data/*.json`，
由 `updater/` 下的十二个抓取任务生成：

- 每天凌晨 3 点自动跑一次（`updater/__init__.py` 里注册的定时任务）
- 超级用户可以发 `*gdsearch_update` 手动触发
- 超级用户可以发 `*gdsearch_store_update` 立即更新 GDDL 本地缓存
- GDDL 快照写入 `data/gddl_levels.json`；它是 best-effort 任务，远端失败会保留旧快照，不阻塞其他数据源发布

两条路径跑完都会调 `reload_all()` 把新数据读进内存，**不需要重启 bot**。
各个 api 模块是在 import 时把数据读进模块级 list/dict 的，
所以少了这一步的话，抓回来的新 json 要等下次重启才生效。

### 分层并发 + 跑完才发布

任务按依赖分成两层，同一层并发跑（都是网络 IO，丢线程池里）：

```
第 1 层： nlw  ids  lw  hds  idl  lists  tpl  pemonlist  platdiff  sfh
第 2 层： platbatch（要 tpl/pemonlist/platdiff）  getmetadata（要 nlw/ids/lw/hds）
```

抓下来的东西先写进 `data/.staging/`，**全部任务成功后才原子地搬进 `data/`**。

这一步是必须的：`nlw`/`ids`/`lw`/`hds` 抓下来的数据是不带 metadata 的，
要等最后 `getmetadata` 回填。以前是直接写进 `data/`，中间任何一步失败
（runner 默认遇错即停）都会让 bot 读到缺 metadata 的半成品，把好数据冲掉。
现在任一任务失败就不发布，`data/` 保持上一次的样子，中间产物留在 `.staging/` 方便排查。
GDDL 不在这个流水线里：它作为独立后台任务与主更新同时启动，失败只上报管理员，
不影响主流水线。

`gddlapi.py` 查询时优先访问 GDDL 在线接口；在线请求失败后，按关卡 ID、名称或搜索条件回退到
`data/gddl_levels.json` 快照，因此 GDDL 暂时不可用时已有数据仍可查询。

## 关键配置与外部服务

- 本地存储：`JsonRedis`，各插件的 `data/storage.json`（`jrrp`, `zhua`, `guess`, `gdlevelsearch` 使用）。这些文件都在 `.gitignore` 里。
- 本地 LLM：示例默认 `http://127.0.0.1:1234`，可替换为你的模型服务地址。
- 消息适配：`bot.py` 注册 OneBot V11 和 QQ 适配器，图片、音频等媒体发送由 `xiaozu_bot/utils/adapter_compat.py` 统一兼容。
- TTS：`say` 插件使用 `mlx_audio` 模型，需预先准备模型与依赖。
- API Key / 凭据：请不要在仓库中明文存放密钥，使用环境变量或配置文件。

## 项目结构（重要路径）

- 插件目录：xiaozu_bot/plugins
- GD 关卡缓存：xiaozu_bot/plugins/gdlevelsearch/data
- 猜图题库：xiaozu_bot/plugins/guess/data（含多级题库）
- 抓图资源：xiaozu_bot/plugins/zhua/data
- 临时音频：当前工作目录（`say` 插件生成音频并发送后会删除文件）

## 插件开发建议

- 在 `xiaozu_bot/plugins/<your_plugin>` 下创建插件，导出 `__plugin_meta__` 与 `Config`。
- 使用 `on_command` / `on_message` 等 NoneBot API 注册事件。
- 开发时建议使用 `nb run --reload` 热重载测试。
- 将敏感信息放入 `.env` 或配置文件，并在 README 或 `.env.example` 中说明。

## 常用命令示例（视前缀设置而定）

- `ai <文本>` — 与本地 LLM 对话（插件 `ai`）
- `say <文本>` — 文本转语音并发送（插件 `say`）
- `gdsearch <id/name>` — 查询 GD 关卡，数据来自本地收录的榜单（插件 `gdlevelsearch`）
- `gdfullsearch <关键词> [-a] [-d [难度]] [-u <难度>]` — 直连 GD 服务器搜索，能搜到服务器上的任意关卡；
  默认只搜 rated，`-a` 连未评级的一起搜，`-d` 只搜 demon，`-u` 只搜非 demon。
  结果分页显示，输入序号选中、`n` 下一页、`p` 上一页、`结束` 取消
- `gdratings <关卡名或id> [-s <排序>] [-asc] [-v]` — 看这关在 GDDL 上每个人提交的 tier / enjoyment，
  分页显示（`n` / `p` / `结束`）。`-s` 可选 tier / enj / date / progress / attempts / rr，
  `-asc` 正序，`-v` 只看通关的人
- `guess_start` / `guess_giveup` — 猜图小游戏（插件 `guess`）
- `zhua` — 随机抓图（插件 `zhua`）
- `jrrp` — 今日人品查询（插件 `jrrp`）

## 注意事项

- 部分功能依赖外部服务（本地 LLM、Geometry Dash 数据源等），请确认环境可用。
- 仓库中可能包含示例/调试用的硬编码值，生产部署前请替换或移除敏感信息。

## 贡献与反馈

欢迎通过 Issue 或 Pull Request 提交改进建议。提交前请运行测试和 `ruff check .`，并确认工作区没有生成的缓存、运行时数据或敏感信息。
