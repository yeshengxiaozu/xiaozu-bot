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
        """恶魔投降历史上漏挂过 whitelist_rule，这条就是防它再漏。"""
        matchers = {
            name: obj
            for name, obj in vars(game).items()
            if isinstance(obj, type) and issubclass(obj, Matcher) and obj is not Matcher
        }
        assert len(matchers) == 8, f"game 里的命令数变了：{sorted(matchers)}"
        for name, matcher in matchers.items():
            calls = [checker.call for checker in matcher.rule.checkers]
            assert game.whitelist in calls, f"{name} 没挂白名单 rule"


# ==========================================================================
# demon_default
# ==========================================================================
class TestDemonDefault:
    def test_字段与初值(self, clock):
        d = game.demon_default()
        assert d == {
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
            "game_turn": 1,
            "add_atk": False,
            "start": False,
            "identity": 0,
            "demon_coldtime": FAKE_NOW,
            "turn_start_time": FAKE_NOW,
        }

    def test_每次都是新的可变对象(self):
        a, b = game.demon_default(), game.demon_default()
        a["pl"].append("1")
        assert b["pl"] == []


# ==========================================================================
# 道具表本身的完整性
# ==========================================================================
class TestItemTables:
    def test_两张表不重叠且拼起来就是总表(self):
        assert set(game.item_dic1) & set(game.item_dic2) == set()
        assert game.item_dic == game.item_dic1 | game.item_dic2

    def test_id_连续从1开始(self):
        assert sorted(game.item_dic) == list(range(1, 27))
        assert sorted(game.item_dic1) == list(range(1, 16))
        assert sorted(game.item_dic2) == list(range(16, 27))

    def test_道具名不重复(self):
        names = list(game.item_dic.values())
        assert len(set(names)) == len(names)

    def test_普通模式道具数是15(self):
        """get_random_item 拿 len(item_dic) - len(item_dic2) 当普通模式上限，
        这个差必须正好是 item_dic1 的大小。"""
        assert len(game.item_dic) - len(game.item_dic2) == len(game.item_dic1) == 15

    def test_每个道具都有效果说明(self):
        assert set(game.item_effects) == set(game.item_dic.values())

    def test_效果说明都不是空的(self):
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
    def test_普通模式只抽前15个(self, rand):
        item = game.get_random_item(0, 15, "111")
        pool = rand.args_of("choice")[0][0]
        assert sorted(set(pool)) == list(range(1, 16))
        assert len(pool) == 15  # 普通模式没有加权道具
        assert item in game.item_dic1

    def test_身份模式全量并且放大镜双权重(self, rand):
        game.get_random_item(1, 26, "111")
        pool = rand.args_of("choice")[0][0]
        assert sorted(set(pool)) == list(range(1, 27))
        assert pool.count(3) == 2, "放大镜的权重应该是 2"
        assert len(pool) == 27

    def test_膀胱模式和身份模式共用同一张权重表(self, rand):
        game.get_random_item(1, 26, "111")
        game.get_random_item(2, 26, "111")
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
    @pytest.mark.parametrize(
        ("value", "label"),
        [("0", "普通模式"), ("1", "身份模式"), ("2", "膀胱模式")],
    )
    async def test_三个合法值(self, game_r, fake_bot, group_event, value, label):
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
        assert last_text(fake_bot) == f"已将你的游戏模式设置为{label}"

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
        assert last_text(fake_bot) == "请输入一个整数！"
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
        assert "目前只接受0（普通模式），1（身份模式），2（膀胱模式）" in last_text(
            fake_bot
        )
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
        assert "玩家 小卒 加入游戏，等待第二位玩家加入。" in last_text(game_bot)

    async def test_同一个人不能重复加入(self, game_r, game_bot, group_event):
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P0)
        assert game.datas.demon_data[GID_S]["pl"] == [str(P0)]
        assert game.datas.demon_data[GID_S]["start"] is False
        assert "你已经加入了游戏，无需重复加入！" in last_text(game_bot)

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
        msg = last_text(game_bot)
        assert msg.startswith("轮盘，开局!")
        assert "- 本局模式：正常模式" in msg

    async def test_开局后第三个人被挡(self, game_r, game_bot, group_event):
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        await _join(game_bot, group_event, P2)
        assert game.datas.demon_data[GID_S]["pl"] == [str(P0), str(P1)]
        assert last_text(game_bot) == "游戏已开始，无法加入！"

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
        assert "- 本局模式：身份模式" in last_text(game_bot)

    async def test_两人模式不同时二选一(self, game_r, game_bot, group_event, rand):
        game_r.hset("game_mode", str(P0), "0")
        game_r.hset("game_mode", str(P1), "2")
        rand.plan("choice", 2)
        await _join(game_bot, group_event, P0)
        await _join(game_bot, group_event, P1)
        assert rand.args_of("choice")[0] == ([0, 2],)
        assert game.datas.demon_data[GID_S]["identity"] == 2
        assert "- 本局模式：急速模式" in last_text(game_bot)

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
        ("mode", "hp_range", "hp_max", "item_max", "label"),
        [
            (0, (3, 6), 6, 6, "正常模式"),
            (1, (6, 10), 10, 8, "身份模式"),
            (2, (9, 14), 16, 10, "急速模式"),
        ],
    )
    async def test_血量道具上限(
        self, game_r, game_bot, group_event, rand, mode, hp_range, hp_max, item_max, label
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
        assert f"- 本局模式：{label}" in last_text(game_bot)

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
        msg = str(game.death_mode(1, GID_S))
        assert state["hp_max"] == 9
        assert state["hp"] == [9, 4]
        assert "扣1点hp上限，当前hp上限：9" in msg

    def test_道具上限最低到6就不再扣(self, rand):
        state = make_state(identity=1, game_turn=13, hp_max=10, item_max=6)
        msg = str(game.death_mode(1, GID_S))
        assert state["item_max"] == 6
        assert "扣1点道具上限" not in msg

    def test_道具上限大于6时扣一点(self, rand):
        state = make_state(identity=1, game_turn=13, hp_max=10, item_max=8)
        msg = str(game.death_mode(1, GID_S))
        assert state["item_max"] == 7
        assert "扣1点道具上限，当前道具上限：7" in msg

    def test_随机销毁道具(self, rand):
        rand.plan("randint", 2)  # remove_random = 2
        rand.plan("sample", [1, 2], [3])
        state = make_state(
            identity=1, game_turn=13, hp_max=10, item_max=8,
            items0=(1, 2, 3), items1=(3,),
        )
        msg = str(game.death_mode(1, GID_S))
        assert state["item_0"] == [3]
        assert state["item_1"] == []
        assert "失去了2个道具：桃、医疗箱" in msg
        assert "失去了1个道具：放大镜" in msg

    def test_道具栏空的时候不销毁也不刷消息(self, rand):
        rand.plan("randint", 2)
        state = make_state(identity=1, game_turn=13, hp_max=10, item_max=8)
        msg = str(game.death_mode(1, GID_S))
        assert state["item_0"] == []
        assert "失去了" not in msg

    def test_血量上限已经是1就不再扣(self):
        state = make_state(identity=1, game_turn=13, hp=(1, 1), hp_max=1, item_max=6)
        msg = str(game.death_mode(1, GID_S))
        assert state["hp_max"] == 1
        assert "扣1点hp上限" not in msg

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
        msg = str(game.refersh_item(0, GID_S))
        assert state["item_0"] == [1, 3, 5]
        assert state["item_1"] == [2, 4, 6]
        assert "道具(3/6)" in msg
        assert "桃, 放大镜, 手铐" in msg

    def test_超过道具上限的部分被截掉(self, rand):
        rand.plan("randint", 4)
        state = make_state(item_max=3, items0=(1, 1), items1=())
        game.refersh_item(0, GID_S)
        assert len(state["item_0"]) == 3
        assert len(state["item_1"]) == 3

    def test_没道具时的提示语(self, rand):
        rand.plan("randint", 0)
        make_state()
        msg = str(game.refersh_item(0, GID_S))
        assert msg.count("你目前没有道具哦！") == 2
        assert "道具(0/6)" in msg

    def test_首轮才有加成(self, rand):
        make_state(game_turn=1)
        game.refersh_item(0, GID_S)
        assert rand.args_of("randint")[0] == (2, 4)

        rand.calls.clear()
        make_state(game_turn=2)
        game.refersh_item(0, GID_S)
        assert rand.args_of("randint")[0] == (1, 3)

    def test_消息里带血量和上限(self, rand):
        rand.plan("randint", 0)
        make_state(hp=(3, 5), hp_max=6)
        msg = str(game.refersh_item(0, GID_S))
        assert "hp：3/6" in msg
        assert "hp：5/6" in msg


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
    async def test_没开局(self, game_bot, group_event):
        make_state(start=False)
        await _fire(game_bot, group_event, "对方")
        assert "轮盘尚未开始！" in last_text(game_bot)

    async def test_局外人不能动手(self, game_bot, group_event):
        make_state()
        await _fire(game_bot, group_event, "对方", user_id=P2)
        assert "只有当前局内玩家能行动哦！" in last_text(game_bot)

    async def test_不是自己的回合(self, game_bot, group_event):
        make_state(turn=0)
        await _fire(game_bot, group_event, "对方", user_id=P1)
        assert "现在不是你的回合，请等待对方操作！" in last_text(game_bot)

    @pytest.mark.parametrize("bad", ["", "自已", "别人", "self"])
    async def test_参数不认识(self, game_bot, group_event, bad):
        make_state()
        await _fire(game_bot, group_event, bad)
        assert "请输入 <*开枪 自己> 或者 <*开枪 对方> 来开枪哦！" in last_text(game_bot)

    async def test_没开局的群里第一条指令不会炸(self, game_bot, group_event):
        """check_timeout 会顺手把群的默认状态建出来，所以不会 KeyError。"""
        assert GID_S not in game.datas.demon_data
        await _fire(game_bot, group_event, "对方")
        assert GID_S in game.datas.demon_data
        assert "轮盘尚未开始！" in last_text(game_bot)


class TestShoot:
    async def test_打对方命中(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[0, 1, 1], turn=0)
        await _fire(game_bot, group_event, "对方")
        assert state["hp"] == [4, 3]
        assert state["turn"] == 1, "打对方要交出回合"
        assert state["clip"] == [0, 1]
        msg = last_text(game_bot)
        assert "子弹 *【击中了】* 对方！对方剩余hp：3/6" in msg

    async def test_打对方空枪(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[1, 0], turn=0)
        await _fire(game_bot, group_event, "对方")
        assert state["hp"] == [4, 4]
        assert state["turn"] == 1
        assert "子弹未击中对方！对方剩余hp：4/6" in last_text(game_bot)

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

    @pytest.mark.parametrize(
        ("atk", "extra"),
        [(1, ""), (2, ""), (3, "癫狂屠戮！"), (4, "癫狂屠戮！"), (5, "无双，万军取首！")],
    )
    async def test_加伤生效并且打完清零(self, game_bot, group_event, atk, extra):
        state = make_state(hp=(4, 9), hp_max=9, clip=[0, 1, 1], turn=0, atk=atk, add_atk=True)
        await _fire(game_bot, group_event, "对方")
        assert state["hp"][1] == 9 - (1 + atk)
        assert state["atk"] == 0
        assert state["add_atk"] is False
        msg = last_text(game_bot)
        assert f"这颗子弹伤害为……{atk + 1}点！" in msg
        if extra:
            assert extra in msg

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
        msg = last_text(game_bot)
        assert "子弹用尽，重新换弹，道具更新！" in msg
        assert "当前轮数：2" in msg

    async def test_打光对方的血就结算并重置(self, game_bot, group_event):
        make_state(hp=(4, 1), clip=[0, 1, 1], turn=0)
        await _fire(game_bot, group_event, "对方")
        msg = last_text(game_bot)
        assert "游戏结束！" in msg and "恭喜" in msg
        assert f"[CQ:at,qq={P0}]" in msg, "赢的应该是开枪的人"
        # 状态被重置回默认
        state = game.datas.demon_data[GID_S]
        assert state["start"] is False
        assert state["pl"] == []

    async def test_打自己打死自己算对方赢(self, game_bot, group_event):
        make_state(hp=(1, 4), clip=[0, 1, 1], turn=0)
        await _fire(game_bot, group_event, "自己")
        msg = last_text(game_bot)
        assert "游戏结束！" in msg
        assert f"[CQ:at,qq={P1}]" in msg

    async def test_手铐让当前玩家多打一枪(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[1, 1, 0], turn=0, hcf=1)
        await _fire(game_bot, group_event, "对方")
        assert state["turn"] == 0, "对方被拷住，回合留在自己手里"
        assert state["hcf"] == -1
        # 展示用的公式是 (hcf+1)//2，hcf 落到 -1 之后显示成 0，看着别扭
        assert "当前对方剩余束缚回合数：0" in last_text(game_bot)

    async def test_束缚耗尽后回合交还(self, game_bot, group_event):
        state = make_state(hp=(4, 4), clip=[1, 1, 0], turn=0, hcf=-1)
        await _fire(game_bot, group_event, "对方")
        assert state["hcf"] == 0
        assert state["turn"] == 1
        assert "已挣脱束缚！" in last_text(game_bot)

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
        assert api == "send_group_msg"
        msg = str(data["message"])
        assert "回合超时！当前回合玩家" in msg
        assert f"[CQ:at,qq={P0}]" in msg and "自动判负" in msg
        assert f"恭喜[CQ:at,qq={P1}]胜利！" in msg

    async def test_只有一个人等太久就重置(self, game_bot, clock):
        state = make_state(start=False, now=FAKE_NOW)
        state["pl"] = [str(P0)]
        clock.advance(601)
        assert await game.check_timeout(GID_S) is True
        assert game.datas.demon_data[GID_S]["pl"] == []
        assert "由于长时间无第二人进入轮盘，现已重置游戏。" in sent_texts(game_bot)[-1]

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
        assert "回合超时" in sent_texts(game_bot)[0]
        assert len(sent_texts(game_bot)) == 1

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
        assert "回合超时" in sent_texts(game_bot)[-1]
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
    async def test_没开局(self, game_bot, group_event):
        make_state(start=False)
        await _use(game_bot, group_event, "桃")
        assert "轮盘尚未开始！" in last_text(game_bot)

    async def test_局外人(self, game_bot, group_event):
        make_state(items0=(1,))
        await _use(game_bot, group_event, "桃", user_id=P2)
        assert "只有当前局内玩家能行动哦！" in last_text(game_bot)

    async def test_不是自己的回合(self, game_bot, group_event):
        make_state(turn=0, items1=(1,))
        await _use(game_bot, group_event, "桃", user_id=P1)
        assert "现在不是你的回合，请等待对方操作！" in last_text(game_bot)

    @pytest.mark.parametrize("name", ["不存在的道具", "peach", ""])
    async def test_道具名不存在(self, game_bot, group_event, name):
        make_state(items0=(1,))
        await _use(game_bot, group_event, name)
        assert last_text(game_bot) == "你输入的道具不存在，请确认后再使用！"

    async def test_道具名对但自己没有(self, game_bot, group_event):
        make_state(items0=(2,))
        await _use(game_bot, group_event, "桃")
        assert "你并没有这个道具，请确认后再使用！" in last_text(game_bot)

    async def test_道具名忽略大小写(self, game_bot, group_event):
        """唯一带拉丁字母的道具是「烈性TNT」，小写也得认。"""
        tnt = item_id_of("烈性TNT")
        state = make_state(identity=1, hp=(5, 5), hp_max=10, items0=(tnt,))
        await _use(game_bot, group_event, "烈性tnt")
        assert state["item_0"] == []
        assert "使用了道具：烈性TNT" in last_text(game_bot)

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
    @pytest.mark.parametrize("name", sorted(game.item_dic.values()))
    async def test_每个道具都有对应分支(self, game_bot, group_event, name):
        """if/elif 链漏掉任何一个道具都会掉进 else 的「道具不存在或无法使用」。
        新增道具忘了写效果的话这条会红。"""
        item = item_id_of(name)
        state = make_state(
            identity=1, hp=(5, 5), hp_max=10, item_max=8,
            clip=[0, 1, 0, 1], items0=(item,), items1=(1,), turn=0,
        )
        await _use(game_bot, group_event, name)
        msg = last_text(game_bot)
        assert f"使用了道具：{name}" in msg
        assert "道具不存在或无法使用！" not in msg
        assert state["hp_max"] >= 1


class TestItemEffects:
    async def test_桃_回血封顶(self, game_bot, group_event):
        state = make_state(hp=(3, 4), hp_max=6, items0=(item_id_of("桃"),))
        await _use(game_bot, group_event, "桃")
        assert state["hp"] == [4, 4]
        assert "当前hp：4/6" in last_text(game_bot)

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

    async def test_放大镜(self, game_bot, group_event):
        make_state(clip=[0, 0, 1], items0=(item_id_of("放大镜"),))
        await _use(game_bot, group_event, "放大镜")
        assert "下一颗子弹是：实弹！" in last_text(game_bot)

    async def test_眼镜_看后两发(self, game_bot, group_event):
        make_state(clip=[1, 1, 0, 1], items0=(item_id_of("眼镜"),))
        await _use(game_bot, group_event, "眼镜")
        assert "前两颗子弹中有 1 颗实弹。" in last_text(game_bot)

    async def test_眼镜_只剩一发(self, game_bot, group_event):
        make_state(clip=[1], items0=(item_id_of("眼镜"),))
        await _use(game_bot, group_event, "眼镜")
        assert "枪膛里只剩最后一颗子弹了，是实弹！" in last_text(game_bot)

    async def test_墨镜_首尾相加(self, game_bot, group_event):
        make_state(clip=[1, 0, 0, 1], items0=(item_id_of("墨镜"),))
        await _use(game_bot, group_event, "墨镜")
        assert "有2颗实弹！" in last_text(game_bot)

    async def test_墨镜_只剩一发(self, game_bot, group_event):
        make_state(clip=[0], items0=(item_id_of("墨镜"),))
        await _use(game_bot, group_event, "墨镜")
        assert "枪膛里只剩最后一颗子弹了，是空弹！" in last_text(game_bot)

    async def test_手铐(self, game_bot, group_event):
        state = make_state(hcf=0, items0=(item_id_of("手铐"),))
        await _use(game_bot, group_event, "手铐")
        assert state["hcf"] == 1
        assert state["item_0"] == []
        assert "你成功拷住了对方！" in last_text(game_bot)

    async def test_手铐_已经拷着就退回道具(self, game_bot, group_event):
        cuff = item_id_of("手铐")
        state = make_state(hcf=1, items0=(cuff,))
        await _use(game_bot, group_event, "手铐")
        assert state["hcf"] == 1
        assert state["item_0"] == [cuff], "用不掉的道具要还回来"
        assert "不可使用！对方仍处于束缚状态！" in last_text(game_bot)

    @pytest.mark.parametrize(("roll", "hcf", "skip"), [(0, 1, 1), (1, 3, 2)])
    async def test_禁止卡_禁一到两回合(self, game_bot, group_event, rand, roll, hcf, skip):
        ban = item_id_of("禁止卡")
        rand.plan("randint", roll)
        state = make_state(identity=1, hp_max=10, item_max=8,
                           items0=(ban,), items1=())
        await _use(game_bot, group_event, "禁止卡")
        assert state["hcf"] == hcf
        assert state["item_1"] == [ban], "对方会白捡一张禁止卡"
        assert f"禁止了{skip}个回合" in last_text(game_bot)

    async def test_禁止卡_对方道具满了就不给(self, game_bot, group_event, rand):
        ban = item_id_of("禁止卡")
        rand.plan("randint", 0)
        state = make_state(item_max=2, items0=(ban,), items1=(1, 1))
        await _use(game_bot, group_event, "禁止卡")
        assert state["item_1"] == [1, 1]
        assert "对方道具已满，并未获得这张禁止卡" in last_text(game_bot)

    async def test_小刀_伤害变二(self, game_bot, group_event):
        state = make_state(atk=0, items0=(item_id_of("小刀"),))
        await _use(game_bot, group_event, "小刀")
        assert state["atk"] == 1
        assert "攻击力提升至两点！" in last_text(game_bot)

    async def test_小刀_烈弓之后可以叠加(self, game_bot, group_event):
        knife = item_id_of("小刀")
        state = make_state(identity=1, hp_max=10, atk=2, add_atk=True,
                           items0=(knife, knife))
        await _use(game_bot, group_event, "小刀")
        assert state["atk"] == 3
        assert "目前这颗子弹的攻击力为4！" in last_text(game_bot)

    async def test_酒_残血时额外回血(self, game_bot, group_event):
        state = make_state(hp=(1, 4), items0=(item_id_of("酒"),))
        await _use(game_bot, group_event, "酒")
        assert state["atk"] == 1
        assert state["hp"] == [2, 4]
        assert "酒精振奋了你，hp恢复到2点！" in last_text(game_bot)

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
        assert "你退掉了一颗子弹，这颗子弹是：实弹" in last_text(game_bot)

    async def test_啤酒_退掉最后一发实弹就换弹加轮(self, game_bot, group_event, rand):
        rand.plan("choices", [1])
        rand.plan("randint", 2)
        rand.plan("sample", [0])
        rand.plan("randint", 0)
        state = make_state(clip=[0, 1], game_turn=1, items0=(item_id_of("啤酒"),))
        await _use(game_bot, group_event, "啤酒")
        assert state["game_turn"] == 2
        assert state["clip"] == [1, 0]
        assert "子弹已耗尽，重新装填！" in last_text(game_bot)

    async def test_手套_只换弹不刷道具(self, game_bot, group_event, rand):
        rand.plan("choices", [2])
        rand.plan("randint", 4)
        rand.plan("sample", [0, 3])
        state = make_state(clip=[0, 1], game_turn=1, items0=(item_id_of("手套"),))
        await _use(game_bot, group_event, "手套")
        assert state["clip"] == [1, 0, 0, 1]
        assert state["game_turn"] == 1, "换弹不算新的一轮"
        assert state["item_1"] == []
        assert "新弹夹总数：4 实弹数：2" in last_text(game_bot)

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
        assert "新道具为：医疗箱, 放大镜" in last_text(game_bot)

    async def test_刷新票_只有它自己(self, game_bot, group_event):
        state = make_state(items0=(item_id_of("刷新票"),))
        await _use(game_bot, group_event, "刷新票")
        assert state["item_0"] == []
        assert "现在一个新道具都没有！" in last_text(game_bot)

    @pytest.mark.parametrize("roll", [1, 5])
    async def test_欲望之盒_抽道具(self, game_bot, group_event, rand, roll):
        rand.plan("randint", roll)
        rand.plan("choice", 5)
        state = make_state(items0=(item_id_of("欲望之盒"),))
        await _use(game_bot, group_event, "欲望之盒")
        assert state["item_0"] == [5]
        assert "获得了道具：手铐" in last_text(game_bot)

    @pytest.mark.parametrize("roll", [6, 8])
    async def test_欲望之盒_回血(self, game_bot, group_event, rand, roll):
        rand.plan("randint", roll)
        state = make_state(hp=(3, 4), items0=(item_id_of("欲望之盒"),))
        await _use(game_bot, group_event, "欲望之盒")
        assert state["hp"] == [4, 4]
        assert "恢复了1点体力" in last_text(game_bot)

    async def test_欲望之盒_满血转成桃(self, game_bot, group_event, rand):
        rand.plan("randint", 6)
        state = make_state(hp=(6, 4), hp_max=6, items0=(item_id_of("欲望之盒"),))
        await _use(game_bot, group_event, "欲望之盒")
        assert state["hp"] == [6, 4]
        assert state["item_0"] == [1]
        assert "这点体力将转化为桃送给你" in last_text(game_bot)

    @pytest.mark.parametrize("roll", [9, 10])
    async def test_欲望之盒_打对面(self, game_bot, group_event, rand, roll):
        rand.plan("randint", roll)
        state = make_state(hp=(4, 4), items0=(item_id_of("欲望之盒"),))
        await _use(game_bot, group_event, "欲望之盒")
        assert state["hp"] == [4, 3]
        assert "对对面造成了一点伤害" in last_text(game_bot)

    async def test_无中生有_没束缚就跳回合(self, game_bot, group_event, rand):
        rand.plan("choice", 1, 2)
        state = make_state(hcf=0, atk=3, items0=(item_id_of("无中生有"),))
        await _use(game_bot, group_event, "无中生有")
        assert state["item_0"] == [1, 2]
        assert state["turn"] == 1
        assert state["atk"] == 0
        assert "代价是跳过了自己的回合" in last_text(game_bot)

    async def test_无中生有_有束缚就扣束缚(self, game_bot, group_event, rand):
        rand.plan("choice", 1, 2)
        state = make_state(hcf=3, atk=3, items0=(item_id_of("无中生有"),))
        await _use(game_bot, group_event, "无中生有")
        assert state["hcf"] == 1
        assert state["turn"] == 0, "回合留在自己这儿"
        assert state["atk"] == 3, "这条分支不清加伤"
        assert "对方的束缚的回合将-1" in last_text(game_bot)

    async def test_天秤_道具多就打人(self, game_bot, group_event):
        scale = item_id_of("天秤")
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(scale, 1, 1), items1=(1,))
        await _use(game_bot, group_event, "天秤")
        assert state["hp"] == [5, 4]
        assert "由于2≥1，你成功对对方造成一点伤害" in last_text(game_bot)

    async def test_天秤_道具少就回血(self, game_bot, group_event):
        scale = item_id_of("天秤")
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(scale,), items1=(1, 1))
        await _use(game_bot, group_event, "天秤")
        assert state["hp"] == [6, 5]
        assert "由于0<2，你回复一点体力" in last_text(game_bot)

    async def test_休养生息_对面满血只回自己一点(self, game_bot, group_event):
        state = make_state(identity=1, hp=(5, 10), hp_max=10, item_max=8,
                           items0=(item_id_of("休养生息"),))
        await _use(game_bot, group_event, "休养生息")
        assert state["hp"] == [6, 10]
        assert "对方hp已满，你仅恢复了1点hp" in last_text(game_bot)

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
        assert state["item_0"] == [blade]
        assert "你的血量无法支持你使用血刃！" in last_text(game_bot)

    async def test_黑洞_抢一个道具(self, game_bot, group_event, rand):
        rand.plan("randint", 1)
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(item_id_of("黑洞"),), items1=(1, 2, 3))
        await _use(game_bot, group_event, "黑洞")
        assert state["item_1"] == [1, 3]
        assert state["item_0"] == [2]
        assert "对方的【医疗箱】被黑洞吞噬" in last_text(game_bot)

    async def test_黑洞_对面没道具就退回来(self, game_bot, group_event):
        hole = item_id_of("黑洞")
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(hole,), items1=())
        await _use(game_bot, group_event, "黑洞")
        assert state["item_0"] == [hole]
        assert "黑洞在无尽的沉寂中回到了你的手中" in last_text(game_bot)

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
        assert "当前hp上限：11" in last_text(game_bot)

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
        assert state["item_0"] == [adr]
        assert "你无法承受这种后果" in last_text(game_bot)

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
        assert state["item_0"] == [tnt]
        assert "这样做无异于自杀" in last_text(game_bot)

    async def test_双转团_转给对方(self, game_bot, group_event, rand):
        gift = item_id_of("双转团")
        rand.plan("randint", 2)  # kou_first != 1，不触发额外效果
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(gift,), items1=())
        await _use(game_bot, group_event, "双转团")
        assert state["item_0"] == []
        assert state["item_1"] == [gift]
        assert "对方十分感兴趣，所以拿走了这件物品" in last_text(game_bot)

    async def test_双转团_对方满了就丢掉(self, game_bot, group_event, rand):
        gift = item_id_of("双转团")
        rand.plan("randint", 2)
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=2,
                           items0=(gift,), items1=(1, 1))
        await _use(game_bot, group_event, "双转团")
        assert state["item_1"] == [1, 1]
        assert "没办法拿走这件物品，所以把双转团丢了" in last_text(game_bot)

    async def test_双转团_顺手牵羊还摔一跤(self, game_bot, group_event, rand):
        gift = item_id_of("双转团")
        # kou_first=1 -> kou_second=1 -> 抽走 index 0 -> 1/2 判定命中
        rand.plan("randint", 1, 1, 0, 1)
        state = make_state(identity=1, hp=(5, 5), hp_max=10, item_max=8,
                           items0=(gift, 1), items1=())
        await _use(game_bot, group_event, "双转团")
        assert state["item_0"] == []
        assert sorted(state["item_1"]) == sorted([gift, 1])
        assert state["hp"] == [5, 4]
        assert "对方还顺手拿走了你的【桃】" in last_text(game_bot)
        assert "一不小心摔了一跤，hp-1" in last_text(game_bot)

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
        msg = last_text(game_bot)
        assert "游戏结束！" in msg
        assert f"恭喜[CQ:at,qq={P0}]胜利！" in msg
        assert game.datas.demon_data[GID_S]["start"] is False


