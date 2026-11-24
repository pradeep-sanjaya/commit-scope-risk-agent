from datetime import datetime, timedelta, timezone

from scope_agent.worker.retry_policy import RetryPolicy

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_retry_policy_schedule_and_exhaustion():
    p = RetryPolicy([30, 120, 600])
    assert p.next_attempt_at(1, NOW) == NOW + timedelta(seconds=30)
    assert p.next_attempt_at(2, NOW) == NOW + timedelta(seconds=120)
    assert p.next_attempt_at(3, NOW) == NOW + timedelta(seconds=600)
    assert p.next_attempt_at(4, NOW) is None
