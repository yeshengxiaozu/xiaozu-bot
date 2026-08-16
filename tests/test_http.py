"""gdlevelsearch/api/http.py 的共享请求策略。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import requests

from xiaozu_bot.plugins.gdlevelsearch.api.http import (
    RequestSession,
    RetryPolicy,
    ServiceUnavailable,
    async_request,
    request,
)


class TestRequest:
    def test_retries_then_succeeds(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        responses = [
            make_response(503),
            make_response(200, json_data={"ok": True}),
        ]
        seen: list[dict[str, Any]] = []

        def _route(**call: Any) -> Any:
            seen.append(call)
            return responses[len(seen) - 1]

        stub_requests.get("https://example.test/a", _route)

        response = request("GET", "https://example.test/a", timeout=5)

        assert response.json() == {"ok": True}
        assert len(stub_requests.calls) == 2
        assert all(call["timeout"] == 5 for call in stub_requests.calls)

    def test_retryable_status_exhaustion_raises(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get("https://example.test/a", make_response(429))

        with pytest.raises(ServiceUnavailable):
            request("GET", "https://example.test/a", timeout=5)

        assert len(stub_requests.calls) == 3

    def test_non_retryable_status_returns_immediately(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get("https://example.test/a", make_response(404))

        response = request("GET", "https://example.test/a", timeout=5)

        assert response.status_code == 404
        assert len(stub_requests.calls) == 1

    def test_transport_exception_retries_then_raises(
        self, stub_requests: Any
    ) -> None:
        stub_requests.get("https://example.test/a", requests.Timeout("boom"))

        with pytest.raises(ServiceUnavailable):
            request(
                "GET",
                "https://example.test/a",
                timeout=5,
                policy=RetryPolicy(backoff=0),
            )

        assert len(stub_requests.calls) == 3

    def test_custom_retry_statuses_override_default(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        responses = [
            make_response(418),
            make_response(200, json_data={"ok": True}),
        ]
        seen: list[dict[str, Any]] = []

        def _route(**call: Any) -> Any:
            seen.append(call)
            return responses[len(seen) - 1]

        stub_requests.get("https://example.test/a", _route)

        response = request(
            "GET",
            "https://example.test/a",
            timeout=5,
            retry_statuses={418},
        )

        assert response.json() == {"ok": True}
        assert len(stub_requests.calls) == 2


class TestRequestSession:
    def test_get_forwards_params_and_timeout(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.get(
            "https://example.test/search",
            make_response(200, json_data={"hits": []}),
        )
        session = RequestSession()

        response = session.get(
            "https://example.test/search",
            params={"q": "x"},
            timeout=7,
        )

        assert response.json() == {"hits": []}
        call = stub_requests.calls[-1]
        assert call["timeout"] == 7
        assert call["params"] == {"q": "x"}

    def test_post_forwards_data(
        self, stub_requests: Any, make_response: Any
    ) -> None:
        stub_requests.post(
            "https://example.test/upload",
            make_response(200, json_data={"ok": True}),
        )
        session = RequestSession()

        response = session.post(
            "https://example.test/upload",
            data={"name": "x"},
            timeout=7,
        )

        assert response.json() == {"ok": True}
        call = stub_requests.calls[-1]
        assert call["timeout"] == 7
        assert call["data"] == {"name": "x"}

    def test_get_requires_timeout(self) -> None:
        with pytest.raises(TypeError, match="timeout"):
            RequestSession().get("https://example.test/a")

    def test_post_requires_timeout(self) -> None:
        with pytest.raises(TypeError, match="timeout"):
            RequestSession().post("https://example.test/a")


class TestAsyncRequest:
    async def test_retries_then_succeeds(self, stub_httpx: Any) -> None:
        responses = [
            httpx.Response(503),
            httpx.Response(200, content=b"ok"),
        ]
        seen: list[Any] = []

        def _route(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return responses[len(seen) - 1]

        stub_httpx.get("https://example.test/a", _route)

        response = await async_request(
            "GET",
            "https://example.test/a",
            timeout=5,
            policy=RetryPolicy(backoff=0),
        )

        assert response.content == b"ok"
        assert len(seen) == 2

    async def test_empty_200_retries_via_predicate(self, stub_httpx: Any) -> None:
        responses = [
            httpx.Response(200, content=b""),
            httpx.Response(200, content=b"data"),
        ]
        seen: list[Any] = []

        def _route(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return responses[len(seen) - 1]

        stub_httpx.get("https://example.test/a", _route)

        response = await async_request(
            "GET",
            "https://example.test/a",
            timeout=5,
            policy=RetryPolicy(backoff=0),
            retry_if=lambda resp: resp.status_code == 200 and not resp.content,
        )

        assert response.content == b"data"
        assert len(seen) == 2

    async def test_exhaustion_raises(self, stub_httpx: Any) -> None:
        stub_httpx.get("https://example.test/a", httpx.Response(503))

        with pytest.raises(ServiceUnavailable):
            await async_request(
                "GET",
                "https://example.test/a",
                timeout=5,
                policy=RetryPolicy(backoff=0),
            )

        assert len(stub_httpx.requests) == 3

    async def test_transport_exception_retries_then_raises(
        self, stub_httpx: Any
    ) -> None:
        stub_httpx.get("https://example.test/a", httpx.ConnectTimeout("boom"))

        with pytest.raises(ServiceUnavailable):
            await async_request(
                "GET",
                "https://example.test/a",
                timeout=5,
                policy=RetryPolicy(backoff=0),
            )

        assert len(stub_httpx.requests) == 3

    async def test_404_returns_immediately(self, stub_httpx: Any) -> None:
        stub_httpx.get("https://example.test/a", httpx.Response(404))

        response = await async_request(
            "GET",
            "https://example.test/a",
            timeout=5,
        )

        assert response.status_code == 404
        assert len(stub_httpx.requests) == 1


class TestRetryPolicy:
    def test_defaults_come_from_constants(self) -> None:
        policy = RetryPolicy()
        assert policy.attempts == 3
        assert policy.backoff == 0.5
        assert 429 in policy.retry_statuses
        assert 500 in policy.retry_statuses
        assert 502 in policy.retry_statuses
        assert 503 in policy.retry_statuses
        assert 504 in policy.retry_statuses
