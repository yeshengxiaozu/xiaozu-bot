# xiaozu-bot 代码审计报告（第二轮）

> **基线**：`main` @ `657600c`，审计分支 `arena/435971ed-xiaozu-bot`
> **审计日期**：2026-11-26
> **方法**：不只做静态阅读 —— 本轮**真的把仓库跑起来了**。所有标 `[实测]` 的结论都在
> 下面这套环境里复现过；标 `[静态]` 的是读代码得出的、风险明确但未单独构造复现。
>
> 本文件取代了原先的一次性报告。**旧报告已归档为
> [`AUDIT_REPORT_2026-10-30.md`](AUDIT_REPORT_2026-10-30.md)**，内容一字未动，
> `MAINTAINING.md` §10 里引用的 `AUDIT_REPORT.md §6.1 ~ §6.5` 请改看那个文件。
>
> **本轮不包含 C-1 / C-2（硬编码 API key）**：按维护者明确指示，该项已被多轮报告重复
> 提及且确认**不做修改**，本轮不再占用篇幅。旧报告 §2.1 仍是它的完整记录。

---

## 一、实测基线

这一节的意义是：下面所有"修法"都可以用同一套命令验证，不需要再猜环境。

| 项目 | 命令 | 实测结果 |
|---|---|---|
| 安装 | `python -m pip install -e .` | ✅ 成功（Python 3.11.2 干净 venv） |
| 测试 | `python -m pytest tests -q` | ✅ **1772 passed, 9 skipped**（两次实测 64.60s / 66.79s） |
| Lint | `ruff check .` | ✅ All checks passed（ruff 0.17.6） |
| 覆盖率 | `--cov=xiaozu_bot --cov-report=term-missing` | **87%**（7489 语句 / 988 未覆盖） |
| 真启动 | `python bot.py`（跑 12s 后退出） | ✅ 适配器、APScheduler、htmlkit 全部就绪，监听 `:8080` |
| 插件逐个加载 | `nonebot.load_plugin(...)` × 9 | ✅ 全部 OK |
| 工作区洁净 | `git status --porcelain` | ✅ 启动与测试都不残留文件 |

结论：**仓库当前是健康的**，没有"跑不起来"的问题。下面的问题按严重度排，P0 只有三条，
其中两条是**真 bug**（有确定的触发路径），一条是**仓库体积**问题。

---

## 二、P0 —— 建议优先处理

### P0-1 `ai` 的会话回收在超过上限时会把**所有**会话（含当前这轮）一起删掉

- **位置**：`xiaozu_bot/plugins/ai/__init__.py:108-130`，关键在 `:115-119`
  ```python
  if len(context_map) <= CONTEXT_MAX_SESSIONS:
      cutoff = time.time() - CONTEXT_IDLE_TTL
  else:
      # 会话数超过上限时不再等 TTL，直接按最久没说话的顺序砍到上限
      cutoff = time.time()          # ← 这一行
  stale = [key for key, seen in _last_seen.items() if seen < cutoff]
  ```
- **为什么错**：`cutoff = time.time()` 让 `seen < cutoff` 对**每一个**会话都成立
  （`_last_seen` 里的时间戳必然早于"现在"），于是 `stale` 包含全部会话，`:126-128`
  把它们全删。下面 `:121-128` 那段"按最久没说话的顺序砍到上限"的逻辑永远走不到有效分支
  —— 因为要删的已经在 `stale` 里了。
- **触发路径**（很现实）：每个用过 `.ai` 的群/频道都会在 `context_map` 留一条记录。
  跑得够久、跨过 `CONTEXT_MAX_SESSIONS = 500` 就进入这个分支。而
  `_evict_idle_sessions()` 是在 handler **末尾**（`:259`）调用的，此时当前这一轮的
  上下文刚刚 `append` 进去 —— **它自己也被删**。用户看到的现象是：某个群聊得好好的，
  某天开始突然每轮都"失忆"。
- **`[实测]` 复现**：塞 520 个"刚刚说过话"的活跃会话，调一次回收：

  ```
  before: 520 max: 500
  after : 0   <-- 期望 ~500
  ```
