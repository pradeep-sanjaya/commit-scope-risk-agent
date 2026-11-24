import httpx
import respx

from scope_agent.adapters.web_pages import WebPageFetcher


@respx.mock
def test_html_stripped_to_text():
    html = (
        "<html><head><style>p{color:red}</style><script>var x=1;</script></head>"
        "<body><h1>Title</h1>\n<p>Hello   <b>world</b></p></body></html>"
    )
    respx.get("https://docs.test/page").respond(200, text=html)
    assert WebPageFetcher(httpx.Client()).text("https://docs.test/page") == "Title Hello world"


@respx.mock
def test_non_200_is_none():
    respx.get("https://docs.test/gone").respond(404)
    respx.get("https://docs.test/err").respond(500)
    f = WebPageFetcher(httpx.Client())
    assert f.text("https://docs.test/gone") is None
    assert f.text("https://docs.test/err") is None
