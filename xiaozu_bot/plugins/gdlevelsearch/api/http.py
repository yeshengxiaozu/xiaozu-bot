"""Shared HTTP clients and bounded retry policies for gdlevelsearch."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import httpx
from nonebot import logger

from ..constants import (
    HTTP_RETRY_ATTEMPTS,
    HTTP_RETRY_BACKOFF,
    HTTP_RETRY_STATUS_CODES,
    USER_AGENT,
)

_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
_CLIENT_LIMITS = httpx.Limits(
    max_connections=32,
    max_keepalive_connections=16,
    keepalive_expiry=30,
)


class RemoteServiceError(RuntimeError):
    """Base class for transport failures that callers may handle."""


class ServiceUnavailable(httpx.HTTPError, RemoteServiceError):
    """The request could not succeed after the configured retry budget."""


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = HTTP_RETRY_ATTEMPTS
    backoff: float = HTTP_RETRY_BACKOFF
    retry_statuses: frozenset[int] = HTTP_RETRY_STATUS_CODES


DEFAULT_POLICY = RetryPolicy()

_sync_client_lock = threading.Lock()
_sync_client: httpx.Client | None = None
_sync_custom_clients: set[httpx.Client] = set()
_async_clients: dict[int, httpx.AsyncClient] = {}
_async_client_lock = asyncio.Lock()


def _new_sync_client(
    *, verify: str | bool = True, trust_env: bool = True
) -> httpx.Client:
    return httpx.Client(
        verify=verify,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        limits=_CLIENT_LIMITS,
        trust_env=trust_env,
    )


def _get_sync_client() -> httpx.Client:
    global _sync_client
    if _sync_client is None:
        with _sync_client_lock:
            if _sync_client is None:
                _sync_client = _new_sync_client()
    return _sync_client


async def _get_async_client() -> httpx.AsyncClient:
    loop_id = id(asyncio.get_running_loop())
    client = _async_clients.get(loop_id)
    if client is None:
        async with _async_client_lock:
            client = _async_clients.get(loop_id)
            if client is None:
                client = httpx.AsyncClient(
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                    limits=_CLIENT_LIMITS,
                )
                _async_clients[loop_id] = client
    return client


class RequestSession:
    """Requests-like facade backed by one reusable httpx client."""

    def __init__(
        self, *, verify: str | bool = True, trust_env: bool = True
    ) -> None:
        self._client = _new_sync_client(verify=verify, trust_env=trust_env)
        with _sync_client_lock:
            _sync_custom_clients.add(self._client)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        timeout = kwargs.pop("timeout", None)
        if timeout is None:
            raise TypeError("RequestSession.get() missing required 'timeout'")
        return request("GET", url, timeout=timeout, client=self._client, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        timeout = kwargs.pop("timeout", None)
        if timeout is None:
            raise TypeError("RequestSession.post() missing required 'timeout'")
        return request("POST", url, timeout=timeout, client=self._client, **kwargs)

    def close(self) -> None:
        with _sync_client_lock:
            _sync_custom_clients.discard(self._client)
        self._client.close()


def _should_retry(method: str, allow_retry: bool) -> bool:
    return allow_retry or method.upper() in _IDEMPOTENT_METHODS


def request(
    method: str,
    url: str,
    *,
    timeout: float,
    policy: RetryPolicy = DEFAULT_POLICY,
    retry_statuses: Iterable[int] | None = None,
    allow_retry: bool = False,
    client: httpx.Client | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Perform a bounded synchronous request using a reusable httpx client."""
    method = method.upper()
    statuses = frozenset(retry_statuses or policy.retry_statuses)
    attempts = max(1, policy.attempts if _should_retry(method, allow_retry) else 1)
    transport = client or _get_sync_client()
    last_error: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = transport.request(method, url, timeout=timeout, **kwargs)
        except httpx.HTTPError as exc:
            last_error = exc
            logger.warning(
                f"[http] {method} {url} transport failure "
                f"{attempt}/{attempts}: {type(exc).__name__}"
            )
        else:
            if response.status_code not in statuses:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}")
            logger.warning(
                f"[http] {method} {url} retryable status "
                f"{response.status_code} ({attempt}/{attempts})"
            )

        if attempt < attempts:
            time.sleep(policy.backoff * attempt)

    raise ServiceUnavailable(
        f"{method} {url} failed after {attempts} attempts"
    ) from last_error


async def async_request(
    method: str,
    url: str,
    *,
    timeout: float,
    policy: RetryPolicy = DEFAULT_POLICY,
    retry_statuses: Iterable[int] | None = None,
    retry_if: Callable[[httpx.Response], bool] | None = None,
    allow_retry: bool = False,
    **kwargs: Any,
) -> httpx.Response:
    """Perform a bounded async request using a reusable httpx client."""
    method = method.upper()
    statuses = frozenset(retry_statuses or policy.retry_statuses)
    should_retry = _should_retry(method, allow_retry) or retry_if is not None
    attempts = max(1, policy.attempts if should_retry else 1)
    client = await _get_async_client()
    last_error: BaseException | None = None

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
                f"[http] {method} {url} transport failure "
                f"{attempt}/{attempts}: {type(exc).__name__}"
            )
        else:
            retry_response = response.status_code in statuses or (
                retry_if is not None and retry_if(response)
            )
            if not retry_response:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}")
            logger.warning(
                f"[http] {method} {url} retryable status "
                f"{response.status_code} ({attempt}/{attempts})"
            )

            if attempt == attempts:
                break

        if attempt < attempts:
            await asyncio.sleep(policy.backoff * attempt)

    raise ServiceUnavailable(
        f"{method} {url} failed after {attempts} attempts"
    ) from last_error


def close_sync_client() -> None:
    """Close shared and custom synchronous clients."""
    global _sync_client
    with _sync_client_lock:
        clients = list(_sync_custom_clients)
        _sync_custom_clients.clear()
        if _sync_client is not None:
            clients.append(_sync_client)
            _sync_client = None
    for client in clients:
        client.close()


async def close_async_clients() -> None:
    """Close all async clients associated with active event loops."""
    async with _async_client_lock:
        clients = list(_async_clients.values())
        _async_clients.clear()
    for client in clients:
        await client.aclose()
