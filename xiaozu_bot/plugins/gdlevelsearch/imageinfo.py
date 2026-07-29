import base64

from nonebot import on_command, require
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from xiaozu_bot.utils.adapter_compat import send_image

require("nonebot_plugin_htmlkit")


def pic_msg_segment(data: bytes) -> dict[str, str | dict[str, str]]:
    return {
        "type": "image",
        "data": {"file": "base64://" + str(base64.b64encode(data))[2:-1]},
    }


from nonebot_plugin_htmlkit import html_to_pic, md_to_pic, text_to_pic


async def send_ttp(bot: Bot, event: Event, text: str) -> None:
    text.replace("\n", "<br>")
    pic = await text_to_pic(text, css_path="imageinfo.css")
    await send_image(bot, event, pic)


htmltest = on_command("htmltest", permission=SUPERUSER)
texttest = on_command("texttest", permission=SUPERUSER)
mdtest = on_command("mdtest", permission=SUPERUSER)


@htmltest.handle()
async def handle_htmltest(bot: Bot, event: Event, args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    pic = await html_to_pic(text)
    await send_image(bot, event, pic)
    await htmltest.finish()


@texttest.handle()
async def handle_texttest(bot: Bot, event: Event, args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    pic = await text_to_pic(text, css_path="imageinfo.css")
    await send_image(bot, event, pic)
    await texttest.finish()


@mdtest.handle()
async def handle_mdtest(bot: Bot, event: Event, args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    pic = await md_to_pic(text, css_path="imageinfo.css")
    await send_image(bot, event, pic)
    await mdtest.finish()
