# xiaozu-bot 代码审计报告

- **审计日期**：2026-10-30
- **仓库**：`xiaozu-bot`（NoneBot 2 机器人，Python 3.10+）
- **审计范围**：`xiaozu_bot/`（78 个 .py，约 6,500 行）、`scripts/`、`tests/`、构建与 lint 配置
- **审计方法**：全量静态代码审查 + 静态检查工具（`ruff check .`）+ 子系统专项深度审查
- **代码基线**：`main` @ `af7a546`，工作区干净

---

## 一、总体评估

### 1.1 结论摘要

| 维度 | 评级 | 说明 |
|---|---|---|
| **安全** | ⚠️ **差** | 2 个真实密钥硬编码入库；存在"证书校验静默降级"的隐患模式 |
| **性能** | ⚠️ **中等偏下** | 事件循环被同步 I/O（图像渲染/PNG 编码/JSON 落盘）大面积阻塞 |
| **正确性** | ⚠️ **中等** | 多处 check-then-act 竞态、异常吞没、`CancelledError` 逃逸 |
| **可维护性** | ✅ **良好** | 分层清晰、注释质量高、测试量大（1387 个用例）、lint 全绿 |
| **工程规范** | ✅ **优秀** | `pyproject.toml` 的取舍注释、`.gitignore`、`AGENTS.md` 水准很高 |

**总体**：这是一个**工程质量上游水平、但安全与并发模型有明显欠账**的项目。
`pyproject.toml` 里对每条 lint 规则的取舍都写了理由、`MAINTAINING.md` 完整、注释解释了
"为什么"而不是"是什么"——这类素养在同类项目里少见。测试覆盖到每一个生产模块，
1387 个用例，`ruff check .` 零违规。

问题集中在两处：**(1) 凭据管理缺失**——两个第三方 API 密钥被写进源码并已提交进
Git 历史；**(2) 异步模型执行不彻底**——项目正确地引入了 `asyncio.to_thread`，
但只在部分路径使用，最重的那些 CPU/IO 工作（Pillow 出图、PNG 编码、JSON fsync）
反而留在了事件循环上。

### 1.2 统计

| 指标 | 数值 |
|---|---|
| 生产代码行数 | ~6,500（`xiaozu_bot/`）+ 1,121（`scripts/`） |
| 测试代码行数 | ~21,994（1387 个用例） |
| 测试/生产比 | ≈ 2.9 : 1 |
| `ruff check .` | ✅ All checks passed（门禁生效） |
| `pytest` | ⚠️ 本次环境未能执行（venv 为 3.12、可用解释器 3.13，ABI 不兼容） |
| 发现的问题 | **52 项**：严重 2 / 高 15 / 中 20 / 低 15 |

> **关于测试**：审计环境缺少可用的 3.12 解释器（`.venv` 的 `python.exe` 指向已被删除的
> Codex 运行时路径），因此未能实测跑通。结论均基于静态审查，`ruff` 门禁已验证通过。
> 建议在有 3.12 的环境补跑一次 `pytest` 作为交叉验证。

---

## 二、问题清单

> 编号规则：`C`=严重 / `H`=高 / `M`=中 / `L`=低。
> 行号基于 `main @ af7a546`。

### 2.1 🔴 严重（Critical）—— 必须立即处理

#### C-1 GDDL API 密钥硬编码在源码中，已提交进版本库

- **位置**：`xiaozu_bot/plugins/gdlevelsearch/api/gddlapi.py:22`
  ```python
  apikey = "3244ce47ed4cf932ec348d68cdf72496de68ee48a2846044db906baa28a7cf7d"
  ```
  该值在 `gddlapi.py:283 / 332 / 352 / 381 / 427 / 471` 六处拼入 `Authorization` 头。
- **根本原因**：配置外置化从未建立。项目有 `.env` 机制（NoneBot 标准）但插件层没有
  使用它，密钥被当作"常量"直接写死。
- **影响**：Git 历史永久保留该密钥（`.gitignore` 只忽略了 `.env`，不忽略源码）。
  任何有仓库读权限的人都能取出并盗用配额；且密钥无法按环境区分、无法轮换，
  一旦吊销则全站 GDDL 功能失效。
- **修复建议**：
  1. **立即** 到 gdladder 后台吊销并重新签发该密钥（假设它已泄露）。
  2. 改为 `apikey = os.getenv("GDDL_API_KEY")`；缺失时**明确报错**而非静默降级。
  3. 在 `.env.example` 登记该变量，README 补一句说明。
  4. 用 `git log -p` 确认是否还有其他历史密钥，必要时 `git filter-repo` 清理。
  5. 加 pre-commit 密钥扫描（`gitleaks` / `detect-secrets`）。

#### C-2 Google Sheets API Key 硬编码，且被设计为"环境变量缺失时的静默回退"

- **位置**：`xiaozu_bot/plugins/gdlevelsearch/updater/jobs/googlesheetapi.py:81-84`
  ```python
  api_key = os.getenv("GOOGLE_SHEETS_API_KEY")
  if not api_key:
      api_key = "AIzaSyALLzmrnlwXvymi4OdzysGS3rM77_0Qo2E"
  ```
- **根本原因**：同 C-1，但危害更大——**回退分支把"配置缺失"这个故障信号吞掉了**。
  原本"没配 key 就报错"能立刻暴露运维问题，现在会静默使用一个共享 key，
  配额被盗用也不会有任何告警。
- **影响**：Google API key 可用于调用该项目绑定的所有 Sheets 配额，且通常还关联
  Google Cloud 项目的计费。明文入库，同样无法轮换。
- **修复建议**：
  1. 立即到 Google Cloud Console 吊销该 key 并限制 HTTP referrer / IP。
  2. 删除回退分支，改为：
     ```python
     api_key = os.getenv("GOOGLE_SHEETS_API_KEY")
     if not api_key:
         raise RuntimeError("GOOGLE_SHEETS_API_KEY is not configured")
     ```
  3. 同 C-1 的 3/4/5 步。

---

### 2.2 🟠 高（High）—— 应在下个迭代修复

#### H-1 `certifi` 缺失时静默关闭 TLS 证书校验

- **位置**：`xiaozu_bot/plugins/gdlevelsearch/updater/jobs/metadata.py:28-48`
  ```python
  except ImportError:
      CA_BUNDLE = None
      logger.warning("certifi 未安装，将禁用 SSL 验证（不安全）")
      urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
  ...
  DEFAULT_VERIFY = CA_BUNDLE or False          # ← 危险
  http = RequestSession(verify=DEFAULT_VERIFY, trust_env=False)
  ```
- **根本原因**：把"依赖缺失"降级成了"安全降级"。`or False` 让 `None` 变成
  `verify=False`，同时 `disable_warnings` 主动关掉了唯一的可见告警。
- **影响**：一旦 `certifi` 装漏或被打包工具裁剪，所有对
  `history.geometrydash.eu` 的请求变成可 MITM 的明文通道，且运维无从察觉。
- **修复建议**：删掉 `or False`。`certifi` 缺失时直接 `raise`，不要用不安全的方式
  让程序"继续能跑"。`verify` 只能是 `True` 或明确的 CA 路径。

#### H-2 在 import 期全局 monkeypatch `ssl._create_default_https_context`

- **位置**：`xiaozu_bot/plugins/gdlevelsearch/updater/__init__.py:40-61`（模块导入时执行）
  ```python
  ssl._create_default_https_context = functools.partial(
      ssl.create_default_context, cafile=certifi.where(),
  )
  ```
- **根本原因**：为了修一个插件的证书问题，改了 **CPython 的私有全局哨兵**，
  影响进程内所有库（NoneBot 适配器、QQ 适配器、其他插件）的默认 TLS 上下文。
  且发生在 import 期而非 startup 期，无法撤销、无法按插件隔离。
