"""*gdicon 玩家各 gamemode 的图标（本地渲染，不请求网络）。"""

import asyncio
from io import BytesIO

from nonebot import on_command
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg

from xiaozu_bot.utils.adapter_compat import send_image
from xiaozu_bot.utils.args import Flag
from xiaozu_bot.utils.gd_args import ArgError, parse_flags

from ..api.gdapi import get_user_by_name
from ..render import icons

# 单独一条命令，不塞进 *gduser —— 九个 gamemode 就是九次本地渲染，
# 挂在 gduser 上会把那条命令拖慢。
gdicon = on_command("gdicon")

USAGE = (
    "用法：*gdicon 用户名 [gamemode]\n"
    f"gamemode 可选：{icons.form_names()}，不写默认 cube\n"
    "加 -a 把九个 gamemode 拼成一张图\n"
    "例：*gdicon RobTop ship / *gdicon RobTop -a"
)

GDICON_FLAGS = (Flag(("-a", "-all")),)


@gdicon.handle()
async def handle_gdicon(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    """*gdicon 用户名 [gamemode]，加 -a 出全部九个"""
    tokens = arg.extract_plain_text().strip().split()
    if not tokens:
        await gdicon.finish(USAGE)

    try:
        flags, words = parse_flags(tokens, GDICON_FLAGS, USAGE)
    except ArgError as e:
        await gdicon.finish(str(e))

    show_all = bool(flags.get("-a"))
    form_name = ""
    name_words: list[str] = []
    for token in words:
        # 名字后面跟的那个词能对上 gamemode 才当 gamemode，
        # 否则当成用户名的一部分（有人 ID 就叫 wave）
        if icons.resolve_form(token.lower()) is not None and name_words:
            form_name = token.lower()
        else:
            name_words.append(token)

    name = " ".join(name_words).strip()
    if not name:
        await gdicon.finish("请给一个 GD 用户名")

    user = await asyncio.to_thread(get_user_by_name, name)
    if user is None:
        await gdicon.finish(f"没有找到用户「{name}」")

    if show_all:
        items = await icons.fetch_all(user)
        got = sum(1 for _, im in items if im is not None)
        if got == 0:
            await gdicon.finish("一个图标都没取到，本地图集可能缺资源")
        sheet = await asyncio.to_thread(icons.compose_sheet, user, items)
        buffer = BytesIO()
        sheet.save(buffer, format="PNG")
        await send_image(bot, event, buffer)
        if got < len(items):
            await gdicon.finish(f"（有 {len(items) - got} 个没取到，显示成问号了）")
        await gdicon.finish()

    form = icons.resolve_form(form_name or icons.DEFAULT_FORM)
    if form is None:
        await gdicon.finish(
            f"看不懂的 gamemode「{form_name}」，可选：{icons.form_names()}"
        )

    icon = await icons.fetch_one(user, form)
    if icon is None:
        await gdicon.finish(f"{user.user_name} 的 {form.label} 图标没取到，本地图集里可能没有这个图标")

    buffer = BytesIO()
    icon.save(buffer, format="PNG")
    await send_image(
        bot,
        event,
        buffer,
        before=f"{user.user_name} 的 {form.label}：",
    )
    await gdicon.finish()
