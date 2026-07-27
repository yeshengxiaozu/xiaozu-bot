import asyncio
import random
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from PIL import Image, ImageDraw, ImageStat

from xiaozu_bot.utils.json_storage import JsonRedis, plugin_storage

from .config import Config
from .data import maps

# 相对本文件，不是相对当前工作目录 —— 换个地方启动 bot 也能找到题库
_PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = _PLUGIN_DIR / "data"
PICTURES_DIR = _PLUGIN_DIR / "pictures"
COOLDOWN_PREFIX = "guess_cooldown_"
ANSWER_KEY = "guess_answer"
ANSWER_POSITION_KEY = "guess_answer_position"
ANSWER_ORI_KEY = "guess_ori"
TOTAL_TRIES_KEY = "guess_total_tries"
TOTAL_RIGHT_KEY = "guess_total_right"
NOTHING_ANSWER = "NOTHING"
NOISE_THRESHOLD = 300
MAX_CROP_RETRIES = 20

r = JsonRedis(plugin_storage(__file__))

__plugin_meta__ = PluginMetadata(
    name="guess",
    description="",
    usage="",
    config=Config,
)

guess_test = on_command("guess_test", permission=SUPERUSER)
guess_start = on_command("guess_start")
guess_start_hard = on_command("guess_start_hard")
guess_start_ultra = on_command("guess_start_ultra")
guess = on_command("guess")
guess_giveup = on_command("guess_giveup")
guess_removecooldown = on_command("guess_rc", permission=SUPERUSER)
guess_cheat = on_command("guess_cheat", permission=SUPERUSER)
guess_count = on_command("guess_count")

crop_width = 256
crop_height = 256
crop_width_hard = 128
crop_height_hard = 128
crop_width_ultra = 64
crop_height_ultra = 64
def formalize(str: str) -> str:
    str = str.lower()  # noqa: A001
    for s in [" ",".",",","-","'","!","，","！","…","。",":","：","+","_","""
"""] :
        str = str.replace(s,"")  # noqa: A001
    return str


aliases: dict[str, list[str]] = {}
# 答案 -> 所有能算对的写法（都归一化过）
accepted: dict[str, set[str]] = {}

for map_info in maps:
    aliases[map_info["answer"]] = map_info["alias"]
    # 别名表以前是原样存的，而输入是归一化之后再去比的，
    # 所以别名里只要带大写或空格就永远匹配不上（比如 VVVVVV）。
    # 这里两边都归一化。顺带把完整答案名也收进去，写全名也能算对。
    accepted[map_info["answer"]] = {
        formalize(map_info["answer"]),
        *(formalize(a) for a in map_info["alias"]),
    }

def getid(event: GroupMessageEvent | PrivateMessageEvent) -> str:
    if isinstance(event,PrivateMessageEvent) or False:
        return str(event.user_id)
    return "g" + str(event.group_id)

def get_variance(image) -> tuple[float,float,float]:
    # 原来是 image.getdata() 逐像素手算 E[x²]-E[x]²。Pillow 12 把 getdata()
    # 标成了废弃（Pillow 14 移除），而 pytest 那边配了
    # `error::DeprecationWarning:xiaozu_bot`，于是一升 Pillow 就全挂。
    # ImageStat 从 Pillow 1.x 就有，走的是 C 实现的直方图，算的是同一个方差；
    # 实测在插件真正会喂进来的裁剪块（边长 64/128/256，像素数是 2 的幂）上
    # 两种算法逐位相等。前三个波段就是 RGB，RGBA 的 alpha 和以前一样忽略。
    red, green, blue = ImageStat.Stat(image).var[:3]
    return (red, green, blue)

async def _list_files(folder_path: Path) -> list[str]:
    """异步获取文件夹下所有文件的名称列表"""
    def sync_list():
        if not folder_path.exists():
            return []
        return [f.name for f in folder_path.iterdir() if f.is_file()]
    return await asyncio.to_thread(sync_list)


async def _pick_random_shot(matcher: Matcher | type[Matcher]) -> tuple[dict, Path]:
    """随机挑一张题图，返回 (那一条 map 记录, 图片完整路径)。

    **无放回**地过一遍 maps：抽中的目录空着就换下一条，122 条全都空才算题库是空的。

    原来是 `while not file_names` 无限重试，题库整个空着（干净 clone 就是这个状态）
    时就是个死循环 —— 循环体里只 await 了 to_thread，不抛异常也不超时，
    *guess_start 会把整个事件循环拖住。
    但改成「有放回地抽固定次数」同样不行：那样只是把死循环换成了概率性误判，
    122 条里只剩少数几条有图时会把「没抽中」当成「题库是空的」报出去
    （只剩 1 条时误报率 (121/122)**50 ≈ 66%，只剩 5 条也还有 12%），
    而题库是一张一张截出来的，「只填了一部分」恰恰是它平时的状态。
    无放回就没有这个问题：报出去的「空」是真的空，最坏情况也只是多几毫秒 iterdir。
    """
    # 目录不存在和目录空着在 _list_files 里都是返回 []，这里一视同仁
    for map_info in random.sample(maps, len(maps)):
        folder_path = DATA_DIR / map_info["file_path"]
        file_names = await _list_files(folder_path)
        if file_names:
            return map_info, folder_path / random.choice(file_names)

    await matcher.finish(
        "题库是空的！xiaozu_bot/plugins/guess/data/ 下一张截图都没有，先把题库补上再来。"
    )
    # finish() 必定抛 FinishedException，正常走不到这行。
    # 写出来是让「要么返回二元组、要么抛异常」对静态检查和两个调用方都成立
    # ——否则哪天 finish 被 stub 成不抛异常，调用方会在解包处炸得莫名其妙。
    raise AssertionError("matcher.finish() 应该已经抛出 FinishedException")


