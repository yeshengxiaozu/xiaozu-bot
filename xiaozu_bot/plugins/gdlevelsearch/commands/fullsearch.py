"""*gdfullsearch 的参数解析、翻页会话、文本排版和 NoneBot 命令。

会话逻辑在本文件上半部分；命令部分（matcher + handler）在下半部分。
scripts/try_search.py 要单独跑会话逻辑时，会先 init `~none` 驱动再 import 本模块。
"""

import asyncio
import time
from dataclasses import dataclass, field

from nonebot import logger, on_command, on_message
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot.rule import Rule

from ..api.gdapi import (
    DEMON_STARS,
    GD_PAGE_SIZE,
    GDLevel,
    SearchPage,
    search_levels_page,
)
from ..services.search import _clear_all_sessions, send_result

# 会话多久没人动就丢掉。原来 gdsearch 是 30 秒，翻页要慢慢看，放宽一点。
SESSION_TIMEOUT = 120

# -d 的难度：对应请求参数 demonFilter，取值 1-5。
# 注意别和响应字段 43 的刻度搞混（那套是 0=hard 3=easy 4=medium 5=insane 6=extreme）。
DEMON_DIFFICULTIES: dict[str, int] = {
    "1": 1, "easy": 1,
    "2": 2, "medium": 2, "med": 2,
    "3": 3, "hard": 3,
    "4": 4, "insane": 4,
    "5": 5, "extreme": 5, "ex": 5,
}

# -u 的难度：对应请求参数 diff。0 是 auto，映射到 -3。
NONDEMON_DIFFICULTIES: dict[str, int] = {
    "0": -3, "auto": -3,
    "1": 1, "easy": 1,
    "2": 2, "normal": 2,
    "3": 3, "hard": 3,
    "4": 4, "harder": 4,
    "5": 5, "insane": 5,
}

DIFF_DEMON = -2  # diff=-2 表示只搜 demon

USAGE = """用法：*gdfullsearch <关键词> [-a] [-d [难度]] [-u <难度>]
  默认只搜 rated 关卡
  -a          搜全部关卡（包括没评级的）
  -d [难度]   只搜 demon，难度可选：1-5 或 easy/medium/hard/insane/extreme
  -u <难度>   只搜非 demon，难度：0-5 或 auto/easy/normal/hard/harder/insane（0 就是 auto）
  -d 和 -u 不能一起用"""


class ArgError(ValueError):
    """参数有问题，消息直接发给用户看"""


@dataclass
class FullSearchQuery:
    """一次 gdfullsearch 的查询条件"""

    query: str
    rated_only: bool = True
    diff: int | None = None
    demon_filter: int | None = None

    def as_api_kwargs(self) -> dict:
        """转成 search_levels_page 的参数"""
        kwargs: dict = {}
        # -a 的时候是「不传 star」而不是「传 star=0」，让服务器自己按默认来
        if self.rated_only:
            kwargs["star"] = True
        if self.diff is not None:
            kwargs["diff"] = self.diff
        if self.demon_filter is not None:
            kwargs["demon_filter"] = self.demon_filter
        return kwargs

    def describe(self) -> str:
        """给用户看的筛选条件摘要"""
        bits = ["rated" if self.rated_only else "全部关卡"]
        if self.demon_filter is not None:
            name = _alias_name(DEMON_DIFFICULTIES, self.demon_filter)
            bits.append(f"{name} demon")
        elif self.diff == DIFF_DEMON:
            bits.append("demon")
        elif self.diff is not None:
            name = _alias_name(NONDEMON_DIFFICULTIES, self.diff)
            bits.append(f"非 demon / {name}")
        return "、".join(bits)


def _alias_name(table: dict[str, int], value: int) -> str:
    """从别名表里挑一个人类看得懂的名字（跳过纯数字的那些 key）"""
    for key, val in table.items():
        if val == value and not key.isdigit():
            return key
    return str(value)


def parse_args(text: str) -> FullSearchQuery:
    """解析 *gdfullsearch 后面跟的那串东西。

    -d 的难度是可选的，所以只有当它后面紧跟着一个合法难度时才吃掉那个词，
    否则就当成搜索关键词的一部分。也就是说想搜名字里带 "extreme" 的关卡，
    把关键词写在前面：`*gdfullsearch extreme demon -d`
    """
    tokens = text.split()
    if not tokens:
        raise ArgError("请提供要搜索的关卡名\n\n" + USAGE)

    rated_only = True
    diff: int | None = None
    demon_filter: int | None = None
    saw_demon_flag = False
    saw_nondemon_flag = False
    words: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        lowered = token.lower()

        if lowered == "-a":
            rated_only = False
            i += 1

        elif lowered == "-d":
            saw_demon_flag = True
            diff = DIFF_DEMON
            # 后面那个词是合法 demon 难度才吃掉，否则留给关键词
            if i + 1 < len(tokens) and tokens[i + 1].lower() in DEMON_DIFFICULTIES:
                demon_filter = DEMON_DIFFICULTIES[tokens[i + 1].lower()]
                i += 2
            else:
                i += 1

        elif lowered == "-u":
            saw_nondemon_flag = True
            if i + 1 >= len(tokens):
                raise ArgError("-u 后面要跟难度（0-5 或 auto/easy/normal/hard/harder/insane）\n\n" + USAGE)
            key = tokens[i + 1].lower()
            if key not in NONDEMON_DIFFICULTIES:
                raise ArgError(
                    f"看不懂的非 demon 难度：{tokens[i + 1]}\n"
                    "可以用 0-5，或者 auto/easy/normal/hard/harder/insane（0 就是 auto）"
                )
            diff = NONDEMON_DIFFICULTIES[key]
            i += 2

        else:
            words.append(token)
            i += 1

    if saw_demon_flag and saw_nondemon_flag:
        raise ArgError("-d 和 -u 不能一起用：一个是只搜 demon，一个是只搜非 demon")

    query = " ".join(words).strip()
    if not query:
        raise ArgError("请提供要搜索的关卡名\n\n" + USAGE)

    return FullSearchQuery(
        query=query,
        rated_only=rated_only,
        diff=diff,
        demon_filter=demon_filter,
    )


