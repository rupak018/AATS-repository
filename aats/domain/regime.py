"""Regime contracts (design section 8.6)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from aats.domain.base import Contract, NonNegDecimal, Price, UtcDatetime
from aats.domain.enums import RegimeLabel

Fraction = Annotated[Decimal, Field(ge=0, le=1, max_digits=12, decimal_places=6)]


class MacroEvent(Contract):
    """A scheduled macro release (for example FOMC decision, CPI, payrolls)."""

    name: str = Field(min_length=1)
    tier: Literal[1, 2, 3]
    scheduled_utc: UtcDatetime


class RegimeInputs(Contract):
    """Point-in-time inputs. Any None is a missing input and forces UNKNOWN when required."""

    as_of_utc: UtcDatetime
    spy_last: Price | None
    spy_vwap: Price | None
    breadth_above_vwap: Fraction | None  # share of the liquid universe above its own session VWAP
    vix: Price | None = None
    vix3m: Price | None = None
    spy_realized_vol_20d: NonNegDecimal | None = None  # annualised
    spy_5m_abs_return: NonNegDecimal | None
    spy_5m_abs_return_norm: NonNegDecimal | None  # norm for the same time-of-day bucket
    macro_events: tuple[MacroEvent, ...] = ()
    inputs_stale: bool


class RegimeAssessment(Contract):
    assessment_id: str
    as_of_utc: UtcDatetime
    rule_version: str
    label: RegimeLabel
    matched_rule: str
    missing_inputs: tuple[str, ...]
    inputs: RegimeInputs

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.missing_inputs and self.label is not RegimeLabel.UNKNOWN:
            raise ValueError("a regime with missing inputs must be UNKNOWN")
        return self