- **为什么测试没抓到**：`tests/test_small_plugins.py:1502`
  `test_context_map_does_not_grow_without_bound` 只构造了 **2 个**会话去测 TTL 分支，
  完全没覆盖"超过上限"这条分支，所以它一直是绿的。
- **修法**：
  1. 超上限时不要把 `cutoff` 设成 `time.time()`，而是直接按年龄裁到上限
     （`by_age[: len(context_map) - CONTEXT_MAX_SESSIONS]` 那段本来就是对的，只要
     不把它过滤掉就行）；
  2. 补一条测试：造 520 个活跃会话 → 回收后**恰好剩 500**，且**最新那条还在**。
     这条测试必须能在修复前变红，否则等于没写。

---

### P0-2 缺 APScheduler 时 `gdlevelsearch` **静默消失**，但假象是"一切正常"

- **位置**：`xiaozu_bot/plugins/gdlevelsearch/__init__.py:30`
  ```python
  require("nonebot_plugin_apscheduler")
  ```
- **`[实测]` 复现**：屏蔽掉 `nonebot_plugin_apscheduler` 的 import 后跑
  `nonebot.load_from_toml("pyproject.toml")`：

  ```
  [ERROR] nonebot | Failed to import "nonebot_plugin_apscheduler"
  [ERROR] nonebot | Failed to import "gdlevelsearch"     ← 整个插件没了
  RuntimeError: Cannot load plugin "nonebot_plugin_apscheduler"!
  RESULT: load_from_toml returned without raising        ← bot 照样起来了
  LOADED: ['ai', 'guess', 'joy', 'jrrp', ..., 'zhua']    ← 列表里没有 gdlevelsearch
  ```
- **纠正我上一轮的措辞**：我上一轮说"bot 起不来"是**过强的**。实测 `load_from_toml`
  **不抛异常**，bot 会正常启动 —— 但 **`gdlevelsearch` 的 15 个 `on_command` 命令
  （另有 4 个翻页 `on_message` 选择器，共 19 个 matcher）全部消失**：
  `gdsearch` / `gdfullsearch` / `gdratings` / `aredlrank` / `gdicon` / `gdsong` / `gduser` /
  `dailydemon` / `gd随机推关` / `plat随机推关` / `gdsearchhelp` / `gdsearch_update` /
  `gdsearch_store_update` / `gdsearch_manage` / `gdsearch_status`。
  只在启动日志里留两行 ERROR。这比直接崩更难查：用户看到的是"机器人活着但不理我"。
- **根因有两层**：
  1. `require()` 是**硬**依赖，在模块级执行；
  2. `updater/__init__.py:316-321` 的 `_register_daily_update_job_on_startup()` 在
     注册失败时 `raise RuntimeError`，挂在 `on_startup` 上 —— 即使 `require` 改成软的，
     这一层仍会显式失败。
     同一个文件里的 `_register_cron()` 明明已经写了"apscheduler 不可用就禁用并返回 False"
     的容忍路径，`run_updater.py` / `try_search.py` 这些离线脚本也依赖这种容忍 ——
     **只有 bot 这条路径把它升级成了致命错误**。
- **为什么重要**：数据自动更新是**后台增强**，不该有让 14 个命令集体蒸发的否决权。
  这也是全仓库唯一一处"可选功能变必死"的耦合。
- **修法**：
  1. `require(...)` → `try: require(...) except ...: logger.error("自动更新已禁用")`；
  2. `_register_daily_update_job_on_startup()` 失败只 `logger.error`，**不 `raise`**；
  3. 补一条"apscheduler 缺失时 `gdlevelsearch` 仍能加载、命令仍在"的回归测试
     （conftest 已有 `~none` driver 和断网守卫，加这个不难）。

---

### P0-3 仓库里躺着一个 57 MB 的 `main.exe`，且**从未被使用**

- **文件**：`xiaozu_bot/plugins/guess/dist.zip`
- **`[实测]` 内容**：3 个条目 —— `setup.txt`(88B) / `icon.ico`(16,958B) /
  **`main.exe`(57,398,097B)**。`setup.txt` 的内容是：
  ```
  G:\CODES\pycharmCODE\screenshoot\dist\screenshoot   //location
  2   //period(s)
  ```
  也就是说这是**截图工具 PyInstaller 打包产物**，跟 `guess` 插件的代码毫无关系。
