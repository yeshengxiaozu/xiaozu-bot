"""Contract tests for the OneBot V11 / QQ adapter compatibility layer."""

from types import SimpleNamespace
from typing import Any

import nonebot
import pytest
from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import (
    C2CMessageCreateEvent,
    GroupMessageCreateEvent,
    MessageCreateEvent,
)
from nonebot.adapters.qq import Message as QQMessage
from nonebot.adapters.qq.config import BotInfo
from nonebot.drivers import Request

from xiaozu_bot.utils import adapter_compat as compat


class TestQQAdapter(QQAdapter):
    __test__ = False

    def setup(self) -> None:
        pass


def test_message_scene_helpers_cover_both_adapters(
    make_group_event, make_private_event
) -> None:
    onebot_group = make_group_event()
    onebot_private = make_private_event()
    qq_group = GroupMessageCreateEvent.model_construct(
        id="group-message-1", group_openid="group-openid-1"
    )
    qq_private = C2CMessageCreateEvent.model_construct(
        id="private-message-1",
        author=SimpleNamespace(id="user-openid-1", user_openid="user-openid-1"),
    )

    assert compat.is_group_event(onebot_group)
    assert compat.is_private_event(onebot_private)
    assert compat.is_group_event(qq_group)
    assert compat.is_private_event(qq_private)
    assert not compat.is_group_event(onebot_private)
    assert not compat.is_private_event(onebot_group)


