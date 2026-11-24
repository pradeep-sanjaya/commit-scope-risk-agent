import time
from collections.abc import Callable

import httpx

MAX_TRIES = 3
BACKOFF_BASE_SECONDS = 0.5


def _retryable(status: int) -> bool:
    return status == 429 or 500 <= status < 600


class RetryTransport(httpx.BaseTransport):
    """Wraps a transport; retries 5xx and 429 with exponential backoff (0.5, 1, 2 s)."""

    def __init__(self, inner: httpx.BaseTransport, sleep: Callable[[float], None] = time.sleep):
        self._inner = inner
        self._sleep = sleep

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(MAX_TRIES):
            response = self._inner.handle_request(request)
            if not _retryable(response.status_code) or attempt == MAX_TRIES - 1:
                return response
            response.close()
            self._sleep(BACKOFF_BASE_SECONDS * (2**attempt))
        raise AssertionError("unreachable")

    def close(self) -> None:
        self._inner.close()


def build_http_client(
    base_url: str,
    timeout: float,
    auth: httpx.Auth | None = None,
    headers: dict | None = None,
    follow_redirects: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        timeout=timeout,
        auth=auth,
        headers=headers,
        follow_redirects=follow_redirects,
        transport=RetryTransport(httpx.HTTPTransport(), sleep=sleep),
    )
