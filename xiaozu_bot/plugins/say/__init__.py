import asyncio
import os
import re
import threading
import uuid
import wave
from contextlib import suppress
from pathlib import Path

from nonebot import get_plugin_config, logger, on_command
from nonebot.internal.adapter import Bot, Event, Message
from nonebot.matcher import Matcher
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
_MODEL = None
# 加载模型必须加锁：推理时只读只保证推理安全，不保证**初始化**安全。
# 两个用户同时第一次触发 *say 时都会看到 _MODEL is None，然后各加载一份
# 1.7B 的 bf16 模型（数 GB），后写的覆盖先写的。
_MODEL_LOCK = threading.Lock()
# TTS 是重 CPU 活，一次推理要好几秒。不设上限的话几个人连发 *say 就会把
# 默认线程池占满，连 guess 的 `asyncio.to_thread` 裁图都跟着排队卡住。
_TTS_SEMAPHORE = asyncio.Semaphore(1)
# 单次合成的硬超时，避免一个卡死的请求永远占着上面的信号量。
TTS_TIMEOUT = 120
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
    # 双检锁：第一次加载要走网络/磁盘，几秒钟，期间别的线程也会进来。
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                from mlx_audio.tts.utils import load_model

                _MODEL = load_model(
                    "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16"
                )
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
    # 不能用 id(text)：那是字符串对象的内存地址，对象被回收后地址会被新对象
    # 复用，长文本切块时会一次派生出多个文件，撞名就把上一段覆盖掉了。
    base_name = f"audio_{os.getpid()}_{uuid.uuid4().hex}"
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


async def _speak(
    matcher: Matcher, bot: Bot, event: Event, text: str, instruct: str | None
) -> None:
    """合成一段语音并发出去，中间产物无论成败都清掉。

    三个容易踩的点都在这里收口：
    - TTS 是重 CPU 活，用信号量 + 超时卡住，否则几个人连发 *say 就会把默认
      线程池占满，连 guess 的裁图都跟着排队。
    - file_path 先初始化成 None。下面每个提前退出的分支都靠 finish() 抛
      FinishedException 离开，一旦它不抛（比如被桩掉），就会带着未绑定的
      名字走到 finally 里，把真正的错误盖成一个 UnboundLocalError。
    - 给用户看的文案里不带 {e}：异常信息里有机器的绝对路径，不该发到群里，
      细节一律只进日志。
    """
    file_path: str | None = None
    try:
        async with _TTS_SEMAPHORE:
            file_path = await asyncio.wait_for(
                asyncio.to_thread(sync_generate_audio, text, instruct, os.getcwd()),
                timeout=TTS_TIMEOUT,
            )
    except TimeoutError:
        logger.warning("say: 合成超过 %s 秒，放弃", TTS_TIMEOUT)
        await matcher.finish("合成超时了，换短一点再试试")
    except Exception:
        logger.exception("say: 生成音频失败")
        await matcher.finish("生成音频失败，请稍后再试")
    try:
        await send_audio(bot, event, file_path)
    except Exception:
        logger.exception("say: 发送音频失败")
        await matcher.finish("发送音频失败，请稍后再试")
    finally:
        if file_path:
            with suppress(OSError):
                os.remove(file_path)


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

    await _speak(say, bot, event, text, instruct)
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
    await _speak(say_instructed, bot, event, text, instruct)
    await say_instructed.finish("")