- **影响**：一旦 `certifi` 路径异常，全网 TLS 一起坏；排障时极难定位到是这个 patch。
- **修复建议**：删掉这个 monkeypatch。`api/http.py` 的 `_new_sync_client()` **已经**
  支持 `verify` 参数，只对需要的 client 传 `verify=certifi.where()` 即可。
  同时把 `setup_updater()` 挪到 `on_startup`。

#### H-3 Pillow 全图渲染 + PNG 编码阻塞事件循环（影响 7+ 个命令）

- **位置**：
  - `render/draw.py:900-902` —— `create_image_from_gdlevel` 是 `async def` 但
    内部的 `create_level_image(...)` 是**同步调用**，未走 `to_thread`
  - `services/search.py:288-293` —— `send_result` 里 `image.save(buffer, format="PNG")`
    同样是同步
- **根本原因**：项目**已经**意识到这个问题（`commands/search.py:61,71` 用了
  `asyncio.to_thread`），但只在"取数据"这一步用了，最重的"渲染 + 编码"没有。
  `create_level_image` 在 1280×720 上做多次 `alpha_composite`、`GaussianBlur`、
  LANCZOS resize，外加 `create_vertical_gradient`（`draw.py:174-189`）逐行 720 次
  `draw.line` 的纯 Python 循环；PNG 编码再叠加数百毫秒。
- **影响**：出图期间（数百 ms ~ 数秒）**整个 bot 停摆**——心跳、收包、其他 matcher
  全部无响应。受影响入口：
  `search.py:67,78,133`、`fullsearch.py:333,372`、`random.py:103`、
  `plat_random.py:41`、`dailydemon.py:168`、`gdicon.py:69,86`。
- **修复建议**：
  ```python
  async def create_image_from_gdlevel(level_id: int) -> Image.Image:
      fetched = await _fetch_all_data(level_id)
      return await asyncio.to_thread(
          create_level_image, _build_render_data(fetched)
      )

  # send_result 中
  await asyncio.to_thread(image.save, buffer, format="PNG")
  ```

#### H-4 `_collect_result` 漏掉 `CancelledError`，异常会穿透到用户

- **位置**：`services/search.py:96-101`（定义）、`158`（`future.cancel()`）、`161-163`（回收）
  ```python
  def _collect_result(future, source, name):
      try:
          return future.result()
      except Exception as exc:      # ← CancelledError 继承 BaseException，抓不到
  ```
  ```python
  for source, future in futures.items():
      if source not in values and future.done():   # 被 cancel 的 future done() 也是 True
          values[source] = _collect_result(future, source, name)
  ```
- **根本原因**：Python 3.8+ 起 `concurrent.futures.CancelledError` 就是
  `asyncio.CancelledError`，继承 `BaseException`。`as_completed` 超时后对仍
  PENDING 的 future 调 `cancel()`，随后 `future.result()` 抛出，直接穿透
  `search_by_name` → `to_thread` → `handle_gdsearch`（`commands/search.py:71`
  没有任何 except）。
- **影响**：并发搜索或线程池被上一次慢请求占满时，用户看到未处理异常。
- **修复建议**：`except BaseException` 并显式判 `CancelledError` 返回 `None`；
  或在 162 行加 `future.cancelled()` 排除。同项目 `render/draw.py:258-259`
  已正确处理过这个陷阱，可直接参照。同时在 `handle_gdsearch` 加兜底 except。

#### H-5 五个模块级内存索引"清空+重建"，与并发读线程存在数据竞争

- **位置**：
  - `api/gddl_store.py:31-34` + `171-188`（`_rebuild_indexes` 内 `clear()` 后逐条 append）
  - `api/nlwapi.py:81-90` + `141-165`（`reload()` 原地 clear 四个 list + 四个 dict）
  - `api/ratedplusapi.py:56-58` + `88-93`
  - `api/unratedlevels.py:36-38` + `68-83`
  - `api/aredlapi.py:223-236`
- **根本原因**：写侧是 updater 线程（`updater/__init__.py:221`、
  `manage.py:166` 的 `to_thread(reload_all)`），读侧是搜索线程池
  （`services/search.py:136-140`）与出图线程。重建采用"先 clear 再填"，
  读者必然有窗口看到**空表或半张表**；dict 边迭代边 `clear()` 还会抛
  `RuntimeError`。`listsapi.py:27-28,89-106` 是唯一正确加了
  `threading.Lock` 的模块，其余全部裸奔。
- **影响**：每日数据刷新期间，搜索结果随机为空或不完整——**静默错误，最难排查**。
- **修复建议**：统一改为"先构建新容器 → 一次性原子替换模块全局名"，并对该模块
  `reload()` 加 `threading.Lock`。或全部照 `listsapi._get_lists` 的模式用
  `RLock` 包裹读与重建。

#### H-6 出图时原地修改共享缓存对象，永久污染内存索引

- **位置**：`render/draw.py:866-868`
  ```python
  if plat_info and plat_info.tags:
      plat_info.tags = list(set(plat_info.tags) - {"On TPL", "On Pemonlist"})
  ```
- **根本原因**：`plat_info` 来自 `platapi.getlevelbyid` → `platdata.by_id[...]`
  （`api/platapi.py:191-192`），是**模块级缓存里的同一个 `PlatInfo` 实例**。
  渲染这一"读操作"产生了写副作用。
- **影响**：第一次查询某关卡后，"On TPL / On Pemonlist"标签在本次进程生命周期内
  对**所有用户永久消失**（直到 `reload()`）；且 `list(set(...))` 随机化了标签顺序。
- **修复建议**：改为纯局部变量，不写回：
  ```python
  tags = [t for t in plat_info.tags if t not in {"On TPL", "On Pemonlist"}]
  ```

#### H-7 ID 搜索分支的错误处理形同虚设，服务不可用时仍去渲染废图

- **位置**：`commands/search.py:24`（定义）、`57-68`（使用）
  ```python
  GD_API_UNAVAILABLE_MESSAGE = "GD 关卡服务器暂时无法访问，请稍后重试"  # ← 全仓库仅此一处，从未使用
  try:
      gdlevel = await asyncio.to_thread(getlevelinfo, level_id)
  except GDAPIUnavailable:
      gdlevel = None
  else:
      if gdlevel is None:
          await gdsearch.finish("不存在符合这个id的demon关卡")
  await send_result(bot, event, level_id)   # ← 异常分支也走到这里
  ```
- **根本原因**：`except` 分支把 `gdlevel` 置 `None` 后**跳过了** `else` 里的
  "不存在"判断，直接落到 `send_result`。`try/except/else` 的控制流被误用。
- **影响**：GD 服务器宕机时，用户收到一张 `No info / sorry :(` 的废图
  （`draw.py:851-853`）而不是错误提示。第 24 行常量是死代码。
- **修复建议**：`except GDAPIUnavailable: await gdsearch.finish(GD_API_UNAVAILABLE_MESSAGE)`

#### H-8 `say` 模型懒加载无锁，并发首次调用会重复加载 1.7B 模型

- **位置**：`plugins/say/__init__.py:72-78`
  ```python
  def get_model():
      global _MODEL
      if _MODEL is None:
          _MODEL = load_model("mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16")
      return _MODEL
  ```
- **根本原因**：典型 check-then-act。`get_model()` 在 `to_thread` 的工作线程里被调用
  （181/221），两个用户同时首次触发 `*say` 时都会看到 `_MODEL is None`。
  第 49 行注释"线程安全，因为 mlx 模型在推理时是只读的"推理错误——**只读只保证
  推理安全，不保证初始化安全**。
- **影响**：bf16 的 1.7B 模型被加载两份（数 GB 内存），后写入者覆盖前者，
  前一个线程持有的模型对象被丢弃。