# ==========================================================================
# 查看局势
# ==========================================================================
async def _check(bot, group_event, *, user_id: int = P0) -> bool:
    event = group_event("*查看局势", user_id=user_id)
    return await drive(game.check, game.check_handle, bot, event, event=event)


class TestCheckSituation:
    async def test_没开局(self, game_bot, group_event):
        make_state(start=False)
        await _check(game_bot, group_event)
        assert "当前并没有开始任何一句轮盘哦！" in last_text(game_bot)

    async def test_局外人看不了(self, game_bot, group_event):
        make_state()
        await _check(game_bot, group_event, user_id=P2)
        assert "只有当前局内玩家能查看局势哦！" in last_text(game_bot)

    async def test_正常输出(self, game_bot, group_event, clock):
        make_state(hp=(4, 5), hp_max=6, item_max=6, clip=[0, 1, 1],
                   items0=(1, 2), items1=(), game_turn=3, now=FAKE_NOW)
        clock.advance(90)
        await _check(game_bot, group_event)
        msg = last_text(game_bot)
        assert "- 本局模式：正常模式" in msg
        assert "- 本步剩余时间：8分30秒" in msg  # 600-90 = 510s
        assert "- 当前轮数：3" in msg
        assert "hp：4/6" in msg and "hp：5/6" in msg
        assert "道具(2/6)" in msg and "道具(0/6)" in msg
        assert "桃, 医疗箱" in msg
        assert "你目前没有道具哦！" in msg
        assert "总弹数3，实弹数2" in msg

    async def test_束缚和加伤只在非零时显示(self, game_bot, group_event):
        make_state(hcf=3, atk=2)
        await _check(game_bot, group_event)
        msg = last_text(game_bot)
        assert "- 当前对方剩余束缚回合数：2" in msg
        assert "- 本颗子弹伤害为：3点" in msg

    async def test_没束缚没加伤就不显示(self, game_bot, group_event):
        make_state(hcf=0, atk=0)
        await _check(game_bot, group_event)
        msg = last_text(game_bot)
        assert "剩余束缚回合数" not in msg
        assert "本颗子弹伤害为" not in msg

    @pytest.mark.parametrize(
        ("identity", "turn", "label", "death"),
        [
            (1, 12, "身份模式", False),
            (1, 13, "身份模式", True),
            (2, 5, "急速模式", False),
            (2, 6, "急速模式", True),
            (0, 999, "正常模式", False),
        ],
    )
    async def test_模式与死斗标记(
        self, game_bot, group_event, identity, turn, label, death
    ):
        make_state(identity=identity, game_turn=turn, hp_max=10, item_max=8)
        await _check(game_bot, group_event)
        msg = last_text(game_bot)
        assert label in msg
        assert ("（死斗）" in msg) is death


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
        await _surrender(fake_bot, group_event)
        assert "当前没有进行中的游戏！" in last_text(fake_bot)

    async def test_只有一个人等着也算没开局(self, fake_bot, group_event):
        state = make_state(start=False)
        state["pl"] = [str(P0)]
        await _surrender(fake_bot, group_event)
        assert "当前没有进行中的游戏！" in last_text(fake_bot)

    async def test_局外人投不了(self, fake_bot, group_event):
        make_state()
        await _surrender(fake_bot, group_event, user_id=P2)
        assert "你当前不在游戏中，无法投降！" in last_text(fake_bot)

    @pytest.mark.parametrize(("loser", "winner"), [(P0, P1), (P1, P0)])
    async def test_投降判对方胜(self, fake_bot, group_event, loser, winner):
        make_state()
        await _surrender(fake_bot, group_event, user_id=loser)
        msg = last_text(fake_bot)
        assert f"玩家[CQ:at,qq={loser}]已投降。" in msg
        assert f"恭喜[CQ:at,qq={winner}]胜利！" in msg
        assert game.datas.demon_data[GID_S]["start"] is False
        assert game.datas.demon_data[GID_S]["pl"] == []

    async def test_重复投降(self, fake_bot, group_event):
        make_state()
        await _surrender(fake_bot, group_event)
        await _surrender(fake_bot, group_event)
        assert "当前没有进行中的游戏！" in last_text(fake_bot)

    async def test_投降不受回合限制(self, fake_bot, group_event):
        """轮到 P0，但 P1 照样能投。"""
        make_state(turn=0)
        await _surrender(fake_bot, group_event, user_id=P1)
        assert f"恭喜[CQ:at,qq={P0}]胜利！" in last_text(fake_bot)


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
        msg = last_text(fake_bot)
        assert "道具【烈弓】的效果是：" in msg
        assert game.item_effects["烈弓"] in msg

    async def test_查不存在的道具(self, fake_bot, group_event):
        event = group_event("*恶魔道具 不存在")
        await drive(
            game.prop_demon_query, game.prop_demon_query_handle, fake_bot, event,
            bot=fake_bot, event=event, arg=Message("不存在"),
        )
        assert "未找到名为【不存在】的道具" in last_text(fake_bot)

    @pytest.mark.parametrize("arg", ["", "all", "  ALL  "])
    async def test_查全部走合并转发(self, fake_bot, group_event, arg):
        event = group_event("*恶魔道具")
        await drive(
            game.prop_demon_query, game.prop_demon_query_handle, fake_bot, event,
            bot=fake_bot, event=event, arg=Message(arg),
        )
        api, data = fake_bot.calls[-1]
        assert api == "send_group_forward_msg"
        assert data["group_id"] == GID
        content = data["messages"][0]["data"]["content"]
        for name in game.item_dic.values():
            assert f"-【{name}】：" in content

    async def test_大小写不敏感(self, fake_bot, group_event):
        event = group_event("*恶魔道具 烈性tnt")
        await drive(
            game.prop_demon_query, game.prop_demon_query_handle, fake_bot, event,
            bot=fake_bot, event=event, arg=Message("烈性tnt"),
        )
        assert "道具【烈性TNT】的效果是：" in last_text(fake_bot)


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


