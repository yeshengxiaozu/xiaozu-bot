# 一个人怎么维护这套测试和 CI

这份文档是写给「半年后的你」的：那时候你已经忘了这套东西是怎么搭起来的，
只想赶紧把手上的改动推上去。

**先记住一句话**：测试是给你省事的，不是给你添堵的。
**哪条测试开始给你添堵，就删掉它** —— 具体哪些能删、怎么删，见第 3、5 节。

---

## 1. CI 红了怎么办

打开 GitHub 上那次 run，看是哪个 job 红的，然后对号入座：

### 「测试 (Python 3.x)」红了

最常见，说明真的有用例挂了。本地复现：

```bash
python -m pytest tests -q
```

本地也红 → 照第 2 节的表查你改了什么。
本地绿、CI 红 → 大概率是版本差异（本地是 3.10，CI 还跑 3.13），
按第 6 节起一个 3.13 环境再跑一遍。

### 「测试」里的「干净检出守卫」红了

它在说：**有文件被创建出来，而 `.gitignore` 没盖住它**。
上面会打出 `git status --porcelain` 的结果，那就是清单。

- 新加的插件建了自己的 `data/` → 已经被 `xiaozu_bot/plugins/*/data/` 这条通配规则盖住了，
  不该红。如果还是红了，说明产物落在别的位置，把那个位置加进 `.gitignore`。
- 你给 pytest 加了新的报告参数（`--junitxml=xxx.xml` 之类）→ 把产物加进 `.gitignore`。
- 某个用例往仓库里写东西了 → 改成写 `tmp_path`（见第 5 节）。

### 「测试」里的收集阶段就失败了

日志里会有一整段 traceback（这一步专门保证了会把日志打出来）。
几乎总是**某个插件 import 不动**：改坏了语法、少了依赖、
或者插件的 `Config` 加了个必填字段但没给默认值。
本地复现：

```bash
python -m pytest tests --collect-only -q
```

### 「Lint (ruff)」红了

**存量已经清到 0 了，所以它红 = 你这次改动引入的**，不是历史遗留。
本地复现和修：

```bash
ruff check .          # 看是哪条
ruff check --fix .    # 大部分能自动修
```

`--fix` 修不掉的，看报错说的规则名，自己改。
如果你觉得那条规则本身不合理（比如它逼你改一个框架要求的签名），
去 `pyproject.toml` 的 `[tool.ruff.lint] ignore` 里加一条，**并写清楚为什么** ——
那个列表里现在每一条都有理由，别破坏这个习惯。

⚠️ **`ruff check --fix` 可以随便跑，但不要跑 `--unsafe-fixes`。**
实测它会把 `updater/__init__.py` 里的 `from .. import reload_all` 改成
`from gdlevelsearch import reload_all` —— 这个名字在三条代码路径里一条都 import 不到，
而且它在抓取完成后的钩子里，**跑定时任务的时候才炸**，几小时后才发现。

---

## 2. 我改了 X，测试红了几个？

| 你改了什么 | 预期红几个 | 怎么办 |
|---|---|---|
| 机器人的一句回复（改文案、改语气、改错别字、加表情） | **0** | 不用管。这套测试**故意不测文案**，见第 3 节 |
| 给某个插件加了个命令 | **0-1** | 帮助注册表是反射出来的，会自己跟上。还会自动多出两个通过的用例 |
| 调了个常量（重试次数、阈值） | **1-3** | 改掉断言里的数字。断言写成 `== 模块.那个常量` 的形式就不用再改第二次 |
| 重构某个插件内部（行为没变） | **10-40** | 全是 `AttributeError` / `ImportError`，报错很直白，按提示逐个改，机械活 |
| 升级依赖 | **0 或者一大片** | 见下 |
| 加了个新插件 | **0** | 新插件不写测试完全没问题 |

**升级依赖那一行展开说**：要么 0 个，要么 700 个，中间没有。
700 个的时候不要慌，**只有三个地方要改**：

- `tests/test_small_plugins.py` 里的 `run_handler()`
- `tests/conftest.py` 里 `FakeBot.call_api`

所有 handler 测试都是从这三个口子进去的，nonebot 换了内部 API 就是它们仨先炸。
改完这三个，几百个用例一起变绿。

---

## 3. 这套测试**故意不测文案**

回复的文字怎么写，是会一直改的 —— 换语气、改错别字、加表情。
所以这套测试里**没有任何一条断言是在盯回复的措辞**。
改文案不会让任何用例变红，你可以随便改。

测试盯的是「bot 到底有没有正常工作」，具体是这些：

- **有没有回**、回了几条
- **状态对不对** —— 血量、回合、余额、人品值存没存进去
- **数字和 id 对不对** —— 这些出现在回复里是因为它们**就是结果**，不是文案
- **走的是哪个分支** —— 调了哪个接口、传了什么参数
- **发的是图还是文字**

