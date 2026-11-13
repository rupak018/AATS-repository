"""Hand-computed golden values for each feature definition (features-1.0.0)."""

from __future__ import annotations

from decimal import Decimal

from aats.domain.decisions import SessionWindow
from aats.domain.market import DailyBar
from aats.features import atr_sma, opening_range, ratio, relative_volume, session_vwap_approx
from tests.conftest import make_bar, minute


def test_vwap_worked_example(session: SessionWindow) -> None:
    # typical prices: (11+9+10)/3 = 10 with vol 100; (13+11+12)/3 = 12 with vol 300
    b = [
        make_bar(minute(session, 0), "10", "11", "9", "10", 100),
        make_bar(minute(session, 1), "12", "13", "11", "12", 300),
    ]
    assert session_vwap_approx(b) == Decimal("11.500000")  # (10*100 + 12*300) / 400


def test_vwap_none_without_volume(session: SessionWindow) -> None:
    assert session_vwap_approx([make_bar(minute(session, 0), "10", "11", "9", "10", 0)]) is None


def test_vwap_rounds_half_even(session: SessionWindow) -> None:
    b = [
        make_bar(minute(session, 0), "10", "10", "10", "10", 1),
        make_bar(minute(session, 1), "10.01", "10.01", "10.01", "10.01", 2),
    ]
    assert session_vwap_approx(b) == Decimal("10.006667")


def _orb_bars(session: SessionWindow, n: int = 15):  # type: ignore[no-untyped-def]
    return [
        make_bar(minute(session, i), "100", str(Decimal("100.5") + Decimal(i) / 100), "99.8", "100.1")
        for i in range(n)
    ]


def test_orb_from_complete_window(session: SessionWindow) -> None:
    r = opening_range(
        _orb_bars(session) + [make_bar(minute(session, 15), "100", "105", "99", "104")], session, 15
    )
    assert r.reason is None and r.opening_range is not None
    assert (r.opening_range.high, r.opening_range.low) == (
        Decimal("100.64"),
        Decimal("99.8"),
    )  # bar 15 excluded


def test_orb_not_ready_before_window_closes(session: SessionWindow) -> None:
    assert opening_range(_orb_bars(session, 14), session, 15).reason == "ORB_NOT_READY"


def test_orb_incomplete_when_bar_missing(session: SessionWindow) -> None:
    b = _orb_bars(session)
    del b[7]
    b.append(make_bar(minute(session, 15), "100", "100.2", "99.9", "100"))
    assert opening_range(b, session, 15).reason == "ORB_INCOMPLETE"


def test_atr_sma_worked_example() -> None:
    def d(i: int, o: str, h: str, lo: str, c: str) -> DailyBar:
        return DailyBar(
            symbol="ABC",
            session_date=f"2026-10-{i:02d}",
            open=Decimal(o),
            high=Decimal(h),
            low=Decimal(lo),
            close=Decimal(c),
            volume=Decimal(1),
        )

    daily = [d(1, "10", "11", "9", "10"), d(2, "10", "12", "10", "11"), d(3, "13", "14", "12", "13")]
    # TR2 = max(2, |12-10|, |10-10|) = 2 ; TR3 = max(2, |14-11|, |12-11|) = 3 -> mean 2.5
    assert atr_sma(daily, period=2) == Decimal("2.500000")
    assert atr_sma(daily[:2], period=2) is None


def test_relative_volume() -> None:
    assert relative_volume(300, [100, 200, 300], min_sessions=3) == Decimal("1.500000")
    assert relative_volume(300, [100, 0, 300], min_sessions=3) is None  # zero sessions excluded
    assert ratio(Decimal(1), None) is None and ratio(Decimal(1), Decimal(0)) is None
