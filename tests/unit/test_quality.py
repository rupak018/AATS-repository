from __future__ import annotations

from datetime import timedelta

from aats.data.quality import check_intraday_bars
from aats.domain.decisions import SessionWindow
from aats.domain.enums import QualityStatus
from tests.conftest import make_bar, minute


def bars(session: SessionWindow, n: int = 5):  # type: ignore[no-untyped-def]
    return [make_bar(minute(session, i), "100", "100.5", "99.5", "100.2") for i in range(n)]


def codes(session: SessionWindow, bs) -> set[str]:  # type: ignore[no-untyped-def]
    return {i.code for i in check_intraday_bars(bs, session).issues}


def test_clean_stream_is_valid(session: SessionWindow) -> None:
    r = check_intraday_bars(bars(session), session)
    assert r.ok and r.status is QualityStatus.VALID


def test_empty_stream_is_missing(session: SessionWindow) -> None:
    assert check_intraday_bars([], session).status is QualityStatus.MISSING


def test_gap_detected(session: SessionWindow) -> None:
    b = bars(session)
    assert codes(session, b[:2] + b[3:]) == {"GAP"}


def test_late_first_bar_is_gap(session: SessionWindow) -> None:
    assert "GAP" in codes(session, bars(session)[1:])


def test_duplicate_detected(session: SessionWindow) -> None:
    b = bars(session)
    assert codes(session, [*b[:3], b[2], *b[3:]]) == {"DUPLICATE_BAR"}


def test_out_of_order_detected(session: SessionWindow) -> None:
    b = bars(session)
    assert "OUT_OF_ORDER" in codes(session, [b[0], b[1], b[3], b[2], b[4]])


def test_provisional_and_single_venue(session: SessionWindow) -> None:
    b = bars(session, 2) + [make_bar(minute(session, 2), "100", "100.5", "99.5", "100.2", final=False)]
    b.append(make_bar(minute(session, 3), "100", "100.5", "99.5", "100.2", coverage="SINGLE_VENUE"))
    assert codes(session, b) == {"PROVISIONAL_BAR", "SINGLE_VENUE_FEED"}


def test_outside_session(session: SessionWindow) -> None:
    pre = make_bar(session.open_utc - timedelta(minutes=1), "100", "100.5", "99.5", "100.2")
    assert "OUTSIDE_SESSION" in codes(session, [pre, *bars(session)])


def test_mixed_symbols(session: SessionWindow) -> None:
    b = bars(session, 2) + [make_bar(minute(session, 2), "100", "100.5", "99.5", "100.2", symbol="XYZ")]
    assert "MIXED_STREAM" in codes(session, b)