def _game_command_names() -> set[str]:
    """game 模块里所有注册过的命令名 + 别名"""
    names: set[str] = set()
    for obj in vars(game).values():
        if isinstance(obj, type) and issubclass(obj, Matcher) and obj is not Matcher:
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

    @pytest.mark.parametrize("name", sorted(COMMANDS))
    def test_每条都有必填字段(self, name):
        cmd = COMMANDS[name]
        assert isinstance(cmd, Cmd)
        assert name.strip() == name and name, f"{name} 的命令名有多余空白"
        for field in ("usage", "summary", "detail"):
            value = getattr(cmd, field)
            assert isinstance(value, str)
            assert value.strip(), f"{name} 的 {field} 是空的"
        assert cmd.category in CATEGORIES, f"{name} 的分类 {cmd.category} 不在 CATEGORIES 里"
        assert isinstance(cmd.examples, tuple)
        assert isinstance(cmd.aliases, tuple)
        assert all(isinstance(e, str) and e.strip() for e in cmd.examples)
        assert all(isinstance(a, str) and a.strip() for a in cmd.aliases)

    @pytest.mark.parametrize("name", sorted(COMMANDS))
    def test_usage_以前缀加命令名开头(self, name):
        cmd = COMMANDS[name]
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

    def test_前缀只有星号和空(self):
        assert {c.prefix for c in COMMANDS.values()} == {"*", ""}


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

    def test_没有前缀的那条是戳一戳而且真的挂了处理函数(self):
        """唯一 prefix 为空的条目是「戳一戳」，它不是命令而是 on_type 的通知响应。"""
        no_prefix = [n for n, c in COMMANDS.items() if not c.prefix]
        assert no_prefix == ["戳一戳"]
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

    def test_setmode说明里的数字和代码一致(self):
        """help 把三个模式的血量/道具上限写死在正文里了，得和 game 对得上。"""
        detail = COMMANDS["setmode"].detail
        assert f"基础 {len(game.item_dic1)} 个道具，血量上限 6，道具上限 6" in detail
        assert f"全部 {len(game.item_dic)} 个道具，血量上限 10，道具上限 8" in detail
        assert f"超过 {game.death_turn} 轮进死斗" in detail
        assert "血量上限 16，道具上限 10" in detail
        assert f"超过 {game.pangguang_turn} 轮就进死斗" in detail

    def test_betgame说明里的等待超时和代码一致(self):
        assert f"（{game.turn_time // 60} 分钟）" in COMMANDS["betgame"].detail

    def test_开枪说明里的超时和代码一致(self):
        assert f"每步限时 {game.turn_time // 60} 分钟" in COMMANDS["开枪"].constraints

    def test_使用说明里列的道具名和代码一致(self):
        detail = COMMANDS["使用"].detail
        assert f"通用道具 {len(game.item_dic1)} 个" in detail
        assert f"还会多出 {len(game.item_dic2)} 个" in detail
        for name in game.item_dic.values():
            assert name in detail, f"help 的道具清单里漏了 {name}"

    def test_恶魔道具说明里的总数和代码一致(self):
        assert f"全部 {len(game.item_dic)} 个道具" in COMMANDS["恶魔道具"].detail


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
    @pytest.mark.parametrize(
        ("text", "width"),
        [("", 0), ("abc", 3), ("中文", 4), ("*jrrp", 5), ("a中", 3), ("１", 2)],
    )
    def test_显示宽度(self, text, width):
        assert helpmod._width(text) == width

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
    def test_列出了每一条命令(self):
        text = helpmod._overview()
        for name, cmd in COMMANDS.items():
            assert cmd.prefix + name in text, f"总览里少了 {name}"
            assert cmd.summary in text

    def test_列出了每一个分类标题(self):
        text = helpmod._overview()
        for title in CATEGORIES.values():
            assert f"【{title}】" in text

    def test_列出的条目不多不少(self):
        """总览里缩进两格的每个标签，必须和 COMMANDS 一一对应。"""
        text = helpmod._overview()
        labels = set(re.findall(r"^ {2}(\S+)", text, flags=re.M))
        known = {c.prefix + n for n, c in COMMANDS.items()}
        assert labels == known, (
            f"总览多出来的：{labels - known}；漏掉的：{known - labels}"
        )

    def test_头几行是使用提示(self):
        lines = helpmod._overview().splitlines()
        assert lines[0] == "小小卒的命令基本都以 * 开头"
        assert "*help 命令名" in lines[1]
        assert lines[2] == "按分类看：*help " + " / ".join(CATEGORIES)


