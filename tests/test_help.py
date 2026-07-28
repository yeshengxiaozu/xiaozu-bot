"""Tests for the help registry and its deliberately compact overview."""

from nonebot.adapters.onebot.v11 import Message

from tests.conftest import only_text, run_handler
from xiaozu_bot.plugins import xiaozubot_help as helpmod
from xiaozu_bot.plugins.xiaozubot_help.commands import CATEGORIES, COMMANDS, Cmd


def test_registry_entries_are_well_formed() -> None:
    assert COMMANDS
    for name, command in COMMANDS.items():
        assert isinstance(command, Cmd)
        assert name and name == name.strip()
        assert command.category in CATEGORIES
        assert command.usage and command.summary and command.detail


def test_default_overview_hides_ai() -> None:
    overview = helpmod._overview()
    assert CATEGORIES["ai"] not in overview
    for name, command in helpmod._by_category("ai"):
        assert command.prefix + name not in overview
        assert command.summary not in overview


def test_ai_remains_directly_queryable() -> None:
    assert helpmod._by_category("ai")
    assert "*ai" in helpmod._render("ai", COMMANDS["ai"])
    assert CATEGORIES["ai"] in helpmod._render_category("ai")


async def test_help_handler_uses_compact_overview(fake_bot, make_group_event) -> None:
    await run_handler(
        helpmod.xiaozubothelp,
        fake_bot,
        make_group_event("*help"),
        arg=Message(""),
    )
    assert only_text(fake_bot) == helpmod._overview()


async def test_help_handler_can_open_ai_category(fake_bot, make_group_event) -> None:
    await run_handler(
        helpmod.xiaozubothelp,
        fake_bot,
        make_group_event("*help ai"),
        arg=Message("ai"),
    )
    assert only_text(fake_bot) == helpmod._render("ai", COMMANDS["ai"])
