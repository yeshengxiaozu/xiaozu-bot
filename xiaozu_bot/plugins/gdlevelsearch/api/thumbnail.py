"""Thumbnail lookup and retry policy, kept separate from PIL layout code."""

from __future__ import annotations

from nonebot import logger

from ..api.http import RetryPolicy, async_request
from ..constants import (
    HTTP_NOT_FOUND,
    HTTP_OK,
    HTTP_RETRY_ATTEMPTS,
    HTTP_TIMEOUT,
    USER_AGENT,
)

THUMB_RETRIES = HTTP_RETRY_ATTEMPTS
THUMB_BACKOFF = 0.6


async def _none() -> None:
    """Placeholder coroutine for gather branches without a thumbnail."""
    return


def _thumbnail_id_for(level_id: int | None) -> str:
    """Map official level IDs to the thumbnail service's legacy IDs."""
    if level_id is None:
        return ""
    if 0 <= level_id <= 3:
        return ["0", "14", "18", "20"][level_id]
    return str(level_id)


async def _fetch_url(url: str, headers: dict[str, str]) -> bytes | None:
    """Fetch one thumbnail URL with bounded retries and a 404 fast path."""
    try:
        response = await async_request(
            "GET",
            url,
            timeout=HTTP_TIMEOUT,
            headers=headers,
            policy=RetryPolicy(attempts=THUMB_RETRIES, backoff=THUMB_BACKOFF),
            retry_if=lambda resp: resp.status_code == HTTP_OK and not resp.content,
        )
    except Exception as exc:
        logger.warning(f"[thumbnail] exhausted retries: {url}: {exc}")
        return None

    if response.status_code == HTTP_NOT_FOUND:
        logger.info(f"[thumbnail] no thumbnail (404): {url}")
        return None
    if response.status_code == HTTP_OK and response.content:
        return response.content
    logger.warning(f"[thumbnail] non-retryable HTTP {response.status_code}: {url}")
    return None


async def _fetch_thumbnail(thumbnail_id: str) -> bytes | None:
    """Fetch a thumbnail, retrying without the /medium suffix on failure."""
    headers = {"User-Agent": USER_AGENT, "Accept": "image/webp,image/*;q=0.8"}
    base_url = f"https://levelthumbs.prevter.me/thumbnail/{thumbnail_id}"

    content = await _fetch_url(f"{base_url}/medium", headers)
    if content is not None:
        return content

    logger.info(f"[thumbnail] retrying without /medium: {thumbnail_id}")
    return await _fetch_url(base_url, headers)

async def fetch_thumbnail(level_id: int|str) -> bytes | None:
    return await _fetch_thumbnail(_thumbnail_id_for(int(level_id)))