- **`[实测]` 引用数为 0**：全仓库 `grep -rn "dist.zip"`（排除 `.git`）**零命中**，
  `guess` 的任何代码路径都不读它。
- **体积影响**（`[实测]`）：

  | 文件 | git blob 大小 |
  |---|---|
  | `plugins/guess/dist.zip` | **57,011,933** |
  | `plugins/gdlevelsearch/resources/icons_uhd.zip` | 8,078,325 |
  | `plugins/gdlevelsearch/resources/left_bg.png` | 1,154,287 |
  | `plugins/gdlevelsearch/resources/ARIAL.TTF` | 1,016,724 |

  `.git` 已经 **66.17 MiB**，而这一个文件就占了其中 ~87%。
- **为什么重要**：
  1. 每个 clone / CI checkout 都要为它多拉 57 MB；
  2. 它是**可执行文件**躺在 Python 包目录里 —— 对杀软/审计是红旗，
     也是典型的"构建产物误提交"样板；
  3. `pyproject.toml` 的 `include = ["xiaozu_bot*"]` 意味着它随源码树一起分发。
- **修法**（分两步，都不影响运行）：
  1. **立即**：`git rm --cached xiaozu_bot/plugins/guess/dist.zip` + 在 `.gitignore`
     加 `dist.zip` / `*.exe`。工作区行为和现在完全一致（本来没人读它）。
  2. **可选**：真要瘦身历史，需要 `git filter-repo` —— 但那会重写所有 commit hash，
     得单独排期。**只做第 1 步就已经止住增量。**

---

## 三、P1 —— 正确性与测试

### P1-1 `reload_all()` 会联网 + 会覆写缓存，而 `*gdsearch_manage` 告诉用户"没有抓取网络数据"

- **位置**：`xiaozu_bot/plugins/gdlevelsearch/commands/manage.py:168-174`
  ```python
  if text in RELOAD_WORDS:
      await asyncio.to_thread(reload_all)
      return "已重载本地缓存（没有抓取网络数据）"
  ```
  但它调的 `reload_all()`（`gdlevelsearch/__init__.py:40-62`）包含 `aredlapi.reload()`，
  而 `aredlapi.reload()` 在缓存过期时会**发真实网络请求**。
- **`[实测]` 复现**：把 `_fetch_aredl` / `_fetch_arepl` 换成计数桩，缓存设成过期：

  ```
  A) network calls in reload(): 2   |  in-memory levels: 500   ← 确实联网了两次
  ```
  并且因为 `reload_all()` 内部 `except Exception: logger.exception(...)` 兜住了，
  **manage 那边看到的是"成功"**，用户得到的是"已重载（没有抓取网络数据）"。
- **第二个问题（更隐蔽）**：`api/aredlapi.py:222-225` 在拿到**非空但明显缩水**的上游数据时
  会**直接覆写**缓存：
  ```python
  if fetched_levels:
      with path.open("w", encoding="utf-8") as f:
          json.dump({"timestamp": ..., "levels": levels_data}, f, indent=4)
  ```
  `[实测]`：本地 500 条，上游返回 1 条 → 落盘后**磁盘上只剩 1 条**。
  AREDL 这条路径**没有** `updater/paths.py` 里那套 `MIN_KEEP_RATIO` 下限保护
  （那套保护只在 `publish()` 里生效，而 aredl 自己写自己的文件，绕过了它）。
- **顺带纠正我上一轮的另一个说法**：我上一轮说"离线时会把 AREDL 内存清空到 0"。
  **这一半是错的** —— `aredlapi.py:219` 有 `if not fetched_levels and cached_levels:`
  会留住过期缓存，这是对的。真正的问题是**上面两条**（静默联网 + 缩水覆写），
  以及"拉取彻底失败且本地也没有缓存"时 `aredl` 才真的空 —— 那是新部署的正常状态。
- **修法**：
  1. `reload_all(offline: bool = False)`，manage 的"重载"传 `offline=True`，
     跳过 `aredlapi.reload()`（或改成 `aredlapi.reload(allow_network=False)` 只读磁盘）；
  2. 或者把文案改成诚实的"重载本地缓存（aredl 会顺带刷新，可能需要几秒）"；
  3. AREDL 的写盘加一格下限保护，与 `publish()` 的 `MIN_KEEP_RATIO` 对齐。

