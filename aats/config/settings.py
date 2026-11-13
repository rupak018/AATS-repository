"""Application settings. Any 'live' mode value fails at startup (design section 17)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aats.domain.enums import Mode

_FORBIDDEN_MODE_WORDS = ("live", "prod", "production", "real")
_PAPER_HOST_HINTS = ("paper", "sandbox", "localhost", "127.0.0.1", "simulated")


class BrokerSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    adapter: str = Field(pattern=r"^(simulated|alpaca_paper|ibkr_paper)$")
    base_url: str

    @field_validator("base_url")
    @classmethod
    def _paper_only(cls, v: str) -> str:
        if not any(h in v.lower() for h in _PAPER_HOST_HINTS):
            raise ValueError(f"broker base_url must point to a paper/simulated endpoint, got {v!r}")
        return v


class AppSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Mode
    broker: BrokerSettings
    data_root: Path
    event_log_dir: Path
    llm_enabled: bool = False

    @field_validator("mode", mode="before")
    @classmethod
    def _reject_live(cls, v: Any) -> Any:
        if isinstance(v, str) and any(w in v.lower() for w in _FORBIDDEN_MODE_WORDS):
            raise ValueError("live trading is out of scope for v1; only test/replay/paper are recognised")
        return v

    @model_validator(mode="after")
    def _paper_requires_real_paper_adapter(self) -> Self:
        if self.mode is Mode.PAPER and self.broker.adapter == "simulated":
            raise ValueError("paper mode needs a paper broker adapter; use replay/test for the simulator")
        return self


def load_settings(path: str | Path) -> AppSettings:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return AppSettings.model_validate(raw)