一开始这套测试是连文案一起测的（350 个用例在盯字面文本）。
后来全删了，原因很简单：**改一句话就要跟着改测试，那是纯粹的负担，
而且它挡不住任何真正的 bug** —— 文案写错了不会让 bot 崩，
逻辑写错了才会，而逻辑是另外那些断言在盯的。

**所以，如果你以后加新测试**：别把回复的原文抄进断言。
要断言「回了东西」就用 `assert len(sent_texts(fake_bot)) == 1`，
要断言结果就断言那个数字或 id，别断言整句话长什么样。

**万一还是有一条盯文案的测试挂了**（漏网的），处理方式是：
**直接删掉整个测试函数**。删掉不会有任何副作用 —— 不影响别的用例，
不会让覆盖率塌方，CI 也不会因为用例变少而红。

---

## 4. 这些永远别删

下面每一条都对应一个**真的发生过**的故障。删了就等于把那个坑重新挖开。

| 位置 | 它守的是什么 |
|---|---|
| `tests/conftest.py` | 整套测试的地基，还有那个「不许联网」的守卫。删了所有用例都跑不起来，而且会真的去连 GD 的服务器 |
| `tests/test_harness_smoke.py` | 验证地基本身是好的。它挂了，别的用例全都不可信 |
| `tests/test_updater.py::TestGetMetadata` | `getmetadata.py` 用了 `DATA_DIR` 却没 import，`main()` 第一句就 NameError。它是更新流程第二阶段且失败即中止 —— 也就是说**更新器从来没有真正发布过数据** |
| `tests/test_updater.py::TestSetupUpdaterSslPatch` | `ssl._create_default_https_context` 被赋成了一个 SSLContext 实例，而标准库把这个名字当工厂函数调用。全进程的 HTTPS 从 bot 启动那刻起就是坏的 |
| `tests/test_updater.py::TestPaths` | 「跑完才发布」那套 staging → 正式目录的搬运。它坏了会用半截数据覆盖好数据 |
| `tests/test_json_storage.py::TestLoadAndSave` | storage.json 写坏时要改名留档、从空的重来，而不是抛异常。它抛异常的话插件在 import 期就挂了，整个 bot 起不来 |
| `tests/test_small_plugins.py` 里的 `TestGuess*` 空题库守卫 | 题库为空（干净 clone 就是这个状态）时 `*guess_start` 会死循环，把整个事件循环拖死 |

还有一个笨办法可以认出「这条测试记录了一个真实的坑」：

```bash
grep -rn "回归\|看着不对\|以前" tests/
```

这些注释是故意写的。看到就别动。

---

## 5. 怎么安全地删测试

**规则：要么删掉一整个 `class Test...`，要么删掉一整个文件。不要把一个测试掏空一半。**

安全的原因：每个 fixture 都是 function 作用域（每个用例一份全新的），
用例之间没有共享状态，模块级也没有可变全局变量。所以删任何一块，
剩下的都照常跑。

删完确认一下：

```bash
python -m pytest tests -q                                  # 还是全绿
python -m pytest tests --cov=xiaozu_bot --cov-report=term  # 覆盖率没大跌
```

覆盖率掉了一大截，说明你删的不是「重复」而是「唯一一份覆盖」，撤回来。
掉一两个百分点是正常的。

---

## 6. 日常命令速查

```bash
python -m pytest                                     # 全跑，十几秒
python -m pytest tests/test_gdapi.py                 # 只跑一个文件
python -m pytest tests/test_gdapi.py::TestDecryptPassword   # 只跑一个类
python -m pytest -k password                         # 按名字筛
python -m pytest -m "not slow"                       # 跳过 Pillow 合图那些慢的
python -m pytest -x --lf                             # 只跑上次失败的，第一个错就停
python -m pytest --cov=xiaozu_bot --cov-report=term-missing  # 覆盖率 + 没覆盖到的行号
```

装开发依赖：

```bash
python -m pip install -e ".[dev]"
```

**用生产的 Python 版本（3.13）跑一遍**，本地平时用的可能不是它：

```bash
py -3.13 -m venv .venv313
.venv313/Scripts/python -m pip install -e ".[migrate]" pytest pytest-asyncio pytest-cov
.venv313/Scripts/python -m pytest tests -q
```

（`.venv313/` 已经被 `.gitignore` 里的 venv 规则盖住了。）

---

## 7. 加新插件的检查清单

- [ ] 插件放在 `xiaozu_bot/plugins/<名字>/`，`pyproject.toml` 的 `plugin_dirs` 会自动扫到，不用登记
- [ ] 要存数据就用 `JsonRedis(plugin_storage(__file__))`，数据会落在插件自己的 `data/` 下，
      **`.gitignore` 已经用 `xiaozu_bot/plugins/*/data/` 通配盖住了，不用再加规则**