### P1-2 `render/draw.py` 覆盖率只有 61%，恰好是最贵、最该有测试的代码

- **`[实测]`**：`xiaozu_bot/plugins/gdlevelsearch/render/draw.py` 共 **674** 条语句，
  **264 条未覆盖（61%）**，未覆盖集中在 `348-369`、`373-460`、`464-490`、
  `530-578`、`582-645`。
- 对比（同一份覆盖率报告）：

  | 文件 | 覆盖率 |
  |---|---|
  | `api/nlwapi.py` | 100% |
  | `api/gdapi.py` | 99% |
  | `updater/runner.py` | 96% |
  | `services/search.py` | 94% |
  | **`render/draw.py`** | **61%** |

- **为什么重要**：这是每个 `*gdsearch` / `*gdfullsearch` / `*dailydemon` / `*gd随机推关`
  都要走的路径。它出错的后果是"用户什么都不收到"（不是报错，是静默），
  而它同时也是最容易被资源文件（字体/背景/图标）缺失打中的地方。
- **修法**（挑 3 条性价比最高的，都不需要真渲染整张 1280×720）：
  1. `_draw_background`：`left_bg.png` 缺失时的降级路径；
  2. `_draw_card`：图标路径缺失 / 图标高度为 0 时不抛（现在靠
     `except (FileNotFoundError, OSError, ZeroDivisionError): break` 兜）；
  3. `_draw_sidebar`：文本超长时的截断与 `max_y` 边界。
  全部可以用 `tmp_path` + 小尺寸图构造。

### P1-3 `updater/jobs/` 约 700 行重复，且重复的文件覆盖率同样低

- **A 组（4 个 Google Sheets 抓取器）**：`nlw.py`(179) / `ids.py`(166) / `lw.py`(171) /
  `hds.py`(174)。`[实测]` 覆盖率：`ids.py` **59%**、`lw.py` **63%**、`nlw.py` **65%**。
- **B 组（3 个 URL→位置映射抓取器）**：`tpl.py`(54) / `pemonlist.py`(57) / `sfh.py`(54)。
- **为什么重要**：列映射是**外部 Google Sheet 的属性**。改动一张表的列，要同步改 4 个
  文件；漏一个的症状是"名字列读成作者列"这类**静默错位**——不报错、不崩、
  只是数据错。低覆盖率让这个风险加倍。
- 这是旧报告的 **L-1**，至今未动。旧报告 §6.4 的分析和"先补测试再重构"的结论仍然有效。
- **修法（顺序不能反）**：
  1. 先给四张榜单各补一条"列映射 → 期望输出"的表驱动测试（这是重构的安全网）；
  2. 再抽公共模板：`sheet_fetch.py`（参数化列映射/tier 规则/结束哨兵）
     + `fetch_mapping(url, extractor, output_name)`。
  3. 目标：A 组 711 → ~250 行，B 组 324 → ~120 行。

### P1-4 三处告警/异常处理会让排障直接失明

| 位置 | 问题 | 后果 |
|---|---|---|
| `render/draw.py:447-448` | `except Exception: pass`（featured 特效贴图） | 同文件其他 5 处都 `logger.error`/`logger.warning`，只有这里是**完全静默**。出图时特效没贴上，日志里一个字都没有 |
| `render/draw.py:473` | 日志字符串是**乱码**：`"缂╃暐鍥炬嬁鍒颁簡浣嗚В涓嶅紑锛岄€€鍥炲崰浣嶅浘"` | 这是"缩略图拿到了但解不开，退回占位图"的告警，被按 GBK 解读过的 UTF-8 二次编码了。**全仓库仅此一处**（`[实测]` 扫描全部 `.py`）。真出问题时日志里是一串看不懂的字 |
| `render/draw.py:168` | `os.environ["NO_PROXY"] = "history.geometrydash.eu,geometrydash.eu"` | **覆盖**而不是追加。用户自己设过 `NO_PROXY` 的话会被整体抹掉，只影响别人设的值——典型"在我的机器上是好的" |