- **修复建议**：加 `threading.Lock()` + 二次检查（双检锁），或在
  `@driver.on_startup` 预加载一次。

#### H-9 `say` TTS 无并发上限、无超时，可拖垮整个 bot

- **位置**：`say/__init__.py:181-183`（`*say`）、`221-223`（`*say_i`），
  配合 `124-158` 的 `sync_generate_audio`
- **根本原因**：每个请求 `to_thread` 跑一次完整推理（长文本按 200 字切块 =
  多次推理 + `wave` 拼接）。默认线程池 `min(32, cpu+4)`，被 TTS 占满后，
  `guess` 的 `to_thread(_crop_and_save)` 也会排队 → **猜图一起卡死**。
- **影响**：重 CPU 任务只做了线程卸载，没有准入控制。多人连发 `*say` 直接 OOM。
- **修复建议**：模块级 `asyncio.Semaphore(1~2)` 串行化 TTS；加
  `asyncio.wait_for(..., timeout=60)`；给推理单独一个
  `ThreadPoolExecutor(max_workers=1)`。

#### H-10 `guess` 在 async 处理器里同步做 Pillow 图像处理

- **位置**：`plugins/guess/__init__.py:378-387`（放弃）与 `448-457`（答对）
  ```python
  image = Image.open(image_path)          # 380 / 450  全尺寸截图
  ImageDraw.Draw(image).rectangle(...)    # 381 / 451
  image.save(cropped_path)                # 387 / 457
  ```
- **根本原因**：同一文件里 `_crop_and_save` 明明正确用了 `asyncio.to_thread`
  （302-304），但答对/放弃这两条路径漏了——**模式不一致**。
- **影响**：处理 1920×1080 级截图（decode + 画框 + 重新编码）几十到几百毫秒
  纯 CPU + MB 级磁盘写，全部跑在事件循环上，期间全 bot 无响应。
- **修复建议**：抽成 `_draw_answer_box(...)` 同步函数，同样 `await asyncio.to_thread(...)`

#### H-11 `guess` 出题存在 check-then-act 竞态，同群两人同时出题会丢题

- **位置**：`guess/__init__.py:246-255`（`can_start` 读 `ANSWER_KEY`）
  → `286-322`（`guessstart` 写状态）
- **根本原因**：`can_start` 判定"当前没题"之后，`guessstart` 先
  `await _pick_random_shot(...)`（295）、再 `await asyncio.to_thread(_crop_and_save)`
  （302-304）——**中间有多次让出事件循环**，且没有原子占位。
- **影响**：两人都通过检查，后完成的覆盖先完成的写 → 群里出现两张题图但只有一个
  答案，先出的那题永远答不出来。
- **修复建议**：把"检查 + 占位"合成原子操作（给 `JsonRedis` 加 `hsetnx`，
  或在 `can_start` 里立刻写占位值），或加 per-session 的 `asyncio.Lock`。

#### H-12 `zhua` 的 `descriptions` 有一个键指向不存在的文件 → `*zhua` 随机崩溃

- **位置**：`plugins/zhua/__init__.py:89`
  ```python
  "禁止卡卒.jpg": "禁止卡卒卡我启动了",   # 磁盘上实际是 禁止卡卒.png
  ```
- **根本原因**：`descriptions` 是手写字典，与目录内容无任何校验、无测试。
- **影响**：`handle_zhua` 在 110 行 `random.choice(list(descriptions.keys()))`，
  约 **1/65 概率**选中它 → 112-120 行 `send_image` 抛 `FileNotFoundError` →
  处理器无 try/except，用户什么也收不到，只留一条错误日志。
  （反向问题：`擒史皇卒.jpg` 存在于磁盘但不在字典，永远抓不到。）
- **修复建议**：改为从 `DATA_DIR.iterdir()` 生成候选列表，`descriptions` 只作为
  可选的"文件名 → 描述"注解；加一条测试断言两者一致。这是**最容易复现的用户可见崩溃**。

#### H-13 `ai` 插件：`finish()` 写在 try 内被 `except Exception` 吞掉 → 重复发消息

- **位置**：`plugins/ai/__init__.py:181`、`214` 的 `await ai_cmd.finish(...)`
  位于 `try` 块内，而 `223-225` 是 `except Exception as e: ... finish(f"请求失败: {e}")`
- **根本原因**：NoneBot 的 `FinishedException` **继承自 `Exception`**
  （`FinishedException → MatcherException → NoneBotException → Exception`）。
  `finish()` 先 `send()` 再 `raise FinishedException`，于是：
  第 181/214 行发出真实错误消息 → 抛 `FinishedException` → 被 223 行捕获 →
  再发一条 `请求失败: `。用户看到两条消息，且真实原因被覆盖。
- **影响**：API 返回非 JSON / 无法解析时，用户收到的永远是"请求失败: "，
  排障信息丢失。
- **修复建议**：把 `finish()` 移出 `try` 块，或先算出 `error_message` 变量、
  在 `try` 之外统一 `finish`。`except` 收窄为 `httpx.HTTPError` / `json.JSONDecodeError`。
  此模式在 `say/__init__.py:180-192` 同样存在（见 M-9）。

#### H-14 `ai` 插件：会话上下文无上限增长 + LLM 调用无限流

- **位置**：`plugins/ai/__init__.py:72`（`context_map = {}`）、`102-105`、
  `157-172`（timeout 60s / 180s）
- **根本原因**：
  1. `context_map` 以 `session_id` 为键**永不删除**（除非管理员手动 `clear`），
     每个用过 `.ai` 的群/频道永久保留一条记录 —— 无界内存增长。
  2. 没有任何频率限制。单次调用最长占用 180 秒，且是 `await` 在 handler 上。
- **影响**：内存泄漏（缓慢但无界）；少数人并发刷 `.ai` 即可让机器人响应变慢。
  另外用户文本直接进入 `messages`，`SYSTEM_PROMPT` 的"不要提及规则"只是软约束，
  存在**提示词注入**风险。
- **修复建议**：给 `context_map` 加 TTL（记录 `last_used`，惰性清理或定时扫描）；
  加 per-user / per-group 的 `asyncio.Semaphore` + 冷却；敏感操作不要依赖
  提示词约束。

#### H-15 搜索会话按 `user_id` 存储，跨群串状态 + 定时任务持有过期引用

- **位置**：`commands/search.py:22-23, 82-85`
  ```python
  search_cache = {}      # key = user_id
  timeout_tasks = {}
  ...
  search_cache[user_id] = results
  timeout_tasks[user_id] = asyncio.create_task(clear_search_cache(bot, event, user_id))
  ```
- **根本原因**：键只有 `user_id`，**不含会话/适配器限定**（同文件其他逻辑已有
  `get_context_id()` 可用）。30 秒任务里还持有 `bot`、`event` 引用。
- **影响**：
  1. 用户在 A 群搜索后到 B 群发数字 → 选中 A 群的结果（跨群串状态）。
  2. 30 秒后任务用**已过期**的 `event` 调 `bot.send` 可能抛异常；
     任务异常无人 `await`，产生 "Task exception was never retrieved"。
  3. shutdown 时任务不取消。
- **修复建议**：键改用 `get_context_id(event)`；新建任务前先 cancel 旧任务；
  给 `clear_search_cache` 加 `contextlib.suppress(Exception)`；
  注册 `on_shutdown` 统一取消。

---

### 2.3 🟡 中（Medium）

