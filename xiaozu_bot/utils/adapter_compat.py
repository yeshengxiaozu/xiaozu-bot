"""Adapter-neutral messaging helpers for OneBot V11 and QQ Official Bot."""

from __future__ import annotations

import asyncio
import hashlib
from base64 import b64encode
from collections.abc import Iterable, Mapping
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

from nonebot import logger
from nonebot.adapters import (
    Event,  # noqa: TC002 - NoneBot resolves matcher annotations at runtime.
)
from nonebot.adapters.onebot.v11 import (
    Bot as OneBotV11Bot,
)
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent as OneBotV11GroupMessageEvent,
)
from nonebot.adapters.onebot.v11 import (
    Message as OneBotV11Message,
)
from nonebot.adapters.onebot.v11 import (
    MessageEvent as OneBotV11MessageEvent,
)
from nonebot.adapters.onebot.v11 import (
    MessageSegment as OneBotV11MessageSegment,
)
from nonebot.adapters.onebot.v11 import (
    PrivateMessageEvent as OneBotV11PrivateMessageEvent,
)
from nonebot.adapters.qq import (
    AtMessageCreateEvent as QQAtMessageCreateEvent,
)
from nonebot.adapters.qq import (
    Bot as QQBot,
)
from nonebot.adapters.qq import (
    C2CMessageCreateEvent as QQC2CMessageCreateEvent,
)
from nonebot.adapters.qq import (
    DirectMessageCreateEvent as QQDirectMessageCreateEvent,
)
from nonebot.adapters.qq import (
    GroupMessageCreateEvent as QQGroupMessageCreateEvent,
)
from nonebot.adapters.qq import (
    Message as QQMessage,
)
from nonebot.adapters.qq import (
    MessageCreateEvent as QQMessageCreateEvent,
)
from nonebot.adapters.qq import (
    MessageSegment as QQMessageSegment,
)
from nonebot.adapters.qq.models.qq import (
    PostC2CFilesReturn,
    PostGroupFilesReturn,
)
from nonebot.adapters.qq.utils import API
from nonebot.compat import type_validate_python
from nonebot.drivers import Request
from nonebot.permission import Permission

if TYPE_CHECKING:
    from nonebot.adapters import Bot, Message

GroupMessageEvent: TypeAlias = (
    OneBotV11GroupMessageEvent
    | QQGroupMessageCreateEvent
    | QQMessageCreateEvent
    | QQAtMessageCreateEvent
)
PrivateMessageEvent: TypeAlias = (
    OneBotV11PrivateMessageEvent | QQC2CMessageCreateEvent | QQDirectMessageCreateEvent
)
MessageEvent: TypeAlias = GroupMessageEvent | PrivateMessageEvent

GROUP_MESSAGE_EVENTS = (
    OneBotV11GroupMessageEvent,
    QQGroupMessageCreateEvent,
    QQMessageCreateEvent,
    QQAtMessageCreateEvent,
)
PRIVATE_MESSAGE_EVENTS = (
    OneBotV11PrivateMessageEvent,
    QQC2CMessageCreateEvent,
    QQDirectMessageCreateEvent,
)


class AdapterFeatureUnsupported(RuntimeError):
    """Raised when an adapter has no safe equivalent for an operation."""


QQFileType: TypeAlias = Literal[1, 2, 3, 4]
_QQ_ORIGINAL_EXTRACT_MEDIA = QQBot._extract_qq_media


def _qq_extract_media(message: QQMessage) -> dict[str, Any]:
    data = _QQ_ORIGINAL_EXTRACT_MEDIA(message)
    if "file_data" not in data or "file_name" in data:
        return data

    for segment_type in ("file_image", "file_video", "file_audio", "file_file"):
        if segments := message[segment_type]:
            data["file_name"] = segments[-1].data.get("file_name") or "default"
            break
    return data


