from scope_agent.persistence.commits import CommitLedger


def test_ledger(db):
    led = CommitLedger(db)
    assert not led.is_analyzed("PAY/r", "abc")
    led.mark_analyzed("PAY/r", ["abc", "def"])
    led.mark_analyzed("PAY/r", ["abc"])  # idempotent
    assert led.is_analyzed("PAY/r", "abc") and led.is_analyzed("PAY/r", "def")
    assert not led.is_analyzed("OTHER/r", "abc")