- [ ] 命令想出现在 `*help` 里，就往 `xiaozu_bot/plugins/xiaozubot_help/commands.py` 的 `COMMANDS` 里加一条；
      不加也不会有测试红（注册表只检查「文档里写了的命令确实存在」，不检查反过来）
- [ ] **不用写测试**。新插件没有测试不会让 CI 红
- [ ] 推之前本地 `python -m pytest -q` 跑一遍，确认没碰坏别的东西

---

## 8. 更新器（gdlevelsearch 的数据更新）出问题时

手动跑一次（不需要 bot 在线，也不需要连 QQ）：

```bash
python scripts/run_updater.py
```

**2026-07-27 实测跑通过一次**（GDDL 接入前）：10 个任务全绿，发布 6 个文件，用时 135 秒。
数据变动量：hds -83 条、ids +26、nlw +27、plat_combined +53、nong_index +17
（都是上游榜单的正常增删，核对过不是被截断）。

GDDL 是独立于主流水线的后台任务，快照发布为
`xiaozu_bot/plugins/gdlevelsearch/data/gddl_levels.json`。它每天凌晨 3 点和主更新
同时启动（`updater/__init__.py` 里的 `gddl_update_job`）；GDDL 失败只上报管理员，
不影响主流水线。`gddlapi.py` 会在在线请求失败时回退到这份快照。

主 updater 的 APScheduler job 在 NoneBot startup 阶段注册，沿用 APScheduler 的
服务器时区，并使用 job id `gdlevelsearch.daily_update`。启动日志必须出现
`[UPDATER] daily update job registered`；如果只看到 APScheduler 的日志而没有这条，
先检查插件加载是否在 startup 阶段报错。

想先小范围试试，用这个 —— 它只发 1 个 API 请求，而且产出的
`tpl.json` 属于中间文件（不在 `PUBLISHED_FILES` 里），
**跑它绝对不会动 `data/`**：

```bash
python scripts/run_updater.py tpl
```

⚠️ **两条硬规矩**：

1. **别在指定任务名的时候加 `--continue`。** `scripts/run_updater.py:81` 那个分支
   在「有任务失败但加了 --continue」的情况下**照样会发布**。
2. **跑全量之前先备份 `data/`。** 那个目录在 `.gitignore` 里，被盖掉就找不回来了：
   ```bash
   cp -r xiaozu_bot/plugins/gdlevelsearch/data ~/gd-data-backup
   ```

### 已知还没修的坑，出问题先怀疑这几个

- `updater/jobs/getmetadata.py` —— 写回失败只记日志不报错。而且 `open("w")` 是先截断的，
  所以写到一半失败会在 staging 留下一个 0 字节或者半截的文件，然后被发布出去。
  （下面那道下限检查能拦住变空的情况，但拦不住「半截但还有一半」。）
- `updater/jobs/fetchsfh.py` —— 拿到非 200 直接静默 return，不记日志，
  于是你会以为 NONG 索引更新了，其实还是昨天的
- `nong_index.json` 发布了但没人 reload 它 —— `draw.py` 的 `nong_index` 是 import 期读一次，
  要等重启 bot 才生效

### 已经修掉的（别再照着老文档怀疑它们）

- ~~`publish()` 没有下限检查~~ —— 现在有了。新数据条目数不到旧数据的 50% 就拒绝发布
  那个文件，其余文件照常。上游表格改格式导致解析出空列表时，线上数据不会被盖掉。
  （门槛设在 50% 是因为实测一次真实更新的波动是 -3.9% ~ +3.4%。）
- ~~`notify.py` 的 `get_bot()` 写在 try 外面~~ —— 现在拿不到 bot 就把整份报告写进日志，
  不会再用「There are no bots to get.」把真正的错误顶掉。

`tests/test_updater_isolation.py` 专门盯这些「更新器出事会不会连累 bot」的性质，
21 个用例，删了任何一个都等于把对应的坑重新挖开。

---

## 9. 这套 CI 到底在干什么

`.github/workflows/ci.yml`，推 `main`、开 PR、手动触发时跑：

- **Lint** —— `ruff check .`，**会阻塞**。存量已经从 4712 清到 0，
  所以它红一定是这次改动带进来的。清理是一次做完的：
  4712 → 1232（关掉既定风格）→ 578（654 条安全自动修）→ 102（手工清死代码）→ 0（结构性规则）。
  关掉的每条规则在 `pyproject.toml` 里都写了理由。
- **测试** —— 平时只测 **3.10**（`requires-python` 承诺的下限）和 **3.13**（生产实际跑的版本）。
  中间的 3.11 / 3.12 交给每周一的定时任务跑全矩阵。
- 两个「工作区必须干净」的守卫只在 3.13 那一档跑，免得同一个问题报三遍。

不需要任何 secret / token。
