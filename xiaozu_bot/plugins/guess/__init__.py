from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11 import GroupMessageEvent,MessageEvent
from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent
from typing import Union
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot import require
from nonebot import on_command
from nonebot import logger
from PIL import Image,ImageDraw
from pathlib import Path
from nonebot.permission import SUPERUSER
import random
import redis

from .config import Config
from .data import maps
msg = require("zhua_api").msg_api()
berry_manager = require("zhua_api").berry_manager

random.seed()
r = redis.Redis(host='localhost', port=6379, decode_responses=True)  

__plugin_meta__ = PluginMetadata(
    name="guess",
    description="",
    usage="",
    config=Config,
)

guess_test = on_command("guess_test",permission=SUPERUSER)
guess_start = on_command("guess_start")
guess_start_hard = on_command("guess_start_hard")
guess_start_ultra = on_command("guess_start_ultra")
guess = on_command("guess")
guess_giveup = on_command("guess_giveup")
guess_removecooldown = on_command("guess_rc",permission=SUPERUSER)
guess_cheat = on_command("guess_cheat",permission=SUPERUSER)
guess_count = on_command("guess_count")

crop_width = 256
crop_height = 256
crop_width_hard = 128
crop_height_hard = 128
crop_width_ultra = 64
crop_height_ultra = 64
aliases = {}

for map in maps:
    name = map["answer"]
    alias = map["alias"]
    aliases[name] = alias
def formalize(str: str) -> str:
    str = str.lower()
    for s in [" ",".",",","-","'","!","，","！","…","。",":","：","+","_",'''
'''] :
        str = str.replace(s,"")
    return str

def getid(event: Union[GroupMessageEvent,PrivateMessageEvent]) -> str:
    if isinstance(event,PrivateMessageEvent) or False:
        return str(event.user_id)
    else:
        return "g" + str(event.group_id)

def get_variance(image) -> tuple[float,float,float]:
    pixels = image.getdata()
    num_pixels = len(pixels)
    red_sum = red_square_sum = 0
    green_sum = green_square_sum = 0
    blue_sum = blue_square_sum = 0
    for p in pixels:
        red_sum = red_sum + p[0]
        red_square_sum = red_square_sum + p[0]**2
        green_sum = green_sum + p[1]
        green_square_sum = green_square_sum + p[1]**2
        blue_sum = blue_sum + p[2]
        blue_square_sum = blue_square_sum + p[2]**2
    expect_red = red_sum / num_pixels
    expect_red_square = red_square_sum / num_pixels
    expect_green = green_sum / num_pixels
    expect_green_square = green_square_sum / num_pixels
    expect_blue = blue_sum / num_pixels
    expect_blue_square = blue_square_sum / num_pixels
    red_variance = expect_red_square - expect_red ** 2
    green_variance = expect_green_square - expect_green ** 2
    blue_variance = expect_blue_square - expect_blue ** 2
    return (red_variance, green_variance, blue_variance)

def isnonsense(image) -> bool:
    r,g,b = get_variance(image)
    return r+g+b<300

async def canStart(matcher: Matcher, event: Union[GroupMessageEvent,PrivateMessageEvent]):
    id = getid(event)
    if isinstance(event,GroupMessageEvent) and berry_manager.is_berrygroup(event) and berry_manager.isforbid(event.user_id):
        await msg.emoji_like(event.message_id,"128074")
        await matcher.finish()
    if r.ttl(f"guess_cooldown_{id}") > 0:
        await msg.emoji_like(event.message_id,"424")
        await matcher.finish()
    answer = r.hget("guess_answer",f"{id}")
    if answer != None and answer != "NOTHING" and isinstance(event,GroupMessageEvent):
        await matcher.finish(f"请先输入*guess_giveup结束目前的题目！", at_sender = True)