| 编号 | 位置 | 问题 | 根因 / 影响 | 修复建议 |
|---|---|---|---|---|
| M-1 | `utils/json_storage.py:101-113` | `JsonRedis._save` 每次写都在事件循环上做全量 `json.dump` + `fsync` | `guess` 出题要 `hset`×3 + `set`×1 = 4 次全量序列化+落盘，同步阻塞；文件越大越慢 | 改 debounce（脏标记 + 后台 `to_thread(_save)`）；或直接复用同文件的 `write_json_atomic` 并放进线程 |
| M-2 | `api/gddl_store.py:283-286` | 固定 `.tmp` 文件名（无 pid/tid/随机数） | 并发刷新（03:00 job / 07:00 job / 手动命令）互相截断临时文件 → `os.replace` 成损坏的正式文件 | 直接改用 `utils.json_storage.write_json_atomic`（已有随机名 + fsync） |
| M-3 | `utils/json_storage.py:22-58` vs `101-113` | `write_json_atomic` 是**死代码**（全仓库无调用点），且与 `_save` 逻辑重复 | 两份实现、两种临时文件命名、两种异常处理 | `_save` 直接调用 `write_json_atomic`，删掉自己的 tmp 逻辑 |
| M-4 | `commands/manage.py:145,148,172,188,194,248-251` | 管理命令在事件循环上同步解析 ~4.9 MB JSON | `handle_gdsearchstatus` 同步 `json.loads` metadata+nlw+ids+lw+hds+aredl+arepl；`publish()` 也是同步 I/O | 全部包 `await asyncio.to_thread(...)`；`_file_count` 只取长度时改流式或缓存 |
| M-5 | `commands/search.py:89-98` → `services/search.py:38-45` | 多结果渲染的 **N+1 HTTP 请求** | `get_difficulty` 对每条缺 difficulty 的结果发一次 GD 请求（15s×3 重试）；10 条同名关卡最坏 ≈ 8 分钟无反馈 | 并发化（复用 `_SEARCH_EXECUTOR` 或 `gather` + `to_thread`），或加 TTL 缓存，或只取前 N 条 |
| M-6 | `render/draw.py:60-74`（调用点 361,362,363,484,550,551） | 字体每次出图都重新 `ImageFont.truetype` 读盘解析 | `ARIAL.TTF` 1,016,724 字节，一张图至少加载 6 次，无任何缓存 | 加 `functools.lru_cache` 的 `_load_font(path, size)` |
| M-7 | `render/iconrender.py:163-176` | 图标图集在 **import 期**解压进内存并永久驻留 | `icons_uhd.zip` 8,078,325 字节 / 2414 条目，阻塞 NoneBot 启动；`ZipFile` 异常无兜底会连带整个插件加载失败 | 改为惰性按需解压单条；或加 `try/except` 降级 + `lru_cache` |
| M-8 | `api/gddl_store.py:246` | `level_id // PAGE_SIZE` 猜页码，判断错误 | 分页是按 **ID 升序排名**切的，不是按 ID 区间；ID 分布稀疏 → `failed_pages` 非空时几乎不保留任何旧记录，部分失败就丢数据 | 记录每页实际抓到的 ID 集合再匹配；或"新快照里没有的旧 ID 全部保留" |
| M-9 | `updater/jobs/gddl.py:19-21` + `runner.py:82,112` | 03:00 的 GDDL 快照**永远发布不到 `data/`**，20 分钟工作被丢弃 | GDDL 作为后台任务（~20 min），主流水线 5 min 跑完就 `publish()`；GDDL 完成后**没有任何** publish 调用者；次日 `clear_staging()` 删掉。且 `reload_after=False` 事后也没 `reload()` | 让 GDDL job 成功后自行 `publish()` + `reload()`；或直写 `DATA_FILE`（与 07:00 job 一致） |
| M-10 | `runner.py:82,112` + `manage.py:193-194` | staging/publish 与并发路径无互斥 | `runner._lock` 只保护 `run_all_async`；手动命令在不持锁的情况下 `publish()`，可能与 03:00 任务互相破坏 | 把 `publish()` 做成 `async def publish_async()` 并复用 `_lock`；`manage.py` 改调它 |
| M-11 | `commands/manage.py:74-76` | 管理员写入 `metadata.json` **非原子** | 直接 `write_text` 覆写 583 KB 累积缓存；中断即截断，而 `load_metadata_cache` 读到坏文件会**静默从空缓存重来** | 改用 `write_json_atomic`；坏文件应备份而非丢弃 |
| M-12 | `plugins/joy/__init__.py:174` | `if event.user_id is not bot.self_id` —— `is` 比较 int 与 str | `PokeNotifyEvent.user_id` 是 `int`，`Bot.self_id` 是 `str`，`is not` 恒为真 → **"别戳自己"的守卫从未生效** | `if str(event.user_id) != str(bot.self_id)`（更严谨应比较 `event.target_id`） |
| M-13 | `plugins/guess/__init__.py:184-187` | 会话键没有适配器限定 | `get_group_id` 在 OneBot 返回数字群号、QQ 返回 openid/channel_id → 两个适配器同时在线时 `"g123456"` 撞键，共用同一道题 | 改用 `get_context_id(event)`（已是现成工具）。注意这是一次性 key 变更，需迁移 storage |
| M-14 | `guess/__init__.py:307-313, 377, 447` | 会话状态与图片文件**无限增长，从不清理** | 结束/放弃只把值改成 `NOTHING`，字段永不删除；`_clean_expired` 只清理顶层带 `_exp` 的键，hash 字段无 TTL。`guess/pictures/` 每次写全尺寸 PNG 从不删，且 `.gitignore` 是空文件 | 结束时 `hdel` 三个字段；加基于时间戳的惰性清理；`pictures/` 发送后 `unlink` 或按 LRU 保留 |
| M-15 | `guess/__init__.py:378-379, 448-449` | `hget` 返回的 `None` 直接解引用 | `hget` 明确返回 `Any | None`；半写入/手工改动 storage 时 `Path(None)` → `TypeError`、`None.split()` → `AttributeError`。两处还用 `pyright: ignore` 压掉了真实的 Optional 错误 | 取值后判空走"题目已失效"分支；移除 `pyright: ignore` |
| M-16 | `plugins/zhua/__init__.py:106` → `121` | 冷却是 check-then-act，中间隔着一次网络 await | 106 行读冷却 → 113-120 `await send_image`（一次网络往返）→ 121 才写冷却。连发两条 `*zhua` 都会通过，10 分钟冷却被绕过 | 把"占位 + 写冷却"提到 `send_image` 之前；失败再 `delete` 回滚 |
| M-17 | `guess/__init__.py:424, 459, 475` | 全局计数非原子 + 首用显示 `None` | `JsonRedis` 的锁只保护单个操作，`get`+`set` 之间不原子，并发答对丢计数；未初始化时输出"总共进行了None次猜测" | 给 `JsonRedis` 加 `incr`；`t1/t2` 用 `or 0` 兜底 |
| M-18 | `say/__init__.py:180-192` | `file_path` 可能未绑定 + `except Exception` 过宽 + 泄露本机路径 | `file_path` 只在第一个 `try` 里赋值，靠 `finish()` 抛异常退出；一旦不抛则 `finally` 的 `os.remove` 抛 `UnboundLocalError`。`{e}` 含绝对路径原样发到群里 | `file_path: str | None = None` + `finally` 判空；`except` 收窄；用户文案去掉 `{e}`，细节只写日志 |
| M-19 | `say/__init__.py:129, 177, 217` | 中间产物写到 `os.getcwd()`，文件名用 `id(text)` 生成 | `id()` 是内存地址，对象释放后可复用 → 文件名可能撞；写进仓库根目录意味着崩溃时 WAV 残留、多实例互相覆盖 | `uuid.uuid4().hex` 命名 + `tempfile.mkdtemp()` |
| M-20 | `say:166,172,202`、`joy:34`、`ai:20-21`、`notify.py:9` | 管理员/禁用群 ID **硬编码且重复**，适配器不兼容 | 同一禁用群 `"569801410"` 在两个插件各写一遍；`*say_i` 白名单不走 `SUPERUSER`（同项目其他命令都用了，模式不一致）。且这些是 OneBot 数字号，QQ 适配器下永远匹配不上 → 白名单失效、禁言群失效 | 全部挪到 `Config`（各插件 `config.py` 现在是空壳）或 `.env`；管理员一律用 `SUPERUSER`；`notify.py:9` 的注释"改成你的 QQ"说明是忘了配置化的占位符 |