def isnonsense(image: Image.Image) -> bool:
    return sum(get_variance(image)) < NOISE_THRESHOLD


async def can_start(bot: Bot, matcher: Matcher, event: GroupMessageEvent | PrivateMessageEvent) -> None:
    session_id = getid(event)
    if r.ttl(f"{COOLDOWN_PREFIX}{session_id}") > 0:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=event.message_id,
            emoji_id="424",
        )
        await matcher.finish()

    answer = r.hget(ANSWER_KEY, session_id)
    if answer is not None and answer != NOTHING_ANSWER and isinstance(event, GroupMessageEvent):
        await matcher.finish("请先输入*guess_giveup结束目前的题目！", at_sender=True)


def _crop_and_save(
    image_path: Path, crop_width: int, crop_height: int, out_path: Path
) -> tuple[int, int, int, int]:
    """随机裁一块出来存成文件，返回裁剪坐标。

    会跳过纯色/没信息的区域，最多试 MAX_CROP_RETRIES 次；
    实在都很糊就用最后一次的结果。
    这函数是同步的，调用方负责丢线程池。
    """
    image = Image.open(image_path)
    width, height = image.size

    box = (0, 0, crop_width, crop_height)
    for _ in range(MAX_CROP_RETRIES):
        left = random.randint(0, max(0, width - crop_width))
        top = random.randint(0, max(0, height - crop_height))
        box = (left, top, left + crop_width, top + crop_height)
        cropped = image.crop(box)
        if not isnonsense(cropped):
            break
    else:
        cropped = image.crop(box)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(out_path)
    return box


async def guessstart(
    crop_size: tuple[int, int], matcher: Matcher, event: GroupMessageEvent | PrivateMessageEvent
) -> None:
    session_id = getid(event)
    crop_width, crop_height = crop_size

    map_info, image_path = await _pick_random_shot(matcher)
    answer = map_info["answer"]

    # 裁图 + 去噪判定是纯 CPU 活：一张 256x256 就是六万多像素，
    # 每次都要过一遍直方图，最多还要重试 20 次。
    # 放在事件循环里跑会让整个 bot 卡住，丢线程池。
    cropped_path = PICTURES_DIR / f"{session_id}.png"
    left, top, right, bottom = await asyncio.to_thread(
        _crop_and_save, image_path, crop_width, crop_height, cropped_path
    )

    r.set(f"{COOLDOWN_PREFIX}{session_id}", answer, ex=45)
    r.hset(ANSWER_KEY, session_id, answer)
    r.hset(
        ANSWER_POSITION_KEY,
        session_id,
        f"{left} {top} {right} {bottom}",
    )
    r.hset(ANSWER_ORI_KEY, session_id, str(image_path))

    await matcher.send(
        MessageSegment.image(cropped_path)
        + MessageSegment.text("这个截图是出自哪张图呢？\n输入*guess 你的答案 以回答"),
        at_sender=True,
    )


@guess_start.handle()
async def handle_guess_start(bot: Bot, matcher: Matcher, event: GroupMessageEvent | PrivateMessageEvent) -> None:
    await can_start(bot, matcher, event)
    await guessstart((256, 256), matcher, event)
    await guess_start.finish()


@guess_start_hard.handle()
async def handle_guess_start_hard(bot: Bot,  matcher: Matcher, event: GroupMessageEvent | PrivateMessageEvent) -> None:
    await can_start(bot, matcher, event)
    await guessstart((128, 128), matcher, event)
    await guess_start_hard.finish()


@guess_start_ultra.handle()
async def handle_guess_start_ultra(bot: Bot,  matcher: Matcher, event: GroupMessageEvent | PrivateMessageEvent) -> None:
    await can_start(bot, matcher, event)
    await guessstart((64, 64), matcher, event)
    await guess_start_ultra.finish()


