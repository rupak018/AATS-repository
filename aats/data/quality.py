"""A1 Data Quality Sentinel - bar-level checks. Any failure blocks new entries for the symbol."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aats.domain.decisions import SessionWindow
from aats.domain.enums import QualityStatus
from aats.domain.market import MarketBar


@dataclass(frozen=True)
class QualityIssue:
    code: str
    at_utc: datetime | None
    detail: str


@dataclass(frozen=True)
class DataQualityReport:
    symbol: str
    status: QualityStatus
    issues: tuple[QualityIssue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status is QualityStatus.VALID


def check_intraday_bars(
    bars: Sequence[MarketBar], session: SessionWindow, *, require_consolidated: bool = True
) -> DataQualityReport:
    """Validate a symbol's bars for one session, in arrival order.

    Checks: single symbol and interval, provisional bars, coverage, outside-session bars,
    duplicates, out-of-order arrival and sequence gaps. Impossible OHLC is rejected earlier by
    the MarketBar contract itself.
    """
    if not bars:
        return DataQualityReport(
            symbol="?",
            status=QualityStatus.MISSING,
            issues=(QualityIssue("NO_BARS", None, "no bars received"),),
        )
    symbol = bars[0].symbol
    interval = timedelta(seconds=bars[0].interval_seconds)
    issues: list[QualityIssue] = []
    seen: set[datetime] = set()
    prev: datetime | None = None

    for b in bars:
        if b.symbol != symbol or b.interval_seconds != bars[0].interval_seconds:
            issues.append(QualityIssue("MIXED_STREAM", b.start_utc, f"{b.symbol}/{b.interval_seconds}s"))
        if not b.is_final:
            issues.append(QualityIssue("PROVISIONAL_BAR", b.start_utc, "bar not final"))
        if require_consolidated and b.coverage != "CONSOLIDATED":
            issues.append(QualityIssue("SINGLE_VENUE_FEED", b.start_utc, "consolidated coverage required"))
        if b.start_utc < session.open_utc or b.end_utc > session.close_utc:
            issues.append(QualityIssue("OUTSIDE_SESSION", b.start_utc, "bar outside regular session"))
        if b.start_utc in seen:
            issues.append(QualityIssue("DUPLICATE_BAR", b.start_utc, "duplicate start time"))
            continue
        if prev is not None:
            if b.start_utc < prev:
                issues.append(QualityIssue("OUT_OF_ORDER", b.start_utc, f"after {prev.isoformat()}"))
            elif b.start_utc - prev > interval:
                missing = int((b.start_utc - prev) / interval) - 1
                issues.append(QualityIssue("GAP", prev + interval, f"{missing} missing bar(s)"))
        seen.add(b.start_utc)
        prev = b.start_utc if prev is None or b.start_utc > prev else prev

    if bars and min(b.start_utc for b in bars) > session.open_utc:
        issues.append(QualityIssue("GAP", session.open_utc, "first bar after session open"))

    status = QualityStatus.VALID if not issues else QualityStatus.INVALID
    return DataQualityReport(symbol=symbol, status=status, issues=tuple(issues))
