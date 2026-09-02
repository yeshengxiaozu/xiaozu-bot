"""*gdratings 的参数解析、翻页会话、排版和 NoneBot 命令。

展示 GDDL 上某关卡的「Submitted ratings」——每个人给的 tier 和 enjoyment。
会话逻辑在本文件上半部分；命令部分在下半部分。
"""

import asyncio
import time
from dataclasses import dataclass, field

from nonebot import logger, on_command, on_message
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot.rule import Rule

from ..api.gddlapi import GDDL_SUBMISSION_LIMIT, Gddl, Submission, SubmissionPage
from ..services.search import _clear_all_sessions
from .fullsearch import NEXT_WORDS, PREV_WORDS, STOP_WORDS

SESSION_TIMEOUT = 120

# 用户写的排序名 -> API 的字段名。
# API 的 username 排序实测是不起作用的（正倒序结果一样），所以不放出来。
SORT_ALIASES: dict[str, str] = {
    "tier": "rating", "rating": "rating", "评分": "rating",
    "enj": "enjoyment", "enjoyment": "enjoyment", "体验": "enjoyment",
    "date": "dateAdded", "time": "dateAdded", "时间": "dateAdded",
    "progress": "progress", "进度": "progress",
    "attempts": "attempts", "att": "attempts", "次数": "attempts",
    "rr": "refreshRate", "refreshrate": "refreshRate", "帧率": "refreshRate",
}

USAGE = """用法：*gdratings <关卡名或id> [-s <排序>] [-asc] [-v]
  -s <排序>  按什么排：tier / enj / date / progress / attempts
  -asc       正序（默认倒序）
  -v         只看通关的人
翻页：n 下一页 / p 上一页 / 结束 取消"""


class ArgError(ValueError):
    """参数有问题，消息直接发给用户看"""


@dataclass
class RatingsQuery:
    """一次 gdratings 的查询条件"""

    target: str                      # 关卡名或 id，还没解析成 level_id
    sort: str | None = None
    ascending: bool = False
    victors_only: bool = False

    def as_api_kwargs(self) -> dict:
        kwargs: dict = {}
        if self.sort:
            kwargs["sort"] = self.sort
            # 只给 sort 不给方向的话接口按自己的默认来，所以方向一起给
            kwargs["sort_direction"] = "asc" if self.ascending else "desc"
        if self.victors_only:
            kwargs["progress_filter"] = "victors"
        return kwargs

    def describe(self) -> str:
        bits = []
        if self.sort:
            bits.append(f"按 {self.sort} {'正序' if self.ascending else '倒序'}")
        if self.victors_only:
            bits.append("只看通关")
        return "、".join(bits)


def parse_args(text: str) -> RatingsQuery:
    """解析 *gdratings 后面那串东西"""
    tokens = text.split()
    if not tokens:
        raise ArgError("请提供关卡名或 id\n\n" + USAGE)

    sort: str | None = None
    ascending = False
    victors = False
    words: list[str] = []

    i = 0
    while i < len(tokens):
        lowered = tokens[i].lower()
        if lowered == "-asc":
            ascending = True
            i += 1
        elif lowered in ("-v", "-victors"):
            victors = True
            i += 1
        elif lowered == "-s":
            if i + 1 >= len(tokens):
                raise ArgError(
                    "-s 后面要跟排序字段：" + " / ".join(sorted(set(SORT_ALIASES.values())))
                )
            key = tokens[i + 1].lower()
            if key not in SORT_ALIASES:
                raise ArgError(
                    f"看不懂的排序字段：{tokens[i + 1]}\n"
                    "可以用：tier / enj / date / progress / attempts / rr"
                )
            sort = SORT_ALIASES[key]
            i += 2
        else:
            words.append(tokens[i])
            i += 1

    target = " ".join(words).strip()
    if not target:
        raise ArgError("请提供关卡名或 id\n\n" + USAGE)

    return RatingsQuery(target=target, sort=sort, ascending=ascending, victors_only=victors)


