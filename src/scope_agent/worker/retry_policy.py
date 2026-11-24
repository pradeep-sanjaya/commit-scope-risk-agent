from datetime import datetime, timedelta


class RetryPolicy:
    def __init__(self, backoff_seconds: list[int]):
        self._backoff = list(backoff_seconds)

    def next_attempt_at(self, attempts: int, now: datetime) -> datetime | None:
        """`attempts` counts the attempt that just failed; None once the schedule is exhausted."""
        if attempts < 1 or attempts > len(self._backoff):
            return None
        return now + timedelta(seconds=self._backoff[attempts - 1])