async def _qq_post_c2c_files(
    bot: QQBot,
    *,
    openid: str,
    file_type: QQFileType | None = None,
    url: str | None = None,
    srv_send_msg: bool = True,
    file_data: str | bytes | None = None,
    file_name: str | None = None,
    upload_id: str | None = None,
) -> PostC2CFilesReturn:
    data = _qq_files_request_data(
        file_type=file_type,
        url=url,
        srv_send_msg=srv_send_msg,
        file_data=file_data,
        file_name=file_name,
        upload_id=upload_id,
    )
    request = Request(
        "POST",
        bot.adapter.get_api_base().joinpath("v2", "users", openid, "files"),
        json=data,
    )
    return type_validate_python(PostC2CFilesReturn, await bot._request(request))


async def _qq_post_group_files(
    bot: QQBot,
    *,
    group_openid: str,
    file_type: QQFileType | None = None,
    url: str | None = None,
    srv_send_msg: bool = True,
    file_data: str | bytes | None = None,
    file_name: str | None = None,
    upload_id: str | None = None,
) -> PostGroupFilesReturn:
    data = _qq_files_request_data(
        file_type=file_type,
        url=url,
        srv_send_msg=srv_send_msg,
        file_data=file_data,
        file_name=file_name,
        upload_id=upload_id,
    )
    request = Request(
        "POST",
        bot.adapter.get_api_base().joinpath(
            "v2", "groups", group_openid, "files"
        ),
        json=data,
    )
    return type_validate_python(PostGroupFilesReturn, await bot._request(request))


def _qq_files_request_data(
    *,
    file_type: QQFileType | None,
    url: str | None,
    srv_send_msg: bool,
    file_data: str | bytes | None,
    file_name: str | None,
    upload_id: str | None,
) -> dict[str, Any]:
    if upload_id is None and file_type is None:
        raise ValueError("file_type must be provided if upload_id is not provided")
    if isinstance(file_data, bytes):
        file_data = b64encode(file_data).decode()
    return {
        key: value
        for key, value in {
            "file_type": file_type,
            "url": url,
            "srv_send_msg": srv_send_msg,
            "file_data": file_data,
            "file_name": file_name,
            "upload_id": upload_id,
        }.items()
        if value is not None
    }


