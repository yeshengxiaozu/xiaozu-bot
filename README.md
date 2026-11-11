# xiaozu-bot

[![CI](https://github.com/yeshengxiaozu/xiaozu-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/yeshengxiaozu/xiaozu-bot/actions/workflows/ci.yml)

跑在 [NoneBot 2](https://nonebot.dev) 上的个人 QQ 机器人。核心是 Geometry Dash 关卡检索，
其余是群里用得上的小玩具：AI 聊天、语音、猜图、抓图、人品。

> 这是给自己挂着用的，不少逻辑（榜单来源、出图样式）只针对 Geometry Dash。
> 想拿它当 NoneBot 插件样例看也行，除了 `gdlevelsearch` 之外插件都不长。

## 功能

- **GD 关卡检索（`gdlevelsearch`）**：把 AREDL / GDDL / NLW / IDS / LW / HDS / plat chart /
  pointercrate（rated+）几个榜单合一建索引，本地查不到就直连 GD 官方服务器兜底，结果渲染成
  一张图发出去；缓存落盘、每天自动更新。
- **AI 聊天（`ai`）**：接本地 OpenAI 兼容接口（默认 `http://127.0.0.1:1234`），多轮上下文。
- **语音（`say`）**：`mlx_audio` 合成语音，只支持 Apple Silicon。
- **猜图（`guess`）**、**抓图（`zhua`）**、**每日人品（`jrrp`）**、**杂项（`joy`）**、
  **帮助（`xiaozubot_help`）**。
- `roulette` 只剩 `*map`（随机来张地图）和 `*random`（从给的选项里挑一个）；
  轮盘和蓝莓经济已经删了。

## 安装

要求 Python 3.10+。

```bash
python -m pip install -e .
# 可选
python -m pip install -e ".[tts]"      # say 用的 mlx_audio，仅 Apple Silicon
python -m pip install -e ".[migrate]"  # 只有从旧 Redis 迁数据时才要
```

不再依赖 Redis——持久化改用 `JsonRedis`（`xiaozu_bot/utils/json_storage.py`），数据落在
各插件的 `data/storage.json`。

## 跑起来

```bash
nb run --reload
```

## 常用命令

命令前缀按你的配置来。

| 命令 | 作用 |
| --- | --- |
| `ai <文本>` | 和本地 LLM 聊 |
| `say <文本>` | 文本转语音发出去 |
| `gdsearch <id/name>` | 查关卡（本地榜单 + GD 官方服务器） |
| `gdfullsearch <关键词> [-a] [-d 难度] [-u 难度]` | 直连 GD 服务器搜；`-a` 连未评级，`-d` 只搜 demon，`-u` 只搜非 demon |
| `gdratings <名或id> [-s 排序] [-asc] [-v]` | 看这关在 GDDL 上各人提交的 tier / enjoyment |
| `guess_start` / `guess_giveup` | 猜图小游戏 |
| `zhua` | 随机抓图 |
| `jrrp` | 今日人品 |

`gdfullsearch` / `gdratings` 结果分页：输序号选中、`n` 下一页、`p` 上一页、`结束` 取消。

## 配置与外部依赖

- 本地 LLM 地址、各插件开关走环境变量 / 配置文件；密钥别明文提交。
- OneBot V11 与 QQ 两个适配器都注册了，图片、音频等媒体发送统一走
  `xiaozu_bot/utils/adapter_compat.py`。
- 出图依赖 `nonebot_plugin_htmlkit`。

## 目录

- 插件：`xiaozu_bot/plugins`
- GD 关卡缓存：`xiaozu_bot/plugins/gdlevelsearch/data`
- 猜图题库 / 抓图图库：`guess/data`、`zhua/data`（需自备，已 gitignore）
- 临时音频：当前工作目录（`say` 发完即删）

## 开发 / 贡献

- 测试与 CI 怎么查、改什么会红几个用例，看 [MAINTAINING.md](MAINTAINING.md)。
- 项目结构、脚本用法、数据持久化约定，以及 GD 关卡缓存怎么抓怎么发布，看
  [AGENTS.md](AGENTS.md)（其中的「数据更新流程」一节）。
- 新克隆之后：跑一次 `python scripts/run_updater.py` 生成关卡缓存；`guess` / `zhua` 的
  图库要自己放（仓库里没有，也不影响跑测试）。
- 提 PR 前跑 `python -m pytest` 和 `ruff check .`，确认没带上缓存、`data/` 下的运行时文件
  或密钥。

## License

[MIT](LICENSE.md)
