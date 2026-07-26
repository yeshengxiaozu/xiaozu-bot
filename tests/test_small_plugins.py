"""小插件（jrrp / joy / roulette / zhua / say / ai / guess）的行为测试。

这些插件的处理函数都是 nonebot 的 matcher handler，最后一句几乎都是
`await xxx.finish(...)`。`Matcher.finish` 是 classmethod，内部走
`current_bot` / `current_event` 两个 ContextVar 拿到 bot 和事件，
调完 `bot.send(...)` 再抛 `FinishedException`。

所以这里的做法是：**不打桩 finish/send，而是把 ContextVar 设成 FakeBot 和
真事件，直接 await handler 本体，然后捕获 FinishedException**。
这样连 OneBot 适配器里 `at_sender` 拼 @ 段、message_type 推断那一圈
都是真跑的，断言的是真正会发出去的 API 调用（都记在 FakeBot.calls 里）。

注意：handler 的形参默认值是 `CommandArg()` 这类依赖注入对象，直接调必须
显式把 `arg=Message(...)` 传进去，否则 `str(arg)` 拿到的是依赖对象本身。
`run_handler` 按签名挑参数就是干这个的。
"""

from __future__ import annotations

import contextlib
import inspect
import os
import random
import re
import sys
import types
from datetime import datetime as real_datetime
from pathlib import Path
from typing import Any, Callable, Optional, Union
from urllib.parse import unquote

import httpx
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11.event import PokeNotifyEvent
from nonebot.exception import FinishedException
from nonebot.matcher import current_bot, current_event
from PIL import Image

from tests.conftest import BOT_SELF_ID, DEFAULT_GROUP_ID, DEFAULT_USER_ID, FakeBot
from xiaozu_bot.plugins import ai, guess, joy, jrrp, roulette, say, zhua

# ==========================================================================
# 公共工具
# ==========================================================================

#: joy 里 `banned()` 硬编码的被禁群号，say / ai 里也是同一个
BANNED_GROUP = 569801410
#: say / ai 里硬编码的“主人”QQ 号
MASTER_ID = 3251605531


def _as_message(value: Union[None, str, Message]) -> Message:
    if value is None:
        return Message("")
    return value if isinstance(value, Message) else Message(value)


@contextlib.contextmanager
def matcher_context(bot: FakeBot, event: Any):
    """把 nonebot 的 current_bot / current_event 临时设成给定的 bot 和事件。

    `Matcher.send` 就是从这两个 ContextVar 里取东西的，不设的话直接 LookupError。
    """
    token_bot = current_bot.set(bot)
    token_event = current_event.set(event)
    try:
        yield
    finally:
        current_bot.reset(token_bot)
        current_event.reset(token_event)


async def run_handler(
    matcher: Any,
    bot: FakeBot,
    event: Any = None,
    *,
    arg: Union[None, str, Message] = None,
    index: int = 0,
) -> bool:
    """直接调用某个 matcher 的第 index 个 handler。

    返回 True 表示 handler 以 `finish()` 收尾（抛了 FinishedException），
    返回 False 表示它自己 return 掉了。发出去的东西都在 `bot.calls` 里。
    """
    handler = matcher.handlers[index].call
    message = _as_message(arg)
    pool: dict[str, Any] = {
        "bot": bot,
        "event": event,
        "matcher": matcher,
        "arg": message,
        "args": message,
    }
    signature = inspect.signature(handler)
    kwargs = {name: value for name, value in pool.items() if name in signature.parameters}
    with matcher_context(bot, event):
        try:
            await handler(**kwargs)
        except FinishedException:
            return True
    return False


async def run_coro(bot: FakeBot, event: Any, factory: Callable[[], Any]) -> bool:
    """跑一个不是 handler 的协程（比如 guess.can_start），语义同 run_handler。"""
    with matcher_context(bot, event):
        try:
            await factory()
        except FinishedException:
            return True
    return False


def sent_messages(bot: FakeBot) -> list[Message]:
    """FakeBot 收到的所有 send_msg 调用里的消息体"""
    return [data["message"] for api, data in bot.calls if api == "send_msg"]


def sent_texts(bot: FakeBot) -> list[str]:
    """FakeBot 发出去的纯文本（@ 段之类会被 extract_plain_text 滤掉）"""
    return [msg.extract_plain_text().strip() for msg in sent_messages(bot)]


def emoji_likes(bot: FakeBot) -> list[tuple[Any, str]]:
    """所有 set_msg_emoji_like 调用，形如 [(message_id, emoji_id), ...]"""
    return [
        (data["message_id"], data["emoji_id"])
        for api, data in bot.calls
        if api == "set_msg_emoji_like"
    ]


def image_files(message: Message) -> list[str]:
    """消息里所有 image 段的 file 字段（file:// URI，非 ASCII 会被百分号转义，这里解回来）"""
    return [unquote(seg.data["file"]) for seg in message if seg.type == "image"]


def image_size(path: Path) -> tuple[int, int]:
    """读一张图的尺寸，顺手关掉文件句柄（不然 PIL 会留下 ResourceWarning）"""
    with Image.open(path) as image:
        return image.size


def scripted(values: list[Any]) -> Callable[..., Any]:
    """做一个按顺序吐值的假随机函数；吐完了就一直吐最后一个。"""
    remaining = list(values)

    def _call(*_: Any, **__: Any) -> Any:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return _call


