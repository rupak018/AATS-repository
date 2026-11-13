"""Cross-check the committed NYSE calendar file against exchange_calendars (installed in CI)."""

from __future__ import annotations

from datetime import date

import pytest

from aats.calendar import default_calendar

xcals = pytest.importorskip("exchange_calendars")
pytestmark = pytest.mark.integration


def test_sessions_and_closes_match_exchange_calendars() -> None:
    ours = default_calendar()
    ref = xcals.get_calendar("XNYS", start="2025-01-01", end="2027-12-31")
    ref_sessions = {ts.date() for ts in ref.sessions}
    our_sessions = set(ours.sessions_between(date(2025, 1, 1), date(2027, 12, 31)))
    assert our_sessions == ref_sessions, (
        f"only ours: {sorted(our_sessions - ref_sessions)}; only reference: {sorted(ref_sessions - our_sessions)}"
    )
    for d in sorted(our_sessions):
        s = ours.session(d)
        assert s.open_utc == ref.session_open(d.isoformat()).to_pydatetime(), d
        assert s.close_utc == ref.session_close(d.isoformat()).to_pydatetime(), d
