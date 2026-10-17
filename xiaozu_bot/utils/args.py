"""Generic `-flag` splitting for chat commands.

A command receives free text: some words are flags, everything else means
whatever that command wants it to mean (a search keyword, positional numbers, a
user name). This module only splits: it returns the flags it recognised plus the
remaining words in their original order, so each command keeps owning its
positional semantics.

It deliberately knows nothing about any particular command: no help text, no
validation wording, no GD vocabulary. Wording belongs to the caller.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

#: Reason codes carried by FlagError. Callers format them into a reply.
MISSING_VALUE = "missing_value"
REPEATED = "repeated"
CONFLICT = "conflict"


class ArgError(ValueError):
    """Bad argument from the user; callers turn it into a reply."""


class FlagError(ArgError):
    """A flag-level problem, expressed as a reason code plus the flag names.

    `str()` is a debug string on purpose: user-facing wording is the caller's
    job (see `xiaozu_bot.utils.gd_args.format_arg_error`).
    """

    def __init__(self, reason: str, flag: str, other: str = "") -> None:
        self.reason = reason
        self.flag = flag
        self.other = other
        detail = f"{reason}: {flag}" + (f" / {other}" if other else "")
        super().__init__(detail)


@dataclass(frozen=True)
class Flag:
    """One recognised flag.

    names: every spelling the flag answers to. The first one is canonical and is
    the key the value is returned under.
    takes_value: False for switches, True for flags that consume the next token.
    convert: optional normalizer for the consumed value; raising ArgError from it
    is the supported way to reject a value.
    conflicts: canonical names of flags this one cannot be combined with.
    """

    names: tuple[str, ...]
    takes_value: bool = False
    convert: Callable[[str], Any] | None = None
    conflicts: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """Canonical name, i.e. the key this flag is reported under."""
        return self.names[0]


def split_flags(
    tokens: Iterable[str], flags: Iterable[Flag]
) -> tuple[dict[str, Any], list[str]]:
    """Split tokens into (recognised flags, everything else in order).

    Only exact flag names are recognised, and only on tokens that start with
    "-": anything else -- including "-1" and unknown options -- comes back as an
    ordinary word. A flag that takes a value always consumes the token after it,
    whatever that token looks like; judging the value is `convert`'s job.
    """
    specs = list(flags)
    by_name = {name.lower(): spec for spec in specs for name in spec.names}
    # Conflicts are symmetric: declaring them on one side is enough.
    conflict_map: dict[str, set[str]] = {spec.name: set() for spec in specs}
    for spec in specs:
        for other in spec.conflicts:
            conflict_map[spec.name].add(other)
            conflict_map.setdefault(other, set()).add(spec.name)

    pending = list(tokens)
    values: dict[str, Any] = {}
    words: list[str] = []
    i = 0
    while i < len(pending):
        token = pending[i]
        spec = by_name.get(token.lower()) if token.startswith("-") else None
        if spec is None:
            words.append(token)
            i += 1
            continue

        if spec.name in values:
            raise FlagError(REPEATED, token)
        clash = conflict_map[spec.name] & set(values)
        if clash:
            raise FlagError(CONFLICT, token, min(clash))

        if spec.takes_value:
            if i + 1 >= len(pending):
                raise FlagError(MISSING_VALUE, token)
            raw = pending[i + 1]
            values[spec.name] = spec.convert(raw) if spec.convert else raw
            i += 2
        else:
            values[spec.name] = True
            i += 1

    return values, words