class TestHelpRender:
    def test_基本结构(self):
        text = helpmod._render("jrrp", COMMANDS["jrrp"])
        assert text.startswith("【*jrrp】今日人品，每天一次")
        assert COMMANDS["jrrp"].usage in text
        assert COMMANDS["jrrp"].detail in text
        assert "限制：只能在群里用" in text
        assert "别名：" not in text

    def test_有别名才有别名段(self):
        text = helpmod._render("news", COMMANDS["news"])
        assert "别名：*公告、*新闻" in text

    def test_有例子才有例子段(self):
        with_examples = helpmod._render("map", COMMANDS["map"])
        assert "例子：" in with_examples
        assert "  *map" in with_examples
        no_examples = helpmod._render(
            "x", Cmd(usage="*x", summary="s", detail="d", category="fun")
        )
        assert "例子：" not in no_examples
        assert "限制：" not in no_examples

    def test_自带前缀的别名不会被重复加前缀(self):
        cmd = Cmd(
            usage="*x", summary="s", detail="d", category="fun",
            aliases=("。带前缀", "不带前缀"),
        )
        text = helpmod._render("x", cmd)
        assert "别名：。带前缀、*不带前缀" in text

    def test_空前缀命令的标题(self):
        text = helpmod._render("戳一戳", COMMANDS["戳一戳"])
        assert text.startswith("【戳一戳】")

    def test_分类页(self):
        text = helpmod._render_category("guess")
        assert text.startswith("【猜图】")
        for name, cmd in helpmod._by_category("guess"):
            assert cmd.prefix + name in text
        assert text.endswith("看单条的详细用法：*help 命令名")


