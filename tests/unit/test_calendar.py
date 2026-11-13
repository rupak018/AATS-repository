from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from aats.calendar import CalendarCoverageError, default_calendar

cal = default_calendar()


@pytest.mark.parametrize(
    ("d", "open_utc", "close_utc", "early"),
    [
        (
            date(2026, 10, 30),
            datetime(2026, 10, 30, 13, 30, tzinfo=UTC),
            datetime(2026, 10, 30, 20, 0, tzinfo=UTC),
            False,
        ),
        # first session after US DST ends (1 Nov 2026): open shifts to 14:30 UTC
        (
            date(2026, 11, 2),
            datetime(2026, 11, 2, 14, 30, tzinfo=UTC),
            datetime(2026, 11, 2, 21, 0, tzinfo=UTC),
            False,
        ),
        # day after Thanksgiving: 13:00 ET early close
        (
            date(2026, 11, 27),
            datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
            datetime(2026, 11, 27, 18, 0, tzinfo=UTC),
            True,
        ),
        # US DST starts 8 Mar 2026
        (
            date(2026, 3, 6),
            datetime(2026, 3, 6, 14, 30, tzinfo=UTC),
            datetime(2026, 3, 6, 21, 0, tzinfo=UTC),
            False,
        ),
        (
            date(2026, 3, 9),
            datetime(2026, 3, 9, 13, 30, tzinfo=UTC),
            datetime(2026, 3, 9, 20, 0, tzinfo=UTC),
            False,
        ),
    ],
)
def test_session_windows_across_dst_and_early_close(
    d: date, open_utc: datetime, close_utc: datetime, early: bool
) -> None:
    s = cal.session(d)
    assert (s.open_utc, s.close_utc, s.early_close) == (open_utc, close_utc, early)


@pytest.mark.parametrize("d", [date(2026, 11, 26), date(2026, 7, 3), date(2026, 4, 3), date(2026, 10, 24)])
def test_non_sessions(d: date) -> None:
    assert not cal.is_session(d)
    with pytest.raises(ValueError, match="not an exchange session"):
        cal.session(d)


def test_trading_day_count_2026() -> None:
    # 261 weekdays - 10 weekday holidays
    assert len(cal.sessions_between(date(2026, 1, 1), date(2026, 12, 31))) == 251


def test_outside_coverage_never_guesses() -> None:
    with pytest.raises(CalendarCoverageError):
        cal.is_session(date(2028, 1, 3))
