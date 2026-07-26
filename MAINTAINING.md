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

**它不会红。** 这个 job 只打一份统计到日志里，没有任何能让它失败的路径。
如果它真的红了，那是 job 本身坏了（比如 `requirements-lint.txt` 没了），不是你的代码有问题。

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

- `tests/test_game_and_help.py` 里的 `drive()`
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

先知道一个背景：**`publish()` 这一步在 2026-07-27 之前从来没有成功执行过**
（`getmetadata.py` 少 import 了一个名字，第二阶段就 NameError 了，而流程是失败即中止）。
所以这条链路是「刚开始真正跑起来」的状态，后面的代码历史上没被执行过，
出问题很正常。

手动跑一次（不需要 bot 在线）：

```bash
python scripts/run_updater.py
```

已知还没修的坑，出问题先怀疑这几个：

- `updater/jobs/platrank.py` —— `weights[i]` 没做边界检查，Google Sheets 把某列尾部空行裁掉时会 IndexError；
  另外「空权重」被当成分节标题，遇到一整行空的会把后面所有行都丢掉
- `updater/paths.py` 的 `publish()` —— **没有下限检查**。上游表格改个格式导致解析出 0 条，
  这一步照样把空数据发布上去，直接盖掉好的数据
- `updater/jobs/getmetadata.py` —— 写回失败只记日志不报错，于是 `publish()` 会把没补 metadata 的数据当成品发出去
- `updater/notify.py` —— `get_bot()` 写在 try 外面，没有 bot 在线时报错通知本身会抛异常，真正的错误信息就丢了

（这几个都是既有问题，不是新引入的，所以这轮没动。要修的话它们是独立的一次改动。）

---

## 9. 这套 CI 到底在干什么

`.github/workflows/ci.yml`，推 `main`、开 PR、手动触发时跑：

- **Lint** —— 只打 ruff 的违规统计，**永远不阻塞**。存量一千多条，都是历史遗留。
  想让它变成真门禁：等 `ruff check .` 降到两位数，把那一步换成 `ruff check .` 就行。
- **测试** —— 平时只测 **3.10**（`requires-python` 承诺的下限）和 **3.13**（生产实际跑的版本）。
  中间的 3.11 / 3.12 交给每周一的定时任务跑全矩阵。
- 两个「工作区必须干净」的守卫只在 3.13 那一档跑，免得同一个问题报三遍。

不需要任何 secret / token。