# ==========================================================================
# jrrp
# ==========================================================================
class TestJrrp:
    """*jrrp：随机人品值 + 评语分档 + 当天只能算一次"""

    @pytest.fixture
    def store(self, patch_storage):
        """把 jrrp.r 换成临时文件上的 JsonRedis"""
        return patch_storage(jrrp)

    @pytest.fixture(autouse=True)
    def frozen_clock(self, monkeypatch):
        """把 jrrp 看到的时钟钉死在中午，不许读真实挂钟。

        源码第 50 行按 `(23-h)*3600 + (59-m)*60 + (59-s)` 算过期秒数，
        真跑在本地时间 23:59:59 那一秒算出来就是 ex=0；JsonRedis 把
        `now >= _exp` 当成已过期，于是 set 完立刻就读不回来了
        （见下面 test_result_at_the_last_second_of_the_day_is_lost）。
        不钉时钟的话，凡是断言「结果存下来了」的用例每天都有一秒钟是红的。

        需要别的时刻的用例（测过期秒数那两组）在自己函数体里再 setattr
        一次覆盖掉即可 —— monkeypatch 后设的生效，teardown 时逆序还原。
        """
        moment = real_datetime(2026, 7, 27, 12, 0, 0)
        monkeypatch.setattr(
            jrrp,
            "datetime",
            types.SimpleNamespace(datetime=types.SimpleNamespace(now=lambda: moment)),
        )

    # ---- 评语分档的边界 ------------------------------------------------
    # 源码是一串 `rp <= N` 的 elif，每档都测两侧，防止哪天改成 `<` 悄悄错位。
    @pytest.mark.parametrize(
        ("rp", "append"),
        [
            (1, "要不要挑战一下祈愿出1级？"),
            (2, "也许今天不适合玩会boom的道具……"),
            (20, "也许今天不适合玩会boom的道具……"),
            (21, "可能在其他bot那里的rp会高一些……"),
            (40, "可能在其他bot那里的rp会高一些……"),
            (41, "平平淡淡才是真。"),
            (60, "平平淡淡才是真。"),
            (61, "打什么蔚蓝，快来玩zhuamadeline"),
            (80, "打什么蔚蓝，快来玩zhuamadeline"),
            (81, "似乎浪费了一次五级不boom的机会(bushi)"),
            (99, "似乎浪费了一次五级不boom的机会(bushi)"),
            (100, "wow！你裸抓五级/藏品的运气用在这了！"),
        ],
    )
    async def test_comment_buckets(
        self, rp, append, store, fake_bot, make_group_event, monkeypatch
    ):
        """每一档评语的上下边界都对得上"""
        monkeypatch.setattr(random, "randint", lambda a, b: rp)
        event = make_group_event("*jrrp")

        assert await run_handler(jrrp.jrrp, fake_bot, event) is True
        assert sent_texts(fake_bot) == [f"你的今日人品是……{rp}！{append}"]

    async def test_rolls_in_1_to_100(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        """摇的范围是闭区间 [1, 100]"""
        seen: list[tuple[int, int]] = []

        def spy(a, b):
            seen.append((a, b))
            return 50

        monkeypatch.setattr(random, "randint", spy)
        await run_handler(jrrp.jrrp, fake_bot, make_group_event("*jrrp"))
        assert seen == [(1, 100)]

    # ---- 一天一次的缓存 ------------------------------------------------
    async def test_result_is_persisted(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        """摇完要把结果按 jrrp_<qq> 存起来"""
        monkeypatch.setattr(random, "randint", lambda a, b: 42)
        await run_handler(jrrp.jrrp, fake_bot, make_group_event("*jrrp"))
        assert store.get(f"jrrp_{DEFAULT_USER_ID}") == "42"

    async def test_second_call_returns_cached_value(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        """当天第二次问，直接把存下来的值念回去，而且不再摇新的"""
        store.set(f"jrrp_{DEFAULT_USER_ID}", "66")

        def boom(*_a, **_k):
            raise AssertionError("已经有缓存了就不该再摇")

        monkeypatch.setattr(random, "randint", boom)

        assert await run_handler(jrrp.jrrp, fake_bot, make_group_event("*jrrp")) is True
        assert sent_texts(fake_bot) == ["你的今日人品是66！（你不是已经知道了吗）"]

    async def test_cached_reply_ats_the_sender(
        self, store, fake_bot, make_group_event
    ):
        """缓存分支带 at_sender=True，群里会 @ 提问的人"""
        store.set(f"jrrp_{DEFAULT_USER_ID}", "66")
        await run_handler(jrrp.jrrp, fake_bot, make_group_event("*jrrp"))
        assert MessageSegment.at(DEFAULT_USER_ID) in sent_messages(fake_bot)[0]

    async def test_fresh_reply_does_not_at_the_sender(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        """第一次摇的那条反而没有 at_sender —— 和缓存分支不一致，但确实是当前行为"""
        monkeypatch.setattr(random, "randint", lambda a, b: 42)
        await run_handler(jrrp.jrrp, fake_bot, make_group_event("*jrrp"))
        assert MessageSegment.at(DEFAULT_USER_ID) not in sent_messages(fake_bot)[0]

    async def test_cache_is_per_user(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        """缓存的键只带 QQ 号，不带群号，所以换个人就重新摇"""
        store.set(f"jrrp_{DEFAULT_USER_ID}", "66")
        monkeypatch.setattr(random, "randint", lambda a, b: 7)

        await run_handler(jrrp.jrrp, fake_bot, make_group_event("*jrrp", user_id=999))
        assert sent_texts(fake_bot)[0].startswith("你的今日人品是……7！")

    async def test_true_sentinel_is_treated_as_no_cache(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        """历史遗留：存成字符串 "True" 的键会被当成没缓存，重新摇一次

        源码里那句 `!= "True"` 是给早年存布尔值的旧数据兜底的。
        """
        store.set(f"jrrp_{DEFAULT_USER_ID}", "True")
        monkeypatch.setattr(random, "randint", lambda a, b: 55)

        await run_handler(jrrp.jrrp, fake_bot, make_group_event("*jrrp"))
        assert sent_texts(fake_bot)[0].startswith("你的今日人品是……55！")
        assert store.get(f"jrrp_{DEFAULT_USER_ID}") == "55"

    # ---- 过期时间算的是“到今天 23:59:59 还有几秒” ----------------------
    @pytest.mark.parametrize(
        ("hour", "minute", "second", "expected_ex"),
        [
            # (23-h)*3600 + (59-m)*60 + (59-s)
            (0, 0, 0, 86399),  # 刚过零点：离 24:00:00 其实还有 86400 秒，这里少了 1 秒
            (10, 30, 15, 48584),
            (23, 59, 58, 1),
            (23, 59, 59, 0),  # 一天的最后一秒：ex=0 等于立刻过期，这次结果根本存不住
        ],
    )
    async def test_expiry_seconds_until_midnight(
        self,
        hour,
        minute,
        second,
        expected_ex,
        store,
        fake_bot,
        make_group_event,
        monkeypatch,
    ):
        """过期秒数按注入的时钟算，不碰真实时间"""
        moment = real_datetime(2026, 7, 27, hour, minute, second)
        fake_module = types.SimpleNamespace(
            datetime=types.SimpleNamespace(now=lambda: moment)
        )
        monkeypatch.setattr(jrrp, "datetime", fake_module)
        monkeypatch.setattr(random, "randint", lambda a, b: 42)

        recorded: list[tuple[str, Any, Optional[int]]] = []
        real_set = store.set

        def spy(key, value, ex=None):
            recorded.append((key, value, ex))
            real_set(key, value, ex=ex)

        monkeypatch.setattr(store, "set", spy)

        await run_handler(jrrp.jrrp, fake_bot, make_group_event("*jrrp"))
        assert recorded == [(f"jrrp_{DEFAULT_USER_ID}", "42", expected_ex)]

    async def test_result_at_the_last_second_of_the_day_is_lost(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        """23:59:59 摇出来的结果 ex=0，存进去立刻就过期了（真实行为，看着像 bug）"""
        moment = real_datetime(2026, 7, 27, 23, 59, 59)
        monkeypatch.setattr(
            jrrp,
            "datetime",
            types.SimpleNamespace(datetime=types.SimpleNamespace(now=lambda: moment)),
        )
        monkeypatch.setattr(random, "randint", lambda a, b: 42)

        await run_handler(jrrp.jrrp, fake_bot, make_group_event("*jrrp"))
        assert store.get(f"jrrp_{DEFAULT_USER_ID}") is None


# ==========================================================================
# joy
# ==========================================================================
class TestJoyRules:
    """joy 里的 banned / notbanned 两个 Rule 判定函数"""

    def test_banned_only_matches_the_one_group(self, make_group_event):
        assert joy.banned(make_group_event(group_id=BANNED_GROUP)) is True
        assert joy.banned(make_group_event(group_id=BANNED_GROUP + 1)) is False

    def test_private_chat_is_never_banned(self, make_private_event):
        """banned 先判 isinstance(GroupMessageEvent)，私聊一律放行"""
        assert joy.banned(make_private_event()) is False
        assert joy.notbanned(make_private_event()) is True

    def test_notbanned_is_the_exact_negation(self, make_group_event):
        for gid in (BANNED_GROUP, BANNED_GROUP + 1):
            event = make_group_event(group_id=gid)
            assert joy.notbanned(event) is (not joy.banned(event))


class TestJoyUltra:
    """*ultra：把“不要再XX了”模板套到用户给的两个词上"""

    @pytest.fixture
    def bot(self, fake_bot):
        fake_bot.api_results["send_msg"] = {"message_id": 4242}
        return fake_bot

    async def test_overlong_argument_only_gets_an_emoji(self, bot, make_group_event):
        """参数超过 100 个字符就只贴个 424 表情，一个字都不回"""
        event = make_group_event("*ultra " + "x" * 101, message_id=7)
        assert await run_handler(joy.ultra, bot, event, arg="x" * 101) is True
        assert emoji_likes(bot) == [(7, "424")]
        assert sent_messages(bot) == []

    async def test_no_argument_sends_the_copypasta(self, bot, make_group_event):
        """不带参数发原版长文，并给自己那条消息贴 128560"""
        assert await run_handler(joy.ultra, bot, make_group_event("*ultra")) is True
        text = sent_texts(bot)[0]
        assert text.startswith("不要再Ultra了！")
        assert text.endswith("抵制Ultra！！！")
        assert emoji_likes(bot) == [(4242, "128560")]

    async def test_single_argument_is_rejected(self, bot, make_group_event):
        """只给一个词凑不成模板，贴 424 走人"""
        event = make_group_event("*ultra 蔚蓝", message_id=9)
        assert await run_handler(joy.ultra, bot, event, arg="蔚蓝") is True
        assert emoji_likes(bot) == [(9, "424")]
        assert sent_messages(bot) == []

    async def test_two_arguments_fill_the_template(self, bot, make_group_event):
        assert (
            await run_handler(joy.ultra, bot, make_group_event(), arg="蔚蓝 加拿大人")
            is True
        )
        text = sent_texts(bot)[0]
        assert text.startswith("不要再蔚蓝了！蔚蓝是加拿大人研发的新型压片！")
        assert text.endswith("抵制蔚蓝！！！")
        assert emoji_likes(bot) == [(4242, "128560")]

    async def test_arguments_are_lowercased(self, bot, make_group_event):
        """源码里 `str(arg).lower().split()`，大写会被抹平"""
        await run_handler(joy.ultra, bot, make_group_event(), arg="ULTRA Canada")
        assert sent_texts(bot)[0].startswith("不要再ultra了！ultra是canada研发的")

    async def test_extra_arguments_are_ignored(self, bot, make_group_event):
        """第三个词及以后不进模板"""
        await run_handler(joy.ultra, bot, make_group_event(), arg="a b c d")
        text = sent_texts(bot)[0]
        assert text.startswith("不要再a了！a是b研发的")
        assert " c " not in text


class TestJoyNsdd:
    """*nsdd：三选一的“你说的对，但是……”"""

    @pytest.mark.parametrize(
        ("roll", "prefix"),
        [
            (1, "你说的对，但是《几何冲刺》是由RobTop Games自主研发的"),
            (2, "你说的对，但《game2》是一款由Desom-fu不太自主研发的"),
            (3, "你说的对，但《蔚蓝》是一款由EXOK Games Inc.制作并发行的"),
        ],
    )
    async def test_three_variants(
        self, roll, prefix, fake_bot, make_group_event, monkeypatch
    ):
        fake_bot.api_results["send_msg"] = {"message_id": 11}
        monkeypatch.setattr(random, "randint", lambda a, b: roll)

        assert await run_handler(joy.nsdd, fake_bot, make_group_event()) is True
        assert sent_texts(fake_bot)[0].startswith(prefix)
        assert emoji_likes(fake_bot) == [(11, "424")]

    async def test_roll_range_is_1_to_3(self, fake_bot, make_group_event, monkeypatch):
        seen: list[tuple[int, int]] = []
        fake_bot.api_results["send_msg"] = {"message_id": 11}
        monkeypatch.setattr(random, "randint", lambda a, b: (seen.append((a, b)), 1)[1])
        await run_handler(joy.nsdd, fake_bot, make_group_event())
        assert seen == [(1, 3)]


class TestJoyGame:
    """*game N：给几个小游戏瞎出主意"""

    async def test_missing_argument_explains_usage(self, fake_bot, make_group_event):
        assert await run_handler(joy.game, fake_bot, make_group_event()) is True
        assert sent_texts(fake_bot) == [
            "请在使用小小卒的时候带上想要让小小卒帮忙的游戏编号(1~4)哦~免责声明：仅供参考"
        ]

    async def test_unknown_number(self, fake_bot, make_group_event):
        assert (
            await run_handler(joy.game, fake_bot, make_group_event(), arg="5") is True
        )
        assert sent_texts(fake_bot) == ["小小卒不知道你想让我建议什么哦~"]

    async def test_game1_branch_high_low(self, fake_bot, make_group_event, monkeypatch):
        monkeypatch.setattr(random, "randint", lambda a, b: 1)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        await run_handler(joy.game, fake_bot, make_group_event(), arg="1")
        assert sent_texts(fake_bot) == ["小小卒的猜测建议是：大于7"]

    async def test_game1_branch_suit(self, fake_bot, make_group_event, monkeypatch):
        monkeypatch.setattr(random, "randint", lambda a, b: 2)
        monkeypatch.setattr(random, "choice", lambda seq: seq[-1])
        await run_handler(joy.game, fake_bot, make_group_event(), arg="1")
        assert sent_texts(fake_bot) == ["小小卒的猜测建议是：红桃"]

    async def test_game1_branch_two_distinct_cards(
        self, fake_bot, make_group_event, monkeypatch
    ):
        """第三个分支要保证两张牌不一样，重复了就重抽"""
        monkeypatch.setattr(random, "randint", lambda a, b: 3)
        monkeypatch.setattr(random, "choice", scripted(["A", "A", "A", "K"]))
        await run_handler(joy.game, fake_bot, make_group_event(), arg="1")
        assert sent_texts(fake_bot) == ["小小卒的猜测建议是：A/K"]

    async def test_game2_branch(self, fake_bot, make_group_event, monkeypatch):
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        await run_handler(joy.game, fake_bot, make_group_event(), arg="2")
        assert sent_texts(fake_bot) == ["小小卒觉得下一发是：实弹"]

    async def test_game3_branch(self, fake_bot, make_group_event, monkeypatch):
        monkeypatch.setattr(random, "randint", lambda a, b: 6)
        await run_handler(joy.game, fake_bot, make_group_event(), arg="3")
        assert sent_texts(fake_bot) == ["小小卒觉得最有潜力的擂台是：6"]

    async def test_game4_branch(self, fake_bot, make_group_event, monkeypatch):
        monkeypatch.setattr(random, "randint", scripted([1, 2, 3]))
        await run_handler(joy.game, fake_bot, make_group_event(), arg="4")
        assert sent_texts(fake_bot) == ["小小卒觉得今天的宝藏埋藏在：1/2/3"]

    async def test_argument_is_lowercased_and_split(
        self, fake_bot, make_group_event, monkeypatch
    ):
        """只看第一个词，后面的忽略"""
        monkeypatch.setattr(random, "choice", lambda seq: seq[1])
        await run_handler(joy.game, fake_bot, make_group_event(), arg="2 blah blah")
        assert sent_texts(fake_bot) == ["小小卒觉得下一发是：虚弹"]


class TestJoyJwz:
    """*jwz：健忘症挑战模板"""

    @pytest.fixture
    def bot(self, fake_bot):
        fake_bot.api_results["send_msg"] = {"message_id": 55}
        return fake_bot

    async def test_overlong_argument(self, bot, make_group_event):
        event = make_group_event(message_id=3)
        assert await run_handler(joy.jwz, bot, event, arg="y" * 101) is True
        assert emoji_likes(bot) == [(3, "424")]
        assert sent_messages(bot) == []

    async def test_no_argument(self, bot, make_group_event):
        assert await run_handler(joy.jwz, bot, make_group_event()) is True
        assert sent_texts(bot) == ["请输入一个参数！"]

    async def test_single_argument_uses_random_duration(
        self, bot, make_group_event, monkeypatch
    ):
        monkeypatch.setattr(random, "randint", lambda a, b: 3)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        await run_handler(joy.jwz, bot, make_group_event(), arg="蔚蓝")
        text = sent_texts(bot)[0]
        assert text.startswith("我能在患有健忘症的情况下通关蔚蓝吗？")
        assert "蔚蓝推出已经有3年了" in text
        assert emoji_likes(bot) == [(55, "10068")]

    async def test_second_argument_overrides_the_duration(
        self, bot, make_group_event, monkeypatch
    ):
        """给了第二个参数就不用随机时长了"""
        monkeypatch.setattr(random, "randint", lambda a, b: 3)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        await run_handler(joy.jwz, bot, make_group_event(), arg="蔚蓝 十万年")
        text = sent_texts(bot)[0]
        assert "蔚蓝推出已经有十万年了" in text
        assert "3年" not in text


class TestJoyToday:
    """*today：三个参数拼一段祝福"""

    @pytest.mark.parametrize("arg", ["", "a", "a b", "a b c d"])
    async def test_wrong_argument_count(self, arg, fake_bot, make_group_event):
        """必须正好 3 个词"""
        assert await run_handler(joy.today, fake_bot, make_group_event(), arg=arg) is True
        assert sent_texts(fake_bot) == ["请输入3个参数！"]

    async def test_three_arguments(self, fake_bot, make_group_event):
        fake_bot.api_results["send_msg"] = {"message_id": 88}
        # 这个 handler 末尾没有 finish()，是自然 return 的
        assert (
            await run_handler(joy.today, fake_bot, make_group_event(), arg="蔚蓝 小卒 生日")
            is False
        )
        assert sent_texts(fake_bot)[0].startswith("今天是著名蔚蓝大神小卒生日的日子，")
        assert emoji_likes(fake_bot) == [(88, "144")]

    async def test_overlong_argument(self, fake_bot, make_group_event):
        event = make_group_event(message_id=5)
        assert await run_handler(joy.today, fake_bot, event, arg="z" * 101) is True
        assert emoji_likes(fake_bot) == [(5, "424")]


class TestJoyMisc:
    async def test_news_replies_with_the_changelog(self, fake_bot, make_group_event):
        """*news 用的是 @news.handle()（带括号），handler 确实注册上了"""
        assert joy.news.handlers, "*news 没注册 handler"
        assert await run_handler(joy.news, fake_bot, make_group_event()) is True
        text = sent_texts(fake_bot)[0]
        assert text.startswith("更新公告：移除了蓝莓系统因为我不想转移数据了；")
        assert "*gdlevelsearch" in text

    async def test_test_command_echoes_the_api_return(self, fake_bot, make_group_event):
        """*1145141919810 先发一条，再把 send 的返回值原样发第二条"""
        fake_bot.api_results["send_msg"] = {"message_id": 12345}
        assert await run_handler(joy.test, fake_bot, make_group_event()) is True
        assert sent_texts(fake_bot) == ["这是一个测试消息！", "{'message_id': 12345}"]

    def _poke(self, user_id: int, self_id: int = int(BOT_SELF_ID)) -> PokeNotifyEvent:
        return PokeNotifyEvent(
            time=1700000000,
            self_id=self_id,
            post_type="notice",
            notice_type="notify",
            sub_type="poke",
            user_id=user_id,
            target_id=self_id,
            group_id=DEFAULT_GROUP_ID,
        )

    async def test_poke_replies_with_a_poke(self, fake_bot):
        assert await run_handler(joy.group_poke, fake_bot, self._poke(777)) is False
        assert fake_bot.calls == [
            ("group_poke", {"group_id": DEFAULT_GROUP_ID, "user_id": 777})
        ]

    async def test_poke_self_guard_never_fires(self, fake_bot):
        """`event.user_id is not bot.self_id` 拿 int 和 str 比身份，永远为真

        也就是说这个“别戳自己”的判断从来没生效过，戳自己也会回戳。
        """
        self_id = int(BOT_SELF_ID)
        assert await run_handler(joy.group_poke, fake_bot, self._poke(self_id)) is False
        assert fake_bot.called_apis == ["group_poke"]


# ==========================================================================
# roulette（轮盘本体已下线，只剩墓碑 + *map + *random）
# ==========================================================================
class TestRouletteConst:
    """const.sjmap：草莓酱地图名表"""

    def test_table_shape(self):
        assert len(roulette.const.sjmap) == 116
        assert all(isinstance(name, str) and name for name in roulette.const.sjmap)

    def test_no_duplicates(self):
        assert len(set(roulette.const.sjmap)) == len(roulette.const.sjmap)

    def test_star_import_exposes_the_submodule(self):
        """`from .const import *` 只带进来 sjmap，但 `const` 这个名字是子模块
        import 时挂到包上的 —— __init__.py 里 `const.sjmap` 能用就是靠这个。
        """
        assert roulette.const.sjmap is roulette.sjmap


class TestRouletteCommands:
    async def test_roulette_is_a_tombstone(self, fake_bot, make_group_event):
        assert await run_handler(roulette.roulette, fake_bot, make_group_event()) is True
        text = sent_texts(fake_bot)[0]
        assert text.startswith("你来到了那个轮盘原本所在的位置")
        assert "*random" in text
        assert "*map" in text
        assert text == roulette.RETIRED_MSG

    async def test_map_picks_from_sjmap(self, fake_bot, make_group_event, monkeypatch):
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        assert await run_handler(roulette.get_map, fake_bot, make_group_event()) is True
        assert sent_texts(fake_bot) == ["Your map is: " + roulette.const.sjmap[0]]

    async def test_map_with_seeded_random_stays_in_the_table(
        self, fake_bot, make_group_event, seeded_random
    ):
        seeded_random(0)
        await run_handler(roulette.get_map, fake_bot, make_group_event())
        text = sent_texts(fake_bot)[0]
        assert text.startswith("Your map is: ")
        assert text.removeprefix("Your map is: ") in roulette.const.sjmap

    async def test_random_without_arguments(self, fake_bot, make_group_event):
        assert (
            await run_handler(roulette.rand_one, fake_bot, make_group_event()) is True
        )
        assert sent_texts(fake_bot) == ["请输入至少一个参数！"]

    async def test_random_picks_one_of_the_arguments(
        self, fake_bot, make_group_event, monkeypatch
    ):
        monkeypatch.setattr(random, "choice", lambda seq: seq[1])
        await run_handler(roulette.rand_one, fake_bot, make_group_event(), arg="a b c")
        assert sent_texts(fake_bot) == ["Your result is: b."]

    async def test_random_keeps_original_case(
        self, fake_bot, make_group_event, monkeypatch
    ):
        """和 joy 那几条不一样，*random 没有 .lower()"""
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        await run_handler(roulette.rand_one, fake_bot, make_group_event(), arg="ABC def")
        assert sent_texts(fake_bot) == ["Your result is: ABC."]

    async def test_random_with_one_argument(
        self, fake_bot, make_group_event, seeded_random
    ):
        seeded_random(0)
        await run_handler(roulette.rand_one, fake_bot, make_group_event(), arg="只有一个")
        assert sent_texts(fake_bot) == ["Your result is: 只有一个."]


# ==========================================================================
# zhua
# ==========================================================================
class TestZhuaDescriptions:
    """图库描述表本身"""

    def test_every_key_looks_like_a_file_name(self):
        assert zhua.descriptions
        for file_name, description in zhua.descriptions.items():
            assert "." in file_name, file_name
            assert isinstance(description, str)

    def test_data_dir_is_relative_to_the_module(self):
        """图库路径必须相对本文件，不能相对 cwd"""
        assert zhua.DATA_DIR == Path(zhua.__file__).resolve().parent / "data"

    def test_duplicate_stem_is_resolved_by_insertion_order(self):
        """"按钮卒" 有 .gif 和 .png 两份，*show 只会命中先插入的那个"""
        stems = [name.rsplit(".", 1)[0] for name in zhua.descriptions]
        assert stems.count("按钮卒") == 2
        first = next(n for n in zhua.descriptions if n.rsplit(".", 1)[0] == "按钮卒")
        assert first == "按钮卒.gif"


class TestZhuaCommand:
    """*zhua：随机抽一张小卒图，10 分钟冷却"""

    @pytest.fixture
    def store(self, patch_storage):
        return patch_storage(zhua)

    async def test_picks_an_image_and_sets_cooldown(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        assert await run_handler(zhua.zhua, fake_bot, make_group_event()) is True

        message = sent_messages(fake_bot)[0]
        first_key = next(iter(zhua.descriptions))
        assert "恭喜你抓到一个小卒！" in message.extract_plain_text()
        assert first_key.rsplit(".", 1)[0] in message.extract_plain_text()
        assert zhua.descriptions[first_key] in message.extract_plain_text()
        assert image_files(message)[0].endswith(first_key)
        assert store.get(f"zhua_cd_{DEFAULT_USER_ID}") == "waiting"

    async def test_cooldown_ttl_is_600(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        await run_handler(zhua.zhua, fake_bot, make_group_event())
        assert 0 < store.ttl(f"zhua_cd_{DEFAULT_USER_ID}") <= 600

    async def test_cooldown_message(self, store, fake_bot, make_group_event):
        """冷却里再抓就报还剩几秒；ttl 返回 -1 表示这个键没设过期时间"""
        store.set(f"zhua_cd_{DEFAULT_USER_ID}", "waiting")  # 没有 ex → ttl == -1
        assert await run_handler(zhua.zhua, fake_bot, make_group_event()) is True
        assert sent_texts(fake_bot) == ["别抓啦，-1秒后再来吧"]

    async def test_cooldown_message_reports_remaining_seconds(
        self, store, fake_bot, make_group_event
    ):
        store.set(f"zhua_cd_{DEFAULT_USER_ID}", "waiting", ex=600)
        await run_handler(zhua.zhua, fake_bot, make_group_event())
        match = re.fullmatch(r"别抓啦，(\d+)秒后再来吧", sent_texts(fake_bot)[0])
        assert match is not None
        assert 0 < int(match.group(1)) <= 600

    async def test_cooldown_is_per_user(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        store.set(f"zhua_cd_{DEFAULT_USER_ID}", "waiting", ex=600)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        await run_handler(zhua.zhua, fake_bot, make_group_event(user_id=999))
        assert "恭喜你抓到一个小卒！" in sent_texts(fake_bot)[0]

    async def test_missing_image_library_degrades_silently(
        self, store, fake_bot, make_group_event, monkeypatch, tmp_path
    ):
        """图库目录整个不存在时，*zhua 不会报错，照样发一条指向空路径的图片消息

        MessageSegment.image 只是把 Path 拼成 file:// URI，不检查文件在不在，
        所以缺图的表现是“发出去一张裂图”，而不是异常。
        """
        monkeypatch.setattr(zhua, "DATA_DIR", tmp_path / "not-there")
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])

        assert await run_handler(zhua.zhua, fake_bot, make_group_event()) is True
        file_uri = image_files(sent_messages(fake_bot)[0])[0]
        assert "not-there" in file_uri
        assert not (tmp_path / "not-there").exists()


class TestZhuaShow:
    """*show <名字>：按名字点播一张图"""

    async def test_no_argument(self, fake_bot, make_group_event):
        assert await run_handler(zhua.show, fake_bot, make_group_event()) is True
        assert sent_texts(fake_bot) == ["请输入一个名称！"]

    async def test_exact_name(self, fake_bot, make_group_event):
        assert (
            await run_handler(zhua.show, fake_bot, make_group_event(), arg="草地卒")
            is True
        )
        message = sent_messages(fake_bot)[0]
        assert image_files(message)[0].endswith("草地卒.png")
        assert "最普通的小卒，没什么好说的。" in message.extract_plain_text()

    async def test_name_match_is_case_insensitive(self, fake_bot, make_group_event):
        """输入会被 lower()，表里的名字也 lower() 之后再比，UwU卒 才匹配得上"""
        await run_handler(zhua.show, fake_bot, make_group_event(), arg="UWU卒")
        assert image_files(sent_messages(fake_bot)[0])[0].endswith("UwU卒.png")

    async def test_name_with_uppercase_extension(self, fake_bot, make_group_event):
        """"捏捏卒.PNG" 的扩展名是大写的，去扩展名用的是 rsplit(".", 1)"""
        await run_handler(zhua.show, fake_bot, make_group_event(), arg="捏捏卒")
        assert image_files(sent_messages(fake_bot)[0])[0].endswith("捏捏卒.PNG")

    async def test_unknown_name(self, fake_bot, make_group_event):
        assert (
            await run_handler(zhua.show, fake_bot, make_group_event(), arg="根本没有这个")
            is True
        )
        assert sent_texts(fake_bot) == ["请输入一个正确的名称！"]

    async def test_full_file_name_is_not_accepted(self, fake_bot, make_group_event):
        """比的是去扩展名的 stem，所以带扩展名反而查不到"""
        await run_handler(zhua.show, fake_bot, make_group_event(), arg="草地卒.png")
        assert sent_texts(fake_bot) == ["请输入一个正确的名称！"]

    async def test_only_the_first_word_is_used(self, fake_bot, make_group_event):
        await run_handler(zhua.show, fake_bot, make_group_event(), arg="草地卒 多余的词")
        assert image_files(sent_messages(fake_bot)[0])[0].endswith("草地卒.png")


class TestZhuaTest:
    """*zhua_test：超管用的图库文件名 dump"""

    async def test_lists_files_in_the_data_dir(
        self, fake_bot, make_group_event, monkeypatch, tmp_path
    ):
        folder = tmp_path / "lib"
        folder.mkdir()
        (folder / "a.png").write_bytes(b"x")
        (folder / "sub").mkdir()  # 子目录不该被列进去
        monkeypatch.setattr(zhua, "DATA_DIR", folder)

        assert await run_handler(zhua.zhua_test, fake_bot, make_group_event()) is True
        assert sent_texts(fake_bot) == ['[\'["a.png"] = ""\']']

    async def test_missing_folder_raises(
        self, fake_bot, make_group_event, monkeypatch, tmp_path
    ):
        """图库目录不存在时 iterdir() 直接抛 FileNotFoundError（没有兜底）"""
        monkeypatch.setattr(zhua, "DATA_DIR", tmp_path / "nope")
        with pytest.raises(FileNotFoundError):
            await run_handler(zhua.zhua_test, fake_bot, make_group_event())


# ==========================================================================
# say
# ==========================================================================
class TestSayHelpers:
    """say 里两个纯函数：拼 OneBot 语音消息的 payload"""

    def test_group_payload(self):
        assert say.json_group_audio(123, "/tmp/a.wav") == {
            "group_id": 123,
            "message": [{"type": "record", "data": {"file": "/tmp/a.wav"}}],
        }

    def test_private_payload(self):
        assert say.json_private_audio(456, "/tmp/b.wav") == {
            "user_id": 456,
            "message": [{"type": "record", "data": {"file": "/tmp/b.wav"}}],
        }


@pytest.fixture
def fake_mlx_audio(monkeypatch, tmp_path):
    """往 sys.modules 里塞一套假的 mlx_audio（真货只在 Apple Silicon 上装得了）。

    返回一个 dict，跑完可以看 load_model / generate_audio 分别被怎么调的。
    """
    log: dict[str, list] = {"load_model": [], "generate_audio": []}

    def load_model(name):
        log["load_model"].append(name)
        return f"<model {name}>"

    def generate_audio(*, model, text, instruct, file_prefix, path, join_audio):
        log["generate_audio"].append(
            {
                "model": model,
                "text": text,
                "instruct": instruct,
                "file_prefix": file_prefix,
                "path": path,
                "join_audio": join_audio,
            }
        )
        # 真库会把音频写到 path/file_prefix.wav，这里照做，好让 os.remove 有东西删
        Path(path, file_prefix + ".wav").write_bytes(b"RIFFfake")

    root = types.ModuleType("mlx_audio")
    tts = types.ModuleType("mlx_audio.tts")
    utils = types.ModuleType("mlx_audio.tts.utils")
    generate = types.ModuleType("mlx_audio.tts.generate")
    utils.load_model = load_model
    generate.generate_audio = generate_audio
    tts.utils = utils
    tts.generate = generate
    root.tts = tts

    for name, module in [
        ("mlx_audio", root),
        ("mlx_audio.tts", tts),
        ("mlx_audio.tts.utils", utils),
        ("mlx_audio.tts.generate", generate),
    ]:
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setattr(say, "_MODEL", None)  # 别让上一个用例缓存的模型漏过来
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    return log


@pytest.fixture
def no_mlx_audio(monkeypatch):
    """把 mlx_audio 在 sys.modules 里设成 None —— import 它会直接 ImportError。

    这样不管跑测试的机器上到底装没装 mlx_audio，行为都一样。
    """
    monkeypatch.setitem(sys.modules, "mlx_audio", None)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts", None)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", None)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.generate", None)
    monkeypatch.setattr(say, "_MODEL", None)


class TestSay:
    """*say：文字转语音"""

    async def test_banned_group_gets_only_an_emoji(self, fake_bot, make_group_event):
        event = make_group_event(group_id=BANNED_GROUP, message_id=3)
        assert await run_handler(say.say, fake_bot, event, arg="喵") is True
        assert emoji_likes(fake_bot) == [(3, "424")]
        assert sent_messages(fake_bot) == []

    async def test_empty_text(self, fake_bot, make_group_event):
        assert await run_handler(say.say, fake_bot, make_group_event()) is True
        assert sent_texts(fake_bot) == ["你得在say后面加点东西……"]

    async def test_whitespace_only_text_counts_as_empty(
        self, fake_bot, make_group_event
    ):
        """`arg.extract_plain_text().strip()` 之后是空的就算空"""
        await run_handler(say.say, fake_bot, make_group_event(), arg="    ")
        assert sent_texts(fake_bot) == ["你得在say后面加点东西……"]

    async def test_text_over_500_is_rejected(self, fake_bot, make_group_event):
        event = make_group_event(message_id=6)
        assert await run_handler(say.say, fake_bot, event, arg="a" * 501) is True
        assert emoji_likes(fake_bot) == [(6, "424")]
        assert sent_texts(fake_bot) == ["请善待小小卒！"]

    async def test_text_of_exactly_500_is_allowed(
        self, fake_bot, make_group_event, monkeypatch
    ):
        """边界是 `> 500`，正好 500 要放行"""
        monkeypatch.setattr(say, "sync_generate_audio", lambda *a, **k: "/tmp/x.wav")
        monkeypatch.setattr(os, "remove", lambda path: None)
        await run_handler(say.say, fake_bot, make_group_event(), arg="a" * 500)
        assert "send_group_msg" in fake_bot.called_apis

    async def test_master_may_exceed_500(
        self, fake_bot, make_group_event, monkeypatch
    ):
        monkeypatch.setattr(say, "sync_generate_audio", lambda *a, **k: "/tmp/x.wav")
        monkeypatch.setattr(os, "remove", lambda path: None)
        event = make_group_event(user_id=MASTER_ID)
        await run_handler(say.say, fake_bot, event, arg="a" * 501)
        assert "send_group_msg" in fake_bot.called_apis

    async def test_without_mlx_audio_it_reports_a_failure(
        self, fake_bot, make_group_event, no_mlx_audio
    ):
        """非 Apple Silicon 机器上 mlx_audio import 不了，退化成一条错误提示"""
        assert await run_handler(say.say, fake_bot, make_group_event(), arg="喵") is True
        assert len(sent_texts(fake_bot)) == 1
        assert sent_texts(fake_bot)[0].startswith("生成音频失败: ")
        assert "send_group_msg" not in fake_bot.called_apis

    async def test_generation_error_is_reported(
        self, fake_bot, make_group_event, monkeypatch
    ):
        def boom(*_a, **_k):
            raise RuntimeError("模型炸了")

        monkeypatch.setattr(say, "sync_generate_audio", boom)
        await run_handler(say.say, fake_bot, make_group_event(), arg="喵")
        assert sent_texts(fake_bot) == ["生成音频失败: 模型炸了"]

    async def test_group_success_sends_a_record_and_deletes_the_file(
        self, fake_bot, make_group_event, fake_mlx_audio, tmp_path
    ):
        assert await run_handler(say.say, fake_bot, make_group_event(), arg="喵喵") is True

        api, data = next(c for c in fake_bot.calls if c[0] == "send_group_msg")
        assert api == "send_group_msg"
        assert data["group_id"] == DEFAULT_GROUP_ID
        assert data["message"][0]["type"] == "record"
        audio_path = Path(data["message"][0]["data"]["file"])
        assert audio_path.parent == tmp_path
        assert audio_path.suffix == ".wav"
        assert not audio_path.exists(), "发完应该把临时 wav 删掉"

    async def test_private_success_uses_send_private_msg(
        self, fake_bot, make_private_event, fake_mlx_audio
    ):
        await run_handler(say.say, fake_bot, make_private_event(), arg="喵喵")
        api, data = next(c for c in fake_bot.calls if c[0] == "send_private_msg")
        assert data["user_id"] == DEFAULT_USER_ID
        assert data["message"][0]["type"] == "record"

    async def test_default_instruct_is_passed_through(
        self, fake_bot, make_group_event, fake_mlx_audio
    ):
        await run_handler(say.say, fake_bot, make_group_event(), arg="喵喵")
        call = fake_mlx_audio["generate_audio"][0]
        assert call["text"] == "喵喵"
        assert call["instruct"].startswith("体现稚嫩撒娇的少女声线")
        assert call["join_audio"] is True

    async def test_model_is_loaded_once_and_cached(
        self, fake_bot, make_group_event, fake_mlx_audio
    ):
        """_MODEL 是模块级缓存，第二次调用不该再 load 一遍"""
        await run_handler(say.say, fake_bot, make_group_event(), arg="一")
        await run_handler(say.say, fake_bot, make_group_event(), arg="二")
        assert fake_mlx_audio["load_model"] == [
            "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16"
        ]
        assert len(fake_mlx_audio["generate_audio"]) == 2


class TestSayInstructed:
    """*say_i：带音色指令的 TTS，只有白名单里两个人能用"""

    async def test_unauthorized_user_gets_an_emoji_and_returns(
        self, fake_bot, make_group_event
    ):
        """注意这里是 return 而不是 finish，所以 run_handler 返回 False"""
        event = make_group_event(user_id=1, message_id=4)
        assert (
            await run_handler(say.say_instructed, fake_bot, event, arg="a b") is False
        )
        assert emoji_likes(fake_bot) == [(4, "424")]
        assert sent_messages(fake_bot) == []

    @pytest.mark.parametrize("user_id", [3251605531, 2638056139])
    async def test_whitelisted_users(
        self, user_id, fake_bot, make_group_event, fake_mlx_audio
    ):
        event = make_group_event(user_id=user_id)
        await run_handler(say.say_instructed, fake_bot, event, arg="指令 正文")
        assert fake_mlx_audio["generate_audio"][0]["instruct"] == "指令"
        assert fake_mlx_audio["generate_audio"][0]["text"] == "正文"

    async def test_no_argument_explains_usage(self, fake_bot, make_group_event):
        event = make_group_event(user_id=MASTER_ID)
        assert await run_handler(say.say_instructed, fake_bot, event) is True
        assert sent_texts(fake_bot) == [
            "两个参数，第一个参数是指令参数，第二个参数是文本内容哦！"
        ]

    async def test_single_word_is_treated_as_missing_text(
        self, fake_bot, make_group_event
    ):
        """split(maxsplit=1) 只切出一段的话正文就是空的"""
        event = make_group_event(user_id=MASTER_ID)
        await run_handler(say.say_instructed, fake_bot, event, arg="只有指令")
        assert sent_texts(fake_bot) == [
            "两个参数，第一个参数是指令参数，第二个参数是文本内容哦！"
        ]

    async def test_text_over_1000_is_rejected(self, fake_bot, make_group_event):
        event = make_group_event(user_id=MASTER_ID, message_id=8)
        await run_handler(say.say_instructed, fake_bot, event, arg="指令 " + "b" * 1001)
        assert emoji_likes(fake_bot) == [(8, "424")]
        assert sent_texts(fake_bot) == ["请善待小小卒！"]

    async def test_text_of_exactly_1000_is_allowed(
        self, fake_bot, make_group_event, fake_mlx_audio
    ):
        event = make_group_event(user_id=MASTER_ID)
        await run_handler(say.say_instructed, fake_bot, event, arg="指令 " + "b" * 1000)
        assert len(fake_mlx_audio["generate_audio"][0]["text"]) == 1000

    async def test_rest_of_the_line_is_the_text(
        self, fake_bot, make_group_event, fake_mlx_audio
    ):
        """maxsplit=1，所以正文里的空格会被完整保留"""
        event = make_group_event(user_id=MASTER_ID)
        await run_handler(say.say_instructed, fake_bot, event, arg="温柔 你 好 呀")
        assert fake_mlx_audio["generate_audio"][0]["text"] == "你 好 呀"


# ==========================================================================
# ai
# ==========================================================================
API_ENDPOINT = "/v1/chat/completions"


def chat_response(content: str) -> dict[str, Any]:
    """LM Studio / OpenAI 风格的 chat completion 返回体"""
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def request_payload(router) -> dict[str, Any]:
    """最后一次 httpx 请求的 json body"""
    import json as _json

    return _json.loads(router.requests[-1].content)


class TestAiPureHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("hello", "hello"),
            ("  hello  ", "hello"),
            ("a\x00b", "a b"),  # C0 控制字符换成空格
            ("a\x1fb", "a b"),
            ("a\x7fb", "a b"),
            ("a\tb\nc", "a b c"),
            ("a    b", "a b"),  # 连续空白压成一个
            ("", ""),
        ],
    )
    def test_sanitize_text(self, raw, expected):
        assert ai.sanitize_text(raw) == expected

    @pytest.mark.parametrize("value", [None, 123, [], {}])
    def test_sanitize_text_rejects_non_strings(self, value):
        assert ai.sanitize_text(value) == ""

    def test_remove_emoji(self):
        assert ai.remove_emoji("你好😀世界") == "你好世界"
        assert ai.remove_emoji("😀😀") == ""
        assert ai.remove_emoji("纯文本") == "纯文本"

    def test_constants(self):
        assert ai.MAX_TURNS == 5
        assert ai.PROHIBITED_GROUP == [BANNED_GROUP]
        assert ai.MASTER_ID == MASTER_ID
        assert ai.API_URL == "http://127.0.0.1:1234/v1/chat/completions"


@pytest.fixture
def ai_context():
    """ai 的上下文是模块级全局 dict，用例之间必须互相隔离"""
    ai.context_map.clear()
    try:
        yield ai.context_map
    finally:
        ai.context_map.clear()


class TestAiGuards:
    """还没轮到发请求就被挡下来的几条路径"""

    async def test_prohibited_group(self, ai_context, fake_bot, make_group_event):
        event = make_group_event(group_id=BANNED_GROUP, message_id=2)
        assert await run_handler(ai.ai_cmd, fake_bot, event, arg="你好") is True
        assert emoji_likes(fake_bot) == [(2, "424")]
        assert sent_messages(fake_bot) == []
        assert ai_context == {}

    async def test_empty_input(self, ai_context, fake_bot, make_group_event):
        assert await run_handler(ai.ai_cmd, fake_bot, make_group_event()) is True
        assert sent_texts(fake_bot) == ["请输入内容，例如：.ai 你好"]

    async def test_emoji_only_input_is_empty_after_cleaning(
        self, ai_context, fake_bot, make_group_event
    ):
        """先去 emoji 再 sanitize，纯 emoji 就变成空串了"""
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="😀😀")
        assert sent_texts(fake_bot) == ["请输入内容，例如：.ai 你好"]

    async def test_master_can_clear_context(
        self, ai_context, fake_bot, make_group_event
    ):
        ai_context["g" + str(DEFAULT_GROUP_ID)] = [{"role": "user", "content": "旧的"}]
        event = make_group_event(user_id=MASTER_ID)
        assert await run_handler(ai.ai_cmd, fake_bot, event, arg="clear") is True
        assert sent_texts(fake_bot) == ["上下文已清空"]
        assert ai_context == {}

    async def test_chinese_clear_keyword(self, ai_context, fake_bot, make_group_event):
        ai_context["g" + str(DEFAULT_GROUP_ID)] = [{"role": "user", "content": "旧的"}]
        event = make_group_event(user_id=MASTER_ID)
        await run_handler(ai.ai_cmd, fake_bot, event, arg="清空")
        assert ai_context == {}

    async def test_non_master_clear_falls_through_to_the_model(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        """不是主人的话 "clear" 只是一句普通的话，会真的发给模型"""
        stub_httpx.post(
            API_ENDPOINT, make_httpx_response(200, json=chat_response("好的"))
        )
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="clear")
        assert request_payload(stub_httpx)["messages"][-1] == {
            "role": "user",
            "content": "clear",
        }
        assert sent_texts(fake_bot) == ["好的"]


class TestAiRequest:
    """构造请求体的逻辑"""

    @pytest.fixture
    def ok(self, stub_httpx, make_httpx_response):
        stub_httpx.post(
            API_ENDPOINT, make_httpx_response(200, json=chat_response("回答"))
        )
        return stub_httpx

    async def test_first_turn_payload(self, ai_context, fake_bot, make_group_event, ok):
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        payload = request_payload(ok)
        assert payload["model"] == "qwen3.5-2b"
        assert payload["temperature"] == 0.7
        assert payload["messages"] == [
            {"role": "system", "content": ai.SYSTEM_PROMPT},
            {"role": "user", "content": "你好"},
        ]

    async def test_authorization_header(
        self, ai_context, fake_bot, make_group_event, ok
    ):
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert ok.requests[-1].headers["authorization"] == "Bearer lm-studio"

    @pytest.mark.parametrize("keyword", ["复杂", "困难", "推理", "难题"])
    async def test_difficulty_keywords_switch_model(
        self, keyword, ai_context, fake_bot, make_group_event, ok
    ):
        await run_handler(
            ai.ai_cmd, fake_bot, make_group_event(), arg=f"这题很{keyword}"
        )
        assert request_payload(ok)["model"] == ai.THINKING_MODEL

    async def test_ordinary_question_uses_the_small_model(
        self, ai_context, fake_bot, make_group_event, ok
    ):
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="今天天气如何")
        assert request_payload(ok)["model"] in ai.MODEL_NAMES
        assert request_payload(ok)["model"] != ai.THINKING_MODEL

    async def test_only_one_request_is_made(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        """委托逻辑被 `if False and ...` 关掉了，回复以 [委托 开头也不会二次请求"""
        stub_httpx.post(
            API_ENDPOINT, make_httpx_response(200, json=chat_response("[委托: 帮我算]"))
        )
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert len(stub_httpx.requests) == 1
        assert sent_texts(fake_bot) == ["[委托: 帮我算]"]


class TestAiContext:
    """上下文的存、取、裁剪"""

    @pytest.fixture
    def ok(self, stub_httpx, make_httpx_response):
        stub_httpx.post(
            API_ENDPOINT, make_httpx_response(200, json=chat_response("回答"))
        )
        return stub_httpx

    async def test_group_session_id(self, ai_context, fake_bot, make_group_event, ok):
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert list(ai_context) == ["g" + str(DEFAULT_GROUP_ID)]

    async def test_private_session_id(
        self, ai_context, fake_bot, make_private_event, ok
    ):
        await run_handler(ai.ai_cmd, fake_bot, make_private_event(), arg="你好")
        assert list(ai_context) == ["p" + str(DEFAULT_USER_ID)]

    async def test_group_context_is_shared_by_everyone_in_the_group(
        self, ai_context, fake_bot, make_group_event, ok
    ):
        """群会话按群号分，不按人分 —— 同群不同人共用一段上下文"""
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(user_id=1), arg="你好")
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(user_id=2), arg="再问")
        assert len(ai_context["g" + str(DEFAULT_GROUP_ID)]) == 4

    async def test_turn_is_appended_after_a_successful_reply(
        self, ai_context, fake_bot, make_group_event, ok
    ):
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert ai_context["g" + str(DEFAULT_GROUP_ID)] == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "回答"},
        ]

    async def test_history_is_replayed_on_the_next_call(
        self, ai_context, fake_bot, make_group_event, ok
    ):
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="再问一句")
        assert request_payload(ok)["messages"] == [
            {"role": "system", "content": ai.SYSTEM_PROMPT},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "回答"},
            {"role": "user", "content": "再问一句"},
        ]

    async def test_history_is_trimmed_to_max_turns(
        self, ai_context, fake_bot, make_group_event, ok
    ):
        """MAX_TURNS=5，一轮两条，所以最多留 10 条"""
        session = "g" + str(DEFAULT_GROUP_ID)
        ai_context[session] = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"旧{i}"}
            for i in range(12)
        ]
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="新问题")

        # 请求里只带最近 10 条历史
        sent = request_payload(ok)["messages"]
        assert len(sent) == 12  # system + 10 条历史 + 当前提问
        assert sent[1]["content"] == "旧2"
        # 存下来的也裁到 10 条，最后两条是这一轮
        assert len(ai_context[session]) == ai.MAX_TURNS * 2
        assert ai_context[session][-2:] == [
            {"role": "user", "content": "新问题"},
            {"role": "assistant", "content": "回答"},
        ]

    async def test_history_below_the_limit_is_not_trimmed(
        self, ai_context, fake_bot, make_group_event, ok
    ):
        session = "g" + str(DEFAULT_GROUP_ID)
        ai_context[session] = [
            {"role": "user", "content": f"旧{i}"} for i in range(4)
        ]
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="新问题")
        assert len(ai_context[session]) == 6

    async def test_dirty_history_entries_are_filtered_out(
        self, ai_context, fake_bot, make_group_event, ok
    ):
        """content 不是字符串、或者只有空白的历史条目不发给模型"""
        session = "g" + str(DEFAULT_GROUP_ID)
        ai_context[session] = [
            {"role": "user", "content": None},
            {"role": "assistant", "content": "   "},
            {"role": "user", "content": 123},
            {"role": "assistant", "content": "留下我"},
        ]
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="新问题")
        assert request_payload(ok)["messages"] == [
            {"role": "system", "content": ai.SYSTEM_PROMPT},
            {"role": "assistant", "content": "留下我"},
            {"role": "user", "content": "新问题"},
        ]
        # 脏数据只是不发出去，并没有从 context_map 里清掉
        assert len(ai_context[session]) == 6


class TestAiResponseParsing:
    async def test_message_content_format(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        stub_httpx.post(
            API_ENDPOINT, make_httpx_response(200, json=chat_response("  带空格  "))
        )
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert sent_texts(fake_bot) == ["带空格"]

    async def test_choice_text_fallback(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        stub_httpx.post(
            API_ENDPOINT,
            make_httpx_response(200, json={"choices": [{"text": "老格式"}]}),
        )
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert sent_texts(fake_bot) == ["老格式"]

    async def test_top_level_response_fallback(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        stub_httpx.post(
            API_ENDPOINT, make_httpx_response(200, json={"response": "顶层字段"})
        )
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert sent_texts(fake_bot) == ["顶层字段"]

    async def test_emoji_are_stripped_from_the_reply(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        stub_httpx.post(
            API_ENDPOINT, make_httpx_response(200, json=chat_response("好的😀"))
        )
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert sent_texts(fake_bot) == ["好的"]

    async def test_all_emoji_reply_becomes_empty(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        """全是 emoji 的回复被清空之后回一句“回复为空”，但上下文已经写进去了"""
        stub_httpx.post(
            API_ENDPOINT, make_httpx_response(200, json=chat_response("😀😀"))
        )
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert sent_texts(fake_bot) == ["回复为空"]
        assert ai_context["g" + str(DEFAULT_GROUP_ID)][-1] == {
            "role": "assistant",
            "content": "😀😀",
        }


class TestAiErrors:
    async def test_http_error_status_is_reported(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        stub_httpx.post(API_ENDPOINT, make_httpx_response(500, text="boom"))
        assert (
            await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好") is True
        )
        assert sent_texts(fake_bot) == ["API请求失败: 500"]
        assert ai_context == {"g" + str(DEFAULT_GROUP_ID): []}

    async def test_connection_error_is_reported(
        self, ai_context, fake_bot, make_group_event, stub_httpx
    ):
        stub_httpx.post(API_ENDPOINT, httpx.ConnectError("连不上"))
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert sent_texts(fake_bot) == ["请求失败: 连不上"]

    async def test_non_json_body_reports_the_wrong_error(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        """BUG：非 JSON 响应会让用户连收两条消息，第二条是没意义的异常名

        `Matcher.finish()` 是「先 send 再抛 FinishedException」，而
        FinishedException 是 Exception 的子类；这句 finish 又正好写在 try 块里，
        于是被同一个 try 的 `except Exception as e` 兜住，日志里打一条假的
        "Request to AI API failed" 栈，再 finish 一次，把异常名当错误信息发出去。
        （xiaozu_bot/plugins/ai/__init__.py:176 与 :218）
        """
        stub_httpx.post(API_ENDPOINT, make_httpx_response(200, text="<html>not json"))
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert sent_texts(fake_bot) == [
            "API返回非JSON响应: <html>not json",
            "请求失败: FinishedException()",
        ]

    async def test_unparseable_payload_reports_the_wrong_error(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        """同上：`无法解析 API 返回` 那条也被自己的 except Exception 追发了一条
        （xiaozu_bot/plugins/ai/__init__.py:208 与 :218）
        """
        stub_httpx.post(API_ENDPOINT, make_httpx_response(200, json={"choices": []}))
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert sent_texts(fake_bot) == [
            '无法解析 API 返回: {"choices": []}',
            "请求失败: FinishedException()",
        ]

    async def test_failed_turn_is_not_written_to_the_context(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        """请求失败时上下文只多了个空 list，不会记下这一轮"""
        stub_httpx.post(API_ENDPOINT, make_httpx_response(500))
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert ai_context["g" + str(DEFAULT_GROUP_ID)] == []


# ==========================================================================
# guess —— 纯逻辑部分
# ==========================================================================
class TestGuessFormalize:
    """formalize：把用户输入和答案都归一化到能比较的形式"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Loopy Lagoon", "loopylagoon"),
            ("SEEING IS BELIEVING", "seeingisbelieving"),
            ("Java's Crypt", "javascrypt"),
            ("Rightside-Down Cavern", "rightsidedowncavern"),
            ("a.b,c-d'e!f", "abcdef"),
            ("你好，世界！", "你好世界"),
            ("等一下……", "等一下"),
            ("句号。", "句号"),
            ("a:b：c", "abc"),
            ("a+b_c", "abc"),
            ("多\n行", "多行"),
            ("", ""),
        ],
    )
    def test_strips_and_lowercases(self, raw, expected):
        assert guess.formalize(raw) == expected

    @pytest.mark.parametrize("char", ["/", "?", "（", "）", "、", "*"])
    def test_characters_outside_the_table_survive(self, char):
        """删除表是写死的一串，表外的符号原样保留"""
        assert guess.formalize(f"a{char}b") == f"a{char}b"

    def test_is_idempotent(self):
        for raw in ["Loopy Lagoon", "Java's Crypt", "你好，世界！"]:
            once = guess.formalize(raw)
            assert guess.formalize(once) == once


class TestGuessTables:
    """import 期从 data.maps 生成的 aliases / accepted 两张表"""

    def test_map_bank_shape(self):
        from xiaozu_bot.plugins.guess.data import maps

        assert len(maps) == 122
        for entry in maps:
            assert set(entry) == {"file_path", "answer", "alias"}
            assert entry["answer"] and entry["alias"]

    def test_answers_are_unique(self):
        from xiaozu_bot.plugins.guess.data import maps

        answers = [entry["answer"] for entry in maps]
        assert len(set(answers)) == len(answers)

    def test_aliases_table_mirrors_the_bank(self):
        from xiaozu_bot.plugins.guess.data import maps

        assert len(guess.aliases) == len(maps)
        for entry in maps:
            assert guess.aliases[entry["answer"]] == entry["alias"]

    def test_accepted_contains_the_normalized_full_answer(self):
        """写全名（含中英文和空格）也要算对"""
        for answer, tokens in guess.accepted.items():
            assert guess.formalize(answer) in tokens

    def test_accepted_normalizes_aliases(self):
        """别名表里 VVVVVV 是大写的，必须归一化后才匹配得上"""
        tokens = guess.accepted["潜在的一切 Potential for Anything"]
        assert "vvvvvv" in tokens
        assert "VVVVVV" not in tokens

    def test_one_alias_may_serve_two_answers(self):
        """"崩坏" 同时是崩坏天际线和崩碎之歌的别名 —— accepted 按答案分桶，互不干扰"""
        assert "崩坏" in guess.accepted["崩坏天际线 Collapsing Skyline"]
        assert "崩坏" in guess.accepted["崩碎之歌 Shattersong"]


class TestGuessGetId:
    def test_group_session_id_is_prefixed(self, make_group_event):
        assert guess.getid(make_group_event()) == "g" + str(DEFAULT_GROUP_ID)

    def test_private_session_id_is_the_bare_user_id(self, make_private_event):
        assert guess.getid(make_private_event()) == str(DEFAULT_USER_ID)


def make_rgb(pixels: list[tuple[int, int, int]], size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGB", size)
    image.putdata(pixels)
    return image


class TestGuessVariance:
    """get_variance / isnonsense：判断裁出来的一块是不是纯色废图"""

    def test_solid_colour_has_zero_variance(self):
        image = Image.new("RGB", (8, 8), (17, 42, 200))
        assert guess.get_variance(image) == (0.0, 0.0, 0.0)

    def test_two_pixel_variance(self):
        """两个像素时方差是 (Δ/2)²"""
        image = make_rgb([(0, 0, 0), (20, 40, 60)], (2, 1))
        assert guess.get_variance(image) == (100.0, 400.0, 900.0)

    def test_channels_are_independent(self):
        image = make_rgb([(0, 5, 5), (20, 5, 5)], (2, 1))
        assert guess.get_variance(image) == (100.0, 0.0, 0.0)

    def test_threshold_is_strictly_less_than_300(self):
        """三个通道各 100，加起来正好 300，`< 300` 不成立 → 不算废图"""
        image = make_rgb([(0, 0, 0), (20, 20, 20)], (2, 1))
        assert sum(guess.get_variance(image)) == 300.0
        assert guess.isnonsense(image) is False

    def test_just_below_the_threshold_is_nonsense(self):
        image = make_rgb([(0, 0, 0), (20, 20, 18)], (2, 1))
        assert sum(guess.get_variance(image)) == 281.0
        assert guess.isnonsense(image) is True

    def test_solid_colour_is_nonsense(self):
        assert guess.isnonsense(Image.new("RGB", (4, 4), (200, 200, 200))) is True

    def test_grayscale_image_crashes(self):
        """L 模式的像素是 int 不是三元组，p[0] 直接 TypeError

        题库里只要混进一张灰度图，*guess_start 就会炸在这里。
        """
        with pytest.raises(TypeError):
            guess.get_variance(Image.new("L", (4, 4), 128))


class TestGuessListFiles:
    async def test_missing_folder_returns_empty(self, tmp_path):
        assert await guess._list_files(tmp_path / "nope") == []

    async def test_lists_only_files(self, tmp_path):
        (tmp_path / "a.png").write_bytes(b"x")
        (tmp_path / "b.png").write_bytes(b"x")
        (tmp_path / "sub").mkdir()
        assert sorted(await guess._list_files(tmp_path)) == ["a.png", "b.png"]

    async def test_empty_folder_returns_empty(self, tmp_path):
        return_value = await guess._list_files(tmp_path)
        assert return_value == []


def half_and_half(path: Path, size: int = 200) -> Path:
    """造一张左半黑右半白的图，任何跨越中线的裁剪都不会被判成废图"""
    image = Image.new("RGB", (size, size), (0, 0, 0))
    image.paste(Image.new("RGB", (size // 2, size), (255, 255, 255)), (size // 2, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


class TestGuessCropAndSave:
    """_crop_and_save：随机裁一块，躲开纯色区域"""

    def test_returns_the_box_and_writes_the_file(self, tmp_path, monkeypatch):
        source = half_and_half(tmp_path / "src.png")
        out = tmp_path / "out" / "crop.png"
        monkeypatch.setattr(random, "randint", lambda a, b: 68)

        box = guess._crop_and_save(source, 64, 64, out)
        assert box == (68, 68, 132, 132)
        assert out.exists()
        assert image_size(out) == (64, 64)

    def test_informative_crop_stops_after_one_try(self, tmp_path, monkeypatch):
        source = half_and_half(tmp_path / "src.png")
        calls: list[tuple[int, int]] = []
        monkeypatch.setattr(
            random, "randint", lambda a, b: (calls.append((a, b)), 68)[1]
        )

        guess._crop_and_save(source, 64, 64, tmp_path / "out.png")
        assert calls == [(0, 136), (0, 136)]  # 一轮两次（left / top）就够了

    def test_solid_image_exhausts_the_retries(self, tmp_path, monkeypatch):
        """整张纯色时每次裁出来都是废图，重试满 MAX_CROP_RETRIES 次后用最后一次的结果"""
        source = tmp_path / "solid.png"
        Image.new("RGB", (200, 200), (7, 7, 7)).save(source)
        calls: list[int] = []
        monkeypatch.setattr(random, "randint", lambda a, b: (calls.append(1), 0)[1])

        box = guess._crop_and_save(source, 64, 64, tmp_path / "out.png")
        assert len(calls) == guess.MAX_CROP_RETRIES * 2 == 40
        assert box == (0, 0, 64, 64)

    def test_crop_larger_than_the_image(self, tmp_path, monkeypatch):
        """裁剪框比原图还大时 `max(0, w - cw)` 兜住了 randint 的空区间"""
        source = tmp_path / "small.png"
        Image.new("RGB", (32, 32), (200, 0, 0)).save(source)
        ranges: list[tuple[int, int]] = []
        monkeypatch.setattr(
            random, "randint", lambda a, b: (ranges.append((a, b)), 0)[1]
        )

        out = tmp_path / "out.png"
        box = guess._crop_and_save(source, 64, 64, out)
        assert ranges == [(0, 0), (0, 0)]
        assert box == (0, 0, 64, 64)
        assert image_size(out) == (64, 64)  # 不足的部分 PIL 补黑边

    def test_creates_missing_output_directory(self, tmp_path, monkeypatch):
        source = half_and_half(tmp_path / "src.png")
        monkeypatch.setattr(random, "randint", lambda a, b: 68)
        out = tmp_path / "deep" / "nested" / "crop.png"
        guess._crop_and_save(source, 64, 64, out)
        assert out.exists()


# ==========================================================================
# guess —— 指令
# ==========================================================================
@pytest.fixture
def guess_env(patch_storage, monkeypatch, tmp_path):
    """给 guess 装一个完全跑在 tmp_path 上的题库 + 输出目录。

    返回 (存储, 题库根目录, 图片输出目录)。题库里放一张能过去噪判定的图，
    maps 只留一条，随机选到哪张都一样。
    """
    store = patch_storage(guess)
    data_dir = tmp_path / "bank"
    pictures_dir = tmp_path / "pictures"
    monkeypatch.setattr(guess, "DATA_DIR", data_dir)
    monkeypatch.setattr(guess, "PICTURES_DIR", pictures_dir)
    monkeypatch.setattr(
        guess,
        "maps",
        [{"file_path": "Fake/Level", "answer": "假图 Fake Level", "alias": ["假图"]}],
    )
    half_and_half(data_dir / "Fake" / "Level" / "shot.png")
    return store, data_dir, pictures_dir


class TestGuessCanStart:
    async def test_cooldown_blocks_and_marks_the_message(
        self, guess_env, fake_bot, make_group_event
    ):
        store, _, _ = guess_env
        session = "g" + str(DEFAULT_GROUP_ID)
        store.set(f"{guess.COOLDOWN_PREFIX}{session}", "x", ex=45)
        event = make_group_event(message_id=13)

        finished = await run_coro(
            fake_bot,
            event,
            lambda: guess.can_start(fake_bot, guess.guess_start, event),
        )
        assert finished is True
        assert emoji_likes(fake_bot) == [(13, "424")]

    async def test_open_question_in_a_group_blocks_a_new_one(
        self, guess_env, fake_bot, make_group_event
    ):
        store, _, _ = guess_env
        store.hset(guess.ANSWER_KEY, "g" + str(DEFAULT_GROUP_ID), "假图 Fake Level")
        event = make_group_event()

        finished = await run_coro(
            fake_bot, event, lambda: guess.can_start(fake_bot, guess.guess_start, event)
        )
        assert finished is True
        assert sent_texts(fake_bot) == ["请先输入*guess_giveup结束目前的题目！"]

    async def test_nothing_answer_does_not_block(
        self, guess_env, fake_bot, make_group_event
    ):
        store, _, _ = guess_env
        store.hset(guess.ANSWER_KEY, "g" + str(DEFAULT_GROUP_ID), guess.NOTHING_ANSWER)
        event = make_group_event()

        finished = await run_coro(
            fake_bot, event, lambda: guess.can_start(fake_bot, guess.guess_start, event)
        )
        assert finished is False
        assert fake_bot.calls == []

    async def test_private_chat_is_never_blocked_by_an_open_question(
        self, guess_env, fake_bot, make_private_event
    ):
        """判断里带了 isinstance(event, GroupMessageEvent)，私聊可以套娃开新题

        （旧题的答案会被新题直接覆盖掉，看着不像有意为之。）
        """
        store, _, _ = guess_env
        store.hset(guess.ANSWER_KEY, str(DEFAULT_USER_ID), "假图 Fake Level")
        event = make_private_event()

        finished = await run_coro(
            fake_bot, event, lambda: guess.can_start(fake_bot, guess.guess_start, event)
        )
        assert finished is False


class TestGuessStart:
    @pytest.mark.parametrize(
        ("matcher_name", "crop"),
        [("guess_start", 256), ("guess_start_hard", 128), ("guess_start_ultra", 64)],
    )
    async def test_three_difficulties_use_different_crop_sizes(
        self, matcher_name, crop, guess_env, fake_bot, make_group_event, monkeypatch
    ):
        _, _, pictures_dir = guess_env
        monkeypatch.setattr(random, "randint", lambda a, b: 0)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])

        matcher = getattr(guess, matcher_name)
        assert await run_handler(matcher, fake_bot, make_group_event()) is True

        out = pictures_dir / f"g{DEFAULT_GROUP_ID}.png"
        assert image_size(out) == (crop, crop)

    async def test_stores_answer_position_and_original(
        self, guess_env, fake_bot, make_group_event, monkeypatch
    ):
        store, data_dir, _ = guess_env
        monkeypatch.setattr(random, "randint", lambda a, b: 10)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        session = "g" + str(DEFAULT_GROUP_ID)

        await run_handler(guess.guess_start_ultra, fake_bot, make_group_event())

        assert store.hget(guess.ANSWER_KEY, session) == "假图 Fake Level"
        assert store.hget(guess.ANSWER_POSITION_KEY, session) == "10 10 74 74"
        assert store.hget(guess.ANSWER_ORI_KEY, session) == str(
            data_dir / "Fake" / "Level" / "shot.png"
        )

    async def test_sets_a_45_second_cooldown(
        self, guess_env, fake_bot, make_group_event, monkeypatch
    ):
        store, _, _ = guess_env
        monkeypatch.setattr(random, "randint", lambda a, b: 0)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        session = "g" + str(DEFAULT_GROUP_ID)

        await run_handler(guess.guess_start_ultra, fake_bot, make_group_event())
        assert 0 < store.ttl(f"{guess.COOLDOWN_PREFIX}{session}") <= 45

    async def test_sends_the_crop_with_a_prompt(
        self, guess_env, fake_bot, make_group_event, monkeypatch
    ):
        _, _, pictures_dir = guess_env
        monkeypatch.setattr(random, "randint", lambda a, b: 0)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])

        await run_handler(guess.guess_start_ultra, fake_bot, make_group_event())
        message = sent_messages(fake_bot)[0]
        assert "这个截图是出自哪张图呢？" in message.extract_plain_text()
        assert image_files(message)[0].endswith(f"g{DEFAULT_GROUP_ID}.png")
        assert MessageSegment.at(DEFAULT_USER_ID) in message

    async def test_private_session_uses_the_bare_user_id(
        self, guess_env, fake_bot, make_private_event, monkeypatch
    ):
        store, _, pictures_dir = guess_env
        monkeypatch.setattr(random, "randint", lambda a, b: 0)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])

        await run_handler(guess.guess_start_ultra, fake_bot, make_private_event())
        assert (pictures_dir / f"{DEFAULT_USER_ID}.png").exists()
        assert store.hget(guess.ANSWER_KEY, str(DEFAULT_USER_ID)) == "假图 Fake Level"

    async def test_missing_question_bank_spins_forever(
        self, guess_env, fake_bot, make_group_event, monkeypatch
    ):
        """BUG：题库目录空的时候 `while not file_names` 是个死循环，永远出不来

        仓库里题库是打包在 dist.zip 里的，没解压的话 DATA_DIR 下一个文件都没有，
        *guess_start 会把事件循环卡死（循环体里 await 的是 to_thread，不会抛异常）。
        这里用一个计数器在转够 30 圈之后强行掀桌，证明它确实不会自己停。
        """
        _, data_dir, _ = guess_env
        # 把题库掏空，模拟 dist.zip 没解压的情况
        for path in (data_dir / "Fake" / "Level").iterdir():
            path.unlink()

        class LoopGuard(Exception):
            pass

        spins = {"n": 0}

        def counting_choice(seq):
            spins["n"] += 1
            if spins["n"] > 30:
                raise LoopGuard
            return seq[0]

        monkeypatch.setattr(random, "choice", counting_choice)

        with pytest.raises(LoopGuard):
            await run_handler(guess.guess_start, fake_bot, make_group_event())
        assert spins["n"] == 31


class TestGuessAnswer:
    """*guess <答案>"""

    @pytest.fixture
    def started(self, guess_env, tmp_path, monkeypatch):
        """伪造一道已经出好的题：答案、原图路径、裁剪坐标都写进存储。

        accepted 是 import 期从真题库生成的，假答案得手动加一条进去，
        加法和 __init__.py 里那段推导式保持一致（答案全名也算对）。
        """
        store, _, pictures_dir = guess_env
        session = "g" + str(DEFAULT_GROUP_ID)
        answer = "假图 Fake Level"
        original = half_and_half(tmp_path / "original.png")
        store.hset(guess.ANSWER_KEY, session, answer)
        store.hset(guess.ANSWER_ORI_KEY, session, str(original))
        store.hset(guess.ANSWER_POSITION_KEY, session, "10 10 74 74")
        monkeypatch.setitem(
            guess.accepted, answer, {guess.formalize(answer), guess.formalize("假图")}
        )
        return store, session, pictures_dir

    async def test_no_open_question(self, guess_env, fake_bot, make_group_event):
        store, _, _ = guess_env
        event = make_group_event(message_id=21)
        assert await run_handler(guess.guess, fake_bot, event, arg="假图") is True
        assert emoji_likes(fake_bot) == [(21, "10068")]
        assert store.get(guess.TOTAL_TRIES_KEY) is None

    async def test_finished_question_is_not_guessable(
        self, guess_env, fake_bot, make_group_event
    ):
        store, _, _ = guess_env
        store.hset(guess.ANSWER_KEY, "g" + str(DEFAULT_GROUP_ID), guess.NOTHING_ANSWER)
        event = make_group_event(message_id=22)
        await run_handler(guess.guess, fake_bot, event, arg="假图")
        assert emoji_likes(fake_bot) == [(22, "10068")]

    async def test_wrong_guess_counts_a_try_and_marks_the_message(
        self, started, fake_bot, make_group_event, monkeypatch
    ):
        store, session, _ = started
        monkeypatch.setattr(random, "randint", lambda a, b: 5)  # 不触发“给你看题”
        event = make_group_event(message_id=23)

        assert await run_handler(guess.guess, fake_bot, event, arg="别的图") is True
        assert emoji_likes(fake_bot) == [(23, "424")]
        assert store.get(guess.TOTAL_TRIES_KEY) == 1
        assert store.get(guess.TOTAL_RIGHT_KEY) is None
        assert store.hget(guess.ANSWER_KEY, session) == "假图 Fake Level"

    async def test_wrong_guess_sometimes_resends_the_crop(
        self, started, fake_bot, make_group_event, monkeypatch
    ):
        """十分之一的概率把题目图再贴一遍"""
        _, _, pictures_dir = started
        monkeypatch.setattr(random, "randint", lambda a, b: 1)
        await run_handler(
            guess.guess, fake_bot, make_group_event(), arg="别的图"
        )
        message = sent_messages(fake_bot)[0]
        assert "你的猜测是错误的！你的题目是" in message.extract_plain_text()
        assert image_files(message)[0].endswith(f"g{DEFAULT_GROUP_ID}.png")

    async def test_correct_full_answer(self, started, fake_bot, make_group_event):
        store, session, pictures_dir = started
        assert (
            await run_handler(
                guess.guess, fake_bot, make_group_event(), arg="假图 Fake Level"
            )
            is True
        )
        assert "你猜对了！答案是：假图 Fake Level。" in sent_texts(fake_bot)[0]
        assert store.hget(guess.ANSWER_KEY, session) == guess.NOTHING_ANSWER
        assert store.get(guess.TOTAL_TRIES_KEY) == 1
        assert store.get(guess.TOTAL_RIGHT_KEY) == 1
        assert (pictures_dir / f"{session}.png").exists()

    async def test_alias_is_accepted(self, started, fake_bot, make_group_event):
        await run_handler(guess.guess, fake_bot, make_group_event(), arg="假图")
        assert "你猜对了！" in sent_texts(fake_bot)[0]

    async def test_answer_is_normalized_before_comparing(
        self, started, fake_bot, make_group_event
    ):
        """大小写、空格、标点都会被 formalize 抹掉"""
        await run_handler(
            guess.guess, fake_bot, make_group_event(), arg="  假图 FAKE-LEVEL!  "
        )
        assert "你猜对了！" in sent_texts(fake_bot)[0]

    async def test_uppercase_alias_from_the_real_bank_is_accepted(
        self, guess_env, fake_bot, make_group_event, tmp_path
    ):
        """真题库里 VVVVVV 这个别名是大写的，归一化之后才认得出来"""
        store, _, _ = guess_env
        answer = "潜在的一切 Potential for Anything"
        session = "g" + str(DEFAULT_GROUP_ID)
        original = half_and_half(tmp_path / "pfa.png")
        store.hset(guess.ANSWER_KEY, session, answer)
        store.hset(guess.ANSWER_ORI_KEY, session, str(original))
        store.hset(guess.ANSWER_POSITION_KEY, session, "0 0 10 10")

        await run_handler(guess.guess, fake_bot, make_group_event(), arg="VVVVVV")
        assert f"你猜对了！答案是：{answer}。" in sent_texts(fake_bot)[0]

    async def test_guessing_is_allowed_during_the_cooldown(
        self, started, fake_bot, make_group_event
    ):
        """45 秒 cd 只挡出新题，不挡回答"""
        store, session, _ = started
        store.set(f"{guess.COOLDOWN_PREFIX}{session}", "x", ex=45)
        await run_handler(guess.guess, fake_bot, make_group_event(), arg="假图")
        assert "你猜对了！" in sent_texts(fake_bot)[0]


class TestGuessGiveUp:
    @pytest.fixture
    def started(self, guess_env, tmp_path):
        store, _, pictures_dir = guess_env
        session = "g" + str(DEFAULT_GROUP_ID)
        original = half_and_half(tmp_path / "original.png")
        store.hset(guess.ANSWER_KEY, session, "假图 Fake Level")
        store.hset(guess.ANSWER_ORI_KEY, session, str(original))
        store.hset(guess.ANSWER_POSITION_KEY, session, "10 10 74 74")
        return store, session, pictures_dir

    async def test_reveals_the_answer(self, started, fake_bot, make_group_event):
        store, session, pictures_dir = started
        assert await run_handler(guess.guess_giveup, fake_bot, make_group_event()) is True
        message = sent_messages(fake_bot)[0]
        assert "你放弃了！答案是：假图 Fake Level。" in message.extract_plain_text()
        assert image_files(message)[0].endswith(f"{session}.png")
        assert (pictures_dir / f"{session}.png").exists()
        assert store.hget(guess.ANSWER_KEY, session) == guess.NOTHING_ANSWER

    async def test_giveup_does_not_count_as_a_try(
        self, started, fake_bot, make_group_event
    ):
        store, _, _ = started
        await run_handler(guess.guess_giveup, fake_bot, make_group_event())
        assert store.get(guess.TOTAL_TRIES_KEY) is None

    async def test_no_open_question(self, guess_env, fake_bot, make_group_event):
        event = make_group_event(message_id=31)
        assert await run_handler(guess.guess_giveup, fake_bot, event) is True
        assert emoji_likes(fake_bot) == [(31, "10068")]

    async def test_cooldown_blocks_giving_up(
        self, started, fake_bot, make_group_event
    ):
        """出题后 45 秒内连认输都不让 —— cd 是共用同一个键判的"""
        store, session, _ = started
        store.set(f"{guess.COOLDOWN_PREFIX}{session}", "x", ex=45)
        event = make_group_event(message_id=32)

        assert await run_handler(guess.guess_giveup, fake_bot, event) is True
        assert emoji_likes(fake_bot) == [(32, "424")]
        assert store.hget(guess.ANSWER_KEY, session) == "假图 Fake Level"


class TestGuessMisc:
    async def test_count_with_no_data_prints_none(
        self, guess_env, fake_bot, make_group_event
    ):
        """从来没人猜过的时候，两个计数器都是 None，直接被拼进了句子里"""
        assert await run_handler(guess.guess_count, fake_bot, make_group_event()) is True
        assert sent_texts(fake_bot) == ["全服总共进行了None次猜测，猜对了None道题。"]

    async def test_count_reports_the_stored_numbers(
        self, guess_env, fake_bot, make_group_event
    ):
        store, _, _ = guess_env
        store.set(guess.TOTAL_TRIES_KEY, 17)
        store.set(guess.TOTAL_RIGHT_KEY, 5)
        await run_handler(guess.guess_count, fake_bot, make_group_event())
        assert sent_texts(fake_bot) == ["全服总共进行了17次猜测，猜对了5道题。"]

    async def test_remove_cooldown(self, guess_env, fake_bot, make_group_event):
        store, _, _ = guess_env
        session = "g" + str(DEFAULT_GROUP_ID)
        store.set(f"{guess.COOLDOWN_PREFIX}{session}", "x", ex=45)

        assert (
            await run_handler(guess.guess_removecooldown, fake_bot, make_group_event())
            is True
        )
        assert sent_texts(fake_bot) == ["已经移除你（或你所在群）的生成题目cd！"]
        # 实现是把 cd 改成 1 秒而不是删掉，ttl 会立刻落到 0（判定用的是 > 0）
        assert store.ttl(f"{guess.COOLDOWN_PREFIX}{session}") <= 0

    async def test_cheat_sends_a_private_message(
        self, guess_env, fake_bot, make_group_event
    ):
        store, _, _ = guess_env
        session = "g" + str(DEFAULT_GROUP_ID)
        store.hset(guess.ANSWER_KEY, session, "假图 Fake Level")

        assert await run_handler(guess.guess_cheat, fake_bot, make_group_event()) is True
        api, data = fake_bot.calls[0]
        assert api == "send_private_msg"
        assert data["user_id"] == DEFAULT_USER_ID
        assert data["message"][0]["data"]["text"] == f"{session}假图 Fake Level"

    # 生产代码里 Image.open() 从来不 close，gc 的时候会冒 ResourceWarning，
    # 这里只是把噪音压住，别的用例照常暴露
    @pytest.mark.filterwarnings("ignore::ResourceWarning")
    async def test_guess_test_crashes_on_images_smaller_than_the_crop(
        self, guess_env, fake_bot, make_group_event, monkeypatch
    ):
        """*guess_test 里是 `randint(0, width - 256)`，没有 max(0, ...) 兜底

        图比裁剪框小就直接 ValueError；正经出题走的 _crop_and_save 是有兜底的。
        """
        _, data_dir, _ = guess_env
        small = data_dir / "Fake" / "Level" / "shot.png"
        Image.new("RGB", (100, 100), (10, 20, 30)).save(small)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])

        with pytest.raises(ValueError, match="empty range|empty|randrange"):
            await run_handler(guess.guess_test, fake_bot, make_group_event())