async def _qq_local_upload(
    bot: QQBot,
    *,
    scene: Literal["c2c", "group"],
    target: str,
    file_type: QQFileType,
    file_name: str,
    file_data: bytes,
    srv_send_msg: bool,
) -> PostC2CFilesReturn | PostGroupFilesReturn:
    target_key = "openid" if scene == "c2c" else "group_openid"
    prepare = await getattr(bot, f"post_{scene}_upload_prepare")(
        **{
            target_key: target,
            "file_type": file_type,
            "file_name": file_name,
            "file_size": len(file_data),
            "md5": hashlib.md5(file_data).hexdigest(),
            "sha1": hashlib.sha1(file_data).hexdigest(),
            "md5_10m": hashlib.md5(file_data[:10_002_432]).hexdigest(),
        }
    )
    semaphore = asyncio.Semaphore(prepare.upload_config.concurrency)
    upload_part = getattr(bot, f"_{scene}_upload_part")
    tasks = [
        asyncio.create_task(
            upload_part(
                target,
                prepare.upload_id,
                part.index,
                part.presigned_url,
                file_data[i * prepare.block_size : (i + 1) * prepare.block_size],
                semaphore,
            )
        )
        for i, part in enumerate(prepare.parts)
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()

    return await getattr(bot, f"post_{scene}_files")(
        **{
            target_key: target,
            "file_type": file_type,
            "file_name": file_name,
            "srv_send_msg": srv_send_msg,
            "upload_id": prepare.upload_id,
        }
    )


async def _qq_post_c2c_upload(
    bot: QQBot,
    openid: str,
    file_type: QQFileType,
    file_name: str,
    file_data: bytes,
    srv_send_msg: bool = True,
) -> PostC2CFilesReturn:
    result = await _qq_local_upload(
        bot,
        scene="c2c",
        target=openid,
        file_type=file_type,
        file_name=file_name,
        file_data=file_data,
        srv_send_msg=srv_send_msg,
    )
    return type_validate_python(PostC2CFilesReturn, result)


async def _qq_post_group_upload(
    bot: QQBot,
    group_openid: str,
    file_type: QQFileType,
    file_name: str,
    file_data: bytes,
    srv_send_msg: bool = True,
) -> PostGroupFilesReturn:
    result = await _qq_local_upload(
        bot,
        scene="group",
        target=group_openid,
        file_type=file_type,
        file_name=file_name,
        file_data=file_data,
        srv_send_msg=srv_send_msg,
    )
    return type_validate_python(PostGroupFilesReturn, result)


def install_qq_rich_media_compat() -> None:
    """Keep QQ local uploads two-stage until adapter-qq implements the v2 body."""
    if getattr(QQBot, "_xiaozu_rich_media_compat", False):
        return

    for name, function in (
        ("post_c2c_files", _qq_post_c2c_files),
        ("post_group_files", _qq_post_group_files),
    ):
        descriptor = API(function)
        descriptor.__set_name__(QQBot, name)
        setattr(QQBot, name, descriptor)

    QQBot._extract_qq_media = staticmethod(_qq_extract_media)
    QQBot.post_c2c_upload = _qq_post_c2c_upload
    QQBot.post_group_upload = _qq_post_group_upload
    QQBot._xiaozu_rich_media_compat = True


async def _group_permission(event: Event) -> bool:
    return is_group_event(event)


GROUP = Permission(_group_permission)


def is_onebot_v11(bot: Bot) -> bool:
    return isinstance(bot, OneBotV11Bot)


def is_qq(bot: Bot) -> bool:
    return isinstance(bot, QQBot)


def is_group_event(event: Event) -> bool:
    return isinstance(event, GROUP_MESSAGE_EVENTS)


def is_private_event(event: Event) -> bool:
    return isinstance(event, PRIVATE_MESSAGE_EVENTS)


def get_user_id(event: Event) -> str:
    return event.get_user_id()


def get_group_id(event: Event) -> str:
    if isinstance(event, OneBotV11GroupMessageEvent):
        return str(event.group_id)
    if isinstance(event, QQGroupMessageCreateEvent):
        return event.group_openid
    if isinstance(event, (QQMessageCreateEvent, QQAtMessageCreateEvent)):
        return event.channel_id
    raise AdapterFeatureUnsupported(f"{type(event).__name__} is not a group event")


def get_group_target(event: Event) -> str:
    if isinstance(event, OneBotV11GroupMessageEvent):
        return str(event.group_id)
    if isinstance(event, QQGroupMessageCreateEvent):
        return f"qq:group:{event.group_openid}"
    if isinstance(event, (QQMessageCreateEvent, QQAtMessageCreateEvent)):
        return f"qq:channel:{event.channel_id}"
    raise AdapterFeatureUnsupported(f"{type(event).__name__} is not a group event")


def get_context_id(event: Event) -> str:
    """Return a stable, adapter-qualified key for per-chat state."""
    if isinstance(event, OneBotV11GroupMessageEvent):
        return f"ob11:group:{event.group_id}"
    if isinstance(event, OneBotV11PrivateMessageEvent):
        return f"ob11:private:{event.user_id}"
    if isinstance(event, QQGroupMessageCreateEvent):
        return f"qq:group:{event.group_openid}"
    if isinstance(event, (QQMessageCreateEvent, QQAtMessageCreateEvent)):
        return f"qq:channel:{event.channel_id}"
    if isinstance(event, QQC2CMessageCreateEvent):
        return f"qq:c2c:{event.get_user_id()}"
    if isinstance(event, QQDirectMessageCreateEvent):
        return f"qq:dms:{event.guild_id}"
    return f"{event.get_event_name()}:{event.get_session_id()}"


def get_message_id(event: Event) -> str | int:
    if isinstance(event, OneBotV11MessageEvent):
        return event.message_id
    message_id = getattr(event, "id", None)
    if message_id is None:
        raise AdapterFeatureUnsupported(f"{type(event).__name__} has no message id")
    return str(message_id)


def get_user_name(event: Event) -> str:
    sender = getattr(event, "sender", None)
    if sender is not None:
        return str(
            getattr(sender, "card", None)
            or getattr(sender, "nickname", None)
            or get_user_id(event)
        )
    author = getattr(event, "author", None)
    if author is not None:
        return str(
            getattr(author, "username", None)
            or getattr(author, "id", None)
            or get_user_id(event)
        )
    return get_user_id(event)


def extract_sent_message_id(result: Any) -> str | int | None:
    if isinstance(result, Mapping):
        value = result.get("message_id") or result.get("id")
    else:
        value = getattr(result, "message_id", None) or getattr(result, "id", None)
    return value


ImageSource: TypeAlias = bytes | BytesIO | Path | str
AudioSource: TypeAlias = bytes | BytesIO | Path | str


def _onebot_file(source: ImageSource | AudioSource) -> Any:
    if isinstance(source, Path):
        return source
    return source


def _qq_local_file(source: ImageSource | AudioSource) -> bytes | BytesIO | Path:
    if isinstance(source, str):
        return Path(source)
    return source


def build_image_message(
    bot: Bot,
    image: ImageSource,
    *,
    before: str = "",
    after: str = "",
    mention_user_id: str | None = None,
) -> Message:
    if isinstance(bot, OneBotV11Bot):
        message = OneBotV11Message()
        if mention_user_id is not None:
            message.append(OneBotV11MessageSegment.at(mention_user_id))
        if before:
            message.append(OneBotV11MessageSegment.text(before))
        message.append(OneBotV11MessageSegment.image(_onebot_file(image)))
        if after:
            message.append(OneBotV11MessageSegment.text(after))
        return message

    if isinstance(bot, QQBot):
        message = QQMessage()
        if mention_user_id is not None:
            message.append(QQMessageSegment.mention_user(mention_user_id))
        if before:
            message.append(QQMessageSegment.text(before))
        if isinstance(image, str) and image.startswith(("http://", "https://")):
            message.append(QQMessageSegment.image(image))
        else:
            message.append(QQMessageSegment.file_image(_qq_local_file(image)))
        if after:
            message.append(QQMessageSegment.text(after))
        return message

    raise AdapterFeatureUnsupported(
        f"image sending is unsupported by {type(bot).__name__}"
    )


def build_audio_message(bot: Bot, audio: AudioSource) -> Message:
    if isinstance(bot, OneBotV11Bot):
        return OneBotV11Message(OneBotV11MessageSegment.record(_onebot_file(audio)))
    if isinstance(bot, QQBot):
        if isinstance(audio, str) and audio.startswith(("http://", "https://")):
            return QQMessage(QQMessageSegment.audio(audio))
        return QQMessage(QQMessageSegment.file_audio(_qq_local_file(audio)))
    raise AdapterFeatureUnsupported(
        f"audio sending is unsupported by {type(bot).__name__}"
    )


async def send_image(
    bot: Bot,
    event: Event,
    image: ImageSource,
    *,
    before: str = "",
    after: str = "",
    at_sender: bool = False,
) -> Any:
    mention = get_user_id(event) if at_sender else None
    message = build_image_message(
        bot, image, before=before, after=after, mention_user_id=mention
    )
    return await bot.send(event, message)


async def send_audio(bot: Bot, event: Event, audio: AudioSource) -> Any:
    return await bot.send(event, build_audio_message(bot, audio))


async def send_group(bot: Bot, group_id: str | int, message: Any) -> Any:
    target = str(group_id)
    if isinstance(bot, OneBotV11Bot):
        return await bot.call_api("send_group_msg", group_id=group_id, message=message)
    if isinstance(bot, QQBot):
        if target.startswith("qq:group:"):
            return await bot.send_to_group(
                group_openid=target.removeprefix("qq:group:"), message=message
            )
        if target.startswith("qq:channel:"):
            return await bot.send_to_channel(
                channel_id=target.removeprefix("qq:channel:"), message=message
            )
        raise AdapterFeatureUnsupported(
            "QQ proactive group targets must use qq:group: or qq:channel:"
        )
    raise AdapterFeatureUnsupported(
        f"group sending is unsupported by {type(bot).__name__}"
    )


async def send_group_any(
    bots: Mapping[str, Bot], group_id: str | int, message: Any
) -> Any:
    target = str(group_id)
    expected = QQBot if target.startswith("qq:") else OneBotV11Bot
    bot = next((item for item in bots.values() if isinstance(item, expected)), None)
    if bot is None:
        raise AdapterFeatureUnsupported(
            f"no connected {expected.__name__} can send to {target}"
        )
    return await send_group(bot, target, message)


async def send_private(bot: Bot, user_id: str | int, message: Any) -> Any:
    target = str(user_id)
    if isinstance(bot, OneBotV11Bot):
        return await bot.call_api("send_private_msg", user_id=user_id, message=message)
    if isinstance(bot, QQBot):
        if target.startswith("qq:c2c:"):
            return await bot.send_to_c2c(
                openid=target.removeprefix("qq:c2c:"), message=message
            )
        if target.startswith("qq:dms:"):
            return await bot.send_to_dms(
                guild_id=target.removeprefix("qq:dms:"), message=message
            )
        raise AdapterFeatureUnsupported(
            "QQ proactive private targets must use qq:c2c: or qq:dms:"
        )
    raise AdapterFeatureUnsupported(
        f"private sending is unsupported by {type(bot).__name__}"
    )


async def send_private_for_event(bot: Bot, event: Event, message: Any) -> Any:
    if isinstance(event, (OneBotV11GroupMessageEvent, OneBotV11PrivateMessageEvent)):
        return await send_private(bot, event.get_user_id(), message)
    if isinstance(event, QQC2CMessageCreateEvent):
        return await send_private(bot, f"qq:c2c:{event.get_user_id()}", message)
    if isinstance(event, QQDirectMessageCreateEvent):
        return await send_private(bot, f"qq:dms:{event.guild_id}", message)
    raise AdapterFeatureUnsupported(
        "QQ group member openids cannot be used as C2C private-message targets"
    )


async def send_forward(
    bot: Bot,
    event: Event,
    messages: Iterable[str],
    *,
    title: str = "",
) -> Any:
    parts = [str(part) for part in messages]
    if isinstance(bot, OneBotV11Bot) and isinstance(event, OneBotV11GroupMessageEvent):
        nodes = [
            {
                "type": "node",
                "data": {"name": title or "消息", "uin": bot.self_id, "content": part},
            }
            for part in parts
        ]
        return await bot.call_api(
            "send_group_forward_msg", group_id=event.group_id, messages=nodes
        )
    text = (title + "\n" if title else "") + "\n".join(parts)
    return await bot.send(event, text)


async def react(
    bot: Bot,
    event: Event,
    emoji_id: str | int,
    *,
    message_id: str | int | None = None,
) -> bool:
    """React when supported; return False for platforms/scenes without an API."""
    target_message_id = message_id if message_id is not None else get_message_id(event)
    if isinstance(bot, OneBotV11Bot):
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=target_message_id,
            emoji_id=str(emoji_id),
        )
        return True

    if isinstance(bot, QQBot) and isinstance(
        event, (QQMessageCreateEvent, QQAtMessageCreateEvent)
    ):
        numeric_id = int(emoji_id)
        await bot.put_message_reaction(
            channel_id=event.channel_id,
            message_id=str(target_message_id),
            type=2 if numeric_id > 9000 else 1,
            id=str(numeric_id),
        )
        return True

    logger.debug(
        "Adapter %s does not support reactions for %s",
        type(bot).__name__,
        type(event).__name__,
    )
    return False


async def poke(bot: Bot, event: Event, user_id: str | int) -> bool:
    group_id = getattr(event, "group_id", None)
    if isinstance(bot, OneBotV11Bot) and group_id is not None:
        await bot.call_api("group_poke", group_id=group_id, user_id=user_id)
        return True
    return False
