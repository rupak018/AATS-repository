"""ORB/VWAP strategy parameters.

There are deliberately NO default values: a missing parameter must fail validation and
disable the strategy version rather than fall back to a hidden default (design section 8).
Values in configs/strategy/*.yaml are research Hypotheses, not facts.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aats.domain.base import canonical_dumps, sha256_hex
from aats.domain.enums import StrategyVariant


class OrbVwapParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: Literal["ORB_VWAP_LONG"]
    variant: StrategyVariant
    strategy_version: str = Field(pattern=r"^\d+\.\d+\.\d+(-[a-z0-9.]+)?$")
    feature_version: str
    opening_range_minutes: int = Field(gt=0, le=60)
    breakout_buffer_atr: Decimal = Field(ge=0)
    tick_size: Decimal = Field(gt=0)
    orb_width_atr_min: Decimal = Field(ge=0)
    orb_width_atr_max: Decimal = Field(gt=0)
    max_extension_atr: Decimal = Field(gt=0)
    rvol_min: Decimal = Field(gt=0)
    rvol_min_history_sessions: int = Field(ge=1)
    min_price: Decimal = Field(gt=0)
    min_atr: Decimal = Field(gt=0)
    min_stop_distance: Decimal = Field(gt=0)
    stop_rule: Literal["S1_ORB_LOW"]
    proposal_expiry_minutes: int = Field(gt=0)
    last_entry_time_et: time
    regime_rule_version: str

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.orb_width_atr_min >= self.orb_width_atr_max:
            raise ValueError("orb_width_atr_min must be < orb_width_atr_max")
        if self.variant is not StrategyVariant.A_CLOSE_CONFIRM:
            raise ValueError("only variant A is implemented in this milestone")
        return self

    def parameter_hash(self) -> str:
        return sha256_hex(canonical_dumps(self.model_dump(mode="json")))


def load_params(path: str | Path) -> OrbVwapParams:
    with open(path, encoding="utf-8") as fh:
        return OrbVwapParams.model_validate(yaml.safe_load(fh))
