"""A4 Regime Rules - deterministic labels with a fixed precedence (design section 8.6).

Precedence (first match wins): UNKNOWN > NEWS_SHOCK > HIGH_VOLATILITY > BULL_TREND > BEAR_TREND >
RANGE_CHOP. All thresholds live in versioned configuration with no code defaults.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from itertools import pairwise
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aats.domain.enums import RegimeLabel
from aats.domain.ids import stable_id
from aats.domain.regime import RegimeAssessment, RegimeInputs
from aats.features.calc import q6

_CTX = Context(prec=34, rounding=ROUND_HALF_EVEN)
TRADING_DAYS_PER_YEAR = 252


class RegimeParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_version: str = Field(pattern=r"^regime-\d+\.\d+\.\d+$")
    volatility_source: Literal["VIX", "REALIZED"]
    vix_high: Decimal = Field(gt=0)
    realized_vol_high: Decimal = Field(gt=0)
    macro_min_tier: Literal[1, 2, 3]
    macro_window_before_minutes: int = Field(ge=0)
    macro_window_after_minutes: int = Field(ge=0)
    shock_multiple: Decimal = Field(gt=0)
    breadth_bull_min: Decimal = Field(ge=0, le=1)
    breadth_bear_max: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.breadth_bear_max >= self.breadth_bull_min:
            raise ValueError("breadth_bear_max must be < breadth_bull_min")
        return self


def load_regime_params(path: str | Path) -> RegimeParams:
    with open(path, encoding="utf-8") as fh:
        return RegimeParams.model_validate(yaml.safe_load(fh))


def missing_inputs(x: RegimeInputs, p: RegimeParams) -> tuple[str, ...]:
    required: dict[str, object] = {
        "spy_last": x.spy_last,
        "spy_vwap": x.spy_vwap,
        "breadth_above_vwap": x.breadth_above_vwap,
        "spy_5m_abs_return": x.spy_5m_abs_return,
        "spy_5m_abs_return_norm": x.spy_5m_abs_return_norm,
    }
    if p.volatility_source == "VIX":
        required |= {"vix": x.vix, "vix3m": x.vix3m}
    else:
        required["spy_realized_vol_20d"] = x.spy_realized_vol_20d
    missing = [k for k, v in required.items() if v is None]
    if x.inputs_stale:
        missing.append("inputs_stale")
    return tuple(missing)


def classify(x: RegimeInputs, p: RegimeParams) -> RegimeAssessment:
    """Label the market regime at `x.as_of_utc`. Pure function."""
    missing = missing_inputs(x, p)

    def out(label: RegimeLabel, rule: str) -> RegimeAssessment:
        return RegimeAssessment(
            assessment_id=stable_id("regime", p.rule_version, x.content_hash()),
            as_of_utc=x.as_of_utc,
            rule_version=p.rule_version,
            label=label,
            matched_rule=rule,
            missing_inputs=missing if label is RegimeLabel.UNKNOWN else (),
            inputs=x,
        )

    if missing:
        return out(RegimeLabel.UNKNOWN, "missing_or_stale_inputs")

    for ev in x.macro_events:
        start = ev.scheduled_utc - timedelta(minutes=p.macro_window_before_minutes)
        end = ev.scheduled_utc + timedelta(minutes=p.macro_window_after_minutes)
        if ev.tier <= p.macro_min_tier and start <= x.as_of_utc <= end:
            return out(RegimeLabel.NEWS_SHOCK, f"macro_event:{ev.name}")
    assert x.spy_5m_abs_return is not None and x.spy_5m_abs_return_norm is not None
    if x.spy_5m_abs_return_norm > 0 and x.spy_5m_abs_return > p.shock_multiple * x.spy_5m_abs_return_norm:
        return out(RegimeLabel.NEWS_SHOCK, "spy_5m_move_above_norm")

    if p.volatility_source == "VIX":
        assert x.vix is not None and x.vix3m is not None
        if x.vix >= p.vix_high:
            return out(RegimeLabel.HIGH_VOLATILITY, "vix_level")
        if x.vix > x.vix3m:
            return out(RegimeLabel.HIGH_VOLATILITY, "vix_term_structure_inverted")
    else:
        assert x.spy_realized_vol_20d is not None
        if x.spy_realized_vol_20d >= p.realized_vol_high:
            return out(RegimeLabel.HIGH_VOLATILITY, "spy_realized_vol")

    assert x.spy_last is not None and x.spy_vwap is not None and x.breadth_above_vwap is not None
    if x.spy_last > x.spy_vwap and x.breadth_above_vwap >= p.breadth_bull_min:
        return out(RegimeLabel.BULL_TREND, "spy_above_vwap_and_breadth")
    if x.spy_last < x.spy_vwap and x.breadth_above_vwap <= p.breadth_bear_max:
        return out(RegimeLabel.BEAR_TREND, "spy_below_vwap_and_breadth")
    return out(RegimeLabel.RANGE_CHOP, "default")


def realized_vol_annualised(closes: Sequence[Decimal], window: int = 20) -> Decimal | None:
    """Sample standard deviation of daily log returns over `window` returns, annualised (sqrt 252)."""
    if len(closes) < window + 1 or any(c <= 0 for c in closes):
        return None
    with localcontext(_CTX):
        rets = [(b / a).ln() for a, b in pairwise(closes[-(window + 1) :])]
        mean = sum(rets, Decimal(0)) / Decimal(len(rets))
        var = sum(((r - mean) ** 2 for r in rets), Decimal(0)) / Decimal(len(rets) - 1)
        return q6(var.sqrt() * Decimal(TRADING_DAYS_PER_YEAR).sqrt())


def breadth_above_vwap(pairs: Sequence[tuple[Decimal, Decimal | None]]) -> Decimal | None:
    """Share of (last, vwap) pairs with last > vwap. Symbols without a VWAP are excluded."""
    valid = [(last, vwap) for last, vwap in pairs if vwap is not None]
    if not valid:
        return None
    with localcontext(_CTX):
        return q6(Decimal(sum(1 for last, vwap in valid if last > vwap)) / Decimal(len(valid)))


def abs_return(now: Decimal, then: Decimal) -> Decimal:
    with localcontext(_CTX):
        return q6(abs(now / then - Decimal(1)))