- **修法**：①补 `logger.warning`；②把乱码字符串改回
  `"缩略图拿到了但解不开，退回占位图"`；③改成追加：
  `os.environ["NO_PROXY"] = ",".join(filter(None, [old, "history.geometrydash.eu,geometrydash.eu"]))`。

### P1-5 `gdicon` 的 PNG 编码仍在事件循环上

- **位置**：`xiaozu_bot/plugins/gdlevelsearch/commands/gdicon.py:69` 与 `:86`
  ```python
  sheet = await asyncio.to_thread(icons.compose_sheet, user, items)   # ✅ 合成在线程
  buffer = BytesIO()
  sheet.save(buffer, format="PNG")                                    # ❌ 编码在循环上
  ```
  `[实测]`：`compose_sheet` 确实包了 `to_thread`，但紧随其后的两次 `.save()` 没有。
- **为什么重要**：旧报告 H-3 已经把"出图 + PNG 编码移出事件循环"作为**通用修法**立项，
  `services/search.py:send_result` 也确实改成了 `await asyncio.to_thread(image.save, ...)`。
  **唯独 `gdicon` 漏了** —— 这是个一致性缺口，不是新原则。
  `-a` 那张 9 宫格图编码是几十到几百毫秒的纯 CPU，期间整个 bot 不响应心跳。
- **修法**：两处都改成 `await asyncio.to_thread(sheet.save, buffer, format="PNG")`。

### P1-6 `nong_index.json` 发布了但没人 reload（旧报告已标，仍未修）

- **位置**：`render/draw.py:256-264` 在 **import 期**读一次 `data/nong_index.json`
  进模块级 `nong_index`。
- `[实测]`：`reload_all()`（`gdlevelsearch/__init__.py:40-62`）重载了 gddl / nlw / plat /
  aredl / lists / ratedplus / unrated **七个**源，**唯独没有它**。
- **后果**：`updater` 每天发布会更新 `nong_index.json`，但 bot 里 `draw.py` 的
  `nong_index` 一直停在**启动那一刻**的版本。表现为"NONG 歌名显示的是旧数据"，
  要重启 bot 才生效——而且不会有任何日志提示。
- **修法**：给 `draw.py` 加一个 `reload_nong_index()`，在 `reload_all()` 的元组里加一项。

---

## 四、P2 —— 配置、DX 与工程卫生

### P2-1 管理员 / 禁用群 ID 硬编码，且 **QQ 适配器下必然失效**（旧 M-20）

- **全部位置**（`[实测]` 精确行号）：

  | 位置 | 内容 | 用途 |
  |---|---|---|
  | `plugins/ai/__init__.py:21` | `PROHIBITED_GROUP = [569801410]` | 禁用群 |
  | `plugins/ai/__init__.py:22` | `MASTER_ID = 3251605531` | `.ai 清空` 权限 |
  | `plugins/joy/__init__.py:35` | `get_group_id(event) == "569801410"` | 同一个禁用群，**第二次** |
  | `plugins/say/__init__.py:210` | `get_group_id(event) == "569801410"` | 同一个禁用群，**第三次** |
  | `plugins/say/__init__.py:216` | `get_user_id(event) != "3251605531"` | 500 字上限豁免 |
  | `plugins/say/__init__.py:232` | `not in ["3251605531", "2638056139"]` | `*say_i` 白名单 |
  | `updater/notify.py:9` | `ADMIN_ID = "F64625024CCE65B4A389808F50896488"` | 报错私聊对象 |

- **最实际的一条**：这些全是 **OneBot 的数字 QQ 号**。`adapter_compat.py:329-347` 里
  `get_group_id()` 在 QQ 适配器下返回的是 `group_openid`（32 位十六进制），
  `get_user_id()` 返回的是 openid —— **数字串永远匹配不上**。于是：

  - `569801410` 禁言群在 QQ 侧**完全失效**；
  - `3251605531` 的 500 字豁免在 QQ 侧失效（主人自己也会被拦）；
  - `*say_i` 白名单在 QQ 侧**所有人不可用**（`:232` 是裸 `event.get_user_id()`，连
    `adapter_compat` 都没走）；
  - `notify.py:9` 的 `ADMIN_ID` 是 openid 格式，**只在 QQ 侧有效、OneBot 侧无效** ——
    正好和上面几条相反。