class RecordingQQBot(QQBot):
    def __init__(self) -> None:
        super().__init__(
            TestQQAdapter(nonebot.get_driver()),
            "test-qq",
            BotInfo(id="test-qq", token="", secret="test-secret"),
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sent: list[tuple[Any, Any]] = []

    async def call_api(self, api: str, **data: Any) -> Any:
        self.calls.append((api, data))
        if api == "post_group_messages":
            return {"id": "sent-group-message"}
        return None

    async def send(self, event: Any, message: Any, **kwargs: Any) -> Any:
        self.sent.append((event, message))
        return {"id": "sent-reply"}


class TransportRecordingQQBot(QQBot):
    def __init__(self) -> None:
        super().__init__(
            TestQQAdapter(nonebot.get_driver()),
            "test-qq-transport",
            BotInfo(id="test-qq-transport", token="", secret="test-secret"),
        )
        self.requests: list[Request] = []

    async def _request(self, request: Request) -> Any:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/upload_prepare"):
            return {
                "upload_id": "upload-1",
                "block_size": 1024,
                "parts": [
                    {
                        "index": 0,
                        "presigned_url": "https://upload.example.invalid/part-0",
                    }
                ],
                "upload_config": {
                    "concurrency": 1,
                    "retry_timeout": 5,
                    "retry_delay": 0,
                },
            }
        if path.endswith("/files"):
            return {"file_uuid": "file-1", "file_info": "file-info-1", "ttl": 300}
        if path.endswith("/messages"):
            return {"id": "sent-message-1"}
        return None


class APICallRecordingQQBot(QQBot):
    def __init__(self) -> None:
        super().__init__(
            TestQQAdapter(nonebot.get_driver()),
            "test-qq-api",
            BotInfo(id="test-qq-api", token="", secret="test-secret"),
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_api(self, api: str, **data: Any) -> Any:
        self.calls.append((api, data))
        return None


def test_onebot_image_message_uses_onebot_segment(fake_bot, tmp_path) -> None:
    path = tmp_path / "image.png"
    path.write_bytes(b"not-read-by-onebot")

    message = compat.build_image_message(fake_bot, path, before="before", after="after")

    assert [segment.type for segment in message] == ["text", "image", "text"]
    assert message.extract_plain_text() == "beforeafter"


def test_qq_image_message_uses_local_attachment(tmp_path) -> None:
    bot = RecordingQQBot()
    path = tmp_path / "image.png"
    path.write_bytes(b"png-bytes")

    message = compat.build_image_message(bot, path, before="caption")

    assert isinstance(message, QQMessage)
    assert [segment.type for segment in message] == ["text", "file_image"]
    assert message[-1].data["content"] == b"png-bytes"
    assert message[-1].data["file_name"] == "image.png"


def test_qq_audio_message_uses_local_attachment(tmp_path) -> None:
    bot = RecordingQQBot()
    path = tmp_path / "voice.wav"
    path.write_bytes(b"wav-bytes")

    message = compat.build_audio_message(bot, path)

    assert isinstance(message, QQMessage)
    assert message[0].type == "file_audio"
    assert message[0].data["content"] == b"wav-bytes"


async def test_onebot_reaction_keeps_original_api(fake_bot, make_group_event) -> None:
    event = make_group_event(message_id=123)

    assert await compat.react(fake_bot, event, "424") is True
    assert fake_bot.calls[-1] == (
        "set_msg_emoji_like",
        {"message_id": 123, "emoji_id": "424"},
    )


async def test_qq_channel_reaction_uses_official_api() -> None:
    bot = RecordingQQBot()
    event = MessageCreateEvent.model_construct(id="message-1", channel_id="channel-1")

    assert await compat.react(bot, event, "10068") is True
    assert bot.calls[-1] == (
        "put_message_reaction",
        {
            "channel_id": "channel-1",
            "message_id": "message-1",
            "type": 2,
            "id": "10068",
        },
    )


async def test_qq_proactive_group_target_is_routed() -> None:
    bot = RecordingQQBot()

    await compat.send_group(bot, "qq:group:group-openid", "hello")

    api, data = bot.calls[-1]
    assert api == "post_group_messages"
    assert data["group_openid"] == "group-openid"
    assert data["content"] == "hello"


async def test_qq_group_reaction_degrades_without_api() -> None:
    bot = RecordingQQBot()

    class GroupEvent:
        id = "message-1"

    assert await compat.react(bot, GroupEvent(), "424") is False  # type: ignore[arg-type]
    assert bot.calls == []


@pytest.mark.parametrize(
    ("scene", "media_kind", "expected_file_type"),
    [
        ("group", "image", 1),
        ("group", "audio", 3),
        ("c2c", "image", 1),
        ("c2c", "audio", 3),
    ],
)
async def test_qq_openid_local_media_uses_upload_then_message(
    tmp_path, scene: str, media_kind: str, expected_file_type: int
) -> None:
    compat.install_qq_rich_media_compat()
    bot = TransportRecordingQQBot()
    suffix = "png" if media_kind == "image" else "wav"
    path = tmp_path / f"media.{suffix}"
    path.write_bytes(b"local-media")
    if scene == "group":
        event = GroupMessageCreateEvent.model_construct(
            id="incoming-1",
            group_id="group-1",
            group_openid="group-openid-1",
            author=SimpleNamespace(id="member-1", member_openid="member-openid-1"),
            message_scene=None,
        )
    else:
        event = C2CMessageCreateEvent.model_construct(
            id="incoming-1",
            author=SimpleNamespace(id="user-openid-1", user_openid="user-openid-1"),
            message_scene=None,
        )

    if media_kind == "image":
        await compat.send_image(bot, event, path, before="caption")
    else:
        await compat.send_audio(bot, event, path)

    assert [request.url.path.rsplit("/", 1)[-1] for request in bot.requests] == [
        "upload_prepare",
        "part-0",
        "upload_part_finish",
        "files",
        "messages",
    ]
    api_requests = [bot.requests[index] for index in (0, 2, 3, 4)]
    merge_request = api_requests[-2]
    assert merge_request.json == {
        "file_type": expected_file_type,
        "srv_send_msg": False,
        "file_name": path.name,
        "upload_id": "upload-1",
    }
    message_request = api_requests[-1]
    assert message_request.json["msg_type"] == 7
    assert message_request.json["media"] == {"file_info": "file-info-1"}
    assert message_request.json["msg_id"] == "incoming-1"


async def test_qq_channel_image_keeps_channel_multipart_route(tmp_path) -> None:
    bot = APICallRecordingQQBot()
    path = tmp_path / "channel.png"
    path.write_bytes(b"channel-image")
    event = MessageCreateEvent.model_construct(
        id="incoming-channel-1",
        channel_id="channel-1",
        guild_id="guild-1",
        author=SimpleNamespace(id="channel-user-1"),
    )

    await compat.send_image(bot, event, path, before="caption")

    assert [api for api, _ in bot.calls] == ["post_messages"]
    _, data = bot.calls[0]
    assert data["channel_id"] == "channel-1"
    assert data["content"] == "caption"
    assert data["file_image"] == b"channel-image"
