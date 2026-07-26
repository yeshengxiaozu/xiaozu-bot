# xiaozu-bot

[![CI](https://github.com/yeshengxiaozu/xiaozu-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/yeshengxiaozu/xiaozu-bot/actions/workflows/ci.yml)

xiaozu-bot 是一个基于 NoneBot 的模块化机器人仓库示例，包含若干实用与娱乐插件（AI 聊天、TTS、Geometry Dash 关卡检索、猜图小游戏、人品、抓图等）。项目以插件化方式组织，适合游戏社群、兴趣群、以及插件开发测试场景。

## 主要功能

- **AI 聊天（`ai`）**：对接本地 LLM（LM Studio 风格 API），支持多轮上下文对话与简单会话管理。
- **语音合成 / TTS（`say`）**：使用 `mlx_audio` 生成语音并通过本地转发接口发送群/私聊语音消息。
- **Geometry Dash 关卡检索（`gdlevelsearch`）**：整合 AREDL、GDDL、NLW、IDS、LW、HDS、plat chart 等数据源，结果渲染成一张图片发出；带本地缓存和每日自动更新（见下面「数据更新」）。
- **猜图 / 猜关卡（`guess`）**：带题库与图片资源的互动小游戏。
- **抓图 / 表情包（`zhua`）**：从本地图库随机/指定发送图片与描述。
- **随机小工具（`roulette`）**：`*map` 随机来张地图、`*random` 在给的选项里随机挑一个。轮盘和蓝莓经济已下线。
- **每日人品（`jrrp`）**：每日一次的人品查询。
- **对战小游戏（`game`）**：群内 bet / 身份 / 膀胱等模式的回合制小游戏，限白名单群。
- **娱乐指令（`joy`）**：杂七杂八的小指令合集。
- **图片/文本渲染（`imageinfo`）**：将 HTML/Markdown/文本渲染为图片并发送（依赖 `nonebot_plugin_htmlkit`）。
- **帮助命令（`xiaozubot_help`）**：内置简单帮助信息。

## 适用场景

- 游戏社区（例如 Geometry Dash 玩家群）：快速查询关卡、分享讨论、举办小游戏。
- 群聊娱乐：猜图、抓图、人品、恶魔轮盘等互动功能活跃群气氛。
- 语音互动与直播辅助：将文本转语音推送到群中或语音通道。
- 本地私有化 AI：在本地部署 LLM，为群组或个人提供私有化问答服务。
- 插件开发与学习：仓库提供插件示例，便于学习 NoneBot 插件开发模式。

## 快速开始

要求：Python 3.10+。可选：本地 LLM（若使用 AI）、本地消息转发/桥接服务（示例中使用 `http://localhost:3000`）。

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

## 改完怎么验（不用重启 bot）

`scripts/` 下有几个能单独跑的入口，都不需要连 QQ：

```bash
python bot.py
```

只是想确认插件都能正常加载的话，这个就够了 —— 它会把所有插件load 一遍并打日志，
看到 `Succeeded to load plugin` 就可以 Ctrl-C。import 错误、少装依赖、
注册失败都能在这一步暴露出来。

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

这两个脚本靠 `scripts/_bootstrap.py` 绕开了插件包的 `__init__.py`，
所以在没装 onebot 适配器 / htmlkit 的开发机上也能跑。

数据路径现在都是相对文件本身算的，从哪个目录启动都行。

## 测试与 CI

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

有 8 个用例是测 `scripts/migrate_redis_to_json.py` 的，要 `redis` 这个可选依赖。
不装的话它们会 SKIP（`pytest.importorskip`），不会让套件变红；想让它们真跑起来：

```bash
python -m pip install -e ".[dev,migrate]"   # 想让这 8 个也跑起来
python -m pytest --cov=xiaozu_bot --cov-report=term-missing   # 看覆盖率
```

push 到 `main`、开 PR、以及手动触发时，`.github/workflows/ci.yml` 会在
ubuntu-latest 上按 Python 3.10 / 3.11 / 3.12 各跑一遍：

- **ruff check** —— **暂时不阻塞**。存量违规还没清（4600+ 条，全是历史遗留），
  结果只以 annotation 的形式贴在 PR 的 diff 上；清干净之后再把它变成门禁。
- **干净检出守卫** —— 插件在 import 期就会建出 `data/storage.json`，
  这一步确认全新检出上新生成的文件都被 `.gitignore` 覆盖了。
- **全量测试 + 覆盖率** —— 报告以 artifact 上传（`coverage-py3.x`，留 14 天）。
- **测试跑完工作区还得是干净的** —— 有用例往仓库里写东西就红。

CI 上不装 `[tts]`（mlx-audio 只有 Apple Silicon 的包，Linux runner 装不上），
也没跑 pyright —— 仓库基本没写类型标注，等标注补得差不多了再单独加一步。

## 新克隆之后还缺什么

出图用的字体和素材（`xiaozu_bot/plugins/gdlevelsearch/resources/`：
`PUSAB.TTF`、`ARIAL.TTF`、`left_bg.png`、`right_bg.png`、`noThumb.png`、
`moon.png`、`diffIcon/`、`tiers/`、`skillsets/`，一共 114 个文件、4 MB 左右）
**在仓库里**，clone 下来就是全的，不用另外找。
（这段以前写的是「二进制在 7b2f1bb 被清出仓库了，要手动放进去」——
那个说法已经过时，binary 后来又补回来了。）

真正需要自己补的只有下面这些，它们都在 `.gitignore` 里：

**`xiaozu_bot/plugins/gdlevelsearch/data/*.json`** —— 关卡数据缓存，
跑一次 `python scripts/run_updater.py` 就有了（见下）。没有这些 json 的话
搜索不会报错，只是什么都搜不到。

**`xiaozu_bot/plugins/guess/data/`** —— 猜图题库，打包在
`xiaozu_bot/plugins/guess/dist.zip` 里，解开就行。
⚠️ 题库为空时 `*guess_start` 会**死循环**（`while not file_names`
里没有退出条件），务必先解压再用。

**`xiaozu_bot/plugins/zhua/data/`** —— 抓图图库，没有的话 `*zhua` 抓不到东西。

这些都不影响跑测试：`tests/` 里所有用到它们的用例都把目录指向了
pytest 的 `tmp_path`，干净 checkout 上照样全绿。

## 数据更新

`gdlevelsearch` 的关卡数据来自 `xiaozu_bot/plugins/gdlevelsearch/data/*.json`，
由 `updater/` 下的十个抓取任务生成：

- 每天凌晨 3 点自动跑一次（`updater/__init__.py` 里注册的定时任务）
- 超级用户可以发 `*gdsearch_update` 手动触发

两条路径跑完都会调 `reload_all()` 把新数据读进内存，**不需要重启 bot**。
各个 api 模块是在 import 时把数据读进模块级 list/dict 的，
所以少了这一步的话，抓回来的新 json 要等下次重启才生效。

### 分层并发 + 跑完才发布

任务按依赖分成两层，同一层并发跑（都是网络 IO，丢线程池里）：

```
第 1 层： nlw  ids  lw  hds  platdiff  platrank  platdata  sfh
第 2 层： platbatch（要上面三个 plat 文件）  getmetadata（要上面四个 levels 文件）
```

抓下来的东西先写进 `data/.staging/`，**整条流水线全绿了才原子地搬进 `data/`**。

这一步是必须的：`nlw`/`ids`/`lw`/`hds` 抓下来的数据是不带 metadata 的，
要等最后 `getmetadata` 回填。以前是直接写进 `data/`，中间任何一步失败
（runner 默认遇错即停）都会让 bot 读到缺 metadata 的半成品，把好数据冲掉。
现在失败就不发布，`data/` 保持上一次的样子，中间产物留在 `.staging/` 方便排查。

## 关键配置与外部服务

- 本地存储：`JsonRedis`，各插件的 `data/storage.json`（`jrrp`, `zhua`, `game`, `guess`, `gdlevelsearch` 使用）。这些文件都在 `.gitignore` 里。
- 本地 LLM：示例默认 `http://127.0.0.1:1234`，可替换为你的模型服务地址。
- 本地消息转发：示例使用 `http://localhost:3000` 作为本地转发/桥接端点（用于发送音频、转发命令结果等）。
- TTS：`say` 插件使用 `mlx_audio` 模型，需预先准备模型与依赖。
- API Key / 凭据：请不要在仓库中明文存放密钥，使用环境变量或配置文件。

## 项目结构（重要路径）

- 插件目录：xiaozu_bot/plugins
- GD 关卡缓存：xiaozu_bot/plugins/gdlevelsearch/data
- 猜图题库：xiaozu_bot/plugins/guess/data（含多级题库）
- 抓图资源：xiaozu_bot/plugins/zhua/data
- 临时音频：temp/audios 或运行目录（`say` 插件会在工作目录生成临时音频文件）

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
- `guess start` / `guess giveup` — 猜图小游戏（插件 `guess`）
- `zhua` — 随机抓图（插件 `zhua`）
- `jrrp` — 今日人品查询（插件 `jrrp`）

## 注意事项

- 部分功能依赖外部服务（LM、消息桥接等），请确认环境可用。
- 仓库中可能包含示例/调试用的硬编码值，生产部署前请替换或移除敏感信息。

## 贡献与反馈

欢迎通过 Issue 或 Pull Request 提交改进建议。需要我为你生成 `.env.example`、Docker Compose 或英文版 README 吗？

