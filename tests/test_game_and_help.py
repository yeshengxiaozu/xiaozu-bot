"""game（恶魔轮盘）和 xiaozubot_help（命令说明表）的用例。

两块东西放一个文件里是有原因的：help/commands.py 里 demon 分类那几条
说明的具体数字（血量上限 6/10/16、道具上限 6/8/10、12 轮 / 5 轮进死斗）
就是 game 里的常量，放一起才好互相对着断言，help 写飘了立刻就红。

game 里所有的随机和时钟都被顶掉了（见 rand / clock 两个 autouse fixture），
没有一条断言依赖系统熵或者真实时间。
"""

from __future__ import annotations

import importlib
import pkgutil
import random as _random
import re
from typing import Any, Callable, Optional

import pytest
from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException
from nonebot.internal.matcher import Matcher
from nonebot.rule import CommandRule

from tests.conftest import GAME_WHITELIST_GROUP_IDS
from xiaozu_bot.plugins import game
from xiaozu_bot.plugins import xiaozubot_help as helpmod
from xiaozu_bot.plugins.xiaozubot_help.commands import CATEGORIES, COMMANDS, Cmd
from xiaozu_bot.utils.json_storage import JsonRedis

# 白名单群里的一个，所有 game 用例默认用它
GID = GAME_WHITELIST_GROUP_IDS[0]
GID_S = str(GID)
# 不在白名单里的群
GID_OUTSIDE = 4000000001

P0 = 111  # 先手玩家
P1 = 222  # 后手玩家
P2 = 333  # 局外人

FAKE_NOW = 1_700_000_000


# ==========================================================================
# 测试替身
# ==========================================================================
class FakeClock:
    """顶替 game 模块里的 time 模块。game 只用到 time.time()。"""

    def __init__(self, now: int = FAKE_NOW) -> None:
        self.now = now

    def time(self) -> float:
        return float(self.now)

    def advance(self, seconds: int) -> None:
        self.now += seconds


class FakeRandom:
    """顶替 game 模块里的 random。

    plan("randint", 4, 3) 排进去的值按方法名先进先出地取；排空之后回落到
    固定种子的 random.Random，所以没排到的调用也是可复现的，绝不会摸系统熵。
    每次调用的参数都记在 calls 里，方便断言「randint 的上下界到底传了多少」。
    """

    def __init__(self, seed: int = 20240727) -> None:
        self._rng = _random.Random(seed)
        self._plans: dict[str, list[Any]] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def plan(self, method: str, *values: Any) -> "FakeRandom":
        self._plans.setdefault(method, []).extend(values)
        return self

    def args_of(self, method: str) -> list[tuple[Any, ...]]:
        """按顺序列出某个方法被调用时的实参"""
        return [a for m, a in self.calls if m == method]

    def _take(self, method: str, args: tuple[Any, ...], fallback: Callable[[], Any]):
        self.calls.append((method, args))
        queue = self._plans.get(method)
        if queue:
            return queue.pop(0)
        return fallback()

    def randint(self, a: int, b: int) -> int:
        return self._take("randint", (a, b), lambda: self._rng.randint(a, b))

    def choice(self, seq):
        return self._take("choice", (list(seq),), lambda: self._rng.choice(seq))

    def choices(self, population, weights=None, k: int = 1):
        return self._take(
            "choices",
            (list(population), weights, k),
            lambda: self._rng.choices(population, weights=weights, k=k),
        )

    def sample(self, population, k: int):
        pop = list(population)
        return self._take("sample", (pop, k), lambda: self._rng.sample(pop, k))

    def shuffle(self, x) -> None:
        self.calls.append(("shuffle", (list(x),)))
        planned = self._plans.get("shuffle")
        if planned:
            x[:] = planned.pop(0)
        else:
            self._rng.shuffle(x)


# ==========================================================================
# fixture
# ==========================================================================
@pytest.fixture(autouse=True)
def _clean_game_state():
    """datas.demon_data 是模块级全局，用例之间必须清干净，不然有执行顺序依赖。"""
    game.datas.demon_data.clear()
    yield
    game.datas.demon_data.clear()


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """把 game 模块命名空间里的 time 换成假时钟（只影响 game，不动全局 time）。"""
    fake = FakeClock()
    monkeypatch.setattr(game, "time", fake)
    return fake


@pytest.fixture(autouse=True)
def rand(monkeypatch: pytest.MonkeyPatch) -> FakeRandom:
    """把 game 模块命名空间里的 random 换成可编排的假随机。"""
    fake = FakeRandom()
    monkeypatch.setattr(game, "random", fake)
    return fake


@pytest.fixture
def game_r(patch_storage):
    """game 的 JsonRedis 指到 tmp_path，绝不碰仓库里的 storage.json。"""
    return patch_storage(game)


@pytest.fixture
def game_bot(fake_bot, monkeypatch: pytest.MonkeyPatch):
    """check_timeout 里要 get_bots()，没有 bot 它会直接早退，所以必须补上。"""
    monkeypatch.setattr(game, "get_bots", lambda: {"10001": fake_bot})
    return fake_bot


@pytest.fixture
def group_event(make_group_event):
    """默认落在白名单群里的群消息事件工厂"""

    def _make(message: str = "", *, user_id: int = P0, group_id: int = GID, **kw):
        return make_group_event(message, user_id=user_id, group_id=group_id, **kw)

    return _make


# ==========================================================================
# 小工具
# ==========================================================================
async def drive(matcher, handler, bot, event, /, **kwargs) -> bool:
    """在 matcher 的上下文里跑一个 handler，返回它是不是 finish 了。

    前四个参数是仅位置的 —— handler 自己也收 bot= / event=，不这么写会撞名。

    handler 里的 matcher.finish() 走的是 Matcher.send()，而 send 从
    contextvar 里取 bot / event，所以必须套在 ensure_context 里。
    """
    with matcher().ensure_context(bot, event):
        try:
            await handler(**kwargs)
        except FinishedException:
            return True
    return False


_SEND_APIS = {"send_msg", "send_group_msg", "send_private_msg"}


def sent_texts(bot) -> list[str]:
    """FakeBot 上所有发消息类 api 的消息体，转成纯文本"""
    return [str(data["message"]) for api, data in bot.calls if api in _SEND_APIS]


def last_text(bot) -> str:
    texts = sent_texts(bot)
    assert texts, "handler 一条消息都没发出去"
    return texts[-1]


def make_state(
    *,
    players: tuple[str, str] = (str(P0), str(P1)),
    identity: int = 0,
    hp: tuple[int, int] = (4, 4),
    hp_max: int = 6,
    item_max: int = 6,
    clip: Optional[list[int]] = None,
    turn: int = 0,
    items0: tuple[int, ...] = (),
    items1: tuple[int, ...] = (),
    hcf: int = 0,
    atk: int = 0,
    add_atk: bool = False,
    game_turn: int = 1,
    start: bool = True,
    now: int = FAKE_NOW,
) -> dict:
    """直接摆一个进行中的对局状态。

    刻意从 demon_default() 出发再覆盖，这样以后 demon_default 加了字段，
    这里造出来的状态也还是完整的。
    """
    state = game.demon_default()
    state.update(
        {
            "pl": list(players),
            "hp": list(hp),
            "item_0": list(items0),
            "item_1": list(items1),
            "hcf": hcf,
            "clip": list(clip if clip is not None else [0, 1]),
            "turn": turn,
            "atk": atk,
            "hp_max": hp_max,
            "item_max": item_max,
            "game_turn": game_turn,
            "add_atk": add_atk,
            "start": start,
            "identity": identity,
            "demon_coldtime": now,
            "turn_start_time": now,
        }
    )
    game.datas.demon_data[GID_S] = state
    return state


def item_id_of(name: str) -> int:
    """道具名 -> 道具 id"""
    return next(i for i, n in game.item_dic.items() if n == name)


# ==========================================================================
# 白名单
# ==========================================================================
class TestWhitelist:
    """只有两个群能玩，别的群一律不响应"""

    @pytest.mark.parametrize("gid", GAME_WHITELIST_GROUP_IDS)
    def test_白名单群通过(self, group_event, gid):
        assert game.whitelist(group_event(group_id=gid)) is True

    @pytest.mark.parametrize("gid", [GID_OUTSIDE, 0, 1035708050, 870217477])
    def test_非白名单群被拒(self, group_event, gid):
        assert game.whitelist(group_event(group_id=gid)) is False

    async def test_rule_在白名单群里为真(self, fake_bot, group_event):
        assert await game.whitelist_rule(fake_bot, group_event(group_id=GID), {})

    async def test_rule_在别的群里为假(self, fake_bot, group_event):
        assert not await game.whitelist_rule(
            fake_bot, group_event(group_id=GID_OUTSIDE), {}
        )

    def test_game_里每个命令都挂了白名单(self):
        """恶魔投降历史上漏挂过 whitelist_rule，这条就是防它再漏。

        这里刻意不数命令个数：加一条新命令不该让这条红，该红的是
        「新命令忘了挂白名单」。所以断言的是「每一个都挂了」这个性质。
        """
        matchers = _game_matchers()
        assert matchers, "一个 matcher 都没扫到，说明反射逻辑坏了而不是真没命令"
        for name, matcher in matchers.items():
            calls = [checker.call for checker in matcher.rule.checkers]
            assert game.whitelist in calls, f"{name} 没挂白名单 rule"

    def test_轮盘的核心命令一个都不能少(self):
        """代替原来那句 `len(matchers) == 8`。

        数字变了只说明「命令数变了」，不说明变得对不对；这里列的是轮盘
        跑起来必须有的几条，少一条就是功能掉了，多一条不关它的事。
        """
        core = {"setmode", "betgame", "开枪", "使用", "查看局势", "恶魔投降",
                "恶魔道具", "恶魔帮助"}
        missing = sorted(core - _game_command_names())
        assert missing == [], f"game 里少了这些核心命令：{missing}"

    def test_game_的命令在全局注册表里都扫得到(self):
        """_all_registered_commands 是 help 那几条测试的地基，
        它要是扫不到 game 的命令，那边的「help 写了但没注册」就成了空断言。"""
        registered = set(_all_registered_commands())
        assert _game_command_names() <= registered


# ==========================================================================
# demon_default
# ==========================================================================
class TestDemonDefault:
    def test_字段与初值(self, clock):
        """比的是「这些字段必须在，且初值是这些」，不是「字典正好只有这些字段」。

        以后给对局状态加一个新字段不该让这条红 —— 那不是坏事，是加功能。
        真正要钉住的是状态机每一处都读的这几个初值（尤其 game_turn 从 1 起）。
        """
        d = game.demon_default()
        expected = {
            "pl": [],
            "hp": [],
            "item_0": [],
            "item_1": [],
            "hcf": 0,
            "clip": [],
            "turn": 0,
            "atk": 0,
            "hp_max": 0,
            "item_max": 0,
            "game_turn": 1,  # 轮数从 1 开始，不是 0
            "add_atk": False,
            "start": False,
            "identity": 0,
            "demon_coldtime": FAKE_NOW,
            "turn_start_time": FAKE_NOW,
        }
        missing = sorted(set(expected) - set(d))
        assert missing == [], f"demon_default 少了这些字段：{missing}"
        assert {k: d[k] for k in expected} == expected

    def test_每次都是新的可变对象(self):
        a, b = game.demon_default(), game.demon_default()
        a["pl"].append("1")
        assert b["pl"] == []


# ==========================================================================
# 道具表本身的完整性
# ==========================================================================
class TestItemTables:
    def test_两张子表按id把总表切成前后两段(self):
        """原来是三条写死数字的断言（26 / 1-15 / 16-26），加一个道具要手改四处。

        真正的不变量只有三条，而且都能从表本身推出来：两张子表不重叠、
        拼起来正好是总表、id 从 1 连续排到总数（item_dic1 在前，item_dic2 在后）。
        """
        n1, n2 = len(game.item_dic1), len(game.item_dic2)
        assert set(game.item_dic1) & set(game.item_dic2) == set()
        assert game.item_dic == game.item_dic1 | game.item_dic2
        assert sorted(game.item_dic) == list(range(1, n1 + n2 + 1))
        assert sorted(game.item_dic1) == list(range(1, n1 + 1))
        assert sorted(game.item_dic2) == list(range(n1 + 1, n1 + n2 + 1))

    def test_普通模式道具数就是子表1的大小(self):
        """get_random_item 拿 len(item_dic) - len(item_dic2) 当普通模式上限，
        这个差必须正好是 item_dic1 的大小 —— 是不是 15 无所谓，相等才是重点。"""
        assert len(game.item_dic) - len(game.item_dic2) == len(game.item_dic1)

    def test_道具名不重复(self):
        names = list(game.item_dic.values())
        assert len(set(names)) == len(names)

    def test_每个道具都有非空效果说明(self):
        """原来拆成「有说明」和「说明不为空」两条，其实是同一个不变量的两半。"""
        assert set(game.item_effects) == set(game.item_dic.values())
        for name, effect in game.item_effects.items():
            assert effect.strip(), f"{name} 的效果说明是空的"


