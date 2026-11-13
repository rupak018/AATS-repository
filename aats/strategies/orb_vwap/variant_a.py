"""ORB_VWAP_LONG variant A - close confirmation (design section 8.3).

Pure function of its inputs: no I/O, no clock, no randomness. Each finalised 1-minute bar after the
opening range yields exactly one TradeProposal with stable reason codes.

The regime gate (design 8.6) rejects any decision whose regime label is not in the strategy version's
`enabled_regimes`; UNKNOWN regimes carry their missing inputs into the proposal. Not yet implemented:
the spread gate from NBBO quotes (needs quote replay). Proposals are replay-only until the Risk Gate
(AATS-008) exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from aats.domain.decisions import FeatureSnapshot, RegimeRef, SessionWindow, TradeProposal
from aats.domain.enums import ProposalOutcome, QualityStatus, ReasonCode
from aats.domain.ids import stable_id
from aats.domain.regime import RegimeAssessment
from aats.features.calc import q6
from aats.strategies.orb_vwap.params import OrbVwapParams

RC = ReasonCode


@dataclass(frozen=True)
class EvalContext:
    session: SessionWindow
    session_id: str
    snapshot: FeatureSnapshot
    orb_status: str | None  # None, "ORB_NOT_READY" or "ORB_INCOMPLETE"
    already_candidate_today: bool
    decision_key: str  # unique per decision point (e.g. event sequence), keeps IDs unique on duplicates
    regime: RegimeAssessment


def evaluate(ctx: EvalContext, p: OrbVwapParams, exchange_time: datetime) -> TradeProposal:
    """Return the proposal for one decision point.

    `exchange_time` is the snapshot time converted to America/New_York by the caller's calendar,
    passed in so this function stays free of time-zone logic.
    """
    s = ctx.snapshot
    if ctx.regime.rule_version != p.regime_rule_version:
        raise ValueError(
            f"regime rule version {ctx.regime.rule_version} != strategy's {p.regime_rule_version}"
        )
    base: dict[str, Any] = dict(
        proposal_id=stable_id(
            "proposal",
            ctx.session_id,
            s.symbol,
            s.as_of_utc.isoformat(),
            ctx.decision_key,
            p.strategy_version,
        ),
        correlation_id=stable_id("corr", ctx.session_id, s.symbol),
        session_id=ctx.session_id,
        symbol=s.symbol,
        as_of_utc=s.as_of_utc,
        strategy_id=p.strategy_id,
        variant=p.variant,
        strategy_version=p.strategy_version,
        feature_snapshot_id=s.snapshot_id,
        regime=RegimeRef(label=ctx.regime.label, rule_version=ctx.regime.rule_version),
        missing_inputs=tuple(f"regime:{m}" for m in ctx.regime.missing_inputs),
        supporting_evidence_refs=(s.snapshot_id, ctx.regime.assessment_id),
    )

    def out(outcome: ProposalOutcome, *codes: ReasonCode, **extra: object) -> TradeProposal:
        return TradeProposal(outcome=outcome, reason_codes=codes, **base, **extra)

    if s.quality is not QualityStatus.VALID:
        return out(ProposalOutcome.REJECT, RC.DATA_QUALITY_FAILED)
    if ctx.already_candidate_today:
        return out(ProposalOutcome.NO_TRADE, RC.ALREADY_TRADED_TODAY)
    if ctx.orb_status == "ORB_NOT_READY":
        return out(ProposalOutcome.WAIT, RC.ORB_NOT_READY)
    if ctx.orb_status == "ORB_INCOMPLETE" or s.opening_range is None:
        return out(ProposalOutcome.NO_TRADE, RC.ORB_INCOMPLETE)
    if exchange_time.time() > p.last_entry_time_et:
        return out(ProposalOutcome.NO_TRADE, RC.OUTSIDE_ENTRY_WINDOW)
    if ctx.regime.label not in p.enabled_regimes:
        return out(ProposalOutcome.REJECT, RC.REGIME_NOT_ELIGIBLE)
    if s.atr14 is None or s.atr14 < p.min_atr:
        return out(ProposalOutcome.REJECT, RC.ATR_BELOW_MIN)
    if s.last_close < p.min_price:
        return out(ProposalOutcome.REJECT, RC.PRICE_OUT_OF_BAND)
    if s.orb_width_atr is None or not (p.orb_width_atr_min <= s.orb_width_atr <= p.orb_width_atr_max):
        return out(ProposalOutcome.REJECT, RC.ORB_WIDTH_OUT_OF_BAND)
    if s.rvol_open is None:
        return out(ProposalOutcome.REJECT, RC.RVOL_HISTORY_INSUFFICIENT)
    if s.rvol_open < p.rvol_min:
        return out(ProposalOutcome.REJECT, RC.RVOL_BELOW_MIN)

    orb = s.opening_range
    buffer = max(p.tick_size, q6(p.breakout_buffer_atr * s.atr14))
    if s.last_close <= orb.high + buffer:
        return out(ProposalOutcome.WAIT, RC.NO_BREAKOUT)
    if s.session_vwap is None or s.last_close <= s.session_vwap:
        return out(ProposalOutcome.WAIT, RC.BELOW_SESSION_VWAP)
    extension = q6((s.last_close - orb.high) / s.atr14)
    if extension > p.max_extension_atr:
        return out(ProposalOutcome.REJECT, RC.OVEREXTENDED)

    stop = orb.low - p.tick_size
    if s.last_close - stop < p.min_stop_distance or stop <= Decimal(0):
        return out(ProposalOutcome.REJECT, RC.STOP_DISTANCE_TOO_SMALL)

    return out(
        ProposalOutcome.CANDIDATE,
        RC.ORB_BREAKOUT_CONFIRMED,
        RC.ABOVE_SESSION_VWAP,
        entry_reference=s.last_close,
        entry_basis="next_executable_event",
        stop_reference=stop,
        stop_rule=p.stop_rule,
        expires_at_utc=s.as_of_utc + timedelta(minutes=p.proposal_expiry_minutes),
        explanation=(
            f"Close {s.last_close} above ORB high {orb.high} + buffer {buffer}; "
            f"above VWAP {s.session_vwap}; extension {extension} ATR."
        ),
    )
