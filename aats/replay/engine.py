"""Deterministic replay engine (backlog AATS-003).

Feeds fixture bars, in arrival order, through data quality -> features -> regime rules -> strategy
variant A, recording every input and output in a hash-chained event log. The engine reads time only from the
ReplayClock, uses deterministic IDs and Decimal arithmetic, so the same fixture + parameters always
produce the same event-log head hash.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from aats.calendar import SessionCalendar, default_calendar
from aats.domain.base import canonical_dumps, sha256_hex
from aats.domain.decisions import FeatureSnapshot, TradeProposal
from aats.domain.enums import ProposalOutcome
from aats.domain.ids import stable_id
from aats.domain.regime import RegimeAssessment
from aats.features.calc import (
    FEATURE_VERSION,
    atr_sma,
)
from aats.regime import RegimeParams, classify
from aats.replay.clock import ReplayClock
from aats.replay.event_log import EventLog
from aats.replay.fixtures import SessionFixture
from aats.replay.tracker import SymbolTracker, regime_inputs_at
from aats.strategies.orb_vwap.params import OrbVwapParams

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
    regime_params_hash: str
    regime_rule_version: str

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
            "versions": {
                "feature_version": FEATURE_VERSION,
                "params_hash": self.params_hash,
                "regime_rule_version": self.regime_rule_version,
                "regime_params_hash": self.regime_params_hash,
            },
            "quality_issue_codes": sorted(set(self.quality_issue_codes)),
            "opening_range": None if orb is None else {"high": str(orb.high), "low": str(orb.low)},
            "checkpoints": checkpoints,
            "outcome_counts": dict(sorted(Counter(p.outcome.value for p in self.proposals).items())),
            "regime_counts": dict(sorted(Counter(p.regime.label.value for p in self.proposals).items())),
            "decision_runs": decision_runs(self.proposals),
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


def decision_runs(proposals: tuple[TradeProposal, ...]) -> list[dict[str, Any]]:
    """Compress consecutive identical decisions into runs; candidates are always listed in full."""
    runs: list[dict[str, Any]] = []
    for p in proposals:
        key = (p.outcome.value, [r.value for r in p.reason_codes], p.regime.label.value)
        if p.outcome is ProposalOutcome.CANDIDATE:
            runs.append(
                {
                    "from": p.as_of_utc.isoformat(),
                    "to": p.as_of_utc.isoformat(),
                    "count": 1,
                    "outcome": key[0],
                    "reasons": key[1],
                    "regime": key[2],
                    "entry_reference": str(p.entry_reference),
                    "stop_reference": str(p.stop_reference),
                    "expires_at_utc": p.expires_at_utc.isoformat() if p.expires_at_utc else None,
                }
            )
            continue
        last = runs[-1] if runs else None
        if (
            last
            and (last["outcome"], last["reasons"], last["regime"]) == key
            and "entry_reference" not in last
        ):
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
                    "regime": key[2],
                }
            )
    return runs


def run_replay(
    fx: SessionFixture,
    params: OrbVwapParams,
    regime_params: RegimeParams,
    calendar: SessionCalendar | None = None,
    log_path: Path | None = None,
) -> ReplayResult:
    if regime_params.rule_version != params.regime_rule_version:
        raise ValueError("strategy params and regime params reference different regime rule versions")
    cal = calendar or default_calendar()
    session = cal.session(fx.session_date)
    session_id = stable_id("session", fx.session_date.isoformat(), params.strategy_version)
    clock = ReplayClock(session.open_utc)
    log = EventLog(log_path)
    track = SymbolTracker(
        symbol=fx.symbol,
        session=session,
        session_id=session_id,
        params=params,
        atr14=atr_sma(fx.daily_history, 14),
        open_volume_history=fx.open_volume_history,
    )
    feed = fx.benchmark

    for bar in fx.bars:
        now = clock.advance_to(bar.received_at_utc)
        bar_event = log.append("BarReceived", bar, now)
        snap = track.ingest(bar, bar_event.seq)  # point-in-time: only what has arrived so far
        log.append("FeatureSnapshot", snap, now)
        if bar.end_utc < track.orb_end:
            continue  # never evaluate an incomplete opening range
        regime: RegimeAssessment = classify(regime_inputs_at(feed, bar.end_utc, now), regime_params)
        log.append("RegimeAssessment", regime, now)
        proposal = track.decide(snap, str(bar_event.seq), regime, cal.to_exchange_time(bar.end_utc))
        log.append("TradeProposal", proposal, now)

    return ReplayResult(
        fixture=fx.name,
        snapshots=tuple(track.snapshots),
        proposals=tuple(track.proposals),
        log=log,
        quality_issue_codes=tuple(i.code for i in (track.report.issues if track.report else ())),
        session_open_utc=session.open_utc.isoformat(),
        session_close_utc=session.close_utc.isoformat(),
        early_close=session.early_close,
        params_hash=params.parameter_hash(),
        regime_params_hash=sha256_hex(canonical_dumps(regime_params.model_dump(mode="json"))),
        regime_rule_version=regime_params.rule_version,
    )