- **次要点**：同一个禁用群在三个文件各写一遍，`3251605531` 两处。
  项目本身**有** `SUPERUSER` 机制（`guess_test` / `zhua_test` / `gdsearch_update` 都在用），
  但 `*say_i` 白名单是自己拿字符串比 —— 两套权限体系并存。
  另外 7 个插件的 `config.py` 目前全是**空壳**（只有 `class Config(BaseModel): pass`），
  没有一条配置走 `.env`。
- **修法**：填 `config.py` → 用 `get_plugin_config(Config)` 读禁用群/管理员集合；
  纯管理员命令改 `permission=SUPERUSER`；需要区分"人"的地方用适配器中立的键
  （建议在 `adapter_compat` 加 `get_user_key(event)` 返回 `ob11:user:<id>` /
  `qq:user:<openid>`，配置里两种都写）。

### P2-2 `README` 的第一步命令会 `command not found`

- **位置**：`README.md:40` 写的是 `nb run --reload`。
- `[实测]`：`pip install -e .` 之后 `.venv/bin/` 下**没有 `nb`** ——
  `nb-cli` 不在 `pyproject.toml` 的依赖里。真正能跑的是 `python bot.py`
  （已实测启动成功，见第一节）。
- **为什么重要**：新人照 README 走，第一步就卡住。这是最容易劝退的一条 DX 问题，
  而修起来只是改两行字。
- **修法**：README「跑起来」改成
  ```bash
  python bot.py
  # 或者：pip install nb-cli 之后用 nb run --reload
  ```
  `AGENTS.md` 的"运行与本地脚本"一节同理（它把 `nb run` 和 `python bot.py` 并列写了，
  但没说 `nb` 需要额外装）。

### P2-3 `ai` 因为一行 import 把整个 `gdlevelsearch` 拖进内存

- **位置**：`xiaozu_bot/plugins/ai/__init__.py:12`
  ```python
  from xiaozu_bot.plugins.gdlevelsearch.api.http import async_request
  ```
- `[实测]`：只 `load_plugin("xiaozu_bot.plugins.ai")`，日志里出现了
  `[UPDATER] initialized` 和 `NONG index not available`，已加载插件列表变成了
  `['ai', 'nonebot_plugin_apscheduler']` —— 为了一个 20 行的 HTTP 重试封装，
  拉起了图标渲染、字体加载、updater、APScheduler 全套。
- **为什么重要**：它是 P0-2 的**放大器**（`ai` 也跟着一起受 APScheduler 硬依赖的牵连），
  也让"只想跑 ai 的轻量部署"变成必须装全量 GD 依赖。
- **修法**：把 `async_request` 下沉到 `xiaozu_bot/utils/http_client.py`，
  `ai` 和 `gdlevelsearch` 都从那里取。

### P2-4 工程卫生（都不影响运行，但长期扣分）

| 项 | 位置 | 说明 |
|---|---|---|
| 3 个 `.DS_Store` 已入库 | `plugins/{ai,gdlevelsearch,say}/.DS_Store`（各 6148 B） | `.gitignore` 里明明有 `*.DS_Store`，但**已跟踪的文件不受 ignore 影响**。需要 `git rm --cached` 才生效 |
| CI 钉的工具版本会漂 | `.github/workflows/ci.yml`（`pytest==9.1.1 pytest-asyncio==1.4.0 pytest-cov==7.1.0`）、`requirements-lint.txt`（`ruff==0.17.3`） | `[实测]` PyPI 上现在已是 pytest **9.2.0**、pytest-asyncio **1.5.0**、ruff **0.17.6**。这**不会**让 CI 红（钉的版本仍然存在），但意味着"本地绿 = CI 绿"完全靠人肉同步，值得在 `MAINTAINING.md` §6 记一句 |
| `updater/jobs/gddl.py` 覆盖率 25% | `updater/jobs/gddl.py`（16 行，12 行未覆盖） | 它虽小，却是"03:00 GDDL 快照"的唯一入口，且已知有旧报告 M-9 的发布竞态。低覆盖 + 已知竞态 = 出问题只能靠日志猜 |

