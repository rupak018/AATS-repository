"""Canonical, versioned contracts shared by every AATS module."""

from aats.domain.base import SCHEMA_VERSION, Contract, Money, canonical_dumps, sha256_hex
from aats.domain.decisions import (
    AgentResult,
    CircuitBreakerState,
    EntryLeg,
    ExposureAfter,
    FeatureSnapshot,
    OpeningRange,
    OrderIntent,
    ProtectiveStop,
    RegimeRef,
    RiskDecision,
    SessionWindow,
    TradeProposal,
)
from aats.domain.llm import LLMCallRecord, is_contaminated, llm_cache_key
from aats.domain.market import DailyBar, MarketBar, QuoteSnapshot
from aats.domain.regime import MacroEvent, RegimeAssessment, RegimeInputs

__all__ = [
    "SCHEMA_VERSION",
    "AgentResult",
    "CircuitBreakerState",
    "Contract",
    "DailyBar",
    "EntryLeg",
    "ExposureAfter",
    "FeatureSnapshot",
    "LLMCallRecord",
    "MacroEvent",
    "MarketBar",
    "Money",
    "OpeningRange",
    "OrderIntent",
    "ProtectiveStop",
    "QuoteSnapshot",
    "RegimeAssessment",
    "RegimeInputs",
    "RegimeRef",
    "RiskDecision",
    "SessionWindow",
    "TradeProposal",
    "canonical_dumps",
    "is_contaminated",
    "llm_cache_key",
    "sha256_hex",
]