# ==========================================================================
# load（上弹）
# ==========================================================================
class TestLoad:
    @pytest.mark.parametrize(
        ("bullets", "clip_size"),
        [(1, 3), (2, 3), (3, 5), (4, 7)],
    )
    def test_实弹数与弹夹容量(self, rand, bullets, clip_size):
        rand.plan("choices", [bullets])
        rand.plan("randint", clip_size)
        clip = game.load(0)
        assert len(clip) == clip_size
        assert clip.count(1) == bullets
        # 弹夹容量的下界是 实弹*2-1
        assert rand.args_of("randint")[0] == (bullets * 2 - 1, 8)

    def test_五发实弹时不再随机容量直接顶到8(self, rand):
        """min_clip_size = 5*2-1 = 9 > 8，代码走的是 else 分支，不调 randint。"""
        rand.plan("choices", [5])
        clip = game.load(0)
        assert len(clip) == 8
        assert clip.count(1) == 5
        assert rand.args_of("randint") == []

    def test_容量掷到1时退化成一空一实(self, rand):
        """clip_size == 1 的特殊分支：返回打乱过的 [0, 1]，长度是 2 不是 1。"""
        rand.plan("choices", [1])
        rand.plan("randint", 1)
        rand.plan("shuffle", [1, 0])
        clip = game.load(0)
        assert clip == [1, 0]
        assert len(clip) == 2

    def test_实弹数的权重表(self, rand):
        game.load(0)
        population, weights, k = rand.args_of("choices")[0]
        assert population == [1, 2, 3, 4, 5]
        assert weights == [0.3, 0.3, 0.2, 0.1, 0.1]
        assert k == 1

    def test_不变量_大量随机上弹(self, rand):
        """回落到固定种子的随机，跑 300 次，弹夹永远合法。"""
        for _ in range(300):
            clip = game.load(1)
            assert 2 <= len(clip) <= 8
            assert clip.count(1) >= 1
            assert set(clip) <= {0, 1}


# ==========================================================================
# calculate_interval（每轮补几个道具）
# ==========================================================================
class TestCalculateInterval:
    @pytest.mark.parametrize(
        ("add", "identity", "expected"),
        [
            (0, 0, (1, 3)),
            (1, 0, (2, 4)),
            (0, 1, (1, 3)),
            (1, 1, (3, 5)),  # 身份模式首轮加成翻倍
            (0, 2, (3, 5)),
            (1, 2, (4, 6)),
            (0, 999, (3, 5)),
            (1, 999, (4, 6)),
        ],
    )
    def test_区间表(self, add, identity, expected):
        assert game.calculate_interval(add, identity) == expected

    def test_未知模式回落到默认区间(self):
        assert game.calculate_interval(5, 42) == (1, 3)


# ==========================================================================
# get_random_item（按模式抽道具）
# ==========================================================================
class TestGetRandomItem:
    def test_普通模式只抽子表1里的道具(self, rand):
        """上限和期望范围都从表本身推，加道具不用回来改数字（线上传的也是
        len(item_dic) - len(item_dic2)，不是写死的 15）。"""
        limit = len(game.item_dic1)
        item = game.get_random_item(0, limit, "111")
        pool = rand.args_of("choice")[0][0]
        assert sorted(set(pool)) == list(range(1, limit + 1))
        assert len(pool) == limit  # 普通模式没有加权道具
        assert item in game.item_dic1

    def test_身份模式全量并且放大镜双权重(self, rand):
        total = len(game.item_dic)
        game.get_random_item(1, total, "111")
        pool = rand.args_of("choice")[0][0]
        assert sorted(set(pool)) == list(range(1, total + 1))
        assert pool.count(3) == 2, "放大镜的权重应该是 2"
        assert len(pool) == total + 1, "只有放大镜被复制了一份"

    def test_膀胱模式和身份模式共用同一张权重表(self, rand):
        total = len(game.item_dic)
        game.get_random_item(1, total, "111")
        game.get_random_item(2, total, "111")
        assert rand.args_of("choice")[0][0] == rand.args_of("choice")[1][0]

    def test_普通模式上限传0时候选池为空会炸(self):
        """normal_mode_limit=0 时 valid_items 是空的，random.choice([]) 抛
        IndexError。正常调用路径传的是 15，摸不到；但函数本身没有防守。"""
        with pytest.raises(IndexError):
            game.get_random_item(0, 0, "111")

    def test_999模式候选池为空会炸_疑似bug(self):
        """identity 999（注释里说是跑团专用）在 get_random_item 里没有分支，
        所有权重都是 0 -> valid_items 为空 -> random.choice([]) 抛 IndexError。
        setmode 只放 0/1/2 进来，所以线上摸不到，但这是个潜在坑。"""
        with pytest.raises(IndexError):
            game.get_random_item(999, 15, "111")


# ==========================================================================
# setmode
# ==========================================================================
class TestSetmode:
    @pytest.mark.parametrize("value", ["0", "1", "2"])
    async def test_三个合法值(self, game_r, fake_bot, group_event, value):
        event = group_event(f"*setmode {value}")
        assert await drive(
            game.setmode,
            game.handle_function,
            fake_bot,
            event,
            bot=fake_bot,
            event=event,
            arg=Message(value),
        )
        assert game_r.hget("game_mode", str(P0)) == value
        assert len(sent_texts(fake_bot)) == 1

    @pytest.mark.parametrize("bad", ["", "abc", "1.5", "-1", "1 2", "一"])
    async def test_非整数参数(self, game_r, fake_bot, group_event, bad):
        event = group_event("*setmode")
        await drive(
            game.setmode,
            game.handle_function,
            fake_bot,
            event,
            bot=fake_bot,
            event=event,
            arg=Message(bad),
        )
        assert len(sent_texts(fake_bot)) == 1, "该回一句，但别把模式写进去"
        assert game_r.hget("game_mode", str(P0)) is None

    @pytest.mark.parametrize("bad", ["3", "10", "999"])
    async def test_超出范围(self, game_r, fake_bot, group_event, bad):
        event = group_event("*setmode")
        await drive(
            game.setmode,
            game.handle_function,
            fake_bot,
            event,
            bot=fake_bot,
            event=event,
            arg=Message(bad),
        )
        assert len(sent_texts(fake_bot)) == 1
        assert game_r.hget("game_mode", str(P0)) is None

    async def test_设置跟人走不跟群走(self, game_r, fake_bot, make_group_event):
        """同一个人在另一个白名单群里设置，写的还是同一个 key。"""
        for gid in GAME_WHITELIST_GROUP_IDS:
            event = make_group_event("*setmode", user_id=P0, group_id=gid)
            await drive(
                game.setmode,
                game.handle_function,
                fake_bot,
                event,
                bot=fake_bot,
                event=event,
                arg=Message("2"),
            )
        assert game_r.hkeys("game_mode") == [str(P0)]
        assert game_r.hget("game_mode", str(P0)) == "2"


# ==========================================================================
# 存档
# ==========================================================================
class TestPersistence:
    async def test_模式设置重开进程也还在(self, game_r, fake_bot, group_event):
        event = group_event("*setmode")
        await drive(
            game.setmode,
            game.handle_function,
            fake_bot,
            event,
            bot=fake_bot,
            event=event,
            arg=Message("1"),
        )
        # 换一个全新的 JsonRedis 读同一个文件，模拟重启
        reloaded = JsonRedis(game_r.file_path)
        assert reloaded.hget("game_mode", str(P0)) == "1"
        assert reloaded.hkeys("game_mode") == [str(P0)]

    async def test_存储文件坏掉不影响插件(
        self, tmp_path, monkeypatch, fake_bot, group_event
    ):
        """JsonRedis 读到坏 json 会把文件挪走从空的开始，setmode 照样能用。"""
        broken = tmp_path / "broken.json"
        broken.write_text("{这不是 json", encoding="utf-8")
        redis = JsonRedis(broken)
        assert redis.data == {}
        assert broken.with_suffix(".json.broken").exists()

        monkeypatch.setattr(game, "r", redis)
        event = group_event("*setmode")
        await drive(
            game.setmode,
            game.handle_function,
            fake_bot,
            event,
            bot=fake_bot,
            event=event,
            arg=Message("2"),
        )
        assert redis.hget("game_mode", str(P0)) == "2"

    async def test_game_mode_存成了非哈希表也能自愈(
        self, tmp_path, monkeypatch, fake_bot, game_bot, group_event, rand
    ):
        """有人手改成 "game_mode": "oops" 的话，hkeys 返回空、hset 会把它
        重建成字典，开局流程不受影响。"""
        redis = JsonRedis(tmp_path / "weird.json")
        redis.set("game_mode", "oops")
        monkeypatch.setattr(game, "r", redis)
        assert redis.hkeys("game_mode") == []

        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        assert game.datas.demon_data[GID_S]["start"] is True
        assert redis.hget("game_mode", str(P0)) == "0"

    async def test_game_mode_里塞了非数字会炸_健壮性不足(
        self, tmp_path, monkeypatch, fake_bot, game_bot, group_event
    ):
        """开局时是 int(r.hget(...)) 硬转的，存了脏数据直接 ValueError 冒到
        handler 外面。这看着不对：一条脏记录能让整个群都开不了局。"""
        redis = JsonRedis(tmp_path / "dirty.json")
        redis.hset("game_mode", str(P0), "abc")
        redis.hset("game_mode", str(P1), "0")
        monkeypatch.setattr(game, "r", redis)

        await _join(game_bot, group_event, P0)
        with pytest.raises(ValueError, match="invalid literal for int"):
            event = group_event("*betgame", user_id=P1)
            with game.bet().ensure_context(game_bot, event):
                await game.bet_handle(
                    bot=game_bot, event=event, arg=Message("")
                )

    def test_对局状态本身不落盘(self, game_r):
        """datas.demon_data 只在内存里，重启就没了 —— 这是设计现状，不是 bug，
        但要有个用例把它钉住，免得以后有人以为对局能续上。"""
        make_state()
        assert game_r.keys("*") == []
        assert "1035708051" not in game_r.data


# ==========================================================================
# betgame：加入 / 开局
# ==========================================================================
async def _join(bot, group_event, user_id: int, *, nickname: str = "测试用户") -> bool:
    event = group_event("*betgame", user_id=user_id, nickname=nickname)
    return await drive(
        game.bet, game.bet_handle, bot, event, bot=bot, event=event, arg=Message("")
    )


