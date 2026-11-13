"""Per-symbol replay state and the regime-input builder, shared by single- and multi-symbol replay.

Nothing here logs or reads a clock: callers own the event log and pass sequence numbers in, so
both replay runners produce identical snapshots and proposals for identical inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from aats.data.quality import DataQualityReport, check_intraday_bars
from aats.domain.decisions import FeatureSnapshot, SessionWindow, TradeProposal
from aats.domain.enums import ProposalOutcome
from aats.domain.ids import stable_id
from aats.domain.market import MarketBar
from aats.domain.regime import MacroEvent, RegimeAssessment, RegimeInputs
from aats.features.calc import (
    FEATURE_VERSION,
    OrbResult,
    opening_range,
    ratio,
    relative_volume,
    session_vwap_approx,
)
from aats.regime import abs_return, realized_vol_annualised
from aats.strategies.orb_vwap.params import OrbVwapParams
from aats.strategies.orb_vwap.variant_a import EvalContext, evaluate

BENCHMARK_MAX_LAG = timedelta(minutes=1)


@dataclass(frozen=True)
class BenchmarkFeed:
    """SPY bars, breadth series and slow-moving regime inputs for one session."""

    bars: tuple[MarketBar, ...] = ()
    breadth: tuple[tuple[datetime, Decimal], ...] = ()
    spy_daily_closes: tuple[Decimal, ...] = ()
    spy_5m_abs_return_norm: Decimal | None = None
    macro_events: tuple[MacroEvent, ...] = ()


def regime_inputs_at(feed: BenchmarkFeed, as_of: datetime, now: datetime) -> RegimeInputs:
    """Build point-in-time regime inputs from benchmark data that had arrived by `now`."""
    spy = sorted((b for b in feed.bars if b.received_at_utc <= now and b.is_final), key=lambda b: b.start_utc)
    last = spy[-1] if spy else None
    stale = last is None or last.end_utc < as_of - BENCHMARK_MAX_LAG
    breadth = dict(feed.breadth)
    five_ago = next(
        (b for b in reversed(spy) if last and b.end_utc == last.end_utc - timedelta(minutes=5)), None
    )
    return RegimeInputs(
        as_of_utc=as_of,
        spy_last=last.close if last else None,
        spy_vwap=session_vwap_approx(spy) if spy else None,
        breadth_above_vwap=breadth.get(last.start_utc) if last else None,
        spy_realized_vol_20d=realized_vol_annualised(list(feed.spy_daily_closes)),
        spy_5m_abs_return=abs_return(last.close, five_ago.close) if last and five_ago else None,
        spy_5m_abs_return_norm=feed.spy_5m_abs_return_norm,
        macro_events=feed.macro_events,
        inputs_stale=stale,
    )


@dataclass
class SymbolTracker:
    symbol: str
    session: SessionWindow
    session_id: str
    params: OrbVwapParams
    atr14: Decimal | None
    open_volume_history: tuple[int, ...]
    seen: list[MarketBar] = field(default_factory=list)
    snapshots: list[FeatureSnapshot] = field(default_factory=list)
    proposals: list[TradeProposal] = field(default_factory=list)
    report: DataQualityReport | None = None
    orb_result: OrbResult | None = None
    has_candidate: bool = False

    @property
    def orb_end(self) -> datetime:
        return self.session.open_utc + timedelta(minutes=self.params.opening_range_minutes)

    def _usable(self) -> list[MarketBar]:
        return sorted({b.start_utc: b for b in self.seen if b.is_final}.values(), key=lambda b: b.start_utc)

    def opening_volume(self) -> int:
        """Volume of the opening-range bars that have arrived (point in time)."""
        return sum(b.volume for b in self._usable() if b.start_utc < self.orb_end)

    def ingest(self, bar: MarketBar, bar_seq: int) -> FeatureSnapshot:
        if bar.symbol != self.symbol:
            raise ValueError(f"bar for {bar.symbol} sent to tracker for {self.symbol}")
        self.seen.append(bar)
        self.report = check_intraday_bars(self.seen, self.session)
        usable = self._usable()
        self.orb_result = opening_range(usable, self.session, self.params.opening_range_minutes)
        orb = self.orb_result.opening_range
        rvol = None
        if orb is not None:
            rvol = relative_volume(
                self.opening_volume(), self.open_volume_history, self.params.rvol_min_history_sessions
            )
        snap = FeatureSnapshot(
            snapshot_id=stable_id(
                "snapshot", self.session_id, self.symbol, bar.end_utc.isoformat(), bar_seq, FEATURE_VERSION
            ),
            symbol=self.symbol,
            as_of_utc=bar.end_utc,
            feature_version=FEATURE_VERSION,
            raw_event_refs=(stable_id("bar", self.symbol, bar.start_utc.isoformat()),),
            last_close=usable[-1].close if usable else bar.close,
            session_vwap=session_vwap_approx(usable),
            vwap_method="VWAP_APPROX_TYPICAL_PRICE",
            opening_range=orb,
            atr14=self.atr14,
            orb_width_atr=None if orb is None else ratio(orb.width, self.atr14),
            rvol_open=rvol,
            cumulative_volume=sum(b.volume for b in usable),
            quality=self.report.status,
            quality_flags=tuple(sorted({i.code for i in self.report.issues})),
        )
        self.snapshots.append(snap)
        return snap

    def decide(
        self, snap: FeatureSnapshot, decision_key: str, regime: RegimeAssessment, exchange_time: datetime
    ) -> TradeProposal:
        assert self.orb_result is not None
        ctx = EvalContext(
            session=self.session,
            session_id=self.session_id,
            snapshot=snap,
            orb_status=self.orb_result.reason,
            already_candidate_today=self.has_candidate,
            decision_key=decision_key,
            regime=regime,
        )
        proposal = evaluate(ctx, self.params, exchange_time)
        self.proposals.append(proposal)
        self.has_candidate = self.has_candidate or proposal.outcome is ProposalOutcome.CANDIDATE
        return proposal
