from datetime import date

from scope_agent.persistence.cache import KeyValueCache
from scope_agent.persistence.meta import MetaStore
from scope_agent.persistence.usage import TokenUsageMeter


def test_cache_ttl(db, clock):
    c = KeyValueCache(db, clock)
    c.put("ns", "k", "v", ttl_seconds=1)
    assert c.get("ns", "k") == "v"
    clock.advance(seconds=2)
    assert c.get("ns", "k") is None


def test_cache_permanent_and_overwrite_and_namespaces(db, clock):
    c = KeyValueCache(db, clock)
    c.put("ns", "k", "v1", None)
    c.put("ns", "k", "v2", None)
    c.put("other", "k", "o", None)
    clock.advance(days=900)
    assert c.get("ns", "k") == "v2" and c.get("other", "k") == "o"


def test_token_usage_sums_per_day(db):
    m = TokenUsageMeter(db)
    d = date(2026, 1, 1)
    m.add(d, 100)
    m.add(d, 50)
    assert m.used(d) == 150 and m.used(date(2026, 1, 2)) == 0


def test_meta(db):
    m = MetaStore(db)
    assert m.get("k") is None
    m.set("k", "1")
    m.set("k", "2")
    assert m.get("k") == "2"