def format_level_line(index: int, level: GDLevel) -> str:
    """列表里的一行：序号. 名字 by 作者 星数+难度 (ID: xxx)"""
    name = level.level_name or "未知关卡"
    creator = f" by {level.creator_name}" if level.creator_name else ""
    label = level.difficulty_label()

    # difficulty_label() 对非 demon 已经把星数写进去了（"8⭐insane"），
    # demon 那一支只给 "Extreme Demon"，这里补个星数前缀让两种看起来一致。
    # 但 10 星没有 demon_difficulty 时它兜底返回的是 "10⭐demon"，自己就带着星数，
    # 再补一次就成了 "10⭐10⭐demon"，所以只给还没写星数的标签补前缀。
    stars = int(level.stars or 0)
    if stars >= DEMON_STARS and "⭐" not in label and "🌙" not in label:
        sign = "🌙" if level.is_plat() else "⭐"
        label = f"{stars}{sign}{label}"

    return f"{index}. {name}{creator} {label} (ID: {level.level_id})"


@dataclass
class FullSearchSession:
    """一次 gdfullsearch 的翻页会话"""

    query: FullSearchQuery
    page: int = 0
    # 已经取回来的页，翻回去不用再请求服务器
    pages: dict[int, list[GDLevel]] = field(default_factory=dict)
    total: int = 0
    total_is_capped: bool = True
    # 已知的最后一页（翻到服务器返回 -1 才知道）
    last_page: int | None = None
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()

    @property
    def expired(self) -> bool:
        return time.time() - self.updated_at > SESSION_TIMEOUT

    @property
    def current_levels(self) -> list[GDLevel]:
        return self.pages.get(self.page, [])

    @property
    def known_last_page(self) -> int | None:
        """已知的最后一页页码，不知道就返回 None。

        两个来源：翻到越界（服务器回 -1）确知的，
        或者 total 没被封顶时直接算出来的。
        """
        if self.last_page is not None:
            return self.last_page
        if not self.total_is_capped and self.total > 0:
            return max(0, -(-self.total // self.page_size) - 1)  # 向上取整再减一
        return None

    @property
    def page_size(self) -> int:
        return GD_PAGE_SIZE

    def fetch(self, page: int) -> list[GDLevel]:
        """取某一页，取过的直接用缓存"""
        if page in self.pages:
            logger.debug(f"[gdfullsearch] 第 {page + 1} 页用缓存")
            return self.pages[page]

        result: SearchPage = search_levels_page(
            query=self.query.query,
            page=page,
            **self.query.as_api_kwargs(),
        )
        # total 只在第一次拿到有意义的值时记下来
        if not result.is_empty:
            self.total = result.total
            self.total_is_capped = result.total_is_capped

        self.pages[page] = result.levels
        if result.is_empty:
            # 服务器给了 -1，说明这一页已经越界了，上一页就是最后一页
            self.last_page = max(0, page - 1)
        return result.levels

    def go_next(self) -> tuple[bool, str]:
        """翻下一页。返回 (有没有翻成功, 提示语)"""
        last = self.known_last_page
        if last is not None and self.page >= last:
            return False, "已经是最后一页了"
        levels = self.fetch(self.page + 1)
        if not levels:
            return False, "已经是最后一页了"
        self.page += 1
        self.touch()
        return True, ""

    def go_prev(self) -> tuple[bool, str]:
        """翻上一页。返回 (有没有翻成功, 提示语)"""
        if self.page == 0:
            return False, "已经是第一页了"
        self.page -= 1
        self.touch()
        return True, ""

    def render(self) -> str:
        """把当前页排版成一条消息"""
        levels = self.current_levels
        if not levels:
            return f"没有找到符合条件的关卡（{self.query.describe()}）"

        # total 封顶时（9999）那不是真实条数，别显示出来骗人
        if self.total_is_capped:
            head = f"「{self.query.query}」第 {self.page + 1} 页　[{self.query.describe()}]"
        else:
            total_pages = max(1, -(-self.total // self.page_size))  # 向上取整
            head = (
                f"「{self.query.query}」共 {self.total} 条，"
                f"第 {self.page + 1}/{total_pages} 页　[{self.query.describe()}]"
            )

        lines = [format_level_line(i, lv) for i, lv in enumerate(levels, start=1)]

        last = self.known_last_page
        hints = ["输入序号选中"]
        if last is None or self.page < last:
            hints.append("n 下一页")
        if self.page > 0:
            hints.append("p 上一页")
        hints.append("结束 取消")

        return "\n".join([head, *lines, " / ".join(hints)])


def start_session(text: str) -> tuple[FullSearchSession | None, str]:
    """解析参数并取第一页。

    返回 (会话, 出错时的消息)。搜不到东西时会话是 None。
    """
    query = parse_args(text)  # ArgError 由调用方接住
    session = FullSearchSession(query=query)
    levels = session.fetch(0)
    if not levels:
        return None, f"没有找到「{query.query}」相关的关卡（{query.describe()}）"
    return session, ""


# ------------------------------------------------------------------ gdfullsearch
# 直连 GD 服务器搜索，带翻页选择器。会话逻辑在上面。

gdfullsearch = on_command("gdfullsearch")

# 按 session_id 存（群里是 group_xxx_yyy，私聊是 private_yyy）。
# commands/search.py 的 search_cache 只按 user_id，同一个人在两个群里搜会串，新的不继承这毛病。
fullsearch_sessions: dict[str, FullSearchSession] = {}
fullsearch_timeouts: dict[str, asyncio.Task] = {}

NEXT_WORDS = {"n", "next", "下一页", "下页"}
PREV_WORDS = {"p", "prev", "上一页", "上页"}
STOP_WORDS = {"结束", "取消", "退出", "q"}


def _drop_fullsearch(session_id: str) -> None:
    fullsearch_sessions.pop(session_id, None)
    task = fullsearch_timeouts.pop(session_id, None)
    if task:
        task.cancel()


def has_fullsearch(event: Event) -> bool:
    return event.get_session_id() in fullsearch_sessions


gdfullsearchselect = on_message(Rule(has_fullsearch), priority=100, block=False)


async def clear_fullsearch(bot: Bot, event: Event, session_id: str) -> None:
    await asyncio.sleep(SESSION_TIMEOUT)
    if session_id in fullsearch_sessions:
        fullsearch_sessions.pop(session_id, None)
        fullsearch_timeouts.pop(session_id, None)
        await bot.send(event, "搜索超时，已结束")


def _arm_timeout(bot: Bot, event: Event, session_id: str) -> None:
    old = fullsearch_timeouts.pop(session_id, None)
    if old:
        old.cancel()
    fullsearch_timeouts[session_id] = asyncio.create_task(
        clear_fullsearch(bot, event, session_id)
    )


@gdfullsearch.handle()
async def handle_gdfullsearch(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    """直接问 GD 服务器要结果，默认只搜 rated"""
    _clear_all_sessions(event)
    session_id = event.get_session_id()

    try:
        # 请求和解析都是同步阻塞的，别堵在事件循环上
        session, err = await asyncio.to_thread(
            start_session, arg.extract_plain_text().strip()
        )
    except ArgError as e:
        await gdfullsearch.finish(str(e))
    except Exception as e:
        logger.exception("[gdfullsearch] 搜索失败")
        await gdfullsearch.finish(f"搜索出错了：{e}")

    if session is None:
        await gdfullsearch.finish(err)

    # 只有一条就别让人再选一次了，和 gdsearch 的行为保持一致
    if len(session.current_levels) == 1:
        await send_result(bot, event, int(session.current_levels[0].level_id))
        await gdfullsearch.finish()

    fullsearch_sessions[session_id] = session
    _arm_timeout(bot, event, session_id)
    await gdfullsearch.finish(session.render())


@gdfullsearchselect.handle()
async def handle_fullsearch_choice(bot: Bot, event: Event) -> None:
    """处理翻页选择器里的输入"""
    session_id = event.get_session_id()
    session = fullsearch_sessions.get(session_id)
    if session is None:
        await gdfullsearchselect.finish()

    choice = event.get_message().extract_plain_text().strip().lower()

    if choice in STOP_WORDS:
        _drop_fullsearch(session_id)
        await gdfullsearchselect.finish("已结束搜索")

    if choice in NEXT_WORDS or choice in PREV_WORDS:
        go = session.go_next if choice in NEXT_WORDS else session.go_prev
        ok, msg = await asyncio.to_thread(go)
        _arm_timeout(bot, event, session_id)
        await gdfullsearchselect.finish(session.render() if ok else msg)

    if not choice.isdigit():
        # 不是给我们的消息，别吞群聊
        await gdfullsearchselect.finish()

    levels = session.current_levels
    index = int(choice)
    if index < 1 or index > len(levels):
        await gdfullsearchselect.finish(f"请输入 1-{len(levels)} 之间的序号")

    level = levels[index - 1]
    _drop_fullsearch(session_id)
    await send_result(bot, event, int(level.level_id))
    await gdfullsearchselect.finish()