> **附带更正**：我在初稿里写过"`gdicon` / `gdsong` / `gduser` / `plat_random` 没写进帮助
> 文本，用户只能靠猜"。**这句是错的**：它们都登记在
> `xiaozu_bot/plugins/xiaozubot_help/commands.py` 里（`gduser:90`、`gdicon:101`、
> `gdsong:114`、`gd随机推关:127`、`plat随机推关:147`），只是不在 `*gdsearchhelp` 那段
> 纯文本里。帮助体系是完整的，此项作废。

---

## 五、明确**未**发现问题的部分（避免重复劳动）

以下几处我专门验过，**当前实现是对的**，后续报告不必再提：

- **HTTP 层**（`api/http.py`）：异步客户端按 event loop 隔离、同步客户端双检锁、
  非幂等方法默认不重试、重试有界并带退避 —— 设计正确，且 `[实测]` 覆盖 89%。
- **`JsonRedis`**（`utils/json_storage.py`）：原子写（临时文件 + fsync + `os.replace`）、
  坏文件挪 `.broken` 并从空开始、`incr`/`hdel` 的单锁读-改-写（注释把"两把锁会丢计数"
  这个坑写清楚了）—— 实现与注释一致。
- **搜索的"不可用 vs 搜不到"**：`SearchResults.sources_available` 的设计（`services/search.py`）
  是正确的修法，测试也跟上了；索引源与网络源**分两个线程池**这一点（`constants.py`
  的 `SEARCH_INDEX_WORKERS` / `SEARCH_NETWORK_WORKERS` 及注释）也是对的设计。
- **更新器的失败隔离**：`runner.py` 的 `DEPS` / `OUTPUTS` 直接依赖闭包、
  `publish(allowed=...)` 只搬整条闭包成功的文件、`MissingOutputError`、
  `MIN_KEEP_RATIO` 下限保护 —— 逻辑经得起看（`[实测]` 覆盖率 96%）。
- **`areldapi` 的空数据保护**：`aredlapi.py:219` 的
  `if not fetched_levels and cached_levels` 确实会留住过期缓存。**上一轮我说它会把内存
  清空到 0，这半句是错的**，在此更正。（真正的问题只剩 P1-1 的另外两条。）
- **CI 的 action 版本**：`actions/checkout@v7` / `actions/setup-python@v7` ——
  `[实测]` 查 GitHub API 这两个 tag **都真实存在**，不会挂。
- **`pydantic` 的 `B008`（`CommandArg()` 作为默认参数）**：这是 NoneBot 的既定写法，
  不是 bug，ruff 配置里也正确地关掉了。

---

## 六、建议的动手顺序

1. **P0-1** —— 真 bug，改动 ~5 行 + 1 条测试，收益立竿见影。
2. **P0-2** —— 真 bug，决定"GD 命令会不会集体消失"，改动也是局部。
3. **P0-3** —— 纯收益清理；第 1 步（`git rm --cached`）零风险。
4. **P1-2** —— 补 `draw.py` 三条测试，是整个仓库里性价比最高的测试投入。
5. **P1-1** —— 至少先把 manage 的文案改成诚实的，再排期真正的 offline 参数。
6. 其余按 P1 → P2 顺次推进。

---

## 七、与旧报告的关系

| 旧报告编号 | 本轮状态 |
|---|---|
| C-1 / C-2（硬编码 key） | 按维护者指示**不处理**，本轮不展开（见旧报告 §2.1） |
| H-5（五个内存索引重建竞态） | **本轮未复现成可观测故障**，仍属结构性问题。旧报告 §6.1 的分析（含对"原子替换"方案的自我更正）依然有效，索引仍在 |
| M-9（GDDL staging 偶尔不发布） | 尚无新证据；`runner.py:84-87` 的 `UNOWNED_FILES` 已把 `gddl_levels.json` 纳入发布范围，风险比旧报告写作时小 |
| L-1（updater 重复） | 仍然存在 → 本报告 **P1-3** |
| M-20（管理员 ID 硬编码） | 仍然存在 → 本报告 **P2-1**，本轮补充了精确行号和 `adapter_compat` 的调用链证据 |

*本报告的所有 `[实测]` 结论均可在 §1 的环境中用文中给出的命令复现。*
