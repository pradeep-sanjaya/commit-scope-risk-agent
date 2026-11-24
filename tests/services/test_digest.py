from datetime import datetime, timedelta, timezone

import pytest

from scope_agent.services.digest import DigestService
from tests.notify.helpers import finding, links

NOW = datetime(2026, 3, 5, 8, tzinfo=timezone.utc)


class Findings:
    def __init__(self):
        self.window = None

    def since(self, s, e):
        self.window = (s, e)
        return [finding()]

    def errors_between(self, s, e):
        return 3


class Jobs:
    def count_by_status(self):
        return {"deferred": 4}


class Meta:
    def __init__(self, v=None):
        self.d = {"last_digest_at": v} if v else {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v


class Rec:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, msg, to):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append((msg, to))


def _svc(meta, sender, f=None):
    return DigestService(f or Findings(), Jobs(), meta, sender, links(), ["pmo@x.io"], lambda: NOW)


def test_digest_window_from_meta_and_updates_meta():
    last = NOW - timedelta(hours=5)
    f, meta, rec = Findings(), Meta(last.isoformat()), Rec()
    _svc(meta, rec, f).run()
    assert f.window == (last, NOW)
    assert meta.d["last_digest_at"] == NOW.isoformat()
    (msg, to), = rec.sent
    assert to == ["pmo@x.io"]
    body = msg.get_body(("plain",)).get_content()
    assert "Analysis errors: 3" in body and "Deferred (budget): 4" in body


def test_digest_default_window_24h():
    f = Findings()
    _svc(Meta(), Rec(), f).run()
    assert f.window == (NOW - timedelta(hours=24), NOW)


def test_digest_failure_keeps_meta():
    meta = Meta("2026-03-04T08:00:00+00:00")
    with pytest.raises(RuntimeError):
        _svc(meta, Rec(fail=True)).run()
    assert meta.d["last_digest_at"] == "2026-03-04T08:00:00+00:00"
