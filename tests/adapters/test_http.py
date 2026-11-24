import httpx
import respx

from scope_agent.adapters.http import RetryTransport, build_http_client


@respx.mock
def test_http_retries_503_then_succeeds():
    route = respx.get("https://x.test/a").mock(
        side_effect=[httpx.Response(503), httpx.Response(429), httpx.Response(200, json={"ok": 1})]
    )
    sleeps = []
    client = build_http_client("https://x.test", 5.0, sleep=sleeps.append)
    r = client.get("/a")
    assert r.status_code == 200
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]


@respx.mock
def test_http_does_not_retry_404():
    route = respx.get("https://x.test/a").mock(return_value=httpx.Response(404))
    sleeps = []
    client = build_http_client("https://x.test", 5.0, sleep=sleeps.append)
    assert client.get("/a").status_code == 404
    assert route.call_count == 1
    assert sleeps == []


@respx.mock
def test_http_gives_up_after_three_tries():
    route = respx.get("https://x.test/a").mock(return_value=httpx.Response(500))
    client = build_http_client("https://x.test", 5.0, sleep=lambda s: None)
    assert client.get("/a").status_code == 500
    assert route.call_count == 3


def test_headers_and_base_url_applied():
    client = build_http_client("https://x.test", 5.0, headers={"Authorization": "Bearer t"})
    assert client.headers["Authorization"] == "Bearer t"
    assert str(client.base_url).startswith("https://x.test")
    assert isinstance(client._transport, RetryTransport)


def test_build_http_client_follow_redirects_flag():
    from scope_agent.adapters.http import build_http_client
    assert build_http_client("https://x.test", 5, follow_redirects=True).follow_redirects is True
    assert build_http_client("https://x.test", 5).follow_redirects is False
