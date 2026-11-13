"""LLM call record: every model call is cached under a deterministic key so replays never re-call a model."""

from __future__ import annotations

from datetime import date
from typing import Any, Self

from pydantic import Field, model_validator

from aats.domain.base import Contract, UtcDatetime, canonical_dumps, sha256_hex
from aats.domain.enums import LLMMode, LLMValidationStatus


class LLMCallRecord(Contract):
    call_id: str
    skill_id: str = Field(pattern=r"^SK-[A-Z]{3}-\d{2}$")
    mode: LLMMode
    model_id: str
    model_digest: str = Field(min_length=8)
    model_training_cutoff: date
    prompt_template_version: str
    input_hash: str = Field(min_length=64, max_length=64)
    parameters: dict[str, Any]
    output: dict[str, Any]
    validation_status: LLMValidationStatus
    latency_ms: int = Field(ge=0)
    created_at_utc: UtcDatetime

    @model_validator(mode="after")
    def _v1_shadow_only(self) -> Self:
        if self.mode is not LLMMode.SHADOW:
            raise ValueError("v1 permits SHADOW mode only (design section 12.2)")
        return self

    @property
    def cache_key(self) -> str:
        return llm_cache_key(
            self.model_digest, self.prompt_template_version, self.input_hash, self.parameters
        )


def llm_cache_key(model_digest: str, prompt_version: str, input_hash: str, parameters: dict[str, Any]) -> str:
    return sha256_hex(canonical_dumps([model_digest, prompt_version, input_hash, parameters]))


def is_contaminated(event_date: date, cutoff: date, margin_days: int) -> bool:
    """True when an event is too close to (or before) the model's training cutoff to count as evidence."""
    return (event_date - cutoff).days < margin_days
