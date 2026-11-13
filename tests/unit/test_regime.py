from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from aats.domain.enums import RegimeLabel as L
from aats.domain.regime import MacroEvent, RegimeAssessment, RegimeInputs
from aats.regime import (
    RegimeParams,
    abs_return,
    breadth_above_vwap,
    classify,
    load_regime_params,
    realized_vol_annualised,
)
from tests.conftest import ROOT

T = datetime(2026, 10, 14, 14, 30, tzinfo=UTC)
P = load_regime_params(ROOT / "configs" / "regime" / "regime-1.0.0.yaml")
P_VIX = P.model_copy(update={"volatility_source": "VIX"})


def inputs(**kw: Any) -> RegimeInputs:
    base: dict[str, Any] = dict(
        as_of_utc=T,
        spy_last=Decimal("581.00"),
        spy_vwap=Decimal("580.00"),
        breadth_above_vwap=Decimal("0.65"),
        vix=Decimal("15"),
        vix3m=Decimal("17"),
        spy_realized_vol_20d=Decimal("0.12"),
        spy_5m_abs_return=Decimal("0.0005"),
        spy_5m_abs_return_norm=Decimal("0.0010"),
        inputs_stale=False,
    )
    base.update(kw)
    return RegimeInputs(**base)


EVENT = MacroEvent(name="CPI", tier=1, scheduled_utc=T)

CASES: list[tuple[str, dict[str, Any], RegimeParams, L, str]] = [
    ("bull", {}, P, L.BULL_TREND, "spy_above_vwap_and_breadth"),
    (
        "bear",
        dict(spy_last=Decimal("579"), breadth_above_vwap=Decimal("0.30")),
        P,
        L.BEAR_TREND,
        "spy_below_vwap_and_breadth",
    ),
    ("chop_breadth", dict(breadth_above_vwap=Decimal("0.50")), P, L.RANGE_CHOP, "default"),
    (
        "chop_disagree",
        dict(spy_last=Decimal("579"), breadth_above_vwap=Decimal("0.65")),
        P,
        L.RANGE_CHOP,
        "default",
    ),
    ("unknown_missing", dict(breadth_above_vwap=None), P, L.UNKNOWN, "missing_or_stale_inputs"),
    ("unknown_stale", dict(inputs_stale=True), P, L.UNKNOWN, "missing_or_stale_inputs"),
    (
        "unknown_beats_shock",
        dict(spy_vwap=None, macro_events=(EVENT,)),
        P,
        L.UNKNOWN,
        "missing_or_stale_inputs",
    ),
    ("shock_event", dict(macro_events=(EVENT,)), P, L.NEWS_SHOCK, "macro_event:CPI"),
    (
        "shock_window_before",
        dict(as_of_utc=T - timedelta(minutes=5), macro_events=(EVENT,)),
        P,
        L.NEWS_SHOCK,
        "macro_event:CPI",
    ),
    (
        "after_window",
        dict(as_of_utc=T + timedelta(minutes=31), macro_events=(EVENT,)),
        P,
        L.BULL_TREND,
        "spy_above_vwap_and_breadth",
    ),
    (
        "low_tier_ignored",
        dict(macro_events=(EVENT.model_copy(update={"tier": 2}),)),
        P,
        L.BULL_TREND,
        "spy_above_vwap_and_breadth",
    ),
    ("shock_move", dict(spy_5m_abs_return=Decimal("0.0041")), P, L.NEWS_SHOCK, "spy_5m_move_above_norm"),
    (
        "shock_beats_vol",
        dict(spy_5m_abs_return=Decimal("0.0041"), spy_realized_vol_20d=Decimal("0.5")),
        P,
        L.NEWS_SHOCK,
        "spy_5m_move_above_norm",
    ),
    (
        "high_vol_realized",
        dict(spy_realized_vol_20d=Decimal("0.30")),
        P,
        L.HIGH_VOLATILITY,
        "spy_realized_vol",
    ),
    ("high_vol_vix", dict(vix=Decimal("25")), P_VIX, L.HIGH_VOLATILITY, "vix_level"),
    (
        "vix_inverted",
        dict(vix=Decimal("20"), vix3m=Decimal("19")),
        P_VIX,
        L.HIGH_VOLATILITY,
        "vix_term_structure_inverted",
    ),
    ("vix_source_needs_vix", dict(vix=None), P_VIX, L.UNKNOWN, "missing_or_stale_inputs"),
    (
        "realized_source_ignores_vix",
        dict(vix=None, vix3m=None),
        P,
        L.BULL_TREND,
        "spy_above_vwap_and_breadth",
    ),
]


@pytest.mark.parametrize(("name", "kw", "params", "label", "rule"), CASES, ids=[c[0] for c in CASES])
def test_precedence_table(name: str, kw: dict[str, Any], params: RegimeParams, label: L, rule: str) -> None:
    a = classify(inputs(**kw), params)
    assert (a.label, a.matched_rule) == (label, rule)
    assert a.rule_version == "regime-1.0.0"


def test_unknown_lists_missing_inputs() -> None:
    a = classify(inputs(breadth_above_vwap=None, inputs_stale=True), P)
    assert a.missing_inputs == ("breadth_above_vwap", "inputs_stale")


def test_assessment_id_is_deterministic_and_input_sensitive() -> None:
    assert classify(inputs(), P).assessment_id == classify(inputs(), P).assessment_id
    assert classify(inputs(), P).assessment_id != classify(inputs(spy_last=Decimal("582")), P).assessment_id


def test_known_label_cannot_carry_missing_inputs() -> None:
    a = classify(inputs(), P)
    with pytest.raises(ValidationError, match="must be UNKNOWN"):
        RegimeAssessment.model_validate({**a.model_dump(), "missing_inputs": ("vix",)})


def test_params_require_ordered_breadth_band() -> None:
    with pytest.raises(ValidationError, match="breadth_bear_max"):
        P.model_validate({**P.model_dump(), "breadth_bear_max": Decimal("0.6")})


def test_breadth_fraction_bounded() -> None:
    with pytest.raises(ValidationError):
        inputs(breadth_above_vwap=Decimal("1.2"))


def test_realized_vol_worked_example() -> None:
    # alternating +1% / -1% style path: closes 100, 101, 100, 101, ...
    closes = [Decimal(100) if i % 2 == 0 else Decimal(101) for i in range(21)]
    rv = realized_vol_annualised(closes, window=20)
    assert rv is not None
    # log returns are +/- ln(1.01) = 0.00995033; sample sd = 0.00995033 * sqrt(20/19); x sqrt(252)
    expected = Decimal("0.00995033085") * (Decimal(20) / Decimal(19)).sqrt() * Decimal(252).sqrt()
    assert abs(rv - expected) < Decimal("0.000002")
    assert realized_vol_annualised(closes[:20], window=20) is None
    assert realized_vol_annualised([Decimal(100)] * 21, window=20) == Decimal("0.000000")


def test_breadth_and_abs_return() -> None:
    pairs = [
        (Decimal(10), Decimal(9)),
        (Decimal(10), Decimal(11)),
        (Decimal(5), None),
        (Decimal(7), Decimal(6)),
    ]
    assert breadth_above_vwap(pairs) == Decimal("0.666667")
    assert breadth_above_vwap([(Decimal(1), None)]) is None
    assert abs_return(Decimal("99"), Decimal("100")) == Decimal("0.010000")