@guess_giveup.handle()
async def handle_guess_giveup(bot: Bot, event: GroupMessageEvent | PrivateMessageEvent) -> None:
    session_id = getid(event)
    if r.ttl(f"{COOLDOWN_PREFIX}{session_id}") > 0:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=event.message_id,
            emoji_id="424",
        )
        await guess_giveup.finish()

    answer = r.hget(ANSWER_KEY, session_id)
    if answer is None or answer == NOTHING_ANSWER:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=event.message_id,
            emoji_id="10068",
        )
        await guess_giveup.finish()

    r.hset(ANSWER_KEY, session_id, NOTHING_ANSWER)
    image_path = Path(r.hget(ANSWER_ORI_KEY, session_id)) # pyright: ignore[reportArgumentType]
    pos = [int(value) for value in r.hget(ANSWER_POSITION_KEY, session_id).split()] # pyright: ignore[reportOptionalMemberAccess]
    image = Image.open(image_path)
    ImageDraw.Draw(image).rectangle(
        [(pos[0], pos[1]), (pos[2], pos[3])], fill=None, outline="red", width=4
    )

    PICTURES_DIR.mkdir(parents=True, exist_ok=True)
    cropped_path = PICTURES_DIR / f"{session_id}.png"
    image.save(cropped_path)

    await guess_giveup.finish(
        MessageSegment.text(f"你放弃了！答案是：{answer}。")
        + MessageSegment.image(cropped_path),
        at_sender=True,
    )


@guess.handle()
async def handle_guess(bot: Bot, event: GroupMessageEvent | PrivateMessageEvent, arg: Message = CommandArg()) -> None:
    session_id = getid(event)
    guess_input = formalize(str(arg))
    answer = r.hget(ANSWER_KEY, session_id)
    if answer is None or answer == NOTHING_ANSWER:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=event.message_id,
            emoji_id="10068",
        )
        await guess.finish()

    if guess_input in accepted.get(answer, set()):
        guess_input = answer

    r.set(TOTAL_TRIES_KEY, int(r.get(TOTAL_TRIES_KEY) or 0) + 1)

    if guess_input != answer:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=event.message_id,
            emoji_id="424",
        )
        if random.randint(1, 10) <= 1:
            cropped_path = PICTURES_DIR / f"{session_id}.png"
            await guess.finish(
                MessageSegment.text("你的猜测是错误的！你的题目是")
                + MessageSegment.image(cropped_path),
                at_sender=True,
            )
        await guess.finish()

    r.hset(ANSWER_KEY, session_id, NOTHING_ANSWER)
    image_path = Path(r.hget(ANSWER_ORI_KEY, session_id)) # pyright: ignore[reportArgumentType]
    pos = [int(value) for value in r.hget(ANSWER_POSITION_KEY, session_id).split()] # pyright: ignore[reportOptionalMemberAccess]
    image = Image.open(image_path)
    ImageDraw.Draw(image).rectangle(
        [(pos[0], pos[1]), (pos[2], pos[3])], fill=None, outline="red", width=4
    )

    PICTURES_DIR.mkdir(parents=True, exist_ok=True)
    cropped_path = PICTURES_DIR / f"{session_id}.png"
    image.save(cropped_path)

    r.set(TOTAL_RIGHT_KEY, int(r.get(TOTAL_RIGHT_KEY) or 0) + 1)
    await guess.finish(
        MessageSegment.text(f"你猜对了！答案是：{answer}。")
        + MessageSegment.image(cropped_path),
        at_sender=True,
    )


@guess_count.handle()
async def handle_guess_count() -> None:
    t1 = r.get(TOTAL_TRIES_KEY)
    t2 = r.get(TOTAL_RIGHT_KEY)
    await guess_count.finish(
        f"全服总共进行了{t1}次猜测，猜对了{t2}道题。"
    )


@guess_test.handle()
async def handle_guess_test() -> None:
    for _ in range(5):
        _, image_path = await _pick_random_shot(guess_test)
        image = Image.open(image_path)
        width, height = image.size
        left = random.randint(0, width - crop_width)
        top = random.randint(0, height - crop_height)
        right = left + crop_width
        bottom = top + crop_height
        cropped_image = image.crop((left, top, right, bottom))

        PICTURES_DIR.mkdir(parents=True, exist_ok=True)
        cropped_path = PICTURES_DIR / "test.png"
        cropped_image.save(cropped_path)
        await guess_test.send(
            MessageSegment.image(cropped_path)
            + MessageSegment.text(str(get_variance(cropped_image)))
        )
    await guess_test.finish()


@guess_removecooldown.handle()
async def handle_guess_removecooldown(event: GroupMessageEvent | PrivateMessageEvent) -> None:
    session_id = getid(event)
    r.set(f"{COOLDOWN_PREFIX}{session_id}", "removed", ex=1)
    await guess_removecooldown.finish("已经移除你（或你所在群）的生成题目cd！")


@guess_cheat.handle()
async def handle_guess_cheat(bot: Bot, event: GroupMessageEvent | PrivateMessageEvent) -> None:
    session_id = getid(event)
    answer = r.hget(ANSWER_KEY, session_id)
    await bot.call_api(
        "send_private_msg",
        user_id=event.user_id,
        message=[{"type": "text", "data": {"text": str(session_id) + str(answer)}}],
    )
    await guess_cheat.finish()