---

### 2.4 🟢 低（Low）—— 可维护性与杂项

| 编号 | 位置 | 问题 | 修复建议 |
|---|---|---|---|
| L-1 | `updater/jobs/` 下 `nlw.py(200)`、`ids.py(166)`、`lw.py(171)`、`hds.py(174)` | 四份 `fetch_*` 结构几乎逐字重复，仅列字母与 tier 规则不同；`tpl.py` 与 `pemonlist.py` 是同一模板的两份拷贝。合计 **~700 行重复** | 抽 `sheet_fetch.py`，把"列字母映射 + tier 规则 + 结束哨兵"参数化 |
| L-2 | `updater/jobs/ids.py:119-124`、`nlw.py:140-150` | `fetch_levels(..., pending)` 的 `pending` 参数从未被读取；`if pending:` 与 `elif platformer:` 分支体完全相同 | 删除死参数与重复分支 |
| L-3 | `api/aredlapi.py:72-73` vs `209-210`、`88` vs `163` | 模块级 list 被声明两次（前者是死代码）；函数内 `aredllevels = [...]` 是局部变量但无 `global`，语义误导 | 删掉 72-73、88、163；直接 `return [...]` |
| L-4 | `xiaozubot_help/__init__.py:49-50` vs `commands.py:33-38` | `"admin"` 分类别名是死代码（`CATEGORIES` 只有 gd/guess/fun/ai）→ `*help admin` 永远落到"没有这个命令"；`__init__.py:270` 白名单里 `"nlw"` 重复两次 | 删除 admin 别名或补齐分类；去重 |
| L-5 | `xiaozubot_help/__init__.py:269` | `int(args[1])` 用 `isdigit()` 守卫，但 `isdigit()` 对 `int()` 无法解析的 Unicode 数字字符也返回 True → `ValueError` 崩溃 | 改 `try/except`，或 `args[1].isascii() and args[1].isdigit()` |
| L-6 | `xiaozubot_help/__init__.py:72, 112-114` | `max()` 在空列表上抛 `ValueError`；`_render_category` 无空守卫（`_overview` 有、它没有） | 空列表直接返回 `[]`；`_render_category` 开头加判断 |
| L-7 | `plugins/roulette/__init__.py:16, 48` | `from .const import *` 后用 `const.sjmap` —— `const` 从未显式导入，纯靠"导入子模块会 setattr 到父包"的副作用。给 `const.py` 加 `__all__` 或重命名即 `NameError` | `from . import const` + `from .const import sjmap` |
| L-8 | `xiaozubot_help/commands.py:261,311,323,336,346,355` | 文案写"只能在群里用"，但**没有任何处理器检查** `is_group_event`；`today`/`news` 连 `rule_notban` 都没挂 | 要么加 `Rule(is_group_event)`，要么改文案（文档在骗用户） |
| L-9 | `plugins/ai/__init__.py:228` | `if False and not delegated and reply.startswith("[委托")` —— 委托逻辑是**死代码**，且后面跟着 `continue`/`break` 的循环骨架 | 确认废弃后删除整个 `while True` 骨架，改为顺序流程 |
| L-10 | `jrrp/__init__.py:20`、`roulette/__init__.py:18` | `random.seed()` 在导入时重置**全局** Mersenne Twister（guess/zhua/joy 共用同一个 `random`），无参 seed 本身就是默认行为 | 删除 |
| L-11 | `jrrp/__init__.py:49-51` | `delta = (23-hour)*3600 + ...` 在 23:59:59 时等于 0 → `r.set(..., ex=0)` 使键立即过期，"每天一次"的保证在该秒失效；且用无时区 `datetime.now()` | `ex=max(delta, 1)`；考虑显式时区 |
| L-12 | `jrrp/__init__.py:31` | `r.get(...) is not None and r.get(...) != "True"` —— 存储的值恒为数字字符串，`!= "True"` 永远成立（永假分支），且 `get` 被调两次 | 简化为单次取值判空 |
| L-13 | `guess/__init__.py:267, 380, 450` | `Image.open` 未关闭句柄（Windows 上会锁住源截图文件） | `with Image.open(...) as image:` |
| L-14 | `guess/__init__.py:201-209, 227-231` | `_list_files` 返回目录下**全部**文件，非图片会被 `random.choice` 选中并送进 `Image.open` → `UnidentifiedImageError` | 按扩展名过滤 |
| L-15 | 仓库根 `.env.dev.bak` | 明文含机器人 appId/token/secret 的备份文件躺在工作目录（已被 `.gitignore:32` 忽略，未入库，但仍是本地明文凭据） | 确认无需保留后删除；如需保留移出仓库目录 |
| L-16 | `pyproject.toml:152-192` | ruff 规则集**未启用 `S`（flake8-bandit）**，即没有任何安全类静态检查门禁 | 评估加入 `S` 规则（会有一批存量，可先仅对新增代码生效） |
| L-17 | `updater/__init__.py:204-206`、`services/search.py:90-93`、`api/http.py:51,77` | 后台任务 shutdown 时不取消；`_SEARCH_EXECUTOR` 从不 `shutdown()`；`_async_clients` 以 `id(loop)` 为键，loop 销毁后 `id()` 可能复用 → 拿到绑在已关闭 loop 上的 `AsyncClient` | 注册 `on_shutdown` 逐个 `cancel()`；executor 注册 atexit；`_async_clients` 按 loop 对象索引并在 shutdown 清理 |
| L-18 | `updater/jobs/metadata.py:172,197,207` | `input()` 调用残留在库代码里；`MANUAL_FALLBACK` 若为 True，后台线程会阻塞在 stdin 上（当前是 False，属定时炸弹） | 删除 `input()` 路径 |
| L-19 | `commands/search.py:58`、`ratings.py:104,112` | `len(name) > 4` / `MIN_ID_LEN=4` → 4 位数的合法关卡 ID（1000–9999）会被当成关卡名去搜 | 改为 `>= 4` |
| L-20 | `render/draw.py:460` | 日志字符串乱码 `"缂╃暐鍥炬嬁鍒颁簡浣嗚В涓嶅紑"`（UTF-8 被当 GBK 写过） | 修正编码 |

---

## 三、改进优先级

### 第一梯队：立即处理（本周内）

安全事件，需要**吊销凭据**而非仅改代码：

1. **C-1** 吊销并轮换 GDDL API key → 改为环境变量 → 清理 Git 历史
2. **C-2** 吊销并轮换 Google Sheets key → 删除静默回退分支
3. **H-1** 删除 `DEFAULT_VERIFY = CA_BUNDLE or False` 的 `or False`
4. **L-15** 清理 `.env.dev.bak`

> 前两项涉及凭据轮换，**不能只改代码**——旧密钥在 Git 历史里仍然有效。

### 第二梯队：本迭代（影响用户可见行为）

5. **H-12** `zhua` 缺失文件 → 最易复现的崩溃（1/65 概率）
6. **H-13** `ai` 的 `finish()` 被 `except Exception` 吞掉 → 错误信息丢失
7. **H-7** `search.py` 异常分支仍渲染废图 → 改为直接报错
8. **H-6** `draw.py` 污染共享缓存 `plat_info.tags`
9. **H-3** 出图 + PNG 编码移出事件循环（**收益最大的单项性能修复**）

### 第三梯队：下个迭代（并发与数据一致性）