MIN_ID_LEN = 4


def resolve_level(target: str) -> tuple[int | None, str | None, str]:
    """把用户给的关卡名或 id 变成 level_id。

    返回 (level_id, 关卡名, 出错时的提示)。
    """
    if len(target) > MIN_ID_LEN and target.isdigit():
        level_id = int(target)
        level = Gddl.getlevelbyid(level_id)
        name = level.Meta.Name if level and getattr(level, "Meta", None) else None
        return level_id, name, ""

    candidates = Gddl.getlevelsbyname(target)
    if candidates is None:
        return None, None, "GDDL 那边没响应，等会再试试"
    normalized = target.strip().lower()
    exact = [
        lv for lv in candidates
        if lv and getattr(lv, "Name", "").strip().lower() == normalized
    ]
    pool = exact or [lv for lv in candidates if lv and getattr(lv, "Name", "")]

    if not pool:
        return None, None, f"GDDL 上没有找到「{target}」这个关卡"
    if len(pool) == 1:
        return int(pool[0].ID), pool[0].Name, ""

    lines = [f"「{target}」在 GDDL 上匹配到 {len(pool)} 个关卡，请用 id 重新查："]
    for lv in pool[:10]:
        tier = f" t{round(lv.Rating, 2)}" if lv.Rating else ""
        lines.append(f"  {lv.Name}{tier} (ID: {lv.ID})")
    if len(pool) > 10:
        lines.append(f"  ...还有 {len(pool) - 10} 个")
    return None, None, "\n".join(lines)


def format_submission_line(sub: Submission) -> str:
    """一行一条，照着网页上「Tier X, Enjoyment Y by 谁」的写法"""
    tier = f"Tier {sub.rating}" if sub.rating is not None else "Tier N/A"
    enj = f"Enjoyment {sub.enjoyment}" if sub.enjoyment is not None else "Enjoyment N/A"
    who = sub.user_name or f"用户{sub.user_id}"
    if sub.second_user_name:
        who += f" & {sub.second_user_name}"
    return f"{tier}, {enj} by {who}"


@dataclass
class RatingsSession:
    """gdratings 的翻页会话"""

    query: RatingsQuery
    level_id: int
    level_name: str | None = None
    page: int = 0
    pages: dict[int, list[Submission]] = field(default_factory=dict)
    total: int = 0
    total_pages: int = 1
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()

    @property
    def expired(self) -> bool:
        return time.time() - self.updated_at > SESSION_TIMEOUT

    @property
    def current(self) -> list[Submission]:
        return self.pages.get(self.page, [])

    def fetch(self, page: int) -> list[Submission] | None:
        """取某一页。取过的走缓存。请求失败返回 None。"""
        if page in self.pages:
            logger.debug(f"[gdratings] 第 {page + 1} 页用缓存")
            return self.pages[page]

        result: SubmissionPage | None = Gddl.getsubmissions(
            self.level_id,
            page=page,
            limit=GDDL_SUBMISSION_LIMIT,
            **self.query.as_api_kwargs(),
        )
        if result is None:
            return None
        self.total = result.total
        self.total_pages = result.total_pages
        self.pages[page] = result.submissions
        return result.submissions

    def go_next(self) -> tuple[bool, str]:
        if self.page + 1 >= self.total_pages:
            return False, "已经是最后一页了"
        if self.fetch(self.page + 1) is None:
            return False, "翻页失败，GDDL 那边没响应"
        self.page += 1
        self.touch()
        return True, ""

    def go_prev(self) -> tuple[bool, str]:
        if self.page == 0:
            return False, "已经是第一页了"
        self.page -= 1
        self.touch()
        return True, ""

    def render(self) -> str:
        subs = self.current
        title = self.level_name or str(self.level_id)
        if not subs:
            return f"「{title}」在 GDDL 上还没有人提交评分"

        extra = self.query.describe()
        head = (
            f"「{title}」的提交评分　共 {self.total} 条，"
            f"第 {self.page + 1}/{self.total_pages} 页"
        )
        if extra:
            head += f"　[{extra}]"

        lines = [format_submission_line(s) for s in subs]

        hints = []
        if self.page + 1 < self.total_pages:
            hints.append("n 下一页")
        if self.page > 0:
            hints.append("p 上一页")
        hints.append("结束 取消")

        return "\n".join([head, *lines, " / ".join(hints)])