class TestBetJoin:
    async def test_第一个人加入(self, game_r, game_bot, group_event):
        assert await _join(game_bot, group_event, P0, nickname="小卒")
        state = game.datas.demon_data[GID_S]
        assert state["pl"] == [str(P0)]
        assert state["start"] is False
        assert len(sent_texts(game_bot)) == 1
        # 昵称是数据不是措辞：加进来的是谁得报出来
        assert "小卒" in last_text(game_bot)

    async def test_同一个人不能重复加入(self, game_r, game_bot, group_event):
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P0)
        assert game.datas.demon_data[GID_S]["pl"] == [str(P0)]
        assert game.datas.demon_data[GID_S]["start"] is False
        assert len(sent_texts(game_bot)) == 2, "第二次也得有回话，只是不能开局"

    async def test_第二个人进来直接开局(self, game_r, game_bot, group_event, rand):
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        state = game.datas.demon_data[GID_S]
        assert state["pl"] == [str(P0), str(P1)]
        assert state["start"] is True
        assert state["game_turn"] == 1
        assert state["add_atk"] is False
        assert state["turn"] in (0, 1)
        assert len(state["clip"]) >= 2
        assert state["hp"][0] == state["hp"][1], "开局双方血量必须一样"
        assert state["identity"] == 0
        assert sent_texts(game_bot), "开局得有回话"

    async def test_开局后第三个人被挡(self, game_r, game_bot, group_event):
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        await _join(game_bot, group_event, P2)
        assert game.datas.demon_data[GID_S]["pl"] == [str(P0), str(P1)]
        assert game.datas.demon_data[GID_S]["start"] is True, "第三个人不该把局搅黄"

    async def test_没设过模式的人会被写默认0(self, game_r, game_bot, group_event):
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        assert game_r.hget("game_mode", str(P0)) == "0"
        assert game_r.hget("game_mode", str(P1)) == "0"

    async def test_两人模式一致直接用那个(self, game_r, game_bot, group_event, rand):
        game_r.hset("game_mode", str(P0), "1")
        game_r.hset("game_mode", str(P1), "1")
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        assert game.datas.demon_data[GID_S]["identity"] == 1
        # 模式一致的时候不走 random.choice
        assert [a for a in rand.args_of("choice") if a[0] == [1, 1]] == []

    async def test_两人模式不同时二选一(self, game_r, game_bot, group_event, rand):
        game_r.hset("game_mode", str(P0), "0")
        game_r.hset("game_mode", str(P1), "2")
        rand.plan("choice", 2)
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        assert rand.args_of("choice")[0] == ([0, 2],)
        assert game.datas.demon_data[GID_S]["identity"] == 2

    async def test_两个白名单群互不干扰(self, game_r, game_bot, make_group_event):
        other = GAME_WHITELIST_GROUP_IDS[1]

        def ev(gid, uid):
            return make_group_event("*betgame", user_id=uid, group_id=gid)

        for gid, uid in ((GID, P0), (other, P2)):
            e = ev(gid, uid)
            await drive(
                game.bet, game.bet_handle, game_bot, e, bot=game_bot, event=e,
                arg=Message(""),
            )
        assert game.datas.demon_data[GID_S]["pl"] == [str(P0)]
        assert game.datas.demon_data[str(other)]["pl"] == [str(P2)]


class TestBetStartArithmetic:
    """开局的数值必须精确，help 里就是照着这几个数写的"""

    @pytest.mark.parametrize(
        ("mode", "hp_range", "hp_max", "item_max"),
        [
            (0, (3, 6), 6, 6),
            (1, (6, 10), 10, 8),
            (2, (9, 14), 16, 10),
        ],
    )
    async def test_血量道具上限(
        self, game_r, game_bot, group_event, rand, mode, hp_range, hp_max, item_max
    ):
        game_r.hset("game_mode", str(P0), str(mode))
        game_r.hset("game_mode", str(P1), str(mode))
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        state = game.datas.demon_data[GID_S]

        # 第一次 randint 就是抽血量，直接断言它的上下界
        assert rand.args_of("randint")[0] == hp_range
        assert hp_range[0] <= state["hp"][0] <= hp_range[1]
        assert state["hp_max"] == hp_max
        assert state["item_max"] == item_max
        assert state["identity"] == mode

    @pytest.mark.parametrize(
        ("mode", "interval"), [(0, (2, 4)), (1, (3, 5)), (2, (4, 6))]
    )
    async def test_开局补道具的区间(
        self, game_r, game_bot, group_event, rand, mode, interval
    ):
        """game_turn == 1 时 game_turn_add = 1，区间按 calculate_interval 走。"""
        game_r.hset("game_mode", str(P0), str(mode))
        game_r.hset("game_mode", str(P1), str(mode))
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        # randint 调用顺序：血量 -> 弹夹容量(可能没有) -> 先手 -> 补道具数量
        assert interval in rand.args_of("randint")
        state = game.datas.demon_data[GID_S]
        assert len(state["item_0"]) == len(state["item_1"])
        assert interval[0] <= len(state["item_0"]) <= interval[1]

    async def test_普通模式只发前15个道具(self, game_r, game_bot, group_event):
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        state = game.datas.demon_data[GID_S]
        assert set(state["item_0"]) <= set(game.item_dic1)
        assert set(state["item_1"]) <= set(game.item_dic1)

    async def test_先手随机在01之间(self, game_r, game_bot, group_event, rand):
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        assert (0, 1) in rand.args_of("randint")


# ==========================================================================
# death_mode（死斗）
# ==========================================================================
class TestDeathMode:
    def test_轮数限制表(self):
        assert game.turn_limit == {1: game.death_turn, 2: game.pangguang_turn}
        assert game.death_turn == 12
        assert game.pangguang_turn == 5

    def test_普通模式永不死斗(self):
        make_state(identity=0, game_turn=99)
        assert str(game.death_mode(0, GID_S)) == ""

    @pytest.mark.parametrize(
        ("identity", "turn", "should_fire"),
        [(1, 11, False), (1, 12, False), (1, 13, True), (2, 5, False), (2, 6, True)],
    )
    def test_触发轮数边界(self, identity, turn, should_fire):
        make_state(identity=identity, game_turn=turn, hp_max=10, item_max=8)
        msg = str(game.death_mode(identity, GID_S))
        assert bool(msg) is should_fire

    def test_扣血量上限并把血量夹回来(self):
        state = make_state(identity=1, game_turn=13, hp=(10, 4), hp_max=10, item_max=6)
        game.death_mode(1, GID_S)
        assert state["hp_max"] == 9
        assert state["hp"] == [9, 4]

    def test_道具上限最低到6就不再扣(self, rand):
        state = make_state(identity=1, game_turn=13, hp_max=10, item_max=6)
        game.death_mode(1, GID_S)
        assert state["item_max"] == 6

    def test_道具上限大于6时扣一点(self, rand):
        state = make_state(identity=1, game_turn=13, hp_max=10, item_max=8)
        game.death_mode(1, GID_S)
        assert state["item_max"] == 7

    def test_随机销毁道具(self, rand):
        rand.plan("randint", 2)  # remove_random = 2
        rand.plan("sample", [1, 2], [3])
        state = make_state(
            identity=1, game_turn=13, hp_max=10, item_max=8,
            items0=(1, 2, 3), items1=(3,),
        )
        game.death_mode(1, GID_S)
        assert state["item_0"] == [3]
        assert state["item_1"] == []

    def test_道具栏空的时候不销毁也不炸(self, rand):
        """random.sample 摸空列表是要抛异常的，这条守的就是那一下。"""
        rand.plan("randint", 2)
        state = make_state(identity=1, game_turn=13, hp_max=10, item_max=8)
        assert str(game.death_mode(1, GID_S))  # 死斗还是得发话
        assert state["item_0"] == []
        assert state["item_1"] == []

    def test_血量上限已经是1就不再扣(self):
        state = make_state(identity=1, game_turn=13, hp=(1, 1), hp_max=1, item_max=6)
        game.death_mode(1, GID_S)
        assert state["hp_max"] == 1

    def test_999分支是死代码(self):
        """死斗的外层判断是 `identity_found in turn_limit`，turn_limit 只有
        1 和 2 两个 key，所以 `elif identity_found == 999` 那段永远进不去。
        看着不对：注释说 999 是跑团专用模式，要扣 2 点血量上限。"""
        state = make_state(identity=999, game_turn=999, hp_max=10)
        assert str(game.death_mode(999, GID_S)) == ""
        assert state["hp_max"] == 10


# ==========================================================================
# refersh_item（补道具）
# ==========================================================================
class TestRefershItem:
    def test_按区间补给双方(self, rand):
        rand.plan("randint", 3)
        rand.plan("choice", 1, 2, 3, 4, 5, 6)
        state = make_state()
        assert str(game.refersh_item(0, GID_S))
        # 两人交替发牌：奇数次给 0 号，偶数次给 1 号
        assert state["item_0"] == [1, 3, 5]
        assert state["item_1"] == [2, 4, 6]

    def test_超过道具上限的部分被截掉(self, rand):
        rand.plan("randint", 4)
        state = make_state(item_max=3, items0=(1, 1), items1=())
        game.refersh_item(0, GID_S)
        assert len(state["item_0"]) == 3
        assert len(state["item_1"]) == 3

    def test_区间掷到0就一个都不补(self, rand):
        rand.plan("randint", 0)
        state = make_state()
        assert str(game.refersh_item(0, GID_S))
        assert state["item_0"] == []
        assert state["item_1"] == []

    def test_首轮才有加成(self, rand):
        make_state(game_turn=1)
        game.refersh_item(0, GID_S)
        assert rand.args_of("randint")[0] == (2, 4)

        rand.calls.clear()
        make_state(game_turn=2)
        game.refersh_item(0, GID_S)
        assert rand.args_of("randint")[0] == (1, 3)


# ==========================================================================
# 开枪：拦截条件
# ==========================================================================
async def _fire(bot, group_event, target: str, *, user_id: int = P0) -> bool:
    event = group_event(f"*开枪 {target}", user_id=user_id)
    return await drive(
        game.fire, game.fire_handle, bot, event, bot=bot, event=event,
        arg=Message(target),
    )


class TestFireGuards:
    """四条拦截分支。被拦下来的标志不是回了哪句话，而是「这一枪根本没打出去」：
    弹夹没少子弹、血没掉、回合没交出去，而且只回了一句。
    """

    async def test_没开局(self, game_bot, group_event):
        state = make_state(start=False, hp=(4, 4), clip=[0, 1, 1], turn=0)
        await _fire(game_bot, group_event, "对方")
        assert state["clip"] == [0, 1, 1] and state["hp"] == [4, 4]
        assert len(sent_texts(game_bot)) == 1

    async def test_局外人不能动手(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[0, 1, 1], turn=0)
        await _fire(game_bot, group_event, "对方", user_id=P2)
        assert state["clip"] == [0, 1, 1] and state["hp"] == [4, 4]
        assert state["turn"] == 0
        assert len(sent_texts(game_bot)) == 1

    async def test_不是自己的回合(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[0, 1, 1], turn=0)
        await _fire(game_bot, group_event, "对方", user_id=P1)
        assert state["clip"] == [0, 1, 1] and state["hp"] == [4, 4]
        assert state["turn"] == 0, "回合不能被抢走"
        assert len(sent_texts(game_bot)) == 1

    @pytest.mark.parametrize("bad", ["", "自已", "别人", "self"])
    async def test_参数不认识(self, game_bot, group_event, bad):
        state = make_state(hp=(4, 4), clip=[0, 1, 1], turn=0)
        await _fire(game_bot, group_event, bad)
        assert state["clip"] == [0, 1, 1] and state["hp"] == [4, 4]
        assert state["turn"] == 0
        assert len(sent_texts(game_bot)) == 1

    async def test_没开局的群里第一条指令不会炸(self, game_bot, group_event):
        """check_timeout 会顺手把群的默认状态建出来，所以不会 KeyError。"""
        assert GID_S not in game.datas.demon_data
        await _fire(game_bot, group_event, "对方")
        assert GID_S in game.datas.demon_data
        assert len(sent_texts(game_bot)) == 1


