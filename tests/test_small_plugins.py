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

import os
import random
import re
import sys
import types
from collections.abc import Callable
from datetime import datetime as real_datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import unquote

import httpx
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11.event import PokeNotifyEvent
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


# 驱动 handler 的那几个工具（run_handler / sent_texts / only_text ...）现在住在
# tests/conftest.py 里。挪过去的原因：别的测试文件也要用，原来是
# `from tests.test_small_plugins import ...` 拿的，那样这个文件就删不掉了。
from tests.conftest import (
    only_text,
    run_coro,
    run_handler,
    sent_messages,
    sent_texts,
)


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


def picking_spy(pools: list[list[Any]], index: int = 0) -> Callable[[Any], Any]:
    """替掉 `random.choice`：把每次的候选表记进 pools，并固定挑第 index 个。

    有了它就能断言「回复里带上了被挑中的那一项」，而不用把候选表里的词抄进测试。
    候选表里的词属于文案，改词不该让用例红；「从几个里挑一个、挑中的报出来」才是行为。
    """

    def _choice(seq: Any) -> Any:
        pool = list(seq)
        pools.append(pool)
        return pool[index]

    return _choice


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

    # ---- 摇出来的数要报回去 --------------------------------------------
    #: 七档评语的上下边界（源码是一串 `rp <= N` 的 elif），每个边界都摇一次，
    #: 好让整条 elif 链真跑到；配的是哪句评语不管。
    BUCKET_BOUNDS: ClassVar[list[int]] = [1, 2, 20, 21, 40, 41, 60, 61, 80, 81, 99, 100]

    @pytest.mark.parametrize("rp", BUCKET_BOUNDS)
    async def test_any_roll_answers_with_the_number(
        self, rp, store, fake_bot, make_group_event, monkeypatch
    ):
        """不管摇到几，都要正常收场并把摇到的数报出来。

        「摇到几就报几」是行为，「哪一档配哪句评语、评语怎么写」是措辞 ——
        评语一个字都不钉，改文案不该弄红任何一条用例。
        """
        monkeypatch.setattr(random, "randint", lambda a, b: rp)
        assert await run_handler(jrrp.jrrp, fake_bot, make_group_event("*jrrp")) is True
        assert str(rp) in only_text(fake_bot)

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
        assert "66" in only_text(fake_bot)

    async def test_cached_reply_ats_the_sender(self, store, fake_bot, make_group_event):
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
        assert "7" in only_text(fake_bot)
        assert store.get("jrrp_999") == "7"

    async def test_true_sentinel_is_treated_as_no_cache(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        """历史遗留：存成字符串 "True" 的键会被当成没缓存，重新摇一次

        源码里那句 `!= "True"` 是给早年存布尔值的旧数据兜底的。
        """
        store.set(f"jrrp_{DEFAULT_USER_ID}", "True")
        monkeypatch.setattr(random, "randint", lambda a, b: 55)

        await run_handler(jrrp.jrrp, fake_bot, make_group_event("*jrrp"))
        assert "55" in only_text(fake_bot)
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

        recorded: list[tuple[str, Any, int | None]] = []
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
        """不带参数走的是硬编码那份，发完给自己那条消息贴 128560

        那段小作文的内容不钉（改文案是常事），钉的是「回了一条」以及
        「贴表情贴的是自己刚发那条的 message_id，不是用户那条」。
        """
        assert await run_handler(joy.ultra, bot, make_group_event("*ultra")) is True
        assert only_text(bot)
        assert emoji_likes(bot) == [(4242, "128560")]

    async def test_single_argument_is_rejected(self, bot, make_group_event):
        """只给一个词凑不成模板，贴 424 走人"""
        event = make_group_event("*ultra 蔚蓝", message_id=9)
        assert await run_handler(joy.ultra, bot, event, arg="蔚蓝") is True
        assert emoji_likes(bot) == [(9, "424")]
        assert sent_messages(bot) == []

    async def test_two_arguments_fill_the_template(self, bot, make_group_event):
        """两个词都被套进模板（钉的是用户给的词进去了，模板本身怎么写不管）"""
        assert (
            await run_handler(joy.ultra, bot, make_group_event(), arg="甲游 乙国人")
            is True
        )
        text = only_text(bot)
        assert "甲游" in text
        assert "乙国人" in text
        assert emoji_likes(bot) == [(4242, "128560")]

    async def test_arguments_are_lowercased(self, bot, make_group_event):
        """源码里 `str(arg).lower().split()`，大写会被抹平"""
        await run_handler(joy.ultra, bot, make_group_event(), arg="ULTRA Canada")
        text = only_text(bot)
        assert "ultra" in text
        assert "canada" in text
        assert "ULTRA" not in text
        assert "Canada" not in text

    async def test_extra_arguments_are_ignored(self, bot, make_group_event):
        """第三个词及以后不进模板"""
        await run_handler(joy.ultra, bot, make_group_event(), arg="甲 乙 丙 丁")
        text = only_text(bot)
        assert "甲" in text
        assert "乙" in text
        assert "丙" not in text
        assert "丁" not in text


class TestJoyNsdd:
    """*nsdd：三选一的“你说的对，但是……”"""

    async def test_three_variants(self, fake_bot, make_group_event, monkeypatch):
        """1/2/3 三个分支各回各的一段，每段都给自己贴 424。

        原来是把三段长文的开头抄进 parametrize 逐字比 —— 改一个标点就红。
        这里钉的是「三个分支都能走到、而且回的不是同一段」，措辞随便改。
        """
        texts = []
        for roll in (1, 2, 3):
            fake_bot.calls.clear()
            fake_bot.api_results["send_msg"] = {"message_id": 11}
            monkeypatch.setattr(random, "randint", lambda a, b, roll=roll: roll)

            assert await run_handler(joy.nsdd, fake_bot, make_group_event()) is True
            texts.append(only_text(fake_bot))
            assert emoji_likes(fake_bot) == [(11, "424")]

        assert all(texts), "三个分支都得真回点东西"
        assert len(set(texts)) == 3, "三个分支的文案不该重样"

    async def test_roll_range_is_1_to_3(self, fake_bot, make_group_event, monkeypatch):
        seen: list[tuple[int, int]] = []
        fake_bot.api_results["send_msg"] = {"message_id": 11}
        monkeypatch.setattr(random, "randint", lambda a, b: (seen.append((a, b)), 1)[1])
        await run_handler(joy.nsdd, fake_bot, make_group_event())
        assert seen == [(1, 3)]


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
        """没参数就提前收场：回一条、而且不会走到贴 10068 的正常分支"""
        assert await run_handler(joy.jwz, bot, make_group_event()) is True
        assert only_text(bot)
        assert emoji_likes(bot) == []

    async def test_single_argument_uses_random_duration(
        self, bot, make_group_event, monkeypatch
    ):
        """只给一个词时时长是随机摇的：报出来的是「摇到的数 + 挑中的单位」"""
        units: list[list[Any]] = []
        monkeypatch.setattr(random, "randint", lambda a, b: 3)
        monkeypatch.setattr(random, "choice", picking_spy(units))
        await run_handler(joy.jwz, bot, make_group_event(), arg="蔚蓝")
        text = only_text(bot)
        assert "蔚蓝" in text
        assert f"3{units[0][0]}" in text
        assert emoji_likes(bot) == [(55, "10068")]

    async def test_second_argument_overrides_the_duration(
        self, bot, make_group_event, monkeypatch
    ):
        """给了第二个参数就用它当时长，随机摇出来的那个不该出现"""
        units: list[list[Any]] = []
        monkeypatch.setattr(random, "randint", lambda a, b: 3)
        monkeypatch.setattr(random, "choice", picking_spy(units))
        await run_handler(joy.jwz, bot, make_group_event(), arg="蔚蓝 十万年")
        text = only_text(bot)
        assert "十万年" in text
        assert f"3{units[0][0]}" not in text


class TestJoyToday:
    """*today：三个参数拼一段祝福"""

    @pytest.mark.parametrize("arg", ["", "a", "a b", "a b c d"])
    async def test_wrong_argument_count(self, arg, fake_bot, make_group_event):
        """必须正好 3 个词：不够/多了都提前收场，不会走到贴 144 的正常分支"""
        assert (
            await run_handler(joy.today, fake_bot, make_group_event(), arg=arg) is True
        )
        assert only_text(fake_bot)
        assert emoji_likes(fake_bot) == []

    async def test_three_arguments(self, fake_bot, make_group_event):
        """三个词都被套进模板"""
        fake_bot.api_results["send_msg"] = {"message_id": 88}
        # 这个 handler 末尾没有 finish()，是自然 return 的
        assert (
            await run_handler(
                joy.today, fake_bot, make_group_event(), arg="蔚蓝 小卒 生日"
            )
            is False
        )
        text = only_text(fake_bot)
        assert all(word in text for word in ("蔚蓝", "小卒", "生日"))
        assert emoji_likes(fake_bot) == [(88, "144")]

    async def test_overlong_argument(self, fake_bot, make_group_event):
        event = make_group_event(message_id=5)
        assert await run_handler(joy.today, fake_bot, event, arg="z" * 101) is True
        assert emoji_likes(fake_bot) == [(5, "424")]


class TestJoyMisc:
    async def test_news_replies_with_the_changelog(self, fake_bot, make_group_event):
        """*news 用的是 @news.handle()（带括号），handler 确实注册上了

        公告内容每次发版都要改，所以只钉「有 handler、真回了一条非空的」——
        当初的 bug 是漏了括号导致 *news 把消息 block 掉却一个字都不回。
        """
        assert joy.news.handlers, "*news 没注册 handler"
        assert await run_handler(joy.news, fake_bot, make_group_event()) is True
        assert only_text(fake_bot).strip()

    async def test_test_command_echoes_the_api_return(self, fake_bot, make_group_event):
        """*1145141919810 先发一条，再把 send 的返回值原样发第二条"""
        fake_bot.api_results["send_msg"] = {"message_id": 12345}
        assert await run_handler(joy.test, fake_bot, make_group_event()) is True
        texts = sent_texts(fake_bot)
        assert len(texts) == 2
        assert texts[1] == str({"message_id": 12345})

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

    def test_table_is_a_usable_pool(self):
        """只钉「这张表能拿来随机抽」：非空、全是非空字符串、没有重名。

        原来这里钉的是 `len(sjmap) == 116`，加一个地图名就得回来改数字，
        而条数根本不是行为 —— 真正会出问题的是空串和重名。
        """
        assert roulette.const.sjmap, (
            "地图表不能是空的，不然 random.choice 直接 IndexError"
        )
        assert all(
            isinstance(name, str) and name.strip() for name in roulette.const.sjmap
        )
        assert len(set(roulette.const.sjmap)) == len(roulette.const.sjmap)

    def test_star_import_exposes_the_submodule(self):
        """`from .const import *` 只带进来 sjmap，但 `const` 这个名字是子模块
        import 时挂到包上的 —— __init__.py 里 `const.sjmap` 能用就是靠这个。
        """
        assert roulette.const.sjmap is roulette.sjmap


class TestRouletteCommands:
    async def test_roulette_is_a_tombstone(self, fake_bot, make_group_event):
        """墓碑文案跟着模块里的常量走，改文案不用回来改测试"""
        assert (
            await run_handler(roulette.roulette, fake_bot, make_group_event()) is True
        )
        assert only_text(fake_bot) == roulette.RETIRED_MSG

    async def test_map_picks_from_sjmap(self, fake_bot, make_group_event, monkeypatch):
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        assert await run_handler(roulette.get_map, fake_bot, make_group_event()) is True
        assert only_text(fake_bot).endswith(roulette.const.sjmap[0])

    async def test_map_with_seeded_random_stays_in_the_table(
        self, fake_bot, make_group_event, seeded_random
    ):
        """随便播一个种子，报出来的名字都得是表里有的"""
        seeded_random(0)
        await run_handler(roulette.get_map, fake_bot, make_group_event())
        text = only_text(fake_bot)
        assert any(text.endswith(name) for name in roulette.const.sjmap)

    async def test_random_without_arguments(self, fake_bot, make_group_event):
        """一个词都没给时要回一条走人，而不是拿空表去 random.choice 抛 IndexError"""
        assert (
            await run_handler(roulette.rand_one, fake_bot, make_group_event()) is True
        )
        assert only_text(fake_bot)

    async def test_random_picks_one_of_the_arguments(
        self, fake_bot, make_group_event, monkeypatch
    ):
        """报出来的是被挑中的那个词，没被挑中的不许出现"""
        monkeypatch.setattr(random, "choice", lambda seq: seq[1])
        await run_handler(
            roulette.rand_one, fake_bot, make_group_event(), arg="甲 乙 丙"
        )
        text = only_text(fake_bot)
        assert "乙" in text
        assert "甲" not in text
        assert "丙" not in text

    async def test_random_keeps_original_case(
        self, fake_bot, make_group_event, monkeypatch
    ):
        """和 joy 那几条不一样，*random 没有 .lower()"""
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        await run_handler(
            roulette.rand_one, fake_bot, make_group_event(), arg="ABC def"
        )
        text = only_text(fake_bot)
        assert "ABC" in text
        assert "abc" not in text

    async def test_random_with_one_argument(
        self, fake_bot, make_group_event, seeded_random
    ):
        seeded_random(0)
        await run_handler(
            roulette.rand_one, fake_bot, make_group_event(), arg="只有一个"
        )
        assert "只有一个" in only_text(fake_bot)


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
        assert Path(zhua.__file__).resolve().parent / "data" == zhua.DATA_DIR

    async def test_duplicate_stem_is_resolved_by_insertion_order(
        self, fake_bot, make_group_event, monkeypatch
    ):
        """同名不同扩展名时，*show 命中的是 descriptions 里先插入的那个。

        原来钉的是「按钮卒 正好有 2 份、第一份是 .gif」—— 加图、删图、
        改扩展名都会把它弄红，可这些都不是行为。这里换成自己造两条重名记录，
        钉的是「顺序遍历、取第一个匹配」这条规则本身。
        """
        monkeypatch.setattr(
            zhua, "descriptions", {"重名卒.gif": "先插入的", "重名卒.png": "后插入的"}
        )
        await run_handler(zhua.show, fake_bot, make_group_event(), arg="重名卒")

        message = sent_messages(fake_bot)[0]
        assert image_files(message)[0].endswith("重名卒.gif")
        assert "先插入的" in message.extract_plain_text()


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
        message = sent_messages(fake_bot)[0]
        assert "-1" in message.extract_plain_text()
        assert image_files(message) == [], "冷却里不该发图"

    async def test_cooldown_message_reports_remaining_seconds(
        self, store, fake_bot, make_group_event
    ):
        store.set(f"zhua_cd_{DEFAULT_USER_ID}", "waiting", ex=600)
        await run_handler(zhua.zhua, fake_bot, make_group_event())
        # 报的是「还剩几秒」这个数，句子怎么写不管
        numbers = re.findall(r"\d+", only_text(fake_bot))
        assert numbers, "冷却提示里得带上剩余秒数"
        assert 0 < int(numbers[0]) <= 600

    async def test_cooldown_is_per_user(
        self, store, fake_bot, make_group_event, monkeypatch
    ):
        store.set(f"zhua_cd_{DEFAULT_USER_ID}", "waiting", ex=600)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        await run_handler(zhua.zhua, fake_bot, make_group_event(user_id=999))
        # 换个人就照常发图，而不是那条冷却提示
        assert image_files(sent_messages(fake_bot)[0])

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
    """*show <名字>：按名字点播一张图

    这一组以前直接拿真图库里的「草地卒 / UwU卒 / 捏捏卒」当样本，
    改个描述、换个扩展名、删张图都会把它们弄红 —— 可这些都不是 *show 的行为。
    现在换成一张自己造的小图库，把匹配逻辑要考虑的几种形状摆齐，
    钉的是匹配规则本身；真图库的形状由 TestZhuaDescriptions 管。
    """

    #: 普通名 / 名字里带大写 / 扩展名是大写
    FAKE_LIB: ClassVar[dict[str, str]] = {
        "草地卒.png": "最普通的小卒",
        "UwU卒.png": "名字里带大写",
        "捏捏卒.PNG": "扩展名是大写",
    }

    @pytest.fixture(autouse=True)
    def fake_library(self, monkeypatch):
        monkeypatch.setattr(zhua, "descriptions", self.FAKE_LIB)

    async def test_no_argument(self, fake_bot, make_group_event):
        """没给名字时回一条提示、不发图"""
        assert await run_handler(zhua.show, fake_bot, make_group_event()) is True
        assert only_text(fake_bot)
        assert image_files(sent_messages(fake_bot)[0]) == []

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("草地卒", "草地卒.png"),  # 原样
            ("UWU卒", "UwU卒.png"),  # 输入和表里的名字都 lower() 之后再比
            ("捏捏卒", "捏捏卒.PNG"),  # 去扩展名用的是 rsplit(".", 1)，大写扩展名也认
            ("草地卒 多余的词", "草地卒.png"),  # 只看第一个词
        ],
    )
    async def test_name_matching(self, query, expected, fake_bot, make_group_event):
        assert (
            await run_handler(zhua.show, fake_bot, make_group_event(), arg=query)
            is True
        )
        message = sent_messages(fake_bot)[0]
        assert image_files(message)[0].endswith(expected)
        assert self.FAKE_LIB[expected] in message.extract_plain_text()

    @pytest.mark.parametrize(
        "query",
        [
            "根本没有这个",
            "草地卒.png",  # 比的是去扩展名的 stem，所以带扩展名反而查不到
        ],
    )
    async def test_unmatched_name_is_rejected(self, query, fake_bot, make_group_event):
        """查不到就回一条、不发图（发出去一张裂图才是问题，回什么话不是）"""
        assert (
            await run_handler(zhua.show, fake_bot, make_group_event(), arg=query)
            is True
        )
        assert only_text(fake_bot)
        assert image_files(sent_messages(fake_bot)[0]) == []


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
        # 行为是「只列文件、不列子目录」，dump 的排版怎么写不管
        text = only_text(fake_bot)
        assert "a.png" in text
        assert "sub" not in text

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

    @pytest.mark.parametrize(
        ("builder", "target_key"),
        [(say.json_group_audio, "group_id"), (say.json_private_audio, "user_id")],
    )
    def test_audio_payload(self, builder, target_key):
        """两个函数只差收件人字段叫什么，消息体是同一套 record 段"""
        assert builder(123, "/tmp/a.wav") == {
            target_key: 123,
            "message": [{"type": "record", "data": {"file": "/tmp/a.wav"}}],
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
        """空文本回一条提示，不去跑 TTS"""
        assert await run_handler(say.say, fake_bot, make_group_event()) is True
        assert only_text(fake_bot)
        assert "send_group_msg" not in fake_bot.called_apis

    async def test_whitespace_only_text_counts_as_empty(
        self, fake_bot, make_group_event
    ):
        """`arg.extract_plain_text().strip()` 之后是空的就算空 —— 同样不跑 TTS"""
        await run_handler(say.say, fake_bot, make_group_event(), arg="    ")
        assert only_text(fake_bot)
        assert "send_group_msg" not in fake_bot.called_apis

    async def test_text_over_500_is_rejected(self, fake_bot, make_group_event):
        event = make_group_event(message_id=6)
        assert await run_handler(say.say, fake_bot, event, arg="a" * 501) is True
        assert emoji_likes(fake_bot) == [(6, "424")]
        assert only_text(fake_bot)
        assert "send_group_msg" not in fake_bot.called_apis

    async def test_text_of_exactly_500_is_allowed(
        self, fake_bot, make_group_event, monkeypatch
    ):
        """边界是 `> 500`，正好 500 要放行"""
        monkeypatch.setattr(say, "sync_generate_audio", lambda *a, **k: "/tmp/x.wav")
        monkeypatch.setattr(os, "remove", lambda path: None)
        await run_handler(say.say, fake_bot, make_group_event(), arg="a" * 500)
        assert "send_msg" in fake_bot.called_apis

    async def test_master_may_exceed_500(self, fake_bot, make_group_event, monkeypatch):
        monkeypatch.setattr(say, "sync_generate_audio", lambda *a, **k: "/tmp/x.wav")
        monkeypatch.setattr(os, "remove", lambda path: None)
        event = make_group_event(user_id=MASTER_ID)
        await run_handler(say.say, fake_bot, event, arg="a" * 501)
        assert "send_msg" in fake_bot.called_apis

    async def test_without_mlx_audio_it_reports_a_failure(
        self, fake_bot, make_group_event, no_mlx_audio
    ):
        """非 Apple Silicon 机器上 mlx_audio import 不了，退化成一条错误提示"""
        assert (
            await run_handler(say.say, fake_bot, make_group_event(), arg="喵") is True
        )
        assert only_text(fake_bot)
        assert "send_group_msg" not in fake_bot.called_apis

    async def test_generation_error_is_reported(
        self, fake_bot, make_group_event, monkeypatch
    ):
        """生成炸了的时候要把异常信息带出来，而不是闷声不响"""

        def boom(*_a, **_k):
            raise RuntimeError("模型炸了")

        monkeypatch.setattr(say, "sync_generate_audio", boom)
        await run_handler(say.say, fake_bot, make_group_event(), arg="喵")
        assert "模型炸了" in only_text(fake_bot)

    async def test_group_success_sends_a_record_and_deletes_the_file(
        self, fake_bot, make_group_event, fake_mlx_audio, tmp_path
    ):
        assert (
            await run_handler(say.say, fake_bot, make_group_event(), arg="喵喵") is True
        )

        api, data = next(c for c in fake_bot.calls if c[0] == "send_msg")
        assert api == "send_msg"
        assert data["group_id"] == DEFAULT_GROUP_ID
        assert data["message"][0].type == "record"
        audio_path = Path(data["message"][0].data["file"])
        assert audio_path.parent == tmp_path
        assert audio_path.suffix == ".wav"
        assert not audio_path.exists(), "发完应该把临时 wav 删掉"

    async def test_private_success_uses_send_msg(
        self, fake_bot, make_private_event, fake_mlx_audio
    ):
        await run_handler(say.say, fake_bot, make_private_event(), arg="喵喵")
        api, data = next(c for c in fake_bot.calls if c[0] == "send_msg")
        assert api == "send_msg"
        assert data["user_id"] == DEFAULT_USER_ID
        assert data["message"][0].type == "record"

    async def test_default_instruct_is_passed_through(
        self, fake_bot, make_group_event, fake_mlx_audio
    ):
        await run_handler(say.say, fake_bot, make_group_event(), arg="喵喵")
        call = fake_mlx_audio["generate_audio"][0]
        assert call["text"] == "喵喵"
        # 默认音色指令的具体措辞是调参用的，只钉「确实传了一条非空的」
        assert isinstance(call["instruct"], str) and call["instruct"].strip()
        assert call["join_audio"] is True

    async def test_model_is_loaded_once_and_cached(
        self, fake_bot, make_group_event, fake_mlx_audio
    ):
        """_MODEL 是模块级缓存，第二次调用不该再 load 一遍"""
        await run_handler(say.say, fake_bot, make_group_event(), arg="一")
        await run_handler(say.say, fake_bot, make_group_event(), arg="二")
        assert len(fake_mlx_audio["load_model"]) == 1  # 换模型不该让这条红
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
        """没参数时回一条走人，不去跑 TTS"""
        event = make_group_event(user_id=MASTER_ID)
        assert await run_handler(say.say_instructed, fake_bot, event) is True
        assert only_text(fake_bot)
        assert "send_group_msg" not in fake_bot.called_apis

    async def test_single_word_is_treated_as_missing_text(
        self, fake_bot, make_group_event
    ):
        """split(maxsplit=1) 只切出一段的话正文就是空的，走的是同一条收场路径"""
        event = make_group_event(user_id=MASTER_ID)
        await run_handler(say.say_instructed, fake_bot, event, arg="只有指令")
        assert only_text(fake_bot)
        assert "send_group_msg" not in fake_bot.called_apis

    async def test_text_over_1000_is_rejected(self, fake_bot, make_group_event):
        event = make_group_event(user_id=MASTER_ID, message_id=8)
        await run_handler(say.say_instructed, fake_bot, event, arg="指令 " + "b" * 1001)
        assert emoji_likes(fake_bot) == [(8, "424")]
        assert only_text(fake_bot)
        assert "send_group_msg" not in fake_bot.called_apis

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
            # 不是字符串的一律当空串处理，别让上下文里的脏数据把请求带崩
            (None, ""),
            (123, ""),
            ([], ""),
            ({}, ""),
        ],
    )
    def test_sanitize_text(self, raw, expected):
        assert ai.sanitize_text(raw) == expected

    def test_remove_emoji(self):
        assert ai.remove_emoji("你好😀世界") == "你好世界"
        assert ai.remove_emoji("😀😀") == ""
        assert ai.remove_emoji("纯文本") == "纯文本"

    def test_constants_match_what_the_tests_assume(self):
        """本文件里硬编码的群号/主人号得和插件里的一致，不然下面几十条用例全在测空气"""
        assert ai.PROHIBITED_GROUP == [BANNED_GROUP]
        assert ai.MASTER_ID == MASTER_ID
        # 改端口、改路径都行，指到外网就不行
        assert ai.API_URL.startswith("http://127.0.0.1")


