"""Shared retrying HTTP primitives for gdlevelsearch.

The callers still decide whether a failed source should fall back, return an
empty result, or surface an error.  This module only owns transport policy:
timeouts, retryable statuses, backoff, and consistent logging.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import httpx
import requests
from nonebot import logger

from ..constants import (
    HTTP_RETRY_ATTEMPTS,
    HTTP_RETRY_BACKOFF,
    HTTP_RETRY_STATUS_CODES,
)


class RemoteServiceError(RuntimeError):
    """Base class for transport failures that callers may handle."""


class ServiceUnavailable(RemoteServiceError, requests.RequestException):
    """The request could not succeed after the configured retry budget."""


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = HTTP_RETRY_ATTEMPTS
    backoff: float = HTTP_RETRY_BACKOFF
    retry_statuses: frozenset[int] = HTTP_RETRY_STATUS_CODES


DEFAULT_POLICY = RetryPolicy()


class RequestSession:
    """Minimal requests-like facade so legacy callers can share one policy.

    `session.get(...)` / `session.post(...)` map onto :func:`request` with the
    same retry, backoff, timeout, and logging behavior as the rest of the
    plugin while keeping a single object to swap in tests.
    """

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        timeout = kwargs.pop("timeout", None)
        if timeout is None:
            raise TypeError("RequestSession.get() missing required 'timeout'")
        return request("GET", url, timeout=timeout, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        timeout = kwargs.pop("timeout", None)
        if timeout is None:
            raise TypeError("RequestSession.post() missing required 'timeout'")
        return request("POST", url, timeout=timeout, **kwargs)


def request(
    method: str,
    url: str,
    *,
    timeout: float,
    policy: RetryPolicy = DEFAULT_POLICY,
    retry_statuses: Iterable[int] | None = None,
    **kwargs: Any,
) -> requests.Response:
    """Perform a bounded synchronous request with shared retry semantics."""
    statuses = frozenset(retry_statuses or policy.retry_statuses)
    attempts = max(1, policy.attempts)
    last_error: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(
                method,
                url,
                timeout=timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            last_error = exc
            logger.warning(
                f"[http] {method.upper()} {url} transport failure "
                f"{attempt}/{attempts}: {type(exc).__name__}"
            )
        else:
            if response.status_code not in statuses:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}")
            logger.warning(
                f"[http] {method.upper()} {url} retryable status "
                f"{response.status_code} ({attempt}/{attempts})"
            )

        if attempt < attempts:
            time.sleep(policy.backoff * attempt)

    raise ServiceUnavailable(
        f"{method.upper()} {url} failed after {attempts} attempts"
    ) from last_error


async def async_request(
    method: str,
    url: str,
    *,
    timeout: float,
    policy: RetryPolicy = DEFAULT_POLICY,
    retry_statuses: Iterable[int] | None = None,
    retry_if: Callable[[httpx.Response], bool] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Async counterpart of :func:`request` for httpx-based callers.

    ``retry_if`` allows transport-level callers (e.g. an empty 200 body) to
    opt into the same bounded retry loop without duplicating backoff logic.
    """
    statuses = frozenset(retry_statuses or policy.retry_statuses)
    attempts = max(1, policy.attempts)
    last_error: BaseException | None = None

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.request(
                    method,
                    url,
                    timeout=timeout,
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    f"[http] {method.upper()} {url} transport failure "
                    f"{attempt}/{attempts}: {type(exc).__name__}"
                )
            else:
                should_retry = (
                    response.status_code in statuses
                    or (retry_if is not None and retry_if(response))
                )
                if not should_retry:
                    return response
                last_error = RuntimeError(f"HTTP {response.status_code}")
                logger.warning(
                    f"[http] {method.upper()} {url} retryable status "
                    f"{response.status_code} ({attempt}/{attempts})"
                )

            if attempt < attempts:
                await asyncio.sleep(policy.backoff * attempt)

    raise ServiceUnavailable(
        f"{method.upper()} {url} failed after {attempts} attempts"
    ) from last_error