class TestShoot:
    async def test_打对方命中(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[0, 1, 1], turn=0)
        await _fire(game_bot, group_event, "对方")
        assert state["hp"] == [4, 3]
        assert state["turn"] == 1, "打对方要交出回合"
        assert state["clip"] == [0, 1]
        assert len(sent_texts(game_bot)) == 1

    async def test_打对方空枪(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[1, 0], turn=0)
        await _fire(game_bot, group_event, "对方")
        assert state["hp"] == [4, 4], "空弹不该扣血"
        assert state["turn"] == 1
        assert state["clip"] == [1], "空弹一样要退出膛"
        assert len(sent_texts(game_bot)) == 1

    async def test_打自己命中回合不交出(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[0, 1, 1], turn=0)
        await _fire(game_bot, group_event, "自己")
        assert state["hp"] == [3, 4]
        assert state["turn"] == 0, "打自己不管中不中都留着回合"

    async def test_打自己空枪回合不交出(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[1, 1, 0], turn=0)
        await _fire(game_bot, group_event, "自己")
        assert state["hp"] == [4, 4]
        assert state["turn"] == 0

    async def test_后手打对方扣的是玩家0(self, game_bot, group_event):
        """hp[pl - stp] 这个下标写法在 pl=1 时靠的是 1-1=0，别写反了。"""
        state = make_state(hp=(4, 4), clip=[0, 1, 1], turn=1)
        await _fire(game_bot, group_event, "对方", user_id=P1)
        assert state["hp"] == [3, 4]
        assert state["turn"] == 0

    @pytest.mark.parametrize("atk", [1, 2, 3, 4, 5])
    async def test_加伤生效并且打完清零(self, game_bot, group_event, atk):
        """atk 3 / 5 各有一句额外的吹嘘台词，那是措辞；伤害算对了才是行为。"""
        state = make_state(hp=(4, 9), hp_max=9, clip=[0, 1, 1], turn=0, atk=atk, add_atk=True)
        await _fire(game_bot, group_event, "对方")
        assert state["hp"][1] == 9 - (1 + atk)
        assert state["atk"] == 0
        assert state["add_atk"] is False

    async def test_空枪也会把加伤清掉(self, game_bot, group_event):
        state = make_state(clip=[1, 0], turn=0, atk=3, add_atk=True)
        await _fire(game_bot, group_event, "对方")
        assert state["atk"] == 0
        assert state["add_atk"] is False

    async def test_实弹打光就换弹并且轮数加一(self, game_bot, group_event, rand):
        rand.plan("choices", [1])
        rand.plan("randint", 3)  # 新弹夹容量
        rand.plan("sample", [0])
        rand.plan("randint", 0)  # 补道具数量
        state = make_state(hp=(4, 4), clip=[0, 1], turn=0, game_turn=1)
        await _fire(game_bot, group_event, "对方")
        assert state["game_turn"] == 2
        assert state["clip"] == [1, 0, 0]

    async def test_打光对方的血就结算并重置(self, game_bot, group_event):
        make_state(hp=(4, 1), clip=[0, 1, 1], turn=0)
        await _fire(game_bot, group_event, "对方")
        # 赢家的 QQ 号是结算的结果，不是措辞
        assert f"[CQ:at,qq={P0}]" in last_text(game_bot), "赢的应该是开枪的人"
        # 状态被重置回默认
        state = game.datas.demon_data[GID_S]
        assert state["start"] is False
        assert state["pl"] == []

    async def test_打自己打死自己算对方赢(self, game_bot, group_event):
        make_state(hp=(1, 4), clip=[0, 1, 1], turn=0)
        await _fire(game_bot, group_event, "自己")
        assert f"[CQ:at,qq={P1}]" in last_text(game_bot)
        assert game.datas.demon_data[GID_S]["start"] is False

    async def test_手铐让当前玩家多打一枪(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[1, 1, 0], turn=0, hcf=1)
        await _fire(game_bot, group_event, "对方")
        assert state["turn"] == 0, "对方被拷住，回合留在自己手里"
        assert state["hcf"] == -1

    async def test_束缚耗尽后回合交还(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[1, 1, 0], turn=0, hcf=-1)
        await _fire(game_bot, group_event, "对方")
        assert state["hcf"] == 0
        assert state["turn"] == 1

    async def test_打自己不消耗束缚(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[1, 1, 0], turn=0, hcf=1)
        await _fire(game_bot, group_event, "自己")
        assert state["hcf"] == 1, "stp==0 走的是另一条分支，不扣束缚"
        assert state["turn"] == 0

    async def test_开枪会刷新回合计时(self, game_bot, group_event, clock):
        state = make_state(clip=[1, 1, 0], turn=0, now=FAKE_NOW - 100)
        clock.advance(60)
        await _fire(game_bot, group_event, "对方")
        assert state["turn_start_time"] == clock.now


# ==========================================================================
# 超时
# ==========================================================================
class TestTimeout:
    def test_超时阈值是十分钟(self):
        assert game.turn_time == 600

    @pytest.mark.parametrize(("elapsed", "timed_out"), [(599, False), (600, False), (601, True)])
    async def test_边界(self, game_bot, clock, elapsed, timed_out):
        make_state(now=FAKE_NOW)
        clock.advance(elapsed)
        assert await game.check_timeout(GID_S) is timed_out

    async def test_超时判当前回合玩家负(self, game_bot, clock):
        make_state(turn=0)
        clock.advance(601)
        assert await game.check_timeout(GID_S) is True
        state = game.datas.demon_data[GID_S]
        assert state["start"] is False and state["pl"] == []
        api, data = game_bot.calls[-1]
        assert api == "send_group_msg", "超时是主动推送，不是回话"
        assert str(data["group_id"]) == GID_S
        # 判负和判胜的是谁，是结算结果；「自动判负」四个字是措辞
        msg = str(data["message"])
        assert f"[CQ:at,qq={P0}]" in msg and f"[CQ:at,qq={P1}]" in msg

    async def test_只有一个人等太久就重置(self, game_bot, clock):
        state = make_state(start=False, now=FAKE_NOW)
        state["pl"] = [str(P0)]
        clock.advance(601)
        assert await game.check_timeout(GID_S) is True
        assert game.datas.demon_data[GID_S]["pl"] == []
        assert len(sent_texts(game_bot)) == 1, "重置了就得吱一声"

    async def test_一个人都没有就什么也不做(self, game_bot, clock):
        make_state(start=False)
        game.datas.demon_data[GID_S]["pl"] = []
        clock.advance(601)
        assert await game.check_timeout(GID_S) is False
        assert game_bot.calls == []

    async def test_超时会让开枪指令直接返回(self, game_bot, group_event, clock):
        make_state(turn=0)
        clock.advance(601)
        finished = await _fire(game_bot, group_event, "对方")
        assert finished is False, "超时分支是 return，不是 finish"
        # 只有超时那一条推送，开枪本身不该再回话
        assert len(sent_texts(game_bot)) == 1
        assert game.datas.demon_data[GID_S]["pl"] == []

    async def test_没有bot实例时直接早退(self, monkeypatch, fake_bot):
        """get_bots() 为空时 check_timeout 返回 None 且不建默认状态。
        这看着不对：返回值既不是 True 也不是 False，调用方
        `if await check_timeout(...): return` 会当没超时继续往下走，
        而下一行就是 datas.demon_data[group_id][...]，会 KeyError。"""
        monkeypatch.setattr(game, "get_bots", dict)
        assert await game.check_timeout(GID_S) is None
        assert GID_S not in game.datas.demon_data

    async def test_定时任务只扫数字群号(self, game_bot, clock):
        make_state(turn=0)
        junk = game.demon_default()
        junk["pl"] = [str(P2)]
        game.datas.demon_data["不是群号"] = junk
        clock.advance(601)
        await game.check_all_games()
        assert game.datas.demon_data[GID_S]["pl"] == []
        # 非数字的 key 被 isdigit 判断挡掉，一条消息都不会发
        assert game.datas.demon_data["不是群号"]["pl"] == [str(P2)]
        assert len(sent_texts(game_bot)) == 1

    def test_定时任务已注册(self):
        jobs = [j for j in game.scheduler.get_jobs() if j.func is game.check_all_games]
        assert len(jobs) == 1


# ==========================================================================
# 使用道具：拦截条件
# ==========================================================================
async def _use(bot, group_event, name: str, *, user_id: int = P0) -> bool:
    event = group_event(f"*使用 {name}", user_id=user_id)
    return await drive(
        game.prop_demon, game.prop_demon_handle, bot, event,
        bot=bot, event=event, arg=Message(name),
    )


class TestUseItemGuards:
    """五条拦截分支。被拦下来的标志是「道具没被吃掉、血没变、回合没动」，
    回的是哪句话不管。
    """

    async def test_没开局(self, game_bot, group_event):
        state = make_state(start=False, hp=(3, 4), items0=(1,))
        await _use(game_bot, group_event, "桃")
        assert state["item_0"] == [1] and state["hp"] == [3, 4]
        assert len(sent_texts(game_bot)) == 1

    async def test_局外人(self, game_bot, group_event):
        state = make_state(hp=(3, 4), items0=(1,))
        await _use(game_bot, group_event, "桃", user_id=P2)
        assert state["item_0"] == [1] and state["hp"] == [3, 4]
        assert len(sent_texts(game_bot)) == 1

    async def test_不是自己的回合(self, game_bot, group_event):
        state = make_state(turn=0, hp=(3, 4), items1=(1,))
        await _use(game_bot, group_event, "桃", user_id=P1)
        assert state["item_1"] == [1] and state["hp"] == [3, 4]
        assert state["turn"] == 0, "回合不能被抢走"
        assert len(sent_texts(game_bot)) == 1

    @pytest.mark.parametrize("name", ["不存在的道具", "peach", ""])
    async def test_道具名不存在(self, game_bot, group_event, name):
        state = make_state(hp=(3, 4), items0=(1,))
        await _use(game_bot, group_event, name)
        assert state["item_0"] == [1], "名字不认识就不该动道具栏"
        assert state["hp"] == [3, 4]
        assert len(sent_texts(game_bot)) == 1

    async def test_道具名对但自己没有(self, game_bot, group_event):
        state = make_state(hp=(3, 4), items0=(2,))
        await _use(game_bot, group_event, "桃")
        assert state["item_0"] == [2], "手里没有的道具不能凭空用掉"
        assert state["hp"] == [3, 4]
        assert len(sent_texts(game_bot)) == 1

    async def test_道具名忽略大小写(self, game_bot, group_event):
        """唯一带拉丁字母的道具是「烈性TNT」，小写也得认。

        认没认出来看的是道具有没有被吃掉，不是回话怎么写。
        """
        tnt = item_id_of("烈性TNT")
        state = make_state(identity=1, hp=(5, 5), hp_max=10, items0=(tnt,))
        await _use(game_bot, group_event, "烈性tnt")
        assert state["item_0"] == []
        assert state["hp_max"] == 9, "TNT 的效果真的结算了"

    async def test_只消耗一个同名道具(self, game_bot, group_event):
        state = make_state(hp=(3, 4), items0=(1, 1, 1))
        await _use(game_bot, group_event, "桃")
        assert state["item_0"] == [1, 1]

    async def test_使用道具会刷新回合计时(self, game_bot, group_event, clock):
        state = make_state(items0=(1,), now=FAKE_NOW - 100)
        clock.advance(30)
        await _use(game_bot, group_event, "桃")
        assert state["turn_start_time"] == clock.now


class TestItemsCoverage:
    async def test_每个道具都有对应分支(self, game_bot, group_event):
        """if/elif 链漏掉任何一个道具，就会掉进最后那条「无法使用」的 else。

        怎么在不钉措辞的前提下发现「掉进 else」：else 分支和道具本身无关，
        两个都掉进去的道具会给出**除了名字以外一模一样**的回话。所以把名字
        抠掉之后，26 条回话必须两两不同 —— 谁跟谁撞了，谁就漏写了分支。
        文案随便改，这条都不会红。
        """
        bodies: dict[str, str] = {}
        for i, name in enumerate(sorted(game.item_dic.values()), start=1):
            item = item_id_of(name)
            state = make_state(
                identity=1, hp=(5, 5), hp_max=10, item_max=8,
                clip=[0, 1, 0, 1], items0=(item,), items1=(1,), turn=0,
            )
            await _use(game_bot, group_event, name)
            assert len(sent_texts(game_bot)) == i, f"{name} 用完没回话"
            assert state["hp_max"] >= 1, f"{name} 把血量上限打到 0 以下了"
            bodies[name] = last_text(game_bot).replace(name, "")

        seen: dict[str, str] = {}
        for name, body in bodies.items():
            twin = seen.setdefault(body, name)
            assert twin == name, f"{name} 和 {twin} 的回话完全一样，多半漏写了效果分支"


