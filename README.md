# xiaozu-bot

xiaozu-bot 是一个基于 NoneBot 的模块化机器人仓库示例，包含若干实用与娱乐插件（AI 聊天、TTS、Geometry Dash 关卡检索、猜图小游戏、轮盘/经济、人品、抓图等）。项目以插件化方式组织，适合游戏社群、兴趣群、以及插件开发测试场景。

## 主要功能

- **AI 聊天（`ai`）**：对接本地 LLM（LM Studio 风格 API），支持多轮上下文对话与简单会话管理。
- **语音合成 / TTS（`say`）**：使用 `mlx_audio` 生成语音并通过本地转发接口发送群/私聊语音消息。
- **Geometry Dash 关卡检索（`gdlevelsearch`）**：整合 AREDL、GDDL、NLW、IDS、LW、HDS 等数据源并提供本地缓存以加速查询。
- **猜图 / 猜关卡（`guess`）**：带题库与图片资源的互动小游戏。
- **抓图 / 表情包（`zhua`）**：从本地图库随机/指定发送图片与描述。
- **轮盘 / 奖池（`roulette` + `zhua_api`）**：示例性抽奖与虚拟货币交互逻辑（可扩展）。
- **每日人品（`jrrp`）**：每日一次的人品查询，使用 Redis 存储结果。
- **图片/文本渲染（`imageinfo`）**：将 HTML/Markdown/文本渲染为图片并发送（依赖 `nonebot_plugin_htmlkit`）。
- **帮助命令（`xiaozubot_help`）**：内置简单帮助信息。

## 适用场景

- 游戏社区（例如 Geometry Dash 玩家群）：快速查询关卡、分享讨论、举办小游戏。
- 群聊娱乐：猜图、轮盘、抓图、人品等互动功能活跃群气氛。
- 语音互动与直播辅助：将文本转语音推送到群中或语音通道。
- 本地私有化 AI：在本地部署 LLM，为群组或个人提供私有化问答服务。
- 插件开发与学习：仓库提供插件示例，便于学习 NoneBot 插件开发模式。

## 快速开始

要求：Python 3.10+。可选但推荐：Redis（部分插件依赖）、本地 LLM（若使用 AI）、本地消息转发/桥接服务（示例中使用 `http://localhost:3000`）。

安装依赖（示例）：

```bash
python -m pip install -U pip
python -m pip install "nonebot2[fastapi,httpx,websockets]>=2.5.0" nonebot-adapter-onebot nonebot-plugin-apscheduler nonebot-plugin-localstore nonebot-plugin-htmlkit requests httpx redis
# 或使用 Poetry：
# poetry install
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

## 关键配置与外部服务

- Redis：默认 `localhost:6379`（`jrrp`, `roulette`, `zhua` 等使用）。
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
- `gdsearch <id/name>` — 查询 GD 关卡（插件 `gdlevelsearch`）
- `guess start` / `guess giveup` — 猜图小游戏（插件 `guess`）
- `zhua` — 随机抓图（插件 `zhua`）
- `jrrp` — 今日人品查询（插件 `jrrp`）
- `roulette` / `roulette_pool` — 抽奖与奖池（插件 `roulette`）

## 注意事项

- 部分功能依赖外部服务（Redis、LM、消息桥接等），请确认环境可用。
- 仓库中可能包含示例/调试用的硬编码值，生产部署前请替换或移除敏感信息。

## 贡献与反馈

欢迎通过 Issue 或 Pull Request 提交改进建议。需要我为你生成 `.env.example`、Docker Compose 或英文版 README 吗？

