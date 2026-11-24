from scope_agent.domain.models import Severity
from scope_agent.notify.push_email import render_push, should_send_push
from tests.notify.helpers import finding, links, result, summary


def test_should_send_push_rules():
    assert not should_send_push(result([finding(severity=Severity.info)]))
    assert should_send_push(result([finding(severity=Severity.info)], {"c": [summary()]}))
    assert should_send_push(result([finding(severity=Severity.low)]))


def test_push_subject_order_links():
    r = result([finding("lo", Severity.low, "LOWTITLE"), finding("inf", Severity.info, "INFOTITLE"),
                finding("hi", Severity.high, "HIGHTITLE")], {"c": [summary()]})
    msg = render_push(r, links())
    assert msg["Subject"] == "[Scope Agent] 3 item(s) for review: PAY/gateway development (1111111..9999999)"
    assert msg["From"] is None
    text = msg.get_body(("plain",)).get_content()
    assert text.index("HIGHTITLE") < text.index("LOWTITLE")
    assert "INFOTITLE" not in text
    assert "/rate/hi?v=useful&t=" in text and "/rate/hi?v=noise&t=" in text
    assert "/browse/PAY-12" in text
    assert "lead@x.io" in text and "because reasons" in text
    assert "Library changes" in text and "bumped" in text
    assert text.index("Library changes") < text.index("Nothing has been blocked.")
    html = msg.get_body(("html",)).get_content()
    assert "/rate/hi?v=useful&amp;t=" in html and "Nothing has been blocked." in html


def test_push_html_escapes_untrusted():
    r = result([finding(title="<script>x</script>", evidence=("<b>e</b>",))])
    msg = render_push(r, links())
    html = msg.get_body(("html",)).get_content()
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert "<b>e</b>" not in html
    assert "<script>x</script>" in msg.get_body(("plain",)).get_content()