class TestItemEffects:
    async def test_桃_回血封顶(self, game_bot, group_event):
        state = make_state(hp=(3, 4), hp_max=6, items0=(item_id_of("桃"),))
        await _use(game_bot, group_event, "桃")
        assert state["hp"] == [4, 4]

    async def test_桃_满血时不溢出(self, game_bot, group_event):
        state = make_state(hp=(6, 4), hp_max=6, items0=(item_id_of("桃"),))
        await _use(game_bot, group_event, "桃")
        assert state["hp"] == [6, 4]

    async def test_医疗箱_回二点跳回合解束缚(self, game_bot, group_event):
        state = make_state(hp=(2, 4), hp_max=6, hcf=3, atk=2,
                           items0=(item_id_of("医疗箱"),))
        await _use(game_bot, group_event, "医疗箱")
        assert state["hp"] == [4, 4]
        assert state["hcf"] == 0
        assert state["atk"] == 0
        assert state["turn"] == 1

    @pytest.mark.parametrize(
        ("name", "clips"),
        [
            ("放大镜", ([0, 0, 1], [0, 0, 0])),
            ("眼镜", ([1, 1, 0, 1], [1, 1, 1, 1], [1])),
            ("墨镜", ([1, 0, 0, 1], [0, 0, 0, 1], [0])),
        ],
    )
    async def test_窥视类道具_不同弹夹给出不同结论(
        self, game_bot, group_event, name, clips
    ):
        """放大镜 / 眼镜 / 墨镜 只报「膛里是什么」，报出来的句子怎么写不管，
        但不同的弹夹必须报出不同的结论 —— 报得一模一样就是没在看弹夹。
        每组的最后一个弹夹是「只剩一发」那条单独的分支。
        """
        replies = []
        for clip in clips:
            state = make_state(clip=list(clip), items0=(item_id_of(name),))
            await _use(game_bot, group_event, name)
            assert state["item_0"] == [], f"{name} 没被消耗"
            assert state["clip"] == list(clip), f"{name} 只是看，不该动弹夹"
            replies.append(last_text(game_bot))
        assert len(set(replies)) == len(clips), f"{name} 对不同弹夹给了一样的回话"

    async def test_手铐(self, game_bot, group_event):
        state = make_state(hcf=0, items0=(item_id_of("手铐"),))
        await _use(game_bot, group_event, "手铐")
        assert state["hcf"] == 1
        assert state["item_0"] == []

    async def test_手铐_已经拷着就退回道具(self, game_bot, group_event):
        cuff = item_id_of("手铐")
        state = make_state(hcf=1, items0=(cuff,))
        await _use(game_bot, group_event, "手铐")
        assert state["hcf"] == 1
        assert state["item_0"] == [cuff], "用不掉的道具要还回来"

    @pytest.mark.parametrize(("roll", "hcf"), [(0, 1), (1, 3)])
    async def test_禁止卡_禁一到两回合(self, game_bot, group_event, rand, roll, hcf):
        ban = item_id_of("禁止卡")
        rand.plan("randint", roll)
        state = make_state(identity=1, hp_max=10, item_max=8,
                           items0=(ban,), items1=())
        await _use(game_bot, group_event, "禁止卡")
        assert state["hcf"] == hcf
        assert state["item_1"] == [ban], "对方会白捡一张禁止卡"

    async def test_禁止卡_对方道具满了就不给(self, game_bot, group_event, rand):
        ban = item_id_of("禁止卡")
        rand.plan("randint", 0)
        state = make_state(item_max=2, items0=(ban,), items1=(1, 1))
        await _use(game_bot, group_event, "禁止卡")
        assert state["item_1"] == [1, 1]

    async def test_小刀_伤害变二(self, game_bot, group_event):
        state = make_state(atk=0, items0=(item_id_of("小刀"),))
        await _use(game_bot, group_event, "小刀")
        assert state["atk"] == 1

    async def test_小刀_烈弓之后可以叠加(self, game_bot, group_event):
        knife = item_id_of("小刀")
        state = make_state(identity=1, hp_max=10, atk=2, add_atk=True,
                           items0=(knife, knife))
        await _use(game_bot, group_event, "小刀")
        assert state["atk"] == 3

    async def test_酒_残血时额外回血(self, game_bot, group_event):
        state = make_state(hp=(1, 4), items0=(item_id_of("酒"),))
        await _use(game_bot, group_event, "酒")
        assert state["atk"] == 1
        assert state["hp"] == [2, 4]

    async def test_酒_血量不是1就不回(self, game_bot, group_event):
        state = make_state(hp=(2, 4), items0=(item_id_of("酒"),))
        await _use(game_bot, group_event, "酒")
        assert state["hp"] == [2, 4]

    async def test_烈弓_开启无限叠加(self, game_bot, group_event):
        state = make_state(identity=1, hp_max=10, atk=0,
                           items0=(item_id_of("烈弓"),))
        await _use(game_bot, group_event, "烈弓")
        assert state["atk"] == 1
        assert state["add_atk"] is True

    async def test_啤酒_退一发(self, game_bot, group_event):
        state = make_state(clip=[0, 1, 1], items0=(item_id_of("啤酒"),))
        await _use(game_bot, group_event, "啤酒")
        assert state["clip"] == [0, 1]

    async def test_啤酒_退掉最后一发实弹就换弹加轮(self, game_bot, group_event, rand):
        rand.plan("choices", [1])
        rand.plan("randint", 2)
        rand.plan("sample", [0])
        rand.plan("randint", 0)
        state = make_state(clip=[0, 1], game_turn=1, items0=(item_id_of("啤酒"),))
        await _use(game_bot, group_event, "啤酒")
        assert state["game_turn"] == 2
        assert state["clip"] == [1, 0]

    async def test_手套_只换弹不刷道具(self, game_bot, group_event, rand):
        rand.plan("choices", [2])
        rand.plan("randint", 4)
        rand.plan("sample", [0, 3])
        state = make_state(clip=[0, 1], game_turn=1, items0=(item_id_of("手套"),))
        await _use(game_bot, group_event, "手套")
        assert state["clip"] == [1, 0, 0, 1]
        assert state["game_turn"] == 1, "换弹不算新的一轮"
        assert state["item_1"] == [], "手套不给对方补道具"

    @pytest.mark.parametrize(
        ("roll", "hp_max", "expected"),
        [(1, 6, 1), (4, 6, 4), (4, 3, 3), (2, 3, 2)],
    )
    async def test_骰子_取一到四再夹上限(
        self, game_bot, group_event, rand, roll, hp_max, expected
    ):
        rand.plan("randint", roll)
        state = make_state(hp=(6, 4), hp_max=hp_max, items0=(item_id_of("骰子"),))
        await _use(game_bot, group_event, "骰子")
        assert state["hp"][0] == expected
        assert rand.args_of("randint")[0] == (1, 4)

    async def test_刷新票_数量不变(self, game_bot, group_event, rand):
        ticket = item_id_of("刷新票")
        rand.plan("choice", 2, 3)
        state = make_state(items0=(ticket, 1, 1))
        await _use(game_bot, group_event, "刷新票")
        assert state["item_0"] == [2, 3]

    async def test_刷新票_只有它自己(self, game_bot, group_event):
        state = make_state(items0=(item_id_of("刷新票"),))
        await _use(game_bot, group_event, "刷新票")
        assert state["item_0"] == []

    @pytest.mark.parametrize("roll", [1, 5])
    async def test_欲望之盒_抽道具(self, game_bot, group_event, rand, roll):
        rand.plan("randint", roll)
        rand.plan("choice", 5)
        state = make_state(items0=(item_id_of("欲望之盒"),))
        await _use(game_bot, group_event, "欲望之盒")
        assert state["item_0"] == [5]

    @pytest.mark.parametrize("roll", [6, 8])
    async def test_欲望之盒_回血(self, game_bot, group_event, rand, roll):
        rand.plan("randint", roll)
        state = make_state(hp=(3, 4), items0=(item_id_of("欲望之盒"),))
        await _use(game_bot, group_event, "欲望之盒")
        assert state["hp"] == [4, 4]

    async def test_欲望之盒_满血转成桃(self, game_bot, group_event, rand):
        rand.plan("randint", 6)
        state = make_state(hp=(6, 4), hp_max=6, items0=(item_id_of("欲望之盒"),))
        await _use(game_bot, group_event, "欲望之盒")
        assert state["hp"] == [6, 4]
        assert state["item_0"] == [1], "回不了血就折成一个桃"

    @pytest.mark.parametrize("roll", [9, 10])
    async def test_欲望之盒_打对面(self, game_bot, group_event, rand, roll):
        rand.plan("randint", roll)
        state = make_state(hp=(4, 4), items0=(item_id_of("欲望之盒"),))
        await _use(game_bot, group_event, "欲望之盒")
        assert state["hp"] == [4, 3]

    async def test_无中生有_没束缚就跳回合(self, game_bot, group_event, rand):
        rand.plan("choice", 1, 2)
        state = make_state(hcf=0, atk=3, items0=(item_id_of("无中生有"),))
        await _use(game_bot, group_event, "无中生有")
        assert state["item_0"] == [1, 2]
        assert state["turn"] == 1
        assert state["atk"] == 0

    async def test_无中生有_有束缚就扣束缚(self, game_bot, group_event, rand):
        rand.plan("choice", 1, 2)
        state = make_state(hcf=3, atk=3, items0=(item_id_of("无中生有"),))
        await _use(game_bot, group_event, "无中生有")
        assert state["hcf"] == 1
        assert state["turn"] == 0, "回合留在自己这儿"
        assert state["atk"] == 3, "这条分支不清加伤"

    async def test_天秤_道具多就打人(self, game_bot, group_event):
        scale = item_id_of("天秤")
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(scale, 1, 1), items1=(1,))
        await _use(game_bot, group_event, "天秤")
        assert state["hp"] == [5, 4]

    async def test_天秤_道具少就回血(self, game_bot, group_event):
        scale = item_id_of("天秤")
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(scale,), items1=(1, 1))
        await _use(game_bot, group_event, "天秤")
        assert state["hp"] == [6, 5]

    async def test_休养生息_对面满血只回自己一点(self, game_bot, group_event):
        state = make_state(identity=1, hp=(5, 10), hp_max=10, item_max=8,
                           items0=(item_id_of("休养生息"),))
        await _use(game_bot, group_event, "休养生息")
        assert state["hp"] == [6, 10]

    async def test_休养生息_双方都回并夹上限(self, game_bot, group_event):
        state = make_state(identity=1, hp=(9, 5), hp_max=10, item_max=8,
                           items0=(item_id_of("休养生息"),))
        await _use(game_bot, group_event, "休养生息")
        assert state["hp"] == [10, 6]
        assert state["turn"] == 0, "休养生息不跳回合"

    @pytest.mark.parametrize(("roll", "hp1"), [(1, 4), (2, 5)])
    async def test_玩具枪(self, game_bot, group_event, rand, roll, hp1):
        rand.plan("randint", roll)
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(item_id_of("玩具枪"),))
        await _use(game_bot, group_event, "玩具枪")
        assert state["hp"] == [5, hp1]

    async def test_血刃_扣血换两个道具(self, game_bot, group_event, rand):
        rand.plan("randint", 1)
        rand.plan("choice", 1, 2)
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(item_id_of("血刃"),))
        await _use(game_bot, group_event, "血刃")
        assert state["hp"] == [4, 5]
        assert state["item_0"] == [1, 2]

    async def test_血刃_一滴血用不了(self, game_bot, group_event):
        blade = item_id_of("血刃")
        state = make_state(identity=1, hp=(1, 5), hp_max=10, item_max=8,
                           items0=(blade,))
        await _use(game_bot, group_event, "血刃")
        assert state["hp"] == [1, 5]
        assert state["item_0"] == [blade], "用不掉的道具要还回来"

    async def test_黑洞_抢一个道具(self, game_bot, group_event, rand):
        rand.plan("randint", 1)
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(item_id_of("黑洞"),), items1=(1, 2, 3))
        await _use(game_bot, group_event, "黑洞")
        assert state["item_1"] == [1, 3]
        assert state["item_0"] == [2]

    async def test_黑洞_对面没道具就退回来(self, game_bot, group_event):
        hole = item_id_of("黑洞")
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(hole,), items1=())
        await _use(game_bot, group_event, "黑洞")
        assert state["item_0"] == [hole], "用不掉的道具要还回来"

    async def test_金苹果_回三点跳两回合(self, game_bot, group_event):
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           atk=2, items0=(item_id_of("金苹果"),))
        await _use(game_bot, group_event, "金苹果")
        assert state["hp"] == [8, 5]
        assert state["hcf"] == 1
        assert state["atk"] == 0
        assert state["turn"] == 1

    async def test_铂金草莓_抬双方上限(self, game_bot, group_event):
        state = make_state(identity=1, hp=(10, 5), hp_max=10, item_max=8,
                           items0=(item_id_of("铂金草莓"),))
        await _use(game_bot, group_event, "铂金草莓")
        assert state["hp_max"] == 11
        assert state["hp"] == [11, 5]

    async def test_肾上腺素_换上限(self, game_bot, group_event, rand):
        rand.plan("choice", 1)
        state = make_state(identity=1, hp=(10, 10), hp_max=10, item_max=8,
                           items0=(item_id_of("肾上腺素"),))
        await _use(game_bot, group_event, "肾上腺素")
        assert state["hp_max"] == 9
        assert state["item_max"] == 9
        assert state["hp"] == [9, 9], "双方血量都被夹到新上限"
        assert state["item_0"] == [1]

    async def test_肾上腺素_上限只剩1就用不了(self, game_bot, group_event):
        adr = item_id_of("肾上腺素")
        state = make_state(identity=1, hp=(1, 1), hp_max=1, item_max=8,
                           items0=(adr,))
        await _use(game_bot, group_event, "肾上腺素")
        assert state["hp_max"] == 1
        assert state["item_max"] == 8
        assert state["item_0"] == [adr], "用不掉的道具要还回来"

    async def test_烈性TNT_先扣上限再扣血(self, game_bot, group_event):
        state = make_state(identity=1, hp=(10, 6), hp_max=10, item_max=8,
                           items0=(item_id_of("烈性TNT"),))
        await _use(game_bot, group_event, "烈性TNT")
        # 上限 10 -> 9，自己被夹到 9 再 -1 = 8；对方 6 没被夹，直接 -1 = 5
        assert state["hp_max"] == 9
        assert state["hp"] == [8, 5]

    @pytest.mark.parametrize(
        ("hp", "hp_max"),
        [((5, 5), 1), ((1, 5), 6), ((2, 5), 2)],
    )
    async def test_烈性TNT_自杀条件下拒绝使用(self, game_bot, group_event, hp, hp_max):
        tnt = item_id_of("烈性TNT")
        state = make_state(identity=1, hp=hp, hp_max=hp_max, item_max=8,
                           items0=(tnt,))
        await _use(game_bot, group_event, "烈性TNT")
        assert state["hp"] == list(hp)
        assert state["hp_max"] == hp_max
        assert state["item_0"] == [tnt], "用不掉的道具要还回来"

    async def test_双转团_转给对方(self, game_bot, group_event, rand):
        gift = item_id_of("双转团")
        rand.plan("randint", 2)  # kou_first != 1，不触发额外效果
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(gift,), items1=())
        await _use(game_bot, group_event, "双转团")
        assert state["item_0"] == []
        assert state["item_1"] == [gift]

    async def test_双转团_对方满了就丢掉(self, game_bot, group_event, rand):
        gift = item_id_of("双转团")
        rand.plan("randint", 2)
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=2,
                           items0=(gift,), items1=(1, 1))
        await _use(game_bot, group_event, "双转团")
        assert state["item_0"] == [], "自己这边一定会少一个"
        assert state["item_1"] == [1, 1], "对方满了就凭空消失"

    async def test_双转团_顺手牵羊还摔一跤(self, game_bot, group_event, rand):
        gift = item_id_of("双转团")
        # kou_first=1 -> kou_second=1 -> 抽走 index 0 -> 1/2 判定命中
        rand.plan("randint", 1, 1, 0, 1)
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(gift, 1), items1=())
        await _use(game_bot, group_event, "双转团")
        assert state["item_0"] == []
        assert sorted(state["item_1"]) == sorted([gift, 1]), "桃也被顺走了"
        assert state["hp"] == [5, 4], "对方摔了一跤，掉一点血"

    @pytest.mark.parametrize(("second", "hp0"), [(2, 4), (3, 6)])
    async def test_双转团_自己掉血或回血(self, game_bot, group_event, rand, second, hp0):
        gift = item_id_of("双转团")
        rand.plan("randint", 1, second)
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(gift,), items1=())
        await _use(game_bot, group_event, "双转团")
        assert state["hp"][0] == hp0

    async def test_用道具打死对方就结算(self, game_bot, group_event, rand):
        rand.plan("randint", 9)  # 欲望之盒 -> 打对面一点
        make_state(hp=(4, 1), items0=(item_id_of("欲望之盒"),))
        await _use(game_bot, group_event, "欲望之盒")
        assert f"[CQ:at,qq={P0}]" in last_text(game_bot), "赢的是用道具的人"
        assert game.datas.demon_data[GID_S]["start"] is False
        assert game.datas.demon_data[GID_S]["pl"] == []