async def guessStart(crop_size: tuple[int,int], matcher: Matcher, event: Union[GroupMessageEvent,PrivateMessageEvent]):
    id = getid(event)
    crop_width, crop_height = crop_size
    file_names = []
    while len(file_names) == 0:
        map = random.choice(maps)
        folder_path = Path("xiaozu_bot/plugins/guess/data/" + map["file_path"])
        file_names = [f.name for f in folder_path.iterdir() if f.is_file()]
    file_name = random.choice(file_names)
    image_path = folder_path / file_name
    answer = map["answer"]
    image = Image.open(image_path)
    width, height = image.size
    left = random.randint(0, width - crop_width)
    top = random.randint(0, height - crop_height)
    right = left + crop_width
    bottom = top + crop_height
    cropped_image = image.crop((left, top, right, bottom))
    for i in range(0,20):
        left = random.randint(0, width - crop_width)
        top = random.randint(0, height - crop_height)
        right = left + crop_width
        bottom = top + crop_height
        cropped_image = image.crop((left, top, right, bottom))
        if (not isnonsense(cropped_image)): break
    cropped_path = Path()/"xiaozu_bot"/"plugins"/"guess"/"pictures"/f"{id}.png"
    cropped_image.save(cropped_path)
    r.set(f"guess_cooldown_{id}",answer,ex = 45)
    r.hset("guess_answer",f"{id}",answer)
    r.hset("guess_answer_position",f"{id}",str(left)+' '+str(top)+' '+str(right)+' '+str(bottom))
    r.hset("guess_ori",f"{id}",str(image_path))
    #logger.success(str(id) + answer)
    await matcher.send(MessageSegment.image(cropped_path) + MessageSegment.text(f"这个截图是出自哪张图呢？\n输入*guess 你的答案 以回答"),at_sender = True)

@guess_start.handle()
async def handle_function(event: Union[GroupMessageEvent,PrivateMessageEvent]):
    await canStart(guess_start, event)
    id = getid(event)
    await guessStart((256,256), guess_start, event)
    await guess_start.finish()

@guess_start_hard.handle()
async def handle_function(event: Union[GroupMessageEvent,PrivateMessageEvent]):
    await canStart(guess_start_hard, event)
    id = getid(event)
    await guessStart((128,128), guess_start_hard, event)
    await guess_start_hard.finish()

@guess_start_ultra.handle()
async def handle_function(event: Union[GroupMessageEvent,PrivateMessageEvent]):
    await canStart(guess_start_ultra, event)
    id = getid(event)
    await guessStart((64,64), guess_start_ultra, event)
    await guess_start_ultra.finish()


@guess_giveup.handle()
async def handle_function(event: Union[GroupMessageEvent,PrivateMessageEvent]):
    id = getid(event)
    if isinstance(event,GroupMessageEvent) and berry_manager.is_berrygroup(event) and berry_manager.isforbid(event.user_id):
        await msg.emoji_like(event.message_id,"128074")
        await guess_giveup.finish()
    if r.ttl(f"guess_cooldown_{id}") > 0:
        await msg.emoji_like(event.message_id,"424")
        await guess_giveup.finish()
    answer = r.hget("guess_answer",f"{id}")
    if answer == None or answer == "NOTHING":
        await msg.emoji_like(event.message_id,"10068")
        await guess_giveup.finish()
    r.hset("guess_answer", f"{id}", "NOTHING")
    image_path = Path(r.hget("guess_ori",f"{id}"))
    pos = r.hget("guess_answer_position",f"{id}").split()
    pos = [int(i) for i in pos]
    image = Image.open(image_path)
    ImageDraw.Draw(image).rectangle([(pos[0],pos[1]),(pos[2],pos[3])],fill = None, outline = "red", width = 4)
    cropped_path = Path()/"xiaozu_bot"/"plugins"/"guess"/"pictures"/f"{id}.png"
    image.save(cropped_path)
    await guess.finish(MessageSegment.text(f"你放弃了！答案是：{answer}。")+MessageSegment.image(cropped_path), at_sender = True)

