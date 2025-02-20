from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11 import GroupMessageEvent,MessageEvent
from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent
from nonebot.params import CommandArg
from nonebot import require
from nonebot import on_command
from typing import Union
from PIL import Image,ImageDraw
from pathlib import Path
from nonebot.permission import SUPERUSER
import random
import redis

from .config import Config
from .data import maps
msg = require("zhua_api").msg_api()

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
guess = on_command("guess")
guess_giveup = on_command("guess_giveup")
guess_removecooldown = on_command("guess_rc",permission=SUPERUSER)

crop_width = 256
crop_height = 256
crop_width_hard = 128
crop_height_hard = 128
aliases = {}

for map in maps:
    name = map["answer"]
    alias = map["alias"]
    aliases[name] = alias
def formalize(str: str) -> str:
    str = str.lower()
    for s in [" ",".",",","-","'","!","，","！","…","。",":","：","+","_"] :
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

@guess_start_hard.handle()
async def handle_function(event: Union[GroupMessageEvent,PrivateMessageEvent]):
    id = getid(event)
    if r.ttl(f"guess_cooldown_{id}") > 0:
        t = r.ttl(f"guess_cooldown_{id}")
        await guess_start_hard.finish(f"休息一下吧！请{t}秒后再来玩哦~", at_sender = True)
    answer = r.hget("guess_answer",f"{id}")
    if answer != None and answer != "NOTHING" and isinstance(event,GroupMessageEvent):
        await guess_start_hard.finish(f"请先输入*guess_giveup结束目前的题目！", at_sender = True)
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
    left = random.randint(0, width - crop_width_hard)
    top = random.randint(0, height - crop_height_hard)
    right = left + crop_width_hard
    bottom = top + crop_width_hard
    cropped_image = image.crop((left, top, right, bottom))
    if (isnonsense(cropped_image)):
        left = random.randint(0, width - crop_width_hard)
        top = random.randint(0, height - crop_height_hard)
        right = left + crop_width_hard
        bottom = top + crop_width_hard
        cropped_image = image.crop((left, top, right, bottom))
    cropped_path = Path()/"xiaozu_bot"/"plugins"/"guess"/"pictures"/f"{id}.png"
    cropped_image.save(cropped_path)
    r.set(f"guess_cooldown_{id}",answer,ex = 30)
    r.hset("guess_answer",f"{id}",answer)
    r.hset("guess_answer_position",f"{id}",str(left)+' '+str(top)+' '+str(right)+' '+str(bottom))
    r.hset("guess_ori",f"{id}",str(image_path))
    await guess_start_hard.send(MessageSegment.image(cropped_path) + MessageSegment.text(f"这个截图是出自哪张图呢？\n输入*guess 你的答案 以回答"),at_sender = True)
    await guess_start_hard.finish()

@guess_start.handle()
async def handle_function(event: Union[GroupMessageEvent,PrivateMessageEvent]):
    id = getid(event)
    if r.ttl(f"guess_cooldown_{id}") > 0:
        t = r.ttl(f"guess_cooldown_{id}")
        await guess_start.finish(f"休息一下吧！请{t}秒后再来玩哦~", at_sender = True)
    answer = r.hget("guess_answer",f"{id}")
    if answer != None and answer != "NOTHING" and isinstance(event,GroupMessageEvent):
        await guess_start.finish(f"请先输入*guess_giveup结束目前的题目！", at_sender = True)
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
    for i in range(0,5):
        left = random.randint(0, width - crop_width)
        top = random.randint(0, height - crop_height)
        right = left + crop_width
        bottom = top + crop_height
        cropped_image = image.crop((left, top, right, bottom))
        if (not isnonsense(cropped_image)): break
    cropped_path = Path()/"xiaozu_bot"/"plugins"/"guess"/"pictures"/f"{id}.png"
    cropped_image.save(cropped_path)
    r.set(f"guess_cooldown_{id}",answer,ex = 30)
    r.hset("guess_answer",f"{id}",answer)
    r.hset("guess_answer_position",f"{id}",str(left)+' '+str(top)+' '+str(right)+' '+str(bottom))
    r.hset("guess_ori",f"{id}",str(image_path))
    await guess_start.send(MessageSegment.image(cropped_path) + MessageSegment.text(f"这个截图是出自哪张图呢？\n输入*guess 你的答案 以回答"),at_sender = True)
    await guess_start.finish()

@guess_giveup.handle()
async def handle_function(event: Union[GroupMessageEvent,PrivateMessageEvent]):
    id = getid(event)
    if r.ttl(f"guess_cooldown_{id}") > 0:
        t = r.ttl(f"guess_cooldown_{id}")
        await guess_start.finish(f"你先别急！距离该命令可用还差{t}秒捏~", at_sender = True)
    answer = r.hget("guess_answer",f"{id}")
    if answer == None or answer == "NOTHING":
        await guess_giveup.finish("你目前没有题目！请输入*guess_start生成一个新题目。", at_sender = True)
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
    input = formalize(str(arg))
    answer = r.hget("guess_answer",f"{id}")
    if answer == None or answer == "NOTHING":
        await guess.finish("你目前没有题目！请输入*guess_start生成一个新题目。", at_sender = True)
    if input in aliases[answer]: input = answer
    if input != answer:
        if random.randint(1,10) <= 2:
            cropped_path = Path()/"xiaozu_bot"/"plugins"/"guess"/"pictures"/f"{id}.png"
            await guess.finish(MessageSegment.text("你的猜测是错误的！你的题目是")+MessageSegment.image(cropped_path), at_sender = True)
        else:
            await msg.emoji_like(event.message_id,"424")
            await guess.finish()
    r.hset("guess_answer", f"{id}", "NOTHING")
    image_path = Path(r.hget("guess_ori",f"{id}"))
    pos = r.hget("guess_answer_position",f"{id}").split()
    pos = [int(i) for i in pos]
    image = Image.open(image_path)
    ImageDraw.Draw(image).rectangle([(pos[0],pos[1]),(pos[2],pos[3])],fill = None, outline = "red", width = 4)
    cropped_path = Path()/"xiaozu_bot"/"plugins"/"guess"/"pictures"/f"{id}.png"
    image.save(cropped_path)
    await guess.finish(MessageSegment.text(f"你猜对了！答案是：{answer}。")+MessageSegment.image(cropped_path), at_sender = True)

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