# ==========================================================================
# 查看局势
# ==========================================================================
async def _check(bot, group_event, *, user_id: int = P0) -> bool:
    event = group_event("*查看局势", user_id=user_id)
    return await drive(game.check, game.check_handle, bot, event, event=event)


class TestCheckSituation:
    async def test_没开局或者不是局内人就不给局势(self, game_bot, group_event):
        """两条拦截分支。断言的是「回的不是局势本身」—— 拿一次正常输出当对照，
        拦下来的两次必须和它不一样。措辞怎么改都不影响这条。
        """
        make_state(hp=(4, 5), hp_max=6, game_turn=3)
        await _check(game_bot, group_event)
        board = last_text(game_bot)

        make_state(start=False, hp=(4, 5), hp_max=6, game_turn=3)
        await _check(game_bot, group_event)
        assert last_text(game_bot) != board, "没开局却把局势报出来了"

        make_state(hp=(4, 5), hp_max=6, game_turn=3)
        await _check(game_bot, group_event, user_id=P2)
        assert last_text(game_bot) != board, "局外人也能看到局势"

        assert len(sent_texts(game_bot)) == 3

    async def test_正常输出把该报的数都报了(self, game_bot, group_event, clock):
        """局势正文怎么排版随便改，但这几个数得在：双方血量、剩余步时、
        双方道具名。都是算出来的值，不是措辞。
        """
        make_state(hp=(4, 5), hp_max=6, item_max=6, clip=[0, 1, 1],
                   items0=(1, 2), items1=(), game_turn=3, now=FAKE_NOW)
        clock.advance(90)
        await _check(game_bot, group_event)
        msg = last_text(game_bot)
        assert "4/6" in msg and "5/6" in msg, "双方血量"
        assert "8分30秒" in msg, "600-90=510 秒，换算成 8 分 30 秒"
        assert "桃" in msg and "医疗箱" in msg, "手里的道具名"
        assert f"[CQ:at,qq={P0}]" in msg and f"[CQ:at,qq={P1}]" in msg

    async def test_束缚和加伤只在非零时才进局势(self, game_bot, group_event):
        """有没有这两行，靠「回话跟没有的时候不一样」来保证。"""
        make_state(hcf=0, atk=0)
        await _check(game_bot, group_event)
        plain = last_text(game_bot)

        make_state(hcf=3, atk=2)
        await _check(game_bot, group_event)
        assert last_text(game_bot) != plain

    async def test_束缚回合数按_hcf加一整除二_显示(self, game_bot, group_event):
        """展示用的公式是 (hcf+1)//2：1 和 2 会显示成同一个数，3 才跳下一档。
        钉的是这个换算关系，不是那一行怎么写。
        """
        texts = []
        for hcf in (1, 2, 3):
            make_state(hcf=hcf, atk=0)
            await _check(game_bot, group_event)
            texts.append(last_text(game_bot))
        assert texts[0] == texts[1], "hcf 1 和 2 该显示成同一个数"
        assert texts[2] != texts[1], "hcf 3 该跳到下一档"

    async def test_三种模式的局势各不相同(self, game_bot, group_event):
        """正常 / 身份 / 急速三条分支，报出来的局势必须能区分开。"""
        texts = []
        for identity in (0, 1, 2):
            make_state(identity=identity, game_turn=1, hp_max=10, item_max=8)
            await _check(game_bot, group_event)
            texts.append(last_text(game_bot))
        assert len(set(texts)) == 3, "三种模式的局势报得一模一样"

    @pytest.mark.parametrize(("identity", "before", "after"), [(1, 12, 13), (2, 5, 6)])
    async def test_越过死斗轮数局势会变个样(
        self, game_bot, group_event, identity, before, after
    ):
        """死斗标记怎么写不管，但越过阈值那一轮，局势里除了轮数以外得多点东西
        —— 所以先把所有数字抹成 # 再比。
        """
        texts = []
        for turn in (before, after):
            make_state(identity=identity, game_turn=turn, hp_max=10, item_max=8)
            await _check(game_bot, group_event)
            texts.append(re.sub(r"\d+", "#", last_text(game_bot)))
        assert texts[0] != texts[1], "越过死斗阈值，局势一点变化都没有"


# ==========================================================================
# 投降
# ==========================================================================
async def _surrender(bot, group_event, *, user_id: int = P0) -> bool:
    event = group_event("*恶魔投降", user_id=user_id)
    return await drive(
        game.demon_surrender, game.demon_surrender_handle, bot, event, event=event
    )


class TestSurrender:
    async def test_没有对局(self, fake_bot, group_event):
        """连状态都不该建出来 —— 投降走的是 .get()，不像开枪会顺手补默认值。"""
        await _surrender(fake_bot, group_event)
        assert GID_S not in game.datas.demon_data
        assert len(sent_texts(fake_bot)) == 1

    async def test_只有一个人等着也算没开局(self, fake_bot, group_event):
        state = make_state(start=False)
        state["pl"] = [str(P0)]
        await _surrender(fake_bot, group_event)
        assert state["pl"] == [str(P0)], "等人的队列不该被投降清掉"
        assert state["start"] is False
        assert len(sent_texts(fake_bot)) == 1

    async def test_局外人投不了(self, fake_bot, group_event):
        state = make_state()
        await _surrender(fake_bot, group_event, user_id=P2)
        assert state["start"] is True, "局外人不能把别人的局投掉"
        assert state["pl"] == [str(P0), str(P1)]
        assert len(sent_texts(fake_bot)) == 1

    @pytest.mark.parametrize(("loser", "winner"), [(P0, P1), (P1, P0)])
    async def test_投降判对方胜(self, fake_bot, group_event, loser, winner):
        make_state()
        await _surrender(fake_bot, group_event, user_id=loser)
        # 结算里两个人的 QQ 号都得点到（谁投的、谁赢的），这是结果不是措辞
        msg = last_text(fake_bot)
        assert f"[CQ:at,qq={loser}]" in msg and f"[CQ:at,qq={winner}]" in msg
        assert game.datas.demon_data[GID_S]["start"] is False
        assert game.datas.demon_data[GID_S]["pl"] == []

    async def test_重复投降(self, fake_bot, group_event):
        make_state()
        await _surrender(fake_bot, group_event)
        second = await _surrender(fake_bot, group_event)
        assert second, "第二次也得有回话，不能挂着"
        assert len(sent_texts(fake_bot)) == 2
        assert game.datas.demon_data[GID_S]["pl"] == []

    async def test_投降不受回合限制(self, fake_bot, group_event):
        """轮到 P0，但 P1 照样能投。"""
        make_state(turn=0)
        await _surrender(fake_bot, group_event, user_id=P1)
        assert f"[CQ:at,qq={P0}]" in last_text(fake_bot)
        assert game.datas.demon_data[GID_S]["start"] is False


# ==========================================================================
# 恶魔道具 / 恶魔帮助
# ==========================================================================
class TestItemQuery:
    async def test_查单个道具(self, fake_bot, group_event):
        event = group_event("*恶魔道具 烈弓")
        await drive(
            game.prop_demon_query, game.prop_demon_query_handle, fake_bot, event,
            bot=fake_bot, event=event, arg=Message("烈弓"),
        )
        # 比的是模块自己那张表里的说明，不是抄一份字面量
        assert game.item_effects["烈弓"] in last_text(fake_bot)

    async def test_查不存在的道具(self, fake_bot, group_event):
        event = group_event("*恶魔道具 不存在")
        await drive(
            game.prop_demon_query, game.prop_demon_query_handle, fake_bot, event,
            bot=fake_bot, event=event, arg=Message("不存在"),
        )
        text = last_text(fake_bot)
        assert not any(e in text for e in game.item_effects.values()), (
            "查不到就一条效果说明都不该吐出来"
        )

    async def test_查全部走合并转发(self, fake_bot, group_event):
        """空参 / all / 带空格的大写 ALL 都归到同一个「查全部」分支，
        原来是三个参数化节点，合并成表内循环。"""
        for arg in ("", "all", "  ALL  "):
            event = group_event("*恶魔道具")
            await drive(
                game.prop_demon_query, game.prop_demon_query_handle, fake_bot, event,
                bot=fake_bot, event=event, arg=Message(arg),
            )
            api, data = fake_bot.calls[-1]
            assert api == "send_group_forward_msg", f"{arg!r} 没走合并转发"
            assert data["group_id"] == GID
            content = data["messages"][0]["data"]["content"]
            for name, effect in game.item_effects.items():
                assert name in content, f"{arg!r} 的清单里漏了 {name}"
                assert effect in content, f"{arg!r} 的清单里 {name} 没带说明"

    async def test_大小写不敏感(self, fake_bot, group_event):
        event = group_event("*恶魔道具 烈性tnt")
        await drive(
            game.prop_demon_query, game.prop_demon_query_handle, fake_bot, event,
            bot=fake_bot, event=event, arg=Message("烈性tnt"),
        )
        assert game.item_effects["烈性TNT"] in last_text(fake_bot)