10. **H-5** 五个内存索引的重建竞态 → 统一为"构建新容器 + 原子替换"
11. **H-4** `_collect_result` 补 `CancelledError`
12. **H-11 / M-16** `guess` 出题竞态 + `zhua` 冷却竞态 → 占位前置
13. **H-8 / H-9** `say` 模型双检锁 + TTS 信号量与超时
14. **H-15** 搜索会话键改为 `get_context_id` + 任务生命周期管理
15. **M-2 / M-3 / M-11** 统一走 `write_json_atomic`（顺带消除死代码）

### 第四梯队：持续改进

16. **H-14** `ai` 上下文 TTL + 限流
17. **M-1** `JsonRedis` 落盘 debounce
18. **M-4 / M-5 / M-6 / M-7** 其余性能项（to_thread、并发化、字体缓存、惰性解压）
19. **M-20** 管理员/白名单配置化
20. **L-1** `updater/jobs` 抽取公共抓取模板（~700 行重复）
21. **L-16** 评估引入 `S` 规则集

### 建议的工程动作（横切）

- **补跑一次测试**：本次环境无可用 3.12 解释器（`.venv/Scripts/python.exe`
  指向已删除的 Codex 运行时），建议修复 venv 后跑 `pytest` 交叉验证。
- **为本次每个修复补测试**：项目已有 1387 个用例且覆盖到每一个生产模块，
  这个基础很好，修复时同步补测试的成本很低。
- **加 pre-commit 密钥扫描**：C-1/C-2 这类问题靠人工 review 拦不住。
- **异步规范写入 `AGENTS.md`**：明确"`async def` 处理器内禁止同步 I/O，
  一律 `await asyncio.to_thread(...)`"，避免 H-3/H-10/M-4 这类模式反复出现。

---

## 四、值得肯定的地方

审计不应只报问题。这个仓库有几处做得相当好，建议在后续重构中**保留**：

1. **`pyproject.toml` 的规则取舍注释**（193-299 行）—— 每一条被禁用的 lint 规则
   都写清了"为什么不禁不行"和"实测代价"，并区分了"还没来得及修"与"明确决定不修"。
   这是极少见的工程素养，也是本次审计能快速定位"哪些是真问题"的关键依据。
2. **`utils/json_storage.py` 的原子写与坏文件隔离** —— `_load` 把损坏文件挪成
   `.broken` 而不是让插件加载失败，`_save` 用 `os.replace` 保证读者看不到半截文件。
   思路正确（本次只是发现它没有被一致地复用）。
3. **`utils/adapter_compat.py`** —— 双适配器抽象做得干净，`get_context_id()`
   这类工具函数正是修复 M-13/H-15 需要的现成方案。
4. **测试体量与组织** —— 1387 个用例、`conftest.py` 的载荷性 import 顺序注释、
   `stub_requests`/`stub_httpx`/`patch_storage` 替身体系，都说明测试是认真做的。
5. **`AGENTS.md` / `MAINTAINING.md`** —— 数据持久化约定、修改原则、注释规范
   都写在明处，降低了本次审计理解成本。

---

## 五、修复记录（2026-10-30 追加）

按"不管硬编码密钥、其余看着改"执行。以下均已改完并跑通全量测试
（`ruff check .` All checks passed；`pytest` **1727 passed**，原基线 1719）。

### 5.1 搜索恒定报「GDDL 没响应」（用户报的具体问题）

根因是**两级叠加**，不是单点：

1. **timeout 预算倒挂**：`SOURCE_LOOKUP_TIMEOUT=5s` 是五个源的公共 deadline，
   而 GDDL 单次请求预算是 `GDDL_TIMEOUT(15s) × attempts(2) + 退避 ≈ 30s`。
   GDDL 是最慢的源，**必然**跑不完，于是总在收集阶段被整轮丢弃。
2. **线程池饱和级联**：超时后 `future.cancel()` 对**运行中**的 future 无效
   （只作用于排队的），那个 GDDL 线程继续占着 5 个 worker 之一。重复几次后
   5 个 worker 全被死掉的 GDDL 调用占满 → 连纯内存的 NLW/plat 都要排队 →
   所有源一起错过 deadline → `results` 恒为空且 `gddl` 恒为 None →
   **无论搜什么都报 GDDL 无响应**。这正好解释"不管遇到什么情况"。

改动：

| 文件 | 改动 |
|---|---|
| `gdlevelsearch/constants.py` | 拆成 `SEARCH_INDEX_WORKERS` / `SEARCH_NETWORK_WORKERS` 两个池；`SOURCE_LOOKUP_TIMEOUT` 不变 |
| `gdlevelsearch/api/gddlapi.py` | 新增 `GDDL_SEARCH_TIMEOUT=4` + `GDDL_SEARCH_RETRY_POLICY(attempts=1)`，名称搜索用它，使总预算落在 deadline 内，函数末尾的本地快照兜底才轮得到执行 |
| `gdlevelsearch/services/search.py` | 网络源(gddl/gd)与内存索引源(nlw/plat/ratedplus)分池；`_collect_result` 补 `CancelledError`（继承 `BaseException`，`except Exception` 抓不到）；`search_by_name` 区分"源不可用"与"搜不到" |
| `gdlevelsearch/commands/search.py` | `None` 只在**所有源都没答上来**时才返回；消息改称"关卡数据源暂时无法访问"；ID 搜索的 `GDAPIUnavailable` 分支改为直接报错（原先仍渲染一张 `No info` 废图，且 `GD_API_UNAVAILABLE_MESSAGE` 常量是死代码） |

新增回归测试：`test_network_and_index_sources_use_separate_pools`、
`test_saturated_network_pool_still_lets_indexes_answer`（把网络池占满后内存索引仍需作答）、
`test_cancelled_future_is_reported_as_no_result`、
`test_gddl_outage_does_not_hijack_the_not_found_answer`、`test_only_a_total_outage_is_reported_as_none`。

### 5.2 其余已修

| 编号 | 修复 |
|---|---|
| H-3 | 出图（`draw.py:create_image_from_gdlevel`）与 PNG 编码（`send_result`）改 `asyncio.to_thread`；`*gdsearchstatus` 近 5MB 的 JSON 解析移入线程池（`_collect_status`） |
| H-6 | `draw.py` 不再写回共享缓存里的 `plat_info.tags`，改局部变量并保留上游顺序 |
| H-8/H-9 | `say` 模型加载加双检锁；新增 `asyncio.Semaphore(1)` + 120s 超时闸门；`sync_generate_audio` 文件名从 `id(text)` 改为 `uuid4` |
| H-10 | `guess` 答对/放弃的 Pillow 处理抽成 `_draw_answer_box` 并 `to_thread`；`Image.open` 改用 `with` |
| H-12 | `zhua` 候选项改为从目录实际存在的图片生成（`_available_files`），字典退为注解；修正 `禁止卡卒.jpg` → `.png`；冷却改到发图**之前**写，失败回滚 |
| H-13 | `ai` 的四个 `finish()` 移出 `try`；解析逻辑抽成 `_extract_reply`；用户文案不再带 `{e}`（避免泄露本机路径） |
| H-14 | `ai` 新增 `_evict_idle_sessions()`：30 分钟空闲回收 + 500 条硬上限 |
| H-15 | `search_cache` / `timeout_tasks` 键从裸 `user_id` 改为 `get_context_id(event)`；超时任务的 `bot.send` 加 `suppress` |
| M-3/M-11/D-2 | `JsonRedis._save`、`manage._save_json`、`gddl_store` 统一走 `write_json_atomic`（随机临时名 + fsync + replace） |
| H-1 | `metadata.py` 删除 `CA_BUNDLE or False`：certifi 缺失直接 `raise`，不再静默关 TLS，也不再 `disable_warnings` |
| M-15 | `guess` 的 `hget` 改走 `_safe_hget`，缺失时降级不画框而非 `TypeError` |
| M-17 | `JsonRedis` 新增原子 `incr` / `hdel`；`guess` 计数走 `incr`，首次显示 0 而不是 `None` |
| M-14 | 结束/放弃时真正 `hdel` 三个字段，不再留 `NOTHING` 僵尸记录 |
| M-12 | `joy` 戳一戳守卫改 `str()` 比较（原先 `int is not str` 恒为真，从未生效） |
| M-18 | `say` 的 `file_path` 预置为 `None`，`finally` 判空 |
| 低 | `draw.py` 字体加 `lru_cache`；`jrrp` 的 `ex=max(delta,1)`、去掉永假分支与全局 `random.seed()`；`roulette` 显式 `from . import const` 并去掉 `random.seed()`；`guess` 文件列表按图片后缀过滤；`xiaozubot_help` 去掉 admin 死别名与重复的 `nlw`、`int()` 加 `try`、`max()` 空列表兜底 |

