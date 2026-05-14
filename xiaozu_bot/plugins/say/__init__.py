import asyncio
import base64
import os
from pathlib import Path
from typing import Union

import requests
from nonebot import get_plugin_config, on_command, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="say",
    description="",
    usage="",
    config=Config,
)


def json_group_audio(group_id: int, path: str) -> set:
    return {
        "group_id": group_id,
        "message": [{"type": "record", "data": {"file": path}}],
    }


def json_private_audio(user_id: int, path: str) -> set:
    return {"user_id": user_id, "message": [{"type": "record", "data": {"file": path}}]}


config = get_plugin_config(Config)

say = on_command("say")
say_instructed = on_command("say_i")

from mlx_audio.tts.generate import generate_audio
from mlx_audio.tts.utils import load_model

_MODEL = None  # 线程安全，因为 mlx 模型在推理时是只读的


def get_model():
    global _MODEL
    if _MODEL is None:
        _MODEL = load_model("mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16")
    return _MODEL


def sync_generate_audio(text: str, instruct: str | None, output_dir: str) -> str:
    """同步执行的 TTS 生成，返回音频文件路径"""
    import os

    # 注意：这里假设 generate_audio 会写入文件并返回文件路径
    # 请根据实际 API 调整
    file_name = f"audio_{os.getpid()}_{id(text)}.wav"  # 避免多线程冲突
    file_path = os.path.join(output_dir, file_name)

    generate_audio(
        model=get_model(),
        text=text,
        instruct=instruct,
        file_prefix=file_name[:-4],
        path=output_dir,
        join_audio=True,
    )
    return file_path


@say.handle()
async def handle_function(
    bot: Bot,
    event: Union[GroupMessageEvent, PrivateMessageEvent],
    arg: Message = CommandArg(),
):
    if isinstance(event, GroupMessageEvent) and event.group_id == 569801410:
        await bot.call_api(
            "set_msg_emoji_like", message_id=event.message_id, emoji_id="424"
        )
        await say.finish()
    text = arg.extract_plain_text().strip()
    if len(text) == 0:
        await say.finish("你得在say后面加点东西……")
    if len(text) > 500:
        await bot.call_api(
            "set_msg_emoji_like", message_id=event.message_id, emoji_id="424"
        )
        await say.finish("请善待小小卒！")
    # 准备参数
    instruct = "体现稚嫩撒娇的少女声线，说话有点含糊有点夹子音，音色有点沙有点糊，语速较快而有活力，营造刻意卖萌又有点搞怪的听觉效果。"
    output_dir = os.getcwd()

    # 使用 asyncio.to_thread 将同步阻塞任务扔到线程池
    try:
        file_path = await asyncio.to_thread(
            sync_generate_audio, text, instruct, output_dir
        )
    except Exception as e:
        await say.finish(f"生成音频失败: {e}")
    if isinstance(event, GroupMessageEvent):
        requests.post(
            "http://localhost:3000/send_group_msg",
            json=json_group_audio(event.group_id, file_path),
        )
    else:
        requests.post(
            "http://localhost:3000/send_private_msg",
            json=json_private_audio(event.user_id, file_path),
        )
    os.remove(file_path)
    await say.finish()


@say_instructed.handle()
async def handle_function(
    bot: Bot,
    event: Union[GroupMessageEvent, PrivateMessageEvent],
    arg: Message = CommandArg(),
):
    if event.get_user_id() not in ["3251605531", "2638056139"]:
        await bot.call_api(
            "set_msg_emoji_like", message_id=event.message_id, emoji_id="424"
        )
        return
    # 把指令参数转换成文本和指令参数分开
    parts = str(arg).split(maxsplit=1)
    text = parts[1] if len(parts) > 1 else ""
    instruct = parts[0] if len(parts) > 0 else ""
    if len(text) == 0:
        await say_instructed.finish(
            "两个参数，第一个参数是指令参数，第二个参数是文本内容哦！"
        )
    if len(text) > 1000:
        await bot.call_api(
            "set_msg_emoji_like", message_id=event.message_id, emoji_id="424"
        )
        await say_instructed.finish("请善待小小卒！")
    # 准备参数
    output_dir = os.getcwd()

    # 使用 asyncio.to_thread 将同步阻塞任务扔到线程池
    try:
        file_path = await asyncio.to_thread(
            sync_generate_audio, text, instruct, output_dir
        )
    except Exception as e:
        await say.finish(f"生成音频失败: {e}")
    if isinstance(event, GroupMessageEvent):
        requests.post(
            "http://localhost:3000/send_group_msg",
            json=json_group_audio(event.group_id, file_path),
        )
    else:
        requests.post(
            "http://localhost:3000/send_private_msg",
            json=json_private_audio(event.user_id, file_path),
        )
    os.remove(file_path)
    await say_instructed.finish("")
