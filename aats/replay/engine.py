"""Deterministic replay engine (backlog AATS-003).

Feeds fixture bars, in arrival order, through data quality -> features -> strategy variant A,
recording every input and output in a hash-chained event log. The engine reads time only from the
ReplayClock, uses deterministic IDs and Decimal arithmetic, so the same fixture + parameters always
produce the same event-log head hash.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aats.calendar import SessionCalendar, default_calendar
from aats.data.quality import check_intraday_bars
from aats.domain.decisions import FeatureSnapshot, TradeProposal
from aats.domain.enums import ProposalOutcome
from aats.domain.ids import stable_id
from aats.domain.market import MarketBar
from aats.features.calc import (
    FEATURE_VERSION,
    atr_sma,
    opening_range,
    ratio,
    relative_volume,
    session_vwap_approx,
)
from aats.replay.clock import ReplayClock
from aats.replay.event_log import EventLog
from aats.replay.fixtures import SessionFixture
from aats.strategies.orb_vwap.params import OrbVwapParams
from aats.strategies.orb_vwap.variant_a import EvalContext, evaluate

CHECKPOINT_MINUTES = (15, 30, 60, 120)


@dataclass(frozen=True)
class ReplayResult:
    fixture: str
    snapshots: tuple[FeatureSnapshot, ...]
    proposals: tuple[TradeProposal, ...]
    log: EventLog
    quality_issue_codes: tuple[str, ...]
    session_open_utc: str
    session_close_utc: str
    early_close: bool
    params_hash: str

    @property
    def digest(self) -> str:
        return self.log.head_hash

    def summary(self) -> dict[str, Any]:
        """Stable, human-reviewable summary used for golden files."""
        open_ts = datetime.fromisoformat(self.session_open_utc)
        checkpoints = []
        for s in self.snapshots:
            if int((s.as_of_utc - open_ts).total_seconds() // 60) in CHECKPOINT_MINUTES:
                checkpoints.append(_snapshot_view(s))
        if self.snapshots:
            checkpoints.append(_snapshot_view(self.snapshots[-1]))
        orb = next((s.opening_range for s in self.snapshots if s.opening_range is not None), None)
        return {
            "fixture": self.fixture,
            "session": {
                "open_utc": self.session_open_utc,
                "close_utc": self.session_close_utc,
                "early_close": self.early_close,
            },
            "versions": {"feature_version": FEATURE_VERSION, "params_hash": self.params_hash},
            "quality_issue_codes": sorted(set(self.quality_issue_codes)),
            "opening_range": None if orb is None else {"high": str(orb.high), "low": str(orb.low)},
            "checkpoints": checkpoints,
            "outcome_counts": dict(sorted(Counter(p.outcome.value for p in self.proposals).items())),
            "decision_runs": _runs(self.proposals),
            "event_count": len(self.log),
            "event_log_head_hash": self.digest,
        }


def _snapshot_view(s: FeatureSnapshot) -> dict[str, Any]:
    return {
        "as_of_utc": s.as_of_utc.isoformat(),
        "last_close": str(s.last_close),
        "session_vwap": None if s.session_vwap is None else str(s.session_vwap),
        "atr14": None if s.atr14 is None else str(s.atr14),
        "orb_width_atr": None if s.orb_width_atr is None else str(s.orb_width_atr),
        "rvol_open": None if s.rvol_open is None else str(s.rvol_open),
        "cumulative_volume": s.cumulative_volume,
        "quality": s.quality.value,
    }


def _runs(proposals: tuple[TradeProposal, ...]) -> list[dict[str, Any]]:
    """Compress consecutive identical decisions into runs; candidates are always listed in full."""
    runs: list[dict[str, Any]] = []
    for p in proposals:
        key = (p.outcome.value, [r.value for r in p.reason_codes])
        if p.outcome is ProposalOutcome.CANDIDATE:
            runs.append(
                {
                    "from": p.as_of_utc.isoformat(),
                    "to": p.as_of_utc.isoformat(),
                    "count": 1,
                    "outcome": key[0],
                    "reasons": key[1],
                    "entry_reference": str(p.entry_reference),
                    "stop_reference": str(p.stop_reference),
                    "expires_at_utc": p.expires_at_utc.isoformat() if p.expires_at_utc else None,
                }
            )
            continue
        if runs and (runs[-1]["outcome"], runs[-1]["reasons"]) == key and "entry_reference" not in runs[-1]:
            runs[-1]["to"] = p.as_of_utc.isoformat()
            runs[-1]["count"] += 1
        else:
            runs.append(
                {
                    "from": p.as_of_utc.isoformat(),
                    "to": p.as_of_utc.isoformat(),
                    "count": 1,
                    "outcome": key[0],
                    "reasons": key[1],
                }
            )
    return runs


def run_replay(
    fx: SessionFixture,
    params: OrbVwapParams,
    calendar: SessionCalendar | None = None,
    log_path: Path | None = None,
) -> ReplayResult:
    cal = calendar or default_calendar()
    session = cal.session(fx.session_date)
    session_id = stable_id("session", fx.session_date.isoformat(), params.strategy_version)
    clock = ReplayClock(session.open_utc)
    log = EventLog(log_path)
    orb_end = session.open_utc + timedelta(minutes=params.opening_range_minutes)
    atr = atr_sma(fx.daily_history, 14)

    seen: list[MarketBar] = []
    snapshots: list[FeatureSnapshot] = []
    proposals: list[TradeProposal] = []
    has_candidate = False
    report = None

    for bar in fx.bars:
        now = clock.advance_to(bar.received_at_utc)
        bar_event = log.append("BarReceived", bar, now)
        seen.append(bar)

        # Point-in-time quality: only what has arrived so far.
        report = check_intraday_bars(seen, session)
        usable = sorted({b.start_utc: b for b in seen if b.is_final}.values(), key=lambda b: b.start_utc)

        orb_res = opening_range(usable, session, params.opening_range_minutes)
        orb = orb_res.opening_range
        rvol = None
        if orb is not None:
            open_vol = sum(b.volume for b in usable if b.start_utc < orb_end)
            rvol = relative_volume(open_vol, fx.open_volume_history, params.rvol_min_history_sessions)

        snap = FeatureSnapshot(
            snapshot_id=stable_id(
                "snapshot", session_id, fx.symbol, bar.end_utc.isoformat(), bar_event.seq, FEATURE_VERSION
            ),
            symbol=fx.symbol,
            as_of_utc=bar.end_utc,
            feature_version=FEATURE_VERSION,
            raw_event_refs=(stable_id("bar", fx.symbol, bar.start_utc.isoformat()),),
            last_close=usable[-1].close if usable else bar.close,
            session_vwap=session_vwap_approx(usable),
            vwap_method="VWAP_APPROX_TYPICAL_PRICE",
            opening_range=orb,
            atr14=atr,
            orb_width_atr=None if orb is None else ratio(orb.width, atr),
            rvol_open=rvol,
            cumulative_volume=sum(b.volume for b in usable),
            quality=report.status,
            quality_flags=tuple(sorted({i.code for i in report.issues})),
        )
        log.append("FeatureSnapshot", snap, now)
        snapshots.append(snap)

        if bar.end_utc < orb_end:
            continue  # never evaluate an incomplete opening range
        ctx = EvalContext(
            session=session,
            session_id=session_id,
            snapshot=snap,
            orb_status=orb_res.reason,
            already_candidate_today=has_candidate,
            decision_key=str(bar_event.seq),
        )
        proposal = evaluate(ctx, params, cal.to_exchange_time(bar.end_utc))
        log.append("TradeProposal", proposal, now)
        proposals.append(proposal)
        has_candidate = has_candidate or proposal.outcome is ProposalOutcome.CANDIDATE

    return ReplayResult(
        fixture=fx.name,
        snapshots=tuple(snapshots),
        proposals=tuple(proposals),
        log=log,
        quality_issue_codes=tuple(i.code for i in (report.issues if report else ())),
        session_open_utc=session.open_utc.isoformat(),
        session_close_utc=session.close_utc.isoformat(),
        early_close=session.early_close,
        params_hash=params.parameter_hash(),
    )