class TestDemonHelpText:
    async def test_恶魔帮助原样输出(self, fake_bot, group_event):
        event = group_event("*恶魔帮助")
        await drive(
            game.prop_demon_help, game.prop_demon_help_handle, fake_bot, event
        )
        assert game.help_msg in last_text(fake_bot)

    def test_帮助里提到的命令都真的存在(self):
        """help_msg 里写的每个 *xxx 都必须是 game 模块里注册过的命令或别名。"""
        registered = _game_command_names()
        mentioned = set(re.findall(r"\*([^\s]+)", game.help_msg))
        assert mentioned, "help_msg 里一个命令都没提到？"
        assert mentioned <= registered, f"帮助里有不存在的命令：{mentioned - registered}"


def _game_matchers() -> dict[str, type]:
    """game 模块里所有 matcher，变量名 -> matcher。

    从模块命名空间反射，不写死清单：加命令自动进来，删命令自动出去。
    """
    return {
        name: obj
        for name, obj in vars(game).items()
        if isinstance(obj, type) and issubclass(obj, Matcher) and obj is not Matcher
    }


def _game_command_names() -> set[str]:
    """game 模块里所有注册过的命令名 + 别名"""
    names: set[str] = set()
    for obj in _game_matchers().values():
        for checker in obj.rule.checkers:
            if isinstance(checker.call, CommandRule):
                names |= {".".join(cmd) for cmd in checker.call.cmds}
    return names


# ==========================================================================
# 奖励常量
# ==========================================================================
class TestPrizeConstants:
    """蓝莓经济下线之后这三个常量在全仓库都没人读了（grep 只有定义处），
    但数字关系还在，先钉住；哪天要重新接上奖励结算不至于算错。"""

    def test_抽成向下取整(self):
        assert game.jiangli == 388
        assert game.bet_tax == 38  # int(388 * 0.1) = 38，不是 38.8
        assert game.final_jiangli == 350
        assert game.final_jiangli == game.jiangli - game.bet_tax


# ==========================================================================
# help：命令表本身的完整性
# ==========================================================================
class TestHelpRegistryIntegrity:
    def test_条目不为空(self):
        assert COMMANDS
        assert CATEGORIES

    def test_每条都有必填字段(self):
        """原来是 @parametrize(sorted(COMMANDS))，36 个节点盯同一条不变量。

        改成表内循环：加一条命令不再多出一个测试节点，出错信息里照样有命令名。
        """
        for name, cmd in sorted(COMMANDS.items()):
            assert isinstance(cmd, Cmd)
            assert name.strip() == name and name, f"{name} 的命令名有多余空白"
            for field in ("usage", "summary", "detail"):
                value = getattr(cmd, field)
                assert isinstance(value, str)
                assert value.strip(), f"{name} 的 {field} 是空的"
            assert cmd.category in CATEGORIES, (
                f"{name} 的分类 {cmd.category} 不在 CATEGORIES 里"
            )
            assert isinstance(cmd.examples, tuple)
            assert isinstance(cmd.aliases, tuple)
            assert all(isinstance(e, str) and e.strip() for e in cmd.examples)
            assert all(isinstance(a, str) and a.strip() for a in cmd.aliases)

    def test_usage_以前缀加命令名开头(self):
        """同上，原来也是 36 个节点。断言本身一个字没改。"""
        for name, cmd in sorted(COMMANDS.items()):
            if cmd.prefix:
                assert cmd.usage.startswith(cmd.prefix + name), (
                    f"{name} 的 usage 和命令名对不上：{cmd.usage}"
                )

    def test_别名不重复也不和命令名撞车(self):
        seen: dict[str, str] = {}
        for name, cmd in COMMANDS.items():
            for alias in cmd.aliases:
                key = alias.lower()
                assert key not in COMMANDS, f"{name} 的别名 {alias} 和命令名撞了"
                assert key not in seen, f"别名 {alias} 被 {seen[key]} 和 {name} 同时占用"
                seen[key] = name
        assert helpmod.ALIASES == {a: n for a, n in seen.items()}

    def test_别名表覆盖所有别名(self):
        expected = {a.lower(): n for n, c in COMMANDS.items() for a in c.aliases}
        assert helpmod.ALIASES == expected
        assert helpmod.ALIASES  # 至少得有几个，否则这个测试没意义

    def test_每个分类都有命令(self):
        for cat in CATEGORIES:
            assert helpmod._by_category(cat), f"分类 {cat} 一条命令都没有"

    def test_by_category_是完整划分(self):
        collected = [n for cat in CATEGORIES for n, _ in helpmod._by_category(cat)]
        assert sorted(collected) == sorted(COMMANDS)
        assert len(collected) == len(set(collected))

    def test_分类别名都指向真分类或空分类(self):
        for alias, cat in helpmod.CATEGORY_ALIASES.items():
            assert alias == alias.lower(), f"分类别名 {alias} 没有小写"
            if cat not in CATEGORIES:
                # admin 这条指向一个不存在的分类，handle_help 里靠
                # `if cat and _by_category(cat)` 兜住，不会 KeyError
                assert helpmod._by_category(cat) == []

    def test_前缀都在合法集合里(self):
        """commands.py 里 Cmd.prefix 的注释白纸黑字写了三种合法前缀
        （* / . / 空），所以断言「每条的前缀都是其中之一」。

        原来写的是 `{...} == {"*", ""}`，也就是「现存前缀恰好是这两种」——
        那条会在有人合法地加一条 . 开头的命令时红，可那是产品允许的。
        """
        legal = {"*", ".", ""}
        for name, cmd in COMMANDS.items():
            assert cmd.prefix in legal, f"{name} 的前缀 {cmd.prefix!r} 不在 {legal} 里"


class TestHelpMatchesReality:
    """help 说有的命令，插件里必须真的注册了 —— 防止下线了功能忘了删说明"""

    def test_每条命令都能找到对应的_matcher(self):
        registered = _all_registered_commands()
        missing = sorted(
            name
            for name, cmd in COMMANDS.items()
            if cmd.prefix == "*" and name not in registered
        )
        assert missing == [], f"help 里有这些命令，但插件里根本没注册：{missing}"

    def test_每个别名都能找到对应的_matcher(self):
        registered = _all_registered_commands()
        missing = sorted(
            f"{name}:{alias}"
            for name, cmd in COMMANDS.items()
            if cmd.prefix == "*"
            for alias in cmd.aliases
            if alias not in registered
        )
        assert missing == [], f"help 里有这些别名，但插件里没注册：{missing}"

    def test_戳一戳没有前缀而且真的挂了处理函数(self):
        """「戳一戳」不是命令，是 on_type 的通知响应，所以 prefix 是空的。

        原来写的是 `no_prefix == ["戳一戳"]`，也就是「全表只有它一条没前缀」——
        再来一条通知类响应（比如入群欢迎）就会红，可那并不是坏事。
        现在只断言它自己：没前缀、注册了、有处理函数。
        """
        assert COMMANDS["戳一戳"].prefix == ""
        joy = importlib.import_module("xiaozu_bot.plugins.joy")
        assert issubclass(joy.group_poke, Matcher)
        assert joy.group_poke.handlers, "戳一戳注册了但没有处理函数"

    def test_每条命令背后都有处理函数(self):
        registered = _all_registered_commands()
        empty = sorted(
            name
            for name, cmd in COMMANDS.items()
            if cmd.prefix == "*"
            and not any(m.handlers for m in registered.get(name, []))
        )
        assert empty == [], f"help 里写了但没有处理函数的命令：{empty}"

    def test_说明里抄的数字和代码对得上(self):
        """help 把 game 里几个常量抄进了正文（道具数、死斗轮数、每步限时）。
        抄错了是真骗人，所以数字还钉着；至于这些数字被写进哪句话里，不管。

        原来是四条，每条都把整句话抄了一遍，改一个字就红。
        """
        setmode = COMMANDS["setmode"].detail
        for value in (
            len(game.item_dic1), len(game.item_dic),
            game.death_turn, game.pangguang_turn,
        ):
            assert str(value) in setmode, f"setmode 说明里没提到 {value}"

        minutes = str(game.turn_time // 60)
        assert minutes in COMMANDS["betgame"].detail
        assert minutes in COMMANDS["开枪"].constraints
        assert str(len(game.item_dic)) in COMMANDS["恶魔道具"].detail

    def test_使用说明里列全了所有道具(self):
        """道具清单漏一个就是 help 骗人 —— 这条跟措辞无关，是清单完整性。"""
        detail = COMMANDS["使用"].detail
        for name in game.item_dic.values():
            assert name in detail, f"help 的道具清单里漏了 {name}"
        assert str(len(game.item_dic1)) in detail
        assert str(len(game.item_dic2)) in detail


def _all_registered_commands() -> dict[str, list[type]]:
    """扫一遍所有插件，收集真正注册过的命令名 -> matcher 列表。

    刻意用运行时反射而不是手写清单，这样删了一个命令而忘了改 help 就会被抓到。
    """
    import xiaozu_bot.plugins as plugins_pkg

    out: dict[str, list[type]] = {}
    for info in pkgutil.iter_modules(plugins_pkg.__path__):
        module = importlib.import_module(f"xiaozu_bot.plugins.{info.name}")
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, Matcher)
                and obj is not Matcher
            ):
                for checker in obj.rule.checkers:
                    if isinstance(checker.call, CommandRule):
                        for cmd in checker.call.cmds:
                            out.setdefault(".".join(cmd), []).append(obj)
    return out


# ==========================================================================
# help：排版函数
# ==========================================================================
class TestHelpLayout:
    def test_显示宽度(self):
        """一张纯函数的输入输出表，原来一行一个参数化节点，合并成表内循环。"""
        cases = [("", 0), ("abc", 3), ("中文", 4), ("*jrrp", 5), ("a中", 3), ("１", 2)]
        for text, width in cases:
            assert helpmod._width(text) == width, f"{text!r} 的宽度算错了"

    def test_按显示宽度补空格(self):
        padded = helpmod._pad("中", 5)
        assert padded == "中   "
        assert helpmod._width(padded) == 5

    def test_已经够宽就不动(self):
        assert helpmod._pad("abcdef", 3) == "abcdef"

    def test_命令行按显示宽度对齐(self):
        cmds = helpmod._by_category("demon")
        lines = helpmod._command_lines(cmds)
        assert len(lines) == len(cmds)
        offsets = {
            helpmod._width(line) - helpmod._width(cmd.summary)
            for line, (_, cmd) in zip(lines, cmds)
        }
        assert len(offsets) == 1, "同一分类里 summary 的起始列必须一致"

    def test_命令行带缩进(self):
        lines = helpmod._command_lines(helpmod._by_category("ai"), indent="  ")
        assert all(line.startswith("  ") for line in lines)


class TestHelpOverview:
    def test_列出的命令不多不少(self):
        """总览里缩进两格的每个标签，必须和 COMMANDS 一一对应。

        原来「列出了每一条命令」和「条目不多不少」是两条，前者是后者的一半，
        合并成一条：标签集合精确相等 + 每条的 summary 都在。
        """
        text = helpmod._overview()
        labels = set(re.findall(r"^ {2}(\S+)", text, flags=re.M))
        known = {c.prefix + n for n, c in COMMANDS.items()}
        assert labels == known, (
            f"总览多出来的：{labels - known}；漏掉的：{known - labels}"
        )
        for name, cmd in COMMANDS.items():
            assert cmd.summary in text, f"总览里少了 {name} 的一句话说明"

    def test_列出了每一个分类标题(self):
        text = helpmod._overview()
        for title in CATEGORIES.values():
            assert title in text