### 5.3 未处理（按约定 / 有意保留）

- **C-1 / C-2 两个硬编码密钥**：按"两个 key 加起来没有半分钱"的指示未动。
  注意它们仍在 Git 历史里，将来若打算公开仓库仍需吊销。
- **L-15 `.env.dev.bak`**、**L-16 ruff 未启用 `S` 规则**：按"机器人 token 没太大办法"未动。
- **H-5 五个内存索引的重建竞态**、**H-4 已随本次改掉**、**M-9 GDDL staging 不发布**、
  **L-1 `updater/jobs` ~700 行重复**、**M-20 管理员 ID 硬编码**：
  均属需要改动结构或配置的较大重构，本次未做，建议单独排期。

### 5.4 关于测试环境

审计时 `.venv` 的 `python.exe` 指向已被删除的运行时路径，系统 3.13 又与 venv 的
3.12 包 ABI 不兼容。最终用 `Python312/python.exe` + `PYTHONPATH=.venv/Lib/site-packages`
跑通（3.12 存在，只是 `py.exe` 启动器因中文用户名路径编码问题起不来）。
建议修一下 `.venv`，或在 `MAINTAINING.md` 里记下这条命令。

---

*本报告基于静态代码审查。所有 Critical / High 级发现均已通过直接读取源码确认。*
*第五节的修复均已通过 `ruff check .` 与 `pytest`（1727 passed）验证。*

---

## 六、未处理项详解

对 5.3 里剩下的五项逐个展开。**含一处对第二节 H-5 原始建议的更正**（见 6.1）。

### 6.1 H-5 —— 五个内存索引的重建竞态

**是什么。** 五个模块把榜单数据常驻内存，updater 跑完后调 `reload()` 重建。重建方式
一律是**原地清空再逐条填充**，而读侧**完全不加锁**。

| 模块 | 索引声明 | 重建方式 |
|---|---|---|
| `api/gddl_store.py:33-35` | `levels` / `by_id` / `_by_name` | `_rebuild_indexes()`（172-190）三个 `clear()` 后 append |
| `api/nlwapi.py:81-90` | 4 个 list + 4 个 dict | `reload()`（154-165）先 clear 四个 list，`get_nlw_levels()` 再 append，`_rebuild_dicts()` 再逐个 `dct.clear()` 重填 |
| `api/ratedplusapi.py:56-58` | `levels` / `by_name` / `by_id` | `levels[:] = parsed`，然后两个 `clear()` + 重填 |
| `api/unratedlevels.py:36-38` | 同上 | 同上 |
| `api/aredlapi.py:209-211` | `aredllevels` / `arepllevels` / `aredl_dict` | `reload()`（214-238）`clear()` + `extend()` |

**为什么错。** 写侧是 updater 线程（`updater/__init__.py` 里的 `to_thread(reload_all)`），
读侧是搜索线程池（`services/search.py:_lookup_sources`，分池后是 `_INDEX_EXECUTOR`）
和出图线程（`draw.py` 的 `to_thread(create_level_image)`）。并发时：

1. 读者看到**空表**（clear 之后、append 之前）→ 搜索返回空 → 用户看到"没找到"；
2. 读者看到**半张表**；
3. 最坏：`nlwapi.py:198` 的 `for level in (*nlwlevels, *idslevels, ...)` 迭代期间
   另一侧 clear/append。list 按索引迭代不会抛异常但会静默截断；**dict 边迭代边改会抛
   `RuntimeError: dictionary changed size during iteration`**。

症状是"明明有关卡却搜不到，过一会儿又好了"——静默错误，最难排查。只在每天 03:00
更新那几分钟内出现。

**⚠️ 更正：我第二节给的方案是错的。** 当时建议"先建新容器 → 一次性原子替换模块全局名"。
**不能这么做**：`nlwapi.py:159` 和 `aredlapi.py:218` 的注释都写明了——别的模块是用
`from .nlwapi import nlwlevels` 把**引用**拿走的，重新赋值它们看不到，会一直读旧数据。
所以必须在**原地修改**的前提下加锁，而不是换成重新绑定。

**正确修法**（原地改 + 锁，照 `api/listsapi.py` 的路子）：

- 每个模块加 `_lock = threading.RLock()`；
- `reload()` 先在**局部变量**里把新数据完整解析构造好（全程不碰共享状态），
  然后在锁内一次性写回：`levels[:] = new`、`by_id.clear(); by_id.update(new_by_id)`、
  `_by_name.clear(); _by_name.update(new_by_name)`；
- 所有读函数也在锁内取快照；
- `nlwapi.get_nlw_levels()` 现在直接 append 到模块级 list，要改成**返回**新 list，
  由 `reload()` 在锁内做 `levels[:] = new`。

参考 `api/listsapi.py`：全仓库唯一做对了的，`_cache_lock` 包住 `reload()` 和
`_get_lists()`，而且它可以整体替换 `_cache`（因为没人 `from ... import _cache`）。

代价：读侧加锁略微降低并发度，但这些都是纯内存微秒级操作，锁竞争可忽略。

### 6.2 H-4 —— 已随本次修复

`services/search.py:_collect_result` 已显式捕获 `CancelledError` 并返回 None。
补充了回归测试 `test_cancelled_future_is_reported_as_no_result`（直接用
`Future()` + `cancel()` 构造 cancelled future，不依赖时序）。

### 6.3 M-9 —— GDDL staging 不发布

**调度**（都在 `updater/__init__.py`）：

- 03:00 `daily_update_job`（`register_daily_update_job`，hour=3）
- 07:00 `gddl_snapshot_refresh_job`（`register_gddl_snapshot_refresh_job`）

**03:00 的流程**（`daily_update_job`，180-218 行）：

```
_create_background_task(gddl_update_job())   # 第 204-206 行，不 await
result = await run_all_async()               # 主流水线，约 5 分钟
... reload_all()                             # 第 216-218 行
```

**数据流**：

- `gddl.fetch()`（`jobs/gddl.py:19-21`）→ `gddl_store.refresh(staged("gddl_levels.json"), reload_after=False)`
- `staged()`（`updater/paths.py`）返回 `STAGING_DIR / name` —— **写进 `data/.staging/`**
  （注意不是 `staged_or_published()`，后者才带 data/ 回退）
- `reload_after=False` → 不重建内存索引
- 主流水线 `run_all_async()`（`runner.py:71-114`）：开头 `clear_staging()`（82 行），
  两层 STAGES 跑完后调 `publish()`（112 行）把 staging 搬进 data/

**为什么丢**：

1. 主流水线约 5 分钟，GDDL 约 20 分钟（docstring 第 190 行）。主流水线 `publish()`
   时 GDDL 的 staging 文件还没生成，自然没被搬。
2. GDDL 跑完后**没有任何地方再调 `publish()`** —— 全仓库 `publish()` 调用点只有
   `runner.py:112` 和 `commands/manage.py` 的手动补 id 分支。
