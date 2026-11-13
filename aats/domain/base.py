"""Base contract, shared field types and canonical hashing.

Rules enforced for every contract:
- immutable (frozen) and closed (extra fields rejected), so LLM or vendor payloads
  cannot smuggle undeclared, action-bearing fields through a hand-off;
- timestamps must be timezone-aware and are normalised to UTC;
- prices are Decimal, never float, so replays are bit-identical across platforms.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

SCHEMA_VERSION: Literal["1.2"] = "1.2"


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_ensure_utc)]
Price = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
NonNegDecimal = Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=6)]
Symbol = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$")]
Currency = Literal["USD"]


class Contract(BaseModel):
    """Base class for every versioned inter-module contract."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=False, validate_default=True)

    schema_version: Literal["1.2"] = SCHEMA_VERSION

    def canonical_json(self) -> str:
        """Deterministic JSON: sorted keys, no whitespace, Decimals as strings."""
        return canonical_dumps(self.model_dump(mode="json"))

    def content_hash(self) -> str:
        return sha256_hex(self.canonical_json())


class Money(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Decimal
    currency: Currency = "USD"


def canonical_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