# ==========================================================================
# help / references 两条命令
# ==========================================================================
async def _help(bot, event_factory, query: str) -> str:
    event = event_factory(f"*help {query}".strip())
    await drive(
        helpmod.xiaozubothelp, helpmod.handle_help, bot, event, arg=Message(query)
    )
    return last_text(bot)


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
        """CATEGORY_ALIASES 里的 admin 指向一个没有命令的分类。"""
        text = await _help(fake_bot, group_event, "admin")
        assert text.startswith("没有「admin」这个命令或分类")

    async def test_未知命令给兜底提示(self, fake_bot, group_event):
        text = await _help(fake_bot, group_event, "zzzzz")
        assert text == "没有「zzzzz」这个命令或分类，*help 看全部。"

    async def test_未知命令带相近推荐(self, fake_bot, group_event):
        text = await _help(fake_bot, group_event, "gdsearchhelpx")
        assert "没有「gdsearchhelpx」这个命令或分类" in text
        assert "你是不是想找：" in text
        assert "*gdsearchhelp" in text

    async def test_推荐最多五条(self, fake_bot, group_event):
        """gues 能匹配到 6 条 guess_*，但提示语只截前 5 条。"""
        assert len([n for n in COMMANDS if "gues" in n]) > 5
        text = await _help(fake_bot, group_event, "gues")
        suggestions = text.split("你是不是想找：")[1].split("、")
        assert len(suggestions) == 5
        assert all(s.startswith("*") for s in suggestions)

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


