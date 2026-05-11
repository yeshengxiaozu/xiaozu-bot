import base64

from nonebot import on_command, require
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

require("nonebot_plugin_htmlkit")

def pic_msg_segment(data):
    return {
        "type": "image",
        "data":
        {
            "file":"base64://" + str(base64.b64encode(data))[2:-1]
        }
    }

from nonebot_plugin_htmlkit import html_to_pic, md_to_pic, template_to_pic, text_to_pic

async def send_ttp(bot: Bot, event: Event, text:str) -> None:
    text.replace("\n","<br>")
    pic = await text_to_pic(text,css_path="imageinfo.css")
    if isinstance(event, GroupMessageEvent):
        await bot.call_api("send_group_msg", group_id=event.group_id, message=[pic_msg_segment(pic)])
    else:
        await bot.call_api("send_private_msg", user_id=event.get_user_id(), message=[pic_msg_segment(pic)])

htmltest=on_command("htmltest",permission=SUPERUSER)
texttest=on_command("texttest",permission=SUPERUSER)
mdtest=on_command("mdtest",permission=SUPERUSER)

@htmltest.handle()
async def handle_function(bot: Bot, event: Event, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    pic = await html_to_pic(text)
    if isinstance(event, GroupMessageEvent):
        await bot.call_api("send_group_msg", group_id=event.group_id, message=[pic_msg_segment(pic)])
    await htmltest.finish()

@texttest.handle()
async def handle_function(bot: Bot, event: Event, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    pic = await text_to_pic(text,css_path="imageinfo.css")
    if isinstance(event, GroupMessageEvent):
        await bot.call_api("send_group_msg", group_id=event.group_id, message=[pic_msg_segment(pic)])
    await texttest.finish()

@mdtest.handle()
async def handle_function(bot: Bot, event: Event, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    pic = await md_to_pic(text,css_path="imageinfo.css")
    if isinstance(event, GroupMessageEvent):
        await bot.call_api("send_group_msg", group_id=event.group_id, message=[pic_msg_segment(pic)])
    await mdtest.finish()