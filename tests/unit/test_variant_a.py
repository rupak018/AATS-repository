"""Decision table for ORB_VWAP_LONG variant A: one row per reason-code branch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from aats.domain.decisions import FeatureSnapshot, OpeningRange, SessionWindow
from aats.domain.enums import ProposalOutcome as O
from aats.domain.enums import QualityStatus
from aats.domain.enums import ReasonCode as R
from aats.strategies.orb_vwap import EvalContext, OrbVwapParams, evaluate

AS_OF = datetime(2026, 10, 14, 14, 5, tzinfo=UTC)  # 10:05 ET
NY = ZoneInfo("America/New_York")
ORB = OpeningRange(
    high=Decimal("101.00"),
    low=Decimal("100.20"),
    start_utc=AS_OF - timedelta(minutes=35),
    end_utc=AS_OF - timedelta(minutes=20),
    bar_count=15,
)


def snap(**kw: Any) -> FeatureSnapshot:
    base: dict[str, Any] = dict(
        snapshot_id="s",
        symbol="ABC",
        as_of_utc=AS_OF,
        feature_version="features-1.0.0",
        raw_event_refs=("b",),
        last_close=Decimal("101.20"),
        session_vwap=Decimal("100.80"),
        vwap_method="VWAP_APPROX_TYPICAL_PRICE",
        opening_range=ORB,
        atr14=Decimal("3.00"),
        orb_width_atr=Decimal("0.266667"),
        rvol_open=Decimal("2.0"),
        cumulative_volume=1_000_000,
        quality=QualityStatus.VALID,
    )
    base.update(kw)
    return FeatureSnapshot(**base)


def run(
    params: OrbVwapParams,
    session: SessionWindow,
    s: FeatureSnapshot,
    *,
    orb_status: str | None = None,
    traded: bool = False,
    local: datetime | None = None,
):  # type: ignore[no-untyped-def]
    ctx = EvalContext(
        session=session,
        session_id="sess",
        snapshot=s,
        orb_status=orb_status,
        already_candidate_today=traded,
        decision_key="1",
    )
    return evaluate(ctx, params, local or s.as_of_utc.astimezone(NY))


CASES = [
    ("quality", dict(quality=QualityStatus.INVALID), {}, O.REJECT, R.DATA_QUALITY_FAILED),
    ("traded", {}, dict(traded=True), O.NO_TRADE, R.ALREADY_TRADED_TODAY),
    ("orb_not_ready", dict(opening_range=None), dict(orb_status="ORB_NOT_READY"), O.WAIT, R.ORB_NOT_READY),
    (
        "orb_incomplete",
        dict(opening_range=None),
        dict(orb_status="ORB_INCOMPLETE"),
        O.NO_TRADE,
        R.ORB_INCOMPLETE,
    ),
    ("late", {}, dict(local=datetime(2026, 10, 14, 11, 31, tzinfo=NY)), O.NO_TRADE, R.OUTSIDE_ENTRY_WINDOW),
    ("atr", dict(atr14=Decimal("0.40")), {}, O.REJECT, R.ATR_BELOW_MIN),
    ("price", dict(last_close=Decimal("4.99")), {}, O.REJECT, R.PRICE_OUT_OF_BAND),
    ("width_low", dict(orb_width_atr=Decimal("0.05")), {}, O.REJECT, R.ORB_WIDTH_OUT_OF_BAND),
    ("width_high", dict(orb_width_atr=Decimal("0.61")), {}, O.REJECT, R.ORB_WIDTH_OUT_OF_BAND),
    ("rvol_none", dict(rvol_open=None), {}, O.REJECT, R.RVOL_HISTORY_INSUFFICIENT),
    ("rvol_low", dict(rvol_open=Decimal("0.99")), {}, O.REJECT, R.RVOL_BELOW_MIN),
    # buffer = max(0.01, 0.02*3.00) = 0.06 -> trigger 101.06
    ("at_trigger", dict(last_close=Decimal("101.06")), {}, O.WAIT, R.NO_BREAKOUT),
    ("below_vwap", dict(session_vwap=Decimal("101.30")), {}, O.WAIT, R.BELOW_SESSION_VWAP),
    # max extension 0.25 * 3.00 = 0.75 above ORB high -> 101.75 allowed, 101.76 rejected
    ("overextended", dict(last_close=Decimal("101.76")), {}, O.REJECT, R.OVEREXTENDED),
    ("candidate", {}, {}, O.CANDIDATE, R.ORB_BREAKOUT_CONFIRMED),
    ("edge_extension", dict(last_close=Decimal("101.75")), {}, O.CANDIDATE, R.ORB_BREAKOUT_CONFIRMED),
]


@pytest.mark.parametrize(("name", "snap_kw", "run_kw", "outcome", "reason"), CASES, ids=[c[0] for c in CASES])
def test_decision_table(
    params: OrbVwapParams,
    session: SessionWindow,
    name: str,
    snap_kw: dict[str, Any],
    run_kw: dict[str, Any],
    outcome: O,
    reason: R,
) -> None:
    p = run(params, session, snap(**snap_kw), **run_kw)
    assert p.outcome is outcome
    assert p.reason_codes[0] is reason


def test_candidate_levels(params: OrbVwapParams, session: SessionWindow) -> None:
    p = run(params, session, snap())
    assert p.entry_reference == Decimal("101.20")
    assert p.stop_reference == Decimal("100.19")  # ORB low - 1 tick
    assert p.expires_at_utc == AS_OF + timedelta(minutes=3)
    assert p.missing_inputs == ("regime",)  # regime gate not implemented yet - recorded, not hidden


def test_stop_distance_too_small(params: OrbVwapParams, session: SessionWindow) -> None:
    tight = OpeningRange(
        high=Decimal("101.00"),
        low=Decimal("101.00"),
        start_utc=ORB.start_utc,
        end_utc=ORB.end_utc,
        bar_count=15,
    )
    # stop = 100.99, close 101.07 -> 0.08 risk/share, below a 0.10 minimum
    strict = params.model_copy(update={"min_stop_distance": Decimal("0.10")})
    p = run(
        strict, session, snap(opening_range=tight, last_close=Decimal("101.07"), session_vwap=Decimal("100"))
    )
    assert (p.outcome, p.reason_codes[0]) == (O.REJECT, R.STOP_DISTANCE_TOO_SMALL)


def test_deterministic_ids(params: OrbVwapParams, session: SessionWindow) -> None:
    assert run(params, session, snap()).proposal_id == run(params, session, snap()).proposal_id