class TestReferences:
    USAGE = "use *references nlw/plat/gddl/hds/ids <page>"

    async def test_不带参数(self, fake_bot, group_event):
        assert await _refs(fake_bot, group_event, "") == self.USAGE

    @pytest.mark.parametrize("name", ["nope", "gddl2", "参考"])
    async def test_未知表名(self, fake_bot, group_event, name):
        assert await _refs(fake_bot, group_event, name) == self.USAGE

    async def test_aredl_没有固定参考线(self, fake_bot, group_event):
        assert "AREDL是实时变化的" in await _refs(fake_bot, group_event, "aredl")

    @pytest.mark.parametrize(
        ("name", "pages"),
        [("gddl", 8), ("nlw", 4), ("plat", 2)],
    )
    async def test_分页表的页数(self, fake_bot, group_event, name, pages):
        table = {"gddl": helpmod.REF_GDDL, "nlw": helpmod.REF_NLW,
                 "plat": helpmod.REF_PDIFF}[name]
        assert len(table) == pages
        for page in range(1, pages + 1):
            text = await _refs(fake_bot, group_event, f"{name} {page}")
            assert text == table[page - 1] + helpmod.pagehint(page, pages)

    @pytest.mark.parametrize(("name", "pages"), [("gddl", 8), ("nlw", 4), ("plat", 2)])
    async def test_页码超出范围(self, fake_bot, group_event, name, pages):
        text = await _refs(fake_bot, group_event, f"{name} {pages + 1}")
        assert f"你输入的页码数超出范围（共{pages}页" in text

    @pytest.mark.parametrize(("name", "pages"), [("gddl", 8), ("nlw", 4), ("plat", 2)])
    async def test_最后一页仍然有效(self, fake_bot, group_event, name, pages):
        """上界是 `page > len`，最后一页必须还能翻到（差一位的另一边）"""
        table = {"gddl": helpmod.REF_GDDL, "nlw": helpmod.REF_NLW,
                 "plat": helpmod.REF_PDIFF}[name]
        text = await _refs(fake_bot, group_event, f"{name} {pages}")
        assert text == table[-1] + helpmod.pagehint(pages, pages)

    @pytest.mark.parametrize(("name", "pages"), [("gddl", 8), ("nlw", 4), ("plat", 2)])
    async def test_页码0被拒绝(self, fake_bot, group_event, name, pages):
        """"0".isdigit() 是 True，以前 page=0 会走到 REF_XXX[-1] 翻出最后一页，
        提示语还写着「第0页」。现在和超上界一样报错。"""
        text = await _refs(fake_bot, group_event, f"{name} 0")
        assert f"你输入的页码数超出范围（共{pages}页" in text
        assert "当前处于第0页" not in text

    @pytest.mark.parametrize(("name", "pages"), [("gddl", 8), ("nlw", 4), ("plat", 2)])
    async def test_第一页有效(self, fake_bot, group_event, name, pages):
        table = {"gddl": helpmod.REF_GDDL, "nlw": helpmod.REF_NLW,
                 "plat": helpmod.REF_PDIFF}[name]
        text = await _refs(fake_bot, group_event, f"{name} 1")
        assert text == table[0] + helpmod.pagehint(1, pages)

    async def test_多个0也被拒绝(self, fake_bot, group_event):
        """"00".isdigit() 同样是 True"""
        assert "你输入的页码数超出范围（共8页" in await _refs(fake_bot, group_event, "gddl 00")

    async def test_不给页码默认第一页(self, fake_bot, group_event):
        assert await _refs(fake_bot, group_event, "gddl") == helpmod.REF_GDDL[
            0
        ] + helpmod.pagehint(1, 8)

    @pytest.mark.parametrize("bad", ["-1", "-8", "abc", "1.5"])
    async def test_页码不是数字就当第一页(self, fake_bot, group_event, bad):
        """负号和小数点都过不了 isdigit()，跟乱输一样退回第一页（不是报错）"""
        assert await _refs(fake_bot, group_event, f"gddl {bad}") == helpmod.REF_GDDL[
            0
        ] + helpmod.pagehint(1, 8)

    @pytest.mark.parametrize(
        ("name", "table_attr"),
        [("lw", "REF_LW"), ("ids", "REF_IDS"), ("hds", "REF_HDS")],
    )
    async def test_单页表忽略页码(self, fake_bot, group_event, name, table_attr):
        table = getattr(helpmod, table_attr)
        assert len(table) == 1
        assert await _refs(fake_bot, group_event, name) == table[0]
        assert await _refs(fake_bot, group_event, f"{name} 99") == table[0]

    async def test_表名大小写不敏感(self, fake_bot, group_event):
        assert "AREDL是实时变化的" in await _refs(fake_bot, group_event, "AREDL")

    def test_pagehint文案(self):
        assert helpmod.pagehint(3, 8) == "\n当前处于第3页，共8页"