3. 第二天 03:00 主流水线开头 `clear_staging()` 把那个孤立文件删掉。
4. 即使侥幸被 publish，`reload_after=False` 加上主流水线的 `reload_all()` 已经在
   ~03:05 跑完，内存索引也不会更新——要等 07:00 那个 job。

**这是竞态，不是必然失败**：实测 `data/gddl_levels.json` 的 mtime 是 `10-28 03:08`。
那天主流水线比平时慢（或 GDDL 比平时快），`publish()` 落在 GDDL 完成之后，文件**恰好**
被发布了。所以症状是"GDDL 数据偶尔新、偶尔是几天前的"，比"永远不更新"更像玄学。

**修法**（推荐 A）：

- **A（最小改动）**：让 GDDL 直写 `data/`，和 07:00 的 job 保持一致——
  `gddl_store.refresh(reload_after=True)`（默认 `path` 就是 `DATA_FILE`）。
  理由：staging 那套协议是为"整批原子发布、防止五个文件里三个新两个旧"设计的；
  GDDL 是**单个、独立、失败不影响别人**的快照，本来就不该走它。写坏的风险由
  `refresh()` 内部的 `MIN_KEEP_RATIO` 和 failed_pages 合并逻辑兜着。
- **B**：保留 staging，但 `gddl_update_job()` 成功后自己 `publish()` + `reload()`。
  注意此时主流水线可能已经 `clear_staging()` 过了，所以 B 必须让 GDDL 也持有
  `runner._lock`，或者干脆错开启动时间。

无论 A 还是 B，**成功后都要重建内存索引**，否则文件更新了但搜索还在用昨天的内存表。

### 6.4 L-1 —— `updater/jobs/` 约 700 行重复

目录共 3157 行。重复集中在两组：

**A. 四个 Google Sheets 榜单抓取器（合计 711 行）**

| 文件 | 行数 | 列映射（节选） |
|---|---|---|
| `nlw.py` | 200 | A=视频 B=名字 C=作者 D=长度 E=技能组 F= enjoyment G=描述 |
| `ids.py` | 166 | A=名字 B=视频 C=作者 D=长度 E=技能组 F=描述 |
| `lw.py` | 171 | A=视频 B=名字 …（另有 `fetch_pending_cells`） |
| `hds.py` | 174 | B=名字 C=视频 D=作者 E=长度 G=技能组 I=描述（跳过 F/H） |

结构完全一样：`fetch_regular_cells` / `fetch_platformer_cells` / `build_level_list` /
`fetch_levels` / `fetch_all_levels` / `fetch`。差异只有：SHEET_ID、列字母映射、
tier 解析规则、结束哨兵。实测 `diff nlw.py ids.py` **只有 114 行不同**（两文件合计 366 行的 31%）。

**B. 三个 "URL → {id: 位置}" 映射抓取器（合计 324 行）**

`tpl.py(116)`、`pemonlist.py(123)`、`sfh.py(85)` 是同一模板的三份拷贝，差异只有
URL、字段名（`levelID` / `level_id`、`position` / `placement`）。
`diff tpl.py pemonlist.py` 只有 47 行不同。

**为什么是问题**：

- 改一个 Sheets 列的语义要改四个文件，漏改一个 → 那张榜单**静默错位**（名字列读成作者列）；
- `ids.py:119-124` 的 `pending` 参数**从来没被读取**，`nlw.py` 里 `if pending:` 与
  `elif platformer:` 两个分支体还完全一样——复制后没清理的死参数和死分支；
- 连带后果：`pyproject.toml` 里 `FBT001/002/003` 就是为了"让这四个保持同一签名好让
  runner 统一调"才特意关掉的——**重复直接导致 lint 配置被放宽**。

**修法**：

1. 抽 `sheet_fetch.py`：把"列字母映射 + tier 解析函数 + 结束哨兵"参数化，
   `build_level_list` / `fetch_levels` / `fetch_all_levels` 只留一份；
2. 抽 `fetch_mapping(url, extractor, output_name)`：`tpl` / `pemonlist` / `sfh` 共用；
3. 四个榜单文件退化成几十行的"配置"（SHEET_ID + 列映射 + tier 规则）。

预计 A 组 711 → ~250 行，B 组 324 → ~120 行。

**风险**：这四张榜单的列布局是**外部 Google Sheet 的属性**，各不相同。抽公共模板时
很容易把某个榜单的特例错误处理掉。**建议先给四个榜单各补一条"列映射 → 期望输出"的
测试再动手**，否则抽完没法证明行为没变。

### 6.5 M-20 —— 管理员 ID 硬编码

**全部位置**（当前行号，我改过 `say`/`joy`/`ai`，以现状为准）：

| 位置 | 内容 | 用途 |
|---|---|---|
| `plugins/ai/__init__.py:22` | `PROHIBITED_GROUP = [569801410]` | 禁用群 |
| `plugins/ai/__init__.py:23` | `MASTER_ID = 3251605531` | `.ai 清空` 权限 |
| `plugins/joy/__init__.py:34` | `get_group_id(event) == "569801410"` | 同一个禁用群，第二次 |
| `plugins/say/__init__.py:223` | `get_group_id(event) == "569801410"` | 第三次 |
| `plugins/say/__init__.py:229` | `get_user_id(event) != "3251605531"` | 绕过 500 字限制 |
| `plugins/say/__init__.py:245` | `not in ["3251605531", "2638056139"]` | `*say_i` 白名单 |
| `gdlevelsearch/updater/notify.py:9` | `ADMIN_ID = "F64625024CCE65B4A389808F50896488"` | 报错私聊对象，注释还写着"改成你的 QQ" |

**三个问题**：

1. **重复**：同一个禁用群在三个文件各写一遍，`3251605531` 两处。
2. **不走既有机制**：项目本身**有** `SUPERUSER`——`guess` 的 `guess_test` 和 `zhua` 的
   `zhua_test` 用的就是 `on_command(..., permission=SUPERUSER)`。但 `*say_i` 的白名单
   没用它，是自己拿字符串比较。两套权限体系并存。
3. **适配器不兼容（最实际的一条）**：这些都是 **OneBot 的数字 QQ 号**。QQ 官方适配器下
   `get_group_id()` 返回 `group_openid`（`F646...` 那种 32 位十六进制），
   `get_user_id()` 返回 openid——**数字串永远匹配不上**。于是：
   - `569801410` 禁言群在 QQ 侧**完全失效**；
   - `3251605531` 的 500 字豁免在 QQ 侧失效（主人自己也会被拦）；
   - `*say_i` 白名单在 QQ 侧**所有人不可用**；
   - `notify.py` 的 `ADMIN_ID` 是 openid 格式，**只在 QQ 侧有效、OneBot 侧无效**——正好相反。

**修法**：

1. 各插件的 `config.py`（`say/config.py`、`joy/config.py` 等）现在都是空壳，填上后用
   `get_plugin_config(Config)` 从 `.env` 读：禁用群列表、管理员 ID 集合；
2. 纯管理员命令改用 `permission=SUPERUSER`，与 `guess_test` / `zhua_test` 一致；
3. 需要"按人区分"的地方用**适配器中立**的身份键。`adapter_compat.get_context_id()`
   已经带了 `ob11:` / `qq:` 前缀，但那是"会话"不是"人"，建议再加一个
   `get_user_key(event)` 返回 `ob11:user:<id>` / `qq:user:<openid>`，配置里写全两种；
4. `notify.py:9` 至少先挪到配置——那条注释说明它本身就是个没配置化的占位符。

**优先级**：如果 bot 只跑 OneBot，这条**只是整洁问题**；如果要跑 QQ 官方适配器，
这是**功能失效**，应提前。
