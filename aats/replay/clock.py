"""Replay clock. Business logic reads time only from a clock object, never from datetime.now()."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class ReplayClock:
    """Monotonic simulated clock driven by event timestamps."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("clock start must be timezone-aware")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def advance_to(self, ts: datetime) -> datetime:
        """Move forward to `ts`; late (earlier) timestamps never move the clock backwards."""
        ts = ts.astimezone(UTC)
        self._now = max(self._now, ts)
        return self._now


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