@pytest.fixture
def ai_context():
    """ai 的上下文是模块级全局 dict，用例之间必须互相隔离"""
    ai.context_map.clear()
    try:
        yield ai.context_map
    finally:
        ai.context_map.clear()


AI_GROUP_SESSION = f"ob11:group:{DEFAULT_GROUP_ID}"
AI_PRIVATE_SESSION = f"ob11:private:{DEFAULT_USER_ID}"


class TestAiGuards:
    """还没轮到发请求就被挡下来的几条路径"""

    async def test_prohibited_group(self, ai_context, fake_bot, make_group_event):
        event = make_group_event(group_id=BANNED_GROUP, message_id=2)
        assert await run_handler(ai.ai_cmd, fake_bot, event, arg="你好") is True
        assert emoji_likes(fake_bot) == [(2, "424")]
        assert sent_messages(fake_bot) == []
        assert ai_context == {}

    async def test_empty_input(self, ai_context, fake_bot, make_group_event):
        """空输入回一条就走，不建会话、不发请求"""
        assert await run_handler(ai.ai_cmd, fake_bot, make_group_event()) is True
        assert only_text(fake_bot)
        assert ai_context == {}

    async def test_emoji_only_input_is_empty_after_cleaning(
        self, ai_context, fake_bot, make_group_event
    ):
        """先去 emoji 再 sanitize，纯 emoji 就变成空串了，走的是同一条路"""
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="😀😀")
        assert only_text(fake_bot)
        assert ai_context == {}

    async def test_master_can_clear_context(
        self, ai_context, fake_bot, make_group_event
    ):
        """主人说 clear 就把上下文清干净并回一条（清没清干净是行为，回什么话不是）"""
        ai_context[AI_GROUP_SESSION] = [{"role": "user", "content": "旧的"}]
        event = make_group_event(user_id=MASTER_ID)
        assert await run_handler(ai.ai_cmd, fake_bot, event, arg="clear") is True
        assert only_text(fake_bot)
        assert ai_context == {}

    async def test_chinese_clear_keyword(self, ai_context, fake_bot, make_group_event):
        ai_context[AI_GROUP_SESSION] = [{"role": "user", "content": "旧的"}]
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
        assert list(ai_context) == [AI_GROUP_SESSION]

    async def test_private_session_id(
        self, ai_context, fake_bot, make_private_event, ok
    ):
        await run_handler(ai.ai_cmd, fake_bot, make_private_event(), arg="你好")
        assert list(ai_context) == [AI_PRIVATE_SESSION]

    async def test_group_context_is_shared_by_everyone_in_the_group(
        self, ai_context, fake_bot, make_group_event, ok
    ):
        """群会话按群号分，不按人分 —— 同群不同人共用一段上下文"""
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(user_id=1), arg="你好")
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(user_id=2), arg="再问")
        assert len(ai_context[AI_GROUP_SESSION]) == 4

    async def test_turn_is_appended_after_a_successful_reply(
        self, ai_context, fake_bot, make_group_event, ok
    ):
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert ai_context[AI_GROUP_SESSION] == [
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
        session = AI_GROUP_SESSION
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
        session = AI_GROUP_SESSION
        ai_context[session] = [{"role": "user", "content": f"旧{i}"} for i in range(4)]
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="新问题")
        assert len(ai_context[session]) == 6

    async def test_dirty_history_entries_are_filtered_out(
        self, ai_context, fake_bot, make_group_event, ok
    ):
        """content 不是字符串、或者只有空白的历史条目不发给模型"""
        session = AI_GROUP_SESSION
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
        """全是 emoji 的回复被清空之后仍要回一条非空消息（空消息 OneBot 会拒），
        但上下文里存的还是模型原样的那串 emoji"""
        stub_httpx.post(
            API_ENDPOINT, make_httpx_response(200, json=chat_response("😀😀"))
        )
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert only_text(fake_bot)
        assert ai_context[AI_GROUP_SESSION][-1] == {
            "role": "assistant",
            "content": "😀😀",
        }