def start_session(text: str) -> tuple[RatingsSession | None, str]:
    """解析参数、定位关卡、取第一页。

    返回 (会话, 出错时的消息)。
    """
    query = parse_args(text)  # ArgError 由调用方接住
    level_id, level_name, err = resolve_level(query.target)
    if level_id is None:
        return None, err

    session = RatingsSession(query=query, level_id=level_id, level_name=level_name)
    subs = session.fetch(0)
    if subs is None:
        return None, "GDDL 那边没响应，等会再试试"
    if not subs:
        return None, f"「{level_name or level_id}」在 GDDL 上还没有人提交评分"
    return session, ""


# ------------------------------------------------------------------ gdratings
# 看某关卡在 GDDL 上的提交评分（网页上「Submitted ratings」那块）。

gdratings = on_command("gdratings")

ratings_sessions: dict[str, RatingsSession] = {}
ratings_timeouts: dict[str, asyncio.Task] = {}


def _drop_ratings(session_id: str) -> None:
    ratings_sessions.pop(session_id, None)
    task = ratings_timeouts.pop(session_id, None)
    if task:
        task.cancel()


def has_ratings(event: Event) -> bool:
    return event.get_session_id() in ratings_sessions


gdratingsselect = on_message(Rule(has_ratings), priority=100, block=False)


async def clear_ratings(bot: Bot, event: Event, session_id: str) -> None:
    await asyncio.sleep(SESSION_TIMEOUT)
    if session_id in ratings_sessions:
        ratings_sessions.pop(session_id, None)
        ratings_timeouts.pop(session_id, None)
        await bot.send(event, "评分列表超时，已结束")


def _arm_ratings_timeout(bot: Bot, event: Event, session_id: str) -> None:
    old = ratings_timeouts.pop(session_id, None)
    if old:
        old.cancel()
    ratings_timeouts[session_id] = asyncio.create_task(
        clear_ratings(bot, event, session_id)
    )


@gdratings.handle()
async def handle_gdratings(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    """看某关卡在 GDDL 上每个人给的 tier / enjoyment"""
    _clear_all_sessions(event)
    session_id = event.get_session_id()

    try:
        session, err = await asyncio.to_thread(
            start_session, arg.extract_plain_text().strip()
        )
    except ArgError as e:
        await gdratings.finish(str(e))
    except Exception as e:
        logger.exception("[gdratings] 查询失败")
        await gdratings.finish(f"查询出错了：{e}")

    if session is None:
        await gdratings.finish(err)

    # 只有一页就不用挂会话了，发完拉倒
    if session.total_pages <= 1:
        await gdratings.finish(session.render())

    ratings_sessions[session_id] = session
    _arm_ratings_timeout(bot, event, session_id)
    await gdratings.finish(session.render())


@gdratingsselect.handle()
async def handle_ratings_choice(bot: Bot, event: Event) -> None:
    """gdratings 只需要翻页，没有选中这一说"""
    session_id = event.get_session_id()
    session = ratings_sessions.get(session_id)
    if session is None:
        await gdratingsselect.finish()

    choice = event.get_message().extract_plain_text().strip().lower()

    if choice in STOP_WORDS:
        _drop_ratings(session_id)
        await gdratingsselect.finish("已结束")

    if choice in NEXT_WORDS or choice in PREV_WORDS:
        go = session.go_next if choice in NEXT_WORDS else session.go_prev
        ok, msg = await asyncio.to_thread(go)
        _arm_ratings_timeout(bot, event, session_id)
        await gdratingsselect.finish(session.render() if ok else msg)

    # 其他消息一概不理，别吞群聊
    await gdratingsselect.finish()
