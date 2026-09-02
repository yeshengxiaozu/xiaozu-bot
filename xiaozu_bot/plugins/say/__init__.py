import asyncio
import os
import re
import wave
from contextlib import suppress
from pathlib import Path

from nonebot import get_plugin_config, on_command
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from xiaozu_bot.utils.adapter_compat import (
    get_group_id,
    get_user_id,
    is_group_event,
    react,
    send_audio,
)

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="say",
    description="",
    usage="",
    config=Config,
)


def json_group_audio(group_id: int, path: str) -> dict:
    return {
        "group_id": group_id,
        "message": [{"type": "record", "data": {"file": path}}],
    }


def json_private_audio(user_id: int, path: str) -> dict:
    return {"user_id": user_id, "message": [{"type": "record", "data": {"file": path}}]}


config = get_plugin_config(Config)

say = on_command("say")
say_instructed = on_command("say_i")

# mlx_audio 只在 Apple Silicon 上装得了，所以延迟到真正要用的时候再 import。
# 这样在没装它的机器上，say 插件本身还是能正常加载，只是 say 指令用不了。
_MODEL = None  # 线程安全，因为 mlx 模型在推理时是只读的
TTS_CHUNK_LENGTH = 200
_TTS_BREAKS = re.compile(r"(?<=[。！？.!?；;，,\n ])")


def split_text(text: str, max_length: int = TTS_CHUNK_LENGTH) -> list[str]:
    """Split text without dropping characters, preferring natural breaks."""
    if max_length <= 0:
        raise ValueError("max_length must be positive")

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_length, len(text))
        if end < len(text):
            candidates = list(_TTS_BREAKS.finditer(text[start:end]))
            if candidates:
                end = start + candidates[-1].end()
        chunks.append(text[start:end])
        start = end
    return chunks


def get_model():
    global _MODEL
    if _MODEL is None:
        from mlx_audio.tts.utils import load_model

        _MODEL = load_model("mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16")
    return _MODEL


def _generated_path(
    result: object, output_dir: Path, file_prefix: str
) -> Path:
    """Resolve the WAV path produced by mlx_audio."""
    expected = output_dir / f"{file_prefix}.wav"
    if expected.exists():
        return expected
    if isinstance(result, (str, Path)):
        returned = Path(result)
        if returned.exists():
            return returned
    matches = sorted(output_dir.glob(f"{file_prefix}*.wav"))
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(f"mlx_audio did not create {expected}")


def _join_wav_files(parts: list[Path], output: Path) -> None:
    """Concatenate PCM WAV files and reject incompatible audio formats."""
    with wave.open(str(parts[0]), "rb") as first:
        params = first.getparams()
        format_signature = (
            first.getnchannels(),
            first.getsampwidth(),
            first.getframerate(),
            first.getcomptype(),
        )
        with wave.open(str(output), "wb") as joined:
            joined.setparams(params)
            joined.writeframes(first.readframes(first.getnframes()))
            for part in parts[1:]:
                with wave.open(str(part), "rb") as current:
                    current_signature = (
                        current.getnchannels(),
                        current.getsampwidth(),
                        current.getframerate(),
                        current.getcomptype(),
                    )
                    if current_signature != format_signature:
                        raise ValueError("mlx_audio returned incompatible WAV files")
                    joined.writeframes(current.readframes(current.getnframes()))


def sync_generate_audio(text: str, instruct: str | None, output_dir: str) -> str:
    """Generate all text chunks and return one concatenated WAV path."""
    from mlx_audio.tts.generate import generate_audio

    output = Path(output_dir)
    base_name = f"audio_{os.getpid()}_{id(text)}"
    parts: list[Path] = []
    final_path = output / f"{base_name}.wav"
    chunks = split_text(text)
    try:
        for index, chunk in enumerate(chunks):
            prefix = base_name if len(chunks) == 1 else f"{base_name}_{index}"
            result = generate_audio(
                model=get_model(),
                text=chunk,
                instruct=instruct,
                file_prefix=prefix,
                path=str(output),
                join_audio=True,
            )
            parts.append(_generated_path(result, output, prefix))

        if len(parts) > 1:
            _join_wav_files(parts, final_path)
        return str(parts[0] if len(parts) == 1 else final_path)
    except Exception:
        with suppress(OSError):
            final_path.unlink()
        raise
    finally:
        for part in parts:
            if part != final_path:
                with suppress(OSError):
                    part.unlink()


@say.handle()
async def handle_function(
    bot: Bot,
    event: Event,
    arg: Message = CommandArg(),
):
    if is_group_event(event) and get_group_id(event) == "569801410":
        await react(bot, event, "424")
        await say.finish()
    text = arg.extract_plain_text().strip()
    if len(text) == 0:
        await say.finish("你得在say后面加点东西……")
    if len(text) > 500 and get_user_id(event) != "3251605531":
        await react(bot, event, "424")
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
    try:
        await send_audio(bot, event, file_path)
    except Exception as e:
        await say.finish(f"发送音频失败: {e}")
    finally:
        with suppress(OSError):
            os.remove(file_path)
    await say.finish()


@say_instructed.handle()
async def handle_function(
    bot: Bot,
    event: Event,
    arg: Message = CommandArg(),
):
    if event.get_user_id() not in ["3251605531", "2638056139"]:
        await react(bot, event, "424")
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
        await react(bot, event, "424")
        await say_instructed.finish("请善待小小卒！")
    # 准备参数
    output_dir = os.getcwd()

    # 使用 asyncio.to_thread 将同步阻塞任务扔到线程池
    try:
        file_path = await asyncio.to_thread(
            sync_generate_audio, text, instruct, output_dir
        )
    except Exception as e:
        await say_instructed.finish(f"生成音频失败: {e}")
    try:
        await send_audio(bot, event, file_path)
    except Exception as e:
        await say_instructed.finish(f"发送音频失败: {e}")
    finally:
        with suppress(OSError):
            os.remove(file_path)
    await say_instructed.finish("")
