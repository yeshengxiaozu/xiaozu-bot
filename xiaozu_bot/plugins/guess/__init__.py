import asyncio
import random
from pathlib import Path
from typing import Union

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from PIL import Image, ImageDraw

from xiaozu_bot.utils.json_storage import JsonRedis

from .config import Config
from .data import maps

DATA_DIR = Path("xiaozu_bot/plugins/guess/data")
PICTURES_DIR = Path("xiaozu_bot/plugins/guess/pictures")
COOLDOWN_PREFIX = "guess_cooldown_"
ANSWER_KEY = "guess_answer"
ANSWER_POSITION_KEY = "guess_answer_position"
ANSWER_ORI_KEY = "guess_ori"
TOTAL_TRIES_KEY = "guess_total_tries"
TOTAL_RIGHT_KEY = "guess_total_right"
NOTHING_ANSWER = "NOTHING"
NOISE_THRESHOLD = 300
MAX_CROP_RETRIES = 20

r = JsonRedis("xiaozu_bot/plugins/guess/data/storage.json")

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
aliases: dict[str, list[str]] = {}

for map_info in maps:
    aliases[map_info["answer"]] = map_info["alias"]
def formalize(str: str) -> str:  # noqa: A002
    str = str.lower()  # noqa: A001
    for s in [" ",".",",","-","'","!","，","！","…","。",":","：","+","_","""
"""] :
        str = str.replace(s,"")  # noqa: A001
    return str

def getid(event: Union[GroupMessageEvent,PrivateMessageEvent]) -> str:
    if isinstance(event,PrivateMessageEvent) or False:
        return str(event.user_id)
    return "g" + str(event.group_id)

def get_variance(image) -> tuple[float,float,float]:  # noqa: ANN001
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

async def _list_files(folder_path: Path) -> list[str]:
    """异步获取文件夹下所有文件的名称列表"""
    def sync_list():
        if not folder_path.exists():
            return []
        return [f.name for f in folder_path.iterdir() if f.is_file()]
    return await asyncio.to_thread(sync_list)

def isnonsense(image: Image.Image) -> bool:
    return sum(get_variance(image)) < NOISE_THRESHOLD


async def can_start(bot: Bot, matcher: Matcher, event: Union[GroupMessageEvent, PrivateMessageEvent]) -> None:
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


async def guessstart(
    crop_size: tuple[int, int], matcher: Matcher, event: Union[GroupMessageEvent, PrivateMessageEvent]
) -> None:
    session_id = getid(event)
    crop_width, crop_height = crop_size
    file_names: list[str] = []
    folder_path = Path()
    map_info = None

    while not file_names or not map_info:
        map_info = random.choice(maps)
        folder_path = DATA_DIR / map_info["file_path"]
        file_names = await _list_files(folder_path)

    file_name = random.choice(file_names)
    image_path = folder_path / file_name
    answer = map_info["answer"]

    image = Image.open(image_path)
    width, height = image.size

    left = random.randint(0, width - crop_width)
    top = random.randint(0, height - crop_height)
    right = left + crop_width
    bottom = top + crop_height
    cropped_image = image.crop((left, top, right, bottom))

    for _ in range(MAX_CROP_RETRIES):
        left = random.randint(0, width - crop_width)
        top = random.randint(0, height - crop_height)
        right = left + crop_width
        bottom = top + crop_height
        cropped_image = image.crop((left, top, right, bottom))
        if not isnonsense(cropped_image):
            break

    PICTURES_DIR.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    cropped_path = PICTURES_DIR / f"{session_id}.png"
    cropped_image.save(cropped_path)

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
async def handle_guess_start(bot: Bot, matcher: Matcher, event: Union[GroupMessageEvent, PrivateMessageEvent]) -> None:
    await can_start(bot, matcher, event)
    await guessstart((256, 256), matcher, event)
    await guess_start.finish()


@guess_start_hard.handle()
async def handle_guess_start_hard(bot: Bot,  matcher: Matcher, event: Union[GroupMessageEvent, PrivateMessageEvent]) -> None:
    await can_start(bot, matcher, event)
    await guessstart((128, 128), matcher, event)
    await guess_start_hard.finish()


@guess_start_ultra.handle()
async def handle_guess_start_ultra(bot: Bot,  matcher: Matcher, event: Union[GroupMessageEvent, PrivateMessageEvent]) -> None:
    await can_start(bot, matcher, event)
    await guessstart((64, 64), matcher, event)
    await guess_start_ultra.finish()


@guess_giveup.handle()
async def handle_guess_giveup(bot: Bot, event: Union[GroupMessageEvent, PrivateMessageEvent]) -> None:
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

    PICTURES_DIR.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    cropped_path = PICTURES_DIR / f"{session_id}.png"
    image.save(cropped_path)

    await guess_giveup.finish(
        MessageSegment.text(f"你放弃了！答案是：{answer}。")
        + MessageSegment.image(cropped_path),
        at_sender=True,
    )


@guess.handle()
async def handle_guess(bot: Bot, event: Union[GroupMessageEvent, PrivateMessageEvent], arg: Message = CommandArg()) -> None:
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

    if guess_input in aliases.get(answer, []):
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

    PICTURES_DIR.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
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
        file_names: list[str] = []
        while not file_names:
            map_info = random.choice(maps)
            folder_path = DATA_DIR / map_info["file_path"]
            file_names = await _list_files(folder_path)

        file_name = random.choice(file_names)
        image_path = folder_path / file_name # type: ignore  # noqa: PGH003
        image = Image.open(image_path)
        width, height = image.size
        left = random.randint(0, width - crop_width)
        top = random.randint(0, height - crop_height)
        right = left + crop_width
        bottom = top + crop_height
        cropped_image = image.crop((left, top, right, bottom))

        PICTURES_DIR.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        cropped_path = PICTURES_DIR / "test.png"
        cropped_image.save(cropped_path)
        await guess_test.send(
            MessageSegment.image(cropped_path)
            + MessageSegment.text(str(get_variance(cropped_image)))
        )
    await guess_test.finish()


@guess_removecooldown.handle()
async def handle_guess_removecooldown(event: Union[GroupMessageEvent, PrivateMessageEvent]) -> None:
    session_id = getid(event)
    r.set(f"{COOLDOWN_PREFIX}{session_id}", "removed", ex=1)
    await guess_removecooldown.finish("已经移除你（或你所在群）的生成题目cd！")


@guess_cheat.handle()
async def handle_guess_cheat(bot: Bot, event: Union[GroupMessageEvent, PrivateMessageEvent]) -> None:
    session_id = getid(event)
    answer = r.hget(ANSWER_KEY, session_id)
    await bot.call_api(
        "send_private_msg",
        user_id=event.user_id,
        message=[{"type": "text", "data": {"text": str(session_id) + str(answer)}}],
    )
    await guess_cheat.finish()
