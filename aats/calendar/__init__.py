"""Exchange session calendar.

All session maths goes through `SessionCalendar`; never hard-code a UTC offset, because US daylight
saving and early closes change the mapping (design section 7.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from importlib import resources
from typing import Protocol
from zoneinfo import ZoneInfo

import yaml

from aats.domain.decisions import SessionWindow

__all__ = ["CalendarCoverageError", "SessionCalendar", "StaticNyseCalendar", "default_calendar"]


class CalendarCoverageError(LookupError):
    """Raised for dates outside the calendar's verified coverage. Never guess."""


class SessionCalendar(Protocol):
    tz: ZoneInfo

    def is_session(self, d: date) -> bool: ...

    def session(self, d: date) -> SessionWindow: ...

    def to_exchange_time(self, ts: datetime) -> datetime: ...


@dataclass(frozen=True)
class StaticNyseCalendar:
    tz: ZoneInfo
    coverage_start: date
    coverage_end: date
    regular_open: time
    regular_close: time
    early_close_time: time
    holidays: frozenset[date]
    early_closes: frozenset[date]

    def _check(self, d: date) -> None:
        if not (self.coverage_start <= d <= self.coverage_end):
            raise CalendarCoverageError(
                f"{d} outside calendar coverage {self.coverage_start}..{self.coverage_end}"
            )

    def is_session(self, d: date) -> bool:
        self._check(d)
        return d.weekday() < 5 and d not in self.holidays

    def session(self, d: date) -> SessionWindow:
        if not self.is_session(d):
            raise ValueError(f"{d} is not an exchange session")
        early = d in self.early_closes
        open_local = datetime.combine(d, self.regular_open, tzinfo=self.tz)
        close_local = datetime.combine(
            d, self.early_close_time if early else self.regular_close, tzinfo=self.tz
        )
        return SessionWindow(
            session_date=d.isoformat(), open_utc=open_local, close_utc=close_local, early_close=early
        )

    def to_exchange_time(self, ts: datetime) -> datetime:
        return ts.astimezone(self.tz)

    def sessions_between(self, start: date, end: date) -> list[date]:
        out, d = [], start
        while d <= end:
            if self.is_session(d):
                out.append(d)
            d += timedelta(days=1)
        return out


def _t(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


@lru_cache(maxsize=1)
def default_calendar() -> StaticNyseCalendar:
    raw = yaml.safe_load(resources.files("aats.calendar").joinpath("nyse_2025_2027.yaml").read_text("utf-8"))
    return StaticNyseCalendar(
        tz=ZoneInfo(raw["timezone"]),
        coverage_start=date.fromisoformat(raw["coverage_start"]),
        coverage_end=date.fromisoformat(raw["coverage_end"]),
        regular_open=_t(raw["regular_open"]),
        regular_close=_t(raw["regular_close"]),
        early_close_time=_t(raw["early_close"]),
        holidays=frozenset(date.fromisoformat(x) for x in raw["holidays"]),
        early_closes=frozenset(date.fromisoformat(x) for x in raw["early_closes"]),
    )