@guess.handle()
async def handle_function(event: Union[GroupMessageEvent,PrivateMessageEvent], arg: Message = CommandArg()):
    id = getid(event)
    if isinstance(event,GroupMessageEvent) and berry_manager.is_berrygroup(event) and berry_manager.isforbid(event.user_id):
        await msg.emoji_like(event.message_id,"128074")
        await guess.finish("")
    input = formalize(str(arg))
    answer = r.hget("guess_answer",f"{id}")
    if answer == None or answer == "NOTHING":
        await msg.emoji_like(event.message_id,"10068")
        await guess.finish()
    r.set("guess_total_tries",int(r.get("guess_total_tries"))+1)
    if input in aliases[answer]: input = answer
    if input != answer:
        await msg.emoji_like(event.message_id,"424")
        if random.randint(1,10) <= 1:
            cropped_path = Path()/"xiaozu_bot"/"plugins"/"guess"/"pictures"/f"{id}.png"
            await guess.finish(MessageSegment.text("你的猜测是错误的！你的题目是")+MessageSegment.image(cropped_path), at_sender = True)
        await guess.finish()
    r.hset("guess_answer", f"{id}", "NOTHING")
    image_path = Path(r.hget("guess_ori",f"{id}"))
    pos = r.hget("guess_answer_position",f"{id}").split()
    pos = [int(i) for i in pos]
    image = Image.open(image_path)
    ImageDraw.Draw(image).rectangle([(pos[0],pos[1]),(pos[2],pos[3])],fill = None, outline = "red", width = 4)
    cropped_path = Path()/"xiaozu_bot"/"plugins"/"guess"/"pictures"/f"{id}.png"
    image.save(cropped_path)
    if isinstance(event,GroupMessageEvent) and berry_manager.is_berrygroup(event):
        if pos[2]-pos[0] == crop_width_ultra:
            berry_manager.addCoins(event.user_id,10)
            berry_manager.addCoins(event.self_id,5)
            append = "本次奖励10蓝莓"
        elif pos[2]-pos[0] == crop_width_hard:
            berry_manager.addCoins(event.user_id,5)
            berry_manager.addCoins(event.self_id,2)
            append = "本次奖励5蓝莓"
        else:
            berry_manager.addCoins(event.user_id,2)
            berry_manager.addCoins(event.self_id,1)
            append = "本次奖励2蓝莓"
    else:
        append = ""
    r.set("guess_total_right",int(r.get("guess_total_right"))+1)
    await guess.finish(MessageSegment.text(f"你猜对了！答案是：{answer}。")+MessageSegment.image(cropped_path) + MessageSegment.text(append), at_sender = True)

@guess_count.handle()
async def handle_function():
    t1 = r.get("guess_total_tries")
    t2 = r.get("guess_total_right")
    await guess_count.finish(f"全服总共进行了{t1}次猜测，猜对了{t2}道题。")

@guess_test.handle()
async def handle_function():
    for i in range(0,5):
        file_names = []
        while len(file_names) == 0:
            map = random.choice(maps)
            folder_path = Path("xiaozu_bot/plugins/guess/data/" + map["file_path"])
            file_names = [f.name for f in folder_path.iterdir() if f.is_file()]
        file_name = random.choice(file_names)
        image_path = folder_path / file_name
        image = Image.open(image_path)
        width, height = image.size
        left = random.randint(0, width - crop_width)
        top = random.randint(0, height - crop_height)
        right = left + crop_width
        bottom = top + crop_height
        cropped_image = image.crop((left, top, right, bottom))
        cropped_path = Path()/"xiaozu_bot"/"plugins"/"guess"/"pictures"/"test.png"
        cropped_image.save(cropped_path)
        await guess_test.send(MessageSegment.image(cropped_path)+MessageSegment.text(str(get_variance(cropped_image))))
    guess_test.finish()

@guess_removecooldown.handle()
async def handle_function(event: Union[GroupMessageEvent,PrivateMessageEvent]):
    id = getid(event)
    r.set(f"guess_cooldown_{id}","removed",ex = 1)
    await guess_removecooldown.finish("已经移除你（或你所在群）的生成题目cd！")

@guess_cheat.handle()
async def handle_function(event: Union[GroupMessageEvent,PrivateMessageEvent]):
    id = getid(event)
    answer = r.hget("guess_answer",f"{id}")
    await msg.send_private(3251605531,str(id) + str(answer))
    await guess_cheat.finish()