class TestAiErrors:
    async def test_http_error_status_is_reported(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        stub_httpx.post(API_ENDPOINT, make_httpx_response(500, text="boom"))
        assert (
            await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
            is True
        )
        assert "failed after 1 attempts" in only_text(fake_bot)
        assert ai_context == {AI_GROUP_SESSION: []}

    async def test_connection_error_is_reported(
        self, ai_context, fake_bot, make_group_event, stub_httpx
    ):
        stub_httpx.post(API_ENDPOINT, httpx.ConnectError("连不上"))
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert "failed after 1 attempts" in only_text(fake_bot)

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
        texts = sent_texts(fake_bot)
        assert len(texts) == 2, "这个 bug 的症状就是「连收两条」，修好了这条会红"
        assert "<html>not json" in texts[0]
        assert "FinishedException" in texts[1]

    async def test_unparseable_payload_reports_the_wrong_error(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        """同上：`无法解析 API 返回` 那条也被自己的 except Exception 追发了一条
        （xiaozu_bot/plugins/ai/__init__.py:208 与 :218）
        """
        stub_httpx.post(API_ENDPOINT, make_httpx_response(200, json={"choices": []}))
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        texts = sent_texts(fake_bot)
        assert len(texts) == 2, "这个 bug 的症状就是「连收两条」，修好了这条会红"
        assert '{"choices": []}' in texts[0]
        assert "FinishedException" in texts[1]

    async def test_failed_turn_is_not_written_to_the_context(
        self, ai_context, fake_bot, make_group_event, stub_httpx, make_httpx_response
    ):
        """请求失败时上下文只多了个空 list，不会记下这一轮"""
        stub_httpx.post(API_ENDPOINT, make_httpx_response(500))
        await run_handler(ai.ai_cmd, fake_bot, make_group_event(), arg="你好")
        assert ai_context[AI_GROUP_SESSION] == []


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

    def test_characters_outside_the_table_survive(self):
        """删除表是写死的一串，表外的符号原样保留（挨个试一遍，不用一符号一个用例）"""
        for char in ["/", "?", "（", "）", "、", "*"]:
            assert guess.formalize(f"a{char}b") == f"a{char}b", char

    def test_is_idempotent(self):
        for raw in ["Loopy Lagoon", "Java's Crypt", "你好，世界！"]:
            once = guess.formalize(raw)
            assert guess.formalize(once) == once


class TestGuessTables:
    """import 期从 data.maps 生成的 aliases / accepted 两张表"""

    def test_bank_shape_and_aliases_mirror_it(self):
        """题库每条都得有那三个字段、答案不重样，aliases 是它的一比一镜像。

        原来还钉了 `len(maps) == 122` —— 加一张图就得回来改数字，
        而条数不是行为，重复答案和缺字段才是。
        """
        from xiaozu_bot.plugins.guess.data import maps

        assert maps, "题库表不能是空的"
        answers = [entry["answer"] for entry in maps]
        assert len(set(answers)) == len(answers), (
            "答案不能重样，重了 accepted 会互相覆盖"
        )

        for entry in maps:
            assert set(entry) == {"file_path", "answer", "alias"}
            assert entry["answer"] and entry["alias"]
            assert guess.aliases[entry["answer"]] == entry["alias"]
        assert len(guess.aliases) == len(maps)

    def test_accepted_holds_the_normalized_answer_and_every_alias(self):
        """每条答案的「全名 + 所有别名」归一化之后都要在 accepted 里。

        这一条同时盖掉了以前三个单独的用例：写全名算对、别名里的大写
        （VVVVVV）归一化之后才认得出来、同一个别名（崩坏）挂在两个答案下
        互不干扰 —— 而且是遍历整张表，以后再加这种别名不用补用例。
        """
        from xiaozu_bot.plugins.guess.data import maps

        for entry in maps:
            tokens = guess.accepted[entry["answer"]]
            assert guess.formalize(entry["answer"]) in tokens
            for alias in entry["alias"]:
                assert guess.formalize(alias) in tokens, (entry["answer"], alias)
                # 存的一定是归一化后的形式，原样的大写别名不该混进来
                if alias != guess.formalize(alias):
                    assert alias not in tokens


class TestGuessGetId:
    def test_session_id_is_group_prefixed_or_a_bare_user_id(
        self, make_group_event, make_private_event
    ):
        """群会话带 g 前缀，私聊就是光秃秃的 QQ 号 —— 两个分支一起看，免得只对一半"""
        assert guess.getid(make_group_event()) == "g" + str(DEFAULT_GROUP_ID)
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
        """L 模式只有一个波段，解不出 RGB 三个方差，直接 ValueError

        题库里只要混进一张灰度图，*guess_start 就会炸在这里。
        （以前是逐像素取 p[0] 抛 TypeError，换成 ImageStat 之后变成解包失败。）
        """
        with pytest.raises(ValueError, match="unpack"):
            guess.get_variance(Image.new("L", (4, 4), 128))

    def test_alpha_channel_is_ignored(self):
        """RGBA 只看前三个波段，alpha 再怎么变都不影响判定"""
        opaque = Image.new("RGBA", (2, 1))
        opaque.putdata([(0, 0, 0, 255), (20, 40, 60, 255)])
        transparent = Image.new("RGBA", (2, 1))
        transparent.putdata([(0, 0, 0, 0), (20, 40, 60, 255)])

        assert guess.get_variance(opaque) == (100.0, 400.0, 900.0)
        assert guess.get_variance(transparent) == (100.0, 400.0, 900.0)


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
        # 行为是「挡住 + 回一条」，提示怎么写不管
        assert only_text(fake_bot)
        # 挡住了就不该把旧题的答案冲掉
        assert (
            store.hget(guess.ANSWER_KEY, "g" + str(DEFAULT_GROUP_ID))
            == "假图 Fake Level"
        )

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
        """出题那条消息要带上裁好的图、@ 出题人，而且不能把答案漏出去"""
        _, _, pictures_dir = guess_env
        monkeypatch.setattr(random, "randint", lambda a, b: 0)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])

        await run_handler(guess.guess_start_ultra, fake_bot, make_group_event())
        message = sent_messages(fake_bot)[0]
        assert message.extract_plain_text().strip()
        assert "假图 Fake Level" not in message.extract_plain_text()
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

    @staticmethod
    def _empty_bank(monkeypatch, entries: int = 40) -> list:
        """把 maps 换成 entries 条全都指向空目录的记录，返回被看过的目录列表。

        目录压根不存在和目录存在但空着，在 `_list_files` 里都是返回 []，
        这里直接用不存在的路径覆盖两种情况里更常见的那种。
        """
        monkeypatch.setattr(
            guess,
            "maps",
            [
                {"file_path": f"Empty/{i}", "answer": f"空 {i}", "alias": []}
                for i in range(entries)
            ],
        )
        looked_at: list = []
        real_list_files = guess._list_files

        async def counting_list_files(folder_path):
            looked_at.append(folder_path)
            return await real_list_files(folder_path)

        monkeypatch.setattr(guess, "_list_files", counting_list_files)
        return looked_at

    async def test_empty_question_bank_finishes_instead_of_spinning(
        self, guess_env, fake_bot, make_group_event, monkeypatch
    ):
        """题库一张图都没有时要立刻收场，而且只把 maps 扫一遍。

        以前这里是 `while not file_names` 的死循环 —— 循环体里 await 的是
        to_thread，不抛异常也不超时，干净 clone 上一发 *guess_start
        就把整个事件循环卡死。
        """
        looked_at = self._empty_bank(monkeypatch)

        finished = await run_handler(guess.guess_start, fake_bot, make_group_event())

        # 「收场了」才是这个回归要钉的性质，收场时说了什么无所谓
        assert finished is True
        assert len(sent_texts(fake_bot)) == 1
        # 无放回：40 条每条恰好看一次，不多不少
        assert len(looked_at) == len(guess.maps) == 40
        assert len(set(looked_at)) == 40

    async def test_empty_question_bank_directory_that_exists_but_is_empty(
        self, guess_env, fake_bot, make_group_event
    ):
        """目录在、但里面被清空了，走的也是同一条收场路径"""
        _, data_dir, _ = guess_env
        for path in (data_dir / "Fake" / "Level").iterdir():
            path.unlink()

        finished = await run_handler(guess.guess_start, fake_bot, make_group_event())

        assert finished is True
        assert len(sent_texts(fake_bot)) == 1

    async def test_guess_test_also_finishes_on_empty_bank(
        self, guess_env, fake_bot, make_group_event, monkeypatch
    ):
        """*guess_test 是第二个调用点，当初漏改的就是它。

        顺带钉住「把 Matcher 这个类本身传给 _pick_random_shot 也能 finish」
        ——handle_guess_test 传的是 guess_test 类而不是实例，靠的是
        `Matcher.finish` 本来就是 classmethod。
        """
        looked_at = self._empty_bank(monkeypatch)

        finished = await run_handler(guess.guess_test, fake_bot, make_group_event())

        assert finished is True
        assert len(sent_texts(fake_bot)) == 1
        assert len(looked_at) == 40

    async def test_sparse_bank_never_falsely_reports_empty(
        self, guess_env, monkeypatch
    ):
        """只填了一小部分的题库必须每次都能挑出题来。

        这就是无放回扫描的意义：中间那版是**有放回**地固定抽 50 次，
        40 条里只有 1 条有图的话，(39/40)**50 ≈ 28% 的概率会把「没抽中」
        误报成「题库是空的」。题库是一张一张截出来的，「只填了一部分」
        恰恰是它平时的状态，所以这个误判会真的发到群里。
        """
        _, data_dir, _ = guess_env
        monkeypatch.setattr(
            guess,
            "maps",
            [
                {"file_path": f"Empty/{i}", "answer": f"空 {i}", "alias": []}
                for i in range(39)
            ]
            + [
                {
                    "file_path": "Fake/Level",
                    "answer": "假图 Fake Level",
                    "alias": ["假图"],
                }
            ],
        )

        for _ in range(20):
            map_info, image_path = await guess._pick_random_shot(guess.guess_start)
            assert map_info["answer"] == "假图 Fake Level"
            assert image_path == data_dir / "Fake" / "Level" / "shot.png"


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
        await run_handler(guess.guess, fake_bot, make_group_event(), arg="别的图")
        message = sent_messages(fake_bot)[0]
        # 重发的是题图，答案还不能露
        assert image_files(message)[0].endswith(f"g{DEFAULT_GROUP_ID}.png")
        assert "假图 Fake Level" not in message.extract_plain_text()

    async def test_correct_full_answer(self, started, fake_bot, make_group_event):
        """猜对的标志是「把答案念出来」+ 三处状态变更，贺词怎么写不管"""
        store, session, pictures_dir = started
        assert (
            await run_handler(
                guess.guess, fake_bot, make_group_event(), arg="假图 Fake Level"
            )
            is True
        )
        assert "假图 Fake Level" in sent_texts(fake_bot)[0]
        assert store.hget(guess.ANSWER_KEY, session) == guess.NOTHING_ANSWER
        assert store.get(guess.TOTAL_TRIES_KEY) == 1
        assert store.get(guess.TOTAL_RIGHT_KEY) == 1
        assert (pictures_dir / f"{session}.png").exists()

    async def test_alias_is_accepted(self, started, fake_bot, make_group_event):
        store, session, _ = started
        await run_handler(guess.guess, fake_bot, make_group_event(), arg="假图")
        assert "假图 Fake Level" in sent_texts(fake_bot)[0]
        assert store.hget(guess.ANSWER_KEY, session) == guess.NOTHING_ANSWER

    async def test_answer_is_normalized_before_comparing(
        self, started, fake_bot, make_group_event
    ):
        """大小写、空格、标点都会被 formalize 抹掉"""
        store, session, _ = started
        await run_handler(
            guess.guess, fake_bot, make_group_event(), arg="  假图 FAKE-LEVEL!  "
        )
        assert store.hget(guess.ANSWER_KEY, session) == guess.NOTHING_ANSWER

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
        assert answer in sent_texts(fake_bot)[0]
        assert store.hget(guess.ANSWER_KEY, session) == guess.NOTHING_ANSWER

    async def test_guessing_is_allowed_during_the_cooldown(
        self, started, fake_bot, make_group_event
    ):
        """45 秒 cd 只挡出新题，不挡回答"""
        store, session, _ = started
        store.set(f"{guess.COOLDOWN_PREFIX}{session}", "x", ex=45)
        await run_handler(guess.guess, fake_bot, make_group_event(), arg="假图")
        assert "假图 Fake Level" in sent_texts(fake_bot)[0]
        assert store.hget(guess.ANSWER_KEY, session) == guess.NOTHING_ANSWER


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
        assert (
            await run_handler(guess.guess_giveup, fake_bot, make_group_event()) is True
        )
        message = sent_messages(fake_bot)[0]
        assert "假图 Fake Level" in message.extract_plain_text()
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

    async def test_cooldown_blocks_giving_up(self, started, fake_bot, make_group_event):
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
        assert (
            await run_handler(guess.guess_count, fake_bot, make_group_event()) is True
        )
        assert only_text(fake_bot).count("None") == 2

    async def test_count_reports_the_stored_numbers(
        self, guess_env, fake_bot, make_group_event
    ):
        store, _, _ = guess_env
        store.set(guess.TOTAL_TRIES_KEY, 17)
        store.set(guess.TOTAL_RIGHT_KEY, 5)
        await run_handler(guess.guess_count, fake_bot, make_group_event())
        # 报的是这两个数，句子怎么组织不管
        assert re.findall(r"\d+", only_text(fake_bot)) == ["17", "5"]

    async def test_remove_cooldown(self, guess_env, fake_bot, make_group_event):
        store, _, _ = guess_env
        session = "g" + str(DEFAULT_GROUP_ID)
        store.set(f"{guess.COOLDOWN_PREFIX}{session}", "x", ex=45)

        assert (
            await run_handler(guess.guess_removecooldown, fake_bot, make_group_event())
            is True
        )
        assert only_text(fake_bot)
        # 实现是把 cd 改成 1 秒而不是删掉，ttl 会立刻落到 0（判定用的是 > 0）
        assert store.ttl(f"{guess.COOLDOWN_PREFIX}{session}") <= 0

    async def test_cheat_sends_a_private_message(
        self, guess_env, fake_bot, make_group_event
    ):
        store, _, _ = guess_env
        session = "g" + str(DEFAULT_GROUP_ID)
        store.hset(guess.ANSWER_KEY, session, "假图 Fake Level")

        assert (
            await run_handler(guess.guess_cheat, fake_bot, make_group_event()) is True
        )
        api, data = fake_bot.calls[0]
        assert api == "send_private_msg"
        assert data["user_id"] == str(DEFAULT_USER_ID)
        # 行为是「答案私聊给了提问的人、没发到群里」，这条私聊怎么措辞不管
        assert "假图 Fake Level" in str(data["message"])

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
