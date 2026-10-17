"""User-facing argument vocabulary shared by the gdlevelsearch commands.

The commands take the same handful of words from users, but the backends behind
them disagree on the representation: the GD server wants an int index, the GDDL
search API wants one of its own enum strings, the submission list wants a
camelCase sort field. Keeping those tables in one module is what stops "easy"
from meaning two different things in two commands.

The splitting itself lives in `xiaozu_bot.utils.args`; this module adds the GD
vocabulary, the value converters and the wording that goes back to users.
"""

from typing import Any

from .args import ArgError, Flag, FlagError, split_flags

NEXT_WORDS = {"n", "next", "下一页", "下页"}
PREV_WORDS = {"p", "prev", "上一页", "上页"}
STOP_WORDS = {"结束", "取消", "退出", "q"}


# Demon difficulty, keyed by what users may type, valued for the GD server's
# demonFilter parameter (1-5). Do not confuse this scale with response field 43
# (0=hard, 3=easy, 4=medium, 5=insane, 6=extreme).
DEMON_DIFFICULTIES: dict[str, int] = {
    "1": 1, "easy": 1,
    "2": 2, "medium": 2, "med": 2,
    "3": 3, "hard": 3,
    "4": 4, "insane": 4,
    "5": 5, "extreme": 5, "ex": 5,
}

# Non-demon difficulty, keyed by what users may type, valued for the GD server's
# diff parameter. 0 is auto, which that endpoint spells -3.
NONDEMON_DIFFICULTIES: dict[str, int] = {
    "0": -3, "auto": -3,
    "1": 1, "easy": 1,
    "2": 2, "normal": 2,
    "3": 3, "hard": 3,
    "4": 4, "harder": 4,
    "5": 5, "insane": 5,
}

DIFF_DEMON = -2  # diff=-2 asks the GD server for demons only

# The same words again, valued for the GDDL search API's `difficulty` enum.
# "Official" is a legal enum value but is not offered: nobody can ask for it in a
# way that means anything for the commands that use this table.
GDDL_DIFFICULTIES: dict[str, str] = {
    "1": "Easy", "easy": "Easy",
    "2": "Medium", "medium": "Medium", "med": "Medium",
    "3": "Hard", "hard": "Hard",
    "4": "Insane", "insane": "Insane",
    "5": "Extreme", "extreme": "Extreme", "ex": "Extreme",
}

# Sort field of the GDDL submission list, keyed by the names users type.
# The API's username sort does not work in practice (asc and desc return the same
# rows), so it is deliberately absent.
SORT_ALIASES: dict[str, str] = {
    "tier": "rating", "rating": "rating", "评分": "rating",
    "enj": "enjoyment", "enjoyment": "enjoyment", "体验": "enjoyment",
    "date": "dateAdded", "time": "dateAdded", "时间": "dateAdded",
    "progress": "progress", "进度": "progress",
    "attempts": "attempts", "att": "attempts", "次数": "attempts",
    "rr": "refreshRate", "refreshrate": "refreshRate", "帧率": "refreshRate",
}

# What users may type for -d when they only want demons, not a specific
# difficulty (the GD server takes no filter at all in that case).
ANY_DIFFICULTY = {"any", "all", "任意", "全部"}


def parse_demon_filter(text: str) -> int | None:
    """Parse -d of *gdfullsearch; None means "demons of any difficulty"."""
    key = text.strip().lower()
    if key in ANY_DIFFICULTY:
        return None
    try:
        return DEMON_DIFFICULTIES[key]
    except KeyError:
        raise ArgError(
            f"看不懂的 demon 难度：{text}\n"
            "可以用 1-5、easy/medium/hard/insane/extreme，或者 any（不限难度）"
        ) from None


def parse_diff(text: str) -> int:
    """Parse -u of *gdfullsearch (non-demon difficulty)."""
    key = text.strip().lower()
    try:
        return NONDEMON_DIFFICULTIES[key]
    except KeyError:
        raise ArgError(
            f"看不懂的非 demon 难度：{text}\n"
            "可以用 0-5，或者 auto/easy/normal/hard/harder/insane（0 就是 auto）"
        ) from None


def parse_sort(text: str) -> str:
    """Parse -s of *gdratings."""
    key = text.strip().lower()
    try:
        return SORT_ALIASES[key]
    except KeyError:
        raise ArgError(
            f"无法解析的排序字段：{text}\n"
            "可以用：tier / enj / date / progress / attempts / rr"
        ) from None


def parse_difficulty(text: str) -> str:
    """Normalize a user-typed difficulty to the GDDL search enum value."""
    key = text.strip().lower()
    try:
        return GDDL_DIFFICULTIES[key]
    except KeyError:
        raise ArgError(
            f"看不懂的难度：{text}\n"
            "可以用 1-5，或者 easy/medium/hard/insane/extreme"
        ) from None


def parse_number(text: str, name: str, low: float, high: float) -> float:
    """Parse a number the user typed and check it is inside [low, high]."""
    try:
        value = float(text)
    except ValueError:
        raise ArgError(f"{name} 要是个数字，你写的是「{text}」") from None
    if not low <= value <= high:
        raise ArgError(f"{name} 要在 {low:g}-{high:g} 之间，你写的是 {value:g}")
    return value


def alias_name(table: dict[str, int], value: int) -> str:
    """Pick a human-readable name for a value, skipping numeric-only keys."""
    for key, val in table.items():
        if val == value and not key.isdigit():
            return key
    return str(value)


FLAG_ERROR_TEXT = {
    "missing_value": "{flag} 后面要跟一个值",
    "repeated": "{flag} 只能写一次",
    "conflict": "{flag} 和 {other} 不能一起用",
}


def format_arg_error(exc: ArgError, usage: str) -> str:
    """Render a parse error for the user, appending the command usage."""
    if isinstance(exc, FlagError):
        template = FLAG_ERROR_TEXT.get(exc.reason, "{flag} 用不了")
        reason = template.format(flag=exc.flag, other=exc.other)
    else:
        reason = str(exc)
    return f"{reason}\n\n{usage}"


def parse_flags(
    tokens: list[str], flags: tuple[Flag, ...], usage: str
) -> tuple[dict[str, Any], list[str]]:
    """split_flags, but flag problems come back as a ready-to-send ArgError."""
    try:
        return split_flags(tokens, flags)
    except FlagError as exc:
        raise ArgError(format_arg_error(exc, usage)) from None
