"""A3 Quant Feature Engine - pure, deterministic calculations.

All arithmetic uses Decimal with a fixed context and explicit quantisation so results are
identical on every platform. Feature definitions are versioned by FEATURE_VERSION; changing any
formula, window or rounding rule requires a new version (design section 8.2).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from itertools import pairwise

from aats.domain.decisions import OpeningRange, SessionWindow
from aats.domain.market import DailyBar, MarketBar

FEATURE_VERSION = "features-1.0.0"
_CTX = Context(prec=34, rounding=ROUND_HALF_EVEN)
_Q6 = Decimal("0.000001")


def q6(x: Decimal) -> Decimal:
    with localcontext(_CTX):
        return x.quantize(_Q6)


@dataclass(frozen=True)
class OrbResult:
    opening_range: OpeningRange | None
    reason: str | None  # None when valid


def opening_range(bars: Sequence[MarketBar], session: SessionWindow, minutes: int) -> OrbResult:
    """High/low of the finalised bars covering [open, open + minutes).

    Invalid (returns reason) when any expected bar is missing, duplicated, provisional or the
    window is not yet complete. Never evaluate a partial range.
    """
    if not bars:
        return OrbResult(None, "ORB_NOT_READY")
    interval = timedelta(seconds=bars[0].interval_seconds)
    window_end = session.open_utc + timedelta(minutes=minutes)
    expected = int(timedelta(minutes=minutes) / interval)
    in_window = [b for b in bars if session.open_utc <= b.start_utc < window_end]
    if max(b.end_utc for b in bars) < window_end:
        return OrbResult(None, "ORB_NOT_READY")
    starts = [b.start_utc for b in in_window]
    expected_starts = [session.open_utc + i * interval for i in range(expected)]
    if sorted(starts) != expected_starts or any(not b.is_final for b in in_window):
        return OrbResult(None, "ORB_INCOMPLETE")
    return OrbResult(
        OpeningRange(
            high=max(b.high for b in in_window),
            low=min(b.low for b in in_window),
            start_utc=session.open_utc,
            end_utc=window_end,
            bar_count=expected,
        ),
        None,
    )


def session_vwap_approx(bars: Sequence[MarketBar]) -> Decimal | None:
    """VWAP_APPROX: sum(typical_price * volume) / sum(volume) over regular-session bars.

    Labelled approximation; never mixed with trade-based VWAP within one test (design 8.2).
    """
    with localcontext(_CTX):
        vol = sum((Decimal(b.volume) for b in bars), Decimal(0))
        if vol == 0:
            return None
        pv = sum((b.typical_price * Decimal(b.volume) for b in bars), Decimal(0))
        return q6(pv / vol)


def atr_sma(daily: Sequence[DailyBar], period: int = 14) -> Decimal | None:
    """Simple-average true range over the last `period` sessions; needs period + 1 bars."""
    if len(daily) < period + 1:
        return None
    with localcontext(_CTX):
        trs: list[Decimal] = []
        window = daily[-(period + 1) :]
        for prev, cur in pairwise(window):
            trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
        return q6(sum(trs, Decimal(0)) / Decimal(period))


def relative_volume(current: int, history: Sequence[int], min_sessions: int) -> Decimal | None:
    """Current opening-interval volume / mean of the same interval over prior sessions."""
    valid = [h for h in history if h > 0]
    if len(valid) < min_sessions:
        return None
    with localcontext(_CTX):
        mean = Decimal(sum(valid)) / Decimal(len(valid))
        return q6(Decimal(current) / mean)


def ratio(numer: Decimal, denom: Decimal | None) -> Decimal | None:
    if denom is None or denom == 0:
        return None
    with localcontext(_CTX):
        return q6(numer / denom)