class TestHelpRender:
    def test_四个字段一个都不能漏(self):
        """命令名 / 一句话说明 / 用法 / 正文 / 限制，渲染出来必须一个不落。
        内容全部从 COMMANDS 里取，改说明文案不牵连这条；分段标题怎么写不管。
        """
        cmd = COMMANDS["jrrp"]
        text = helpmod._render("jrrp", cmd)
        assert cmd.prefix + "jrrp" in text
        assert cmd.summary in text
        assert cmd.usage in text
        assert cmd.detail in text
        assert cmd.constraints in text

    def test_有别名才有别名段(self):
        cmd = COMMANDS["news"]
        text = helpmod._render("news", cmd)
        assert cmd.aliases, "news 得有别名，否则这条测的是空气"
        for alias in cmd.aliases:
            assert cmd.prefix + alias in text

    def test_有例子才有例子段(self):
        """空的字段不该占版面：同一条命令加上例子/限制，渲染结果必须变长。"""
        bare = Cmd(usage="*x", summary="s", detail="d", category="fun")
        rich = Cmd(
            usage="*x", summary="s", detail="d", category="fun",
            examples=("*x 举个例子",), constraints="只有周三能用",
        )
        short, long = helpmod._render("x", bare), helpmod._render("x", rich)
        assert "*x 举个例子" in long
        assert "只有周三能用" in long
        assert len(short) < len(long), "没例子没限制的时候不该留空段"

    def test_自带前缀的别名不会被重复加前缀(self):
        cmd = Cmd(
            usage="*x", summary="s", detail="d", category="fun",
            aliases=("。带前缀", "不带前缀"),
        )
        text = helpmod._render("x", cmd)
        assert "。带前缀" in text and "*。带前缀" not in text, "别名的前缀被加了两遍"
        assert "*不带前缀" in text

    def test_空前缀命令不会被硬加星号(self):
        text = helpmod._render("戳一戳", COMMANDS["戳一戳"])
        assert "戳一戳" in text
        assert "*戳一戳" not in text

    def test_分类页(self):
        text = helpmod._render_category("guess")
        assert CATEGORIES["guess"] in text
        for name, cmd in helpmod._by_category("guess"):
            assert cmd.prefix + name in text
            assert cmd.summary in text


# ==========================================================================
# help / references 两条命令
# ==========================================================================
async def _help(bot, event_factory, query: str) -> str:
    event = event_factory(f"*help {query}".strip())
    await drive(
        helpmod.xiaozubothelp, helpmod.handle_help, bot, event, arg=Message(query)
    )
    return last_text(bot)


def _suggested(text: str) -> set[str]:
    """从兜底提示里挑出被推荐的命令名。

    只认「带 * 的、而且确实是 COMMANDS 里的名字」这个形状；提示语本身怎么写、
    拿什么分隔都无所谓。help 单独排掉：兜底那句话里固定带一个 `*help 看全部`，
    今天 COMMANDS 里没有 help 这条所以碰不上，哪天加了也不该被当成推荐。
    """
    found = set(re.findall(r"\*([A-Za-z0-9_一-鿿]+)", text))
    return (found & set(COMMANDS)) - {"help"}


class TestHelpHandler:
    async def test_不带参数给总览(self, fake_bot, group_event):
        assert await _help(fake_bot, group_event, "") == helpmod._overview()

    async def test_空白参数也给总览(self, fake_bot, group_event):
        assert await _help(fake_bot, group_event, "   ") == helpmod._overview()

    async def test_按命令名查(self, fake_bot, group_event):
        assert await _help(fake_bot, group_event, "jrrp") == helpmod._render(
            "jrrp", COMMANDS["jrrp"]
        )

    @pytest.mark.parametrize("query", ["*jrrp", ".jrrp", "。jrrp", "JRRP"])
    async def test_前缀和大小写都被抹掉(self, fake_bot, group_event, query):
        assert await _help(fake_bot, group_event, query) == helpmod._render(
            "jrrp", COMMANDS["jrrp"]
        )

    @pytest.mark.parametrize(("alias", "name"), [("公告", "news"), ("新闻", "news")])
    async def test_按别名查(self, fake_bot, group_event, alias, name):
        assert await _help(fake_bot, group_event, alias) == helpmod._render(
            name, COMMANDS[name]
        )

    @pytest.mark.parametrize(
        ("query", "cat"),
        [("gd", "gd"), ("关卡", "gd"), ("猜图", "guess"), ("恶魔", "demon"), ("娱乐", "fun")],
    )
    async def test_按分类查(self, fake_bot, group_event, query, cat):
        assert await _help(fake_bot, group_event, query) == helpmod._render_category(cat)

    async def test_命令名优先于分类名(self, fake_bot, group_event):
        """game 既是命令又长得像分类，必须先当命令解释。"""
        assert await _help(fake_bot, group_event, "game") == helpmod._render(
            "game", COMMANDS["game"]
        )

    async def test_指向空分类的别名走兜底(self, fake_bot, group_event):
        """CATEGORY_ALIASES 里的 admin 指向一个没有命令的分类 —— _render_category
        对它是会 KeyError 的，所以 handle_help 必须在渲染之前就拐进兜底。"""
        assert helpmod._by_category("admin") == []
        with pytest.raises(KeyError):
            helpmod._render_category("admin")

        text = await _help(fake_bot, group_event, "admin")
        assert "admin" in text, "兜底得把查的词原样报回来"
        assert text != helpmod._overview()

    async def test_未知命令不带推荐(self, fake_bot, group_event):
        """zzzzz 和谁都不像，所以一条命令都不该推荐。"""
        text = await _help(fake_bot, group_event, "zzzzz")
        assert "zzzzz" in text
        assert _suggested(text) == set(), "跟谁都不像却给了推荐"

    async def test_未知命令带相近推荐(self, fake_bot, group_event):
        text = await _help(fake_bot, group_event, "gdsearchhelpx")
        assert "gdsearchhelpx" in text
        assert "gdsearchhelp" in _suggested(text)

    async def test_推荐最多五条(self, fake_bot, group_event):
        """gues 能匹配到 6 条 guess_*，但只推前 5 条。"""
        assert len([n for n in COMMANDS if "gues" in n]) > 5
        text = await _help(fake_bot, group_event, "gues")
        assert len(_suggested(text)) == 5

    async def test_私聊也能用(self, fake_bot, make_private_event):
        event = make_private_event("*help jrrp")
        await drive(
            helpmod.xiaozubothelp, helpmod.handle_help, fake_bot, event,
            arg=Message("jrrp"),
        )
        assert last_text(fake_bot) == helpmod._render("jrrp", COMMANDS["jrrp"])


async def _refs(bot, group_event, arg: str) -> str:
    event = group_event(f"*references {arg}".strip())
    await drive(
        helpmod.references, helpmod.handle_references, bot, event, arg=Message(arg)
    )
    return last_text(bot)


# 三张分页参考表：命令名 -> 表。页数一律从表本身量，往表里加一页
# 不用回来改测试（原来 8 / 4 / 2 这三个数字在六条用例里各写了一遍）。
PAGED_REFS = {"gddl": helpmod.REF_GDDL, "nlw": helpmod.REF_NLW, "plat": helpmod.REF_PDIFF}
# 三张单页表：不接受翻页
SINGLE_REFS = {"lw": helpmod.REF_LW, "ids": helpmod.REF_IDS, "hds": helpmod.REF_HDS}


class TestReferences:
    async def test_不带参数或者表名不认识都不给表(self, fake_bot, group_event):
        """走兜底的标志是「一张参考表都没吐出来」，回的那句用法提示怎么写不管。"""
        tables = [page for t in {**PAGED_REFS, **SINGLE_REFS}.values() for page in t]
        for arg in ("", "nope", "gddl2", "参考"):
            text = await _refs(fake_bot, group_event, arg)
            assert not any(page in text for page in tables), f"{arg!r} 居然翻出了表"

    async def test_aredl_单独一条分支(self, fake_bot, group_event):
        """aredl 没有固定参考线，走的是自己那条分支，回的既不是表也不是用法提示。"""
        text = await _refs(fake_bot, group_event, "aredl")
        assert text != await _refs(fake_bot, group_event, "nope")
        assert text not in [page for t in PAGED_REFS.values() for page in t]

    async def test_分页表每一页都翻得到(self, fake_bot, group_event):
        """原来「页数对不对」「第一页有效」「最后一页有效」是三条乘三张表 = 9 个节点，
        其实都是这个循环的某一次迭代：第 1 页到第 len 页，页页对得上，页脚也对。
        末页那次顺带守住上界的差一位（判断写的是 `page > len`，不是 `>=`）。
        """
        for name, table in PAGED_REFS.items():
            pages = len(table)
            assert pages > 1, f"{name} 不是分页表了？"
            for page in range(1, pages + 1):
                text = await _refs(fake_bot, group_event, f"{name} {page}")
                assert text == table[page - 1] + helpmod.pagehint(page, pages), (
                    f"{name} 第 {page} 页翻错了"
                )

    async def test_页码超出上界(self, fake_bot, group_event):
        for name, table in PAGED_REFS.items():
            pages = len(table)
            text = await _refs(fake_bot, group_event, f"{name} {pages + 1}")
            assert not any(page in text for page in table), f"{name} 没挡住越界"
            assert str(pages) in text, "报错里得说清楚一共几页"

    @pytest.mark.parametrize("name", sorted(PAGED_REFS))
    async def test_页码0被拒绝(self, fake_bot, group_event, name):
        """"0".isdigit() 是 True，以前 page=0 会走到 REF_XXX[-1] 翻出最后一页，
        提示语还写着「第0页」。现在和超上界一样报错。

        钉的就是那条回归：page=0 绝不能翻出任何一页，而且和超上界一个待遇。
        """
        table = PAGED_REFS[name]
        pages = len(table)
        text = await _refs(fake_bot, group_event, f"{name} 0")
        assert not any(page in text for page in table), f"{name} 的第0页翻出内容了"
        assert text == await _refs(fake_bot, group_event, f"{name} {pages + 1}")

    async def test_多个0也被拒绝(self, fake_bot, group_event):
        """"00".isdigit() 同样是 True"""
        table = helpmod.REF_GDDL
        text = await _refs(fake_bot, group_event, "gddl 00")
        assert not any(page in text for page in table)
        assert text == await _refs(fake_bot, group_event, f"gddl {len(table) + 1}")

    async def test_不给页码默认第一页(self, fake_bot, group_event):
        expected = helpmod.REF_GDDL[0] + helpmod.pagehint(1, len(helpmod.REF_GDDL))
        assert await _refs(fake_bot, group_event, "gddl") == expected

    async def test_页码不是数字就当第一页(self, fake_bot, group_event):
        """负号和小数点都过不了 isdigit()，跟乱输一样退回第一页（不是报错）"""
        expected = helpmod.REF_GDDL[0] + helpmod.pagehint(1, len(helpmod.REF_GDDL))
        for bad in ("-1", "-8", "abc", "1.5"):
            assert await _refs(fake_bot, group_event, f"gddl {bad}") == expected, (
                f"{bad} 没退回第一页"
            )

    async def test_单页表忽略页码(self, fake_bot, group_event):
        for name, table in SINGLE_REFS.items():
            assert len(table) == 1, f"{name} 不再是单页表了，得换到 PAGED_REFS"
            assert await _refs(fake_bot, group_event, name) == table[0]
            assert await _refs(fake_bot, group_event, f"{name} 99") == table[0]

    async def test_表名大小写不敏感(self, fake_bot, group_event):
        for upper, lower in (("AREDL", "aredl"), ("GDDL", "gddl")):
            assert await _refs(fake_bot, group_event, upper) == await _refs(
                fake_bot, group_event, lower
            ), f"{upper} 和 {lower} 该是同一张表"

    def test_pagehint带上了当前页和总页数(self):
        hint = helpmod.pagehint(3, 8)
        assert "3" in hint and "8" in hint
        assert hint != helpmod.pagehint(4, 8)
        assert hint != helpmod.pagehint(3, 9)
