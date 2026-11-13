"""Decision-path contracts: features, proposals, risk decisions, order intents, agent results."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from aats.domain.base import (
    Contract,
    Money,
    NonNegDecimal,
    Price,
    Symbol,
    UtcDatetime,
    canonical_dumps,
    sha256_hex,
)
from aats.domain.enums import (
    AgentStatus,
    OrderClass,
    OrderType,
    ProposalOutcome,
    QualityStatus,
    ReasonCode,
    RegimeLabel,
    RiskOutcome,
    Side,
    StrategyVariant,
    TimeInForce,
)


class OpeningRange(Contract):
    high: Price
    low: Price
    start_utc: UtcDatetime
    end_utc: UtcDatetime
    bar_count: int = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.low > self.high:
            raise ValueError("opening range low > high")
        return self

    @property
    def width(self) -> Decimal:
        return self.high - self.low


class FeatureSnapshot(Contract):
    snapshot_id: str
    symbol: Symbol
    as_of_utc: UtcDatetime
    feature_version: str
    raw_event_refs: tuple[str, ...]
    last_close: Price
    session_vwap: Price | None
    vwap_method: Literal["VWAP_APPROX_TYPICAL_PRICE", "VWAP_TRADES"]
    opening_range: OpeningRange | None
    atr14: Price | None
    orb_width_atr: NonNegDecimal | None
    rvol_open: NonNegDecimal | None
    cumulative_volume: int = Field(ge=0)
    quality: QualityStatus
    quality_flags: tuple[str, ...] = ()


class RegimeRef(Contract):
    label: RegimeLabel
    rule_version: str


class TradeProposal(Contract):
    proposal_id: str
    correlation_id: str
    session_id: str
    symbol: Symbol
    as_of_utc: UtcDatetime
    strategy_id: Literal["ORB_VWAP_LONG"]
    variant: StrategyVariant
    strategy_version: str
    feature_snapshot_id: str
    regime: RegimeRef
    outcome: ProposalOutcome
    reason_codes: tuple[ReasonCode, ...]
    supporting_evidence_refs: tuple[str, ...] = ()
    contrary_evidence_refs: tuple[str, ...] = ()
    missing_inputs: tuple[str, ...] = ()
    entry_reference: Price | None = None
    entry_basis: Literal["next_executable_event"] | None = None
    stop_reference: Price | None = None
    stop_rule: Literal["S1_ORB_LOW", "S2_ATR_FRACTION"] | None = None
    expires_at_utc: UtcDatetime | None = None
    llm_shadow_refs: tuple[str, ...] = ()
    explanation: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.reason_codes:
            raise ValueError("every proposal needs at least one reason code")
        if self.outcome is ProposalOutcome.CANDIDATE:
            missing = [
                n
                for n in ("entry_reference", "entry_basis", "stop_reference", "stop_rule", "expires_at_utc")
                if getattr(self, n) is None
            ]
            if missing:
                raise ValueError(f"CANDIDATE requires {missing}")
            assert self.entry_reference is not None and self.stop_reference is not None
            assert self.expires_at_utc is not None
            if self.stop_reference >= self.entry_reference:
                raise ValueError("long stop must be below entry reference")
            if self.expires_at_utc <= self.as_of_utc:
                raise ValueError("expiry must be after as_of")
        elif self.entry_reference is not None or self.stop_reference is not None:
            raise ValueError("only CANDIDATE proposals may carry entry/stop levels")
        return self


class CircuitBreakerState(Contract):
    daily: Literal["OK", "TRIPPED"]
    weekly: Literal["OK", "TRIPPED"]
    drawdown: Literal["OK", "TRIPPED"]
    consecutive_losses: int = Field(ge=0)


class ExposureAfter(Contract):
    gross: NonNegDecimal
    beta_adjusted: Decimal
    sector_count: int = Field(ge=0)


class RiskDecision(Contract):
    risk_decision_id: str
    proposal_id: str
    allocation_batch_id: str
    allocation_rank: int = Field(ge=1)
    outcome: RiskOutcome
    risk_policy_version: str
    account_snapshot_id: str
    decision_time_utc: UtcDatetime
    approved_quantity: int = Field(ge=0)
    planned_risk_amount: Money
    estimated_cost_amount: Money
    post_trade_exposure: ExposureAfter
    circuit_breaker_state: CircuitBreakerState
    reason_codes: tuple[str, ...] = ()
    approved_order_payload_hash: str | None = None
    valid_until_utc: UtcDatetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.outcome is RiskOutcome.APPROVE:
            if self.approved_quantity <= 0 or not self.approved_order_payload_hash:
                raise ValueError("APPROVE requires quantity > 0 and an approved payload hash")
        elif self.approved_quantity != 0 or self.approved_order_payload_hash is not None:
            raise ValueError("REJECT/LOCKED must carry zero quantity and no payload hash")
        if self.valid_until_utc <= self.decision_time_utc:
            raise ValueError("valid_until must be after decision time")
        return self


class EntryLeg(Contract):
    type: Literal[OrderType.LIMIT, OrderType.STOP_LIMIT]
    limit_price: Price
    stop_price: Price | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.type is OrderType.STOP_LIMIT) != (self.stop_price is not None):
            raise ValueError("stop_price is required for STOP_LIMIT and forbidden for LIMIT")
        return self


class ProtectiveStop(Contract):
    type: Literal[OrderType.STOP] = OrderType.STOP
    stop_price: Price


class OrderIntent(Contract):
    """An order the OMS may submit. v1 entries are always BUY brackets with a broker-held stop."""

    intent_id: str
    client_order_id: str = Field(min_length=8, max_length=64)
    risk_decision_id: str
    symbol: Symbol
    side: Side
    order_class: OrderClass
    entry: EntryLeg
    protective_stop: ProtectiveStop | None
    quantity: int = Field(gt=0)
    time_in_force: TimeInForce = TimeInForce.DAY
    strategy_version: str
    created_at_utc: UtcDatetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.side is Side.BUY:
            if self.order_class is not OrderClass.BRACKET or self.protective_stop is None:
                raise ValueError("v1 entries must be BRACKET orders with a protective stop")
            if self.protective_stop.stop_price >= self.entry.limit_price:
                raise ValueError("protective stop must be below the entry limit")
        return self

    def payload_hash(self) -> str:
        """Hash of the economically meaningful fields; compared with RiskDecision."""
        payload = self.model_dump(mode="json", exclude={"intent_id", "created_at_utc"})
        return sha256_hex(canonical_dumps(payload))


class AgentResult(Contract):
    task_id: str
    correlation_id: str
    role: str
    status: AgentStatus
    output_ref: str | None = None
    missing_inputs: tuple[str, ...] = ()
    error: str | None = None


class SessionWindow(Contract):
    """Exchange session for one date, already resolved to UTC."""

    session_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    open_utc: UtcDatetime
    close_utc: UtcDatetime
    early_close: bool

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.close_utc <= self.open_utc:
            raise ValueError("close must be after open")
        return self


__all__ = [
    "AgentResult",
    "CircuitBreakerState",
    "EntryLeg",
    "ExposureAfter",
    "FeatureSnapshot",
    "OpeningRange",
    "OrderIntent",
    "ProtectiveStop",
    "RegimeRef",
    "RiskDecision",
    "SessionWindow",
    "TradeProposal",
]
