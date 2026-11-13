from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aats.domain import (
    CircuitBreakerState,
    EntryLeg,
    ExposureAfter,
    LLMCallRecord,
    Money,
    OrderIntent,
    ProtectiveStop,
    QuoteSnapshot,
    RegimeRef,
    RiskDecision,
    TradeProposal,
    is_contaminated,
    llm_cache_key,
)
from aats.domain.enums import (
    LLMMode,
    LLMValidationStatus,
    OrderClass,
    OrderType,
    ProposalOutcome,
    ReasonCode,
    RegimeLabel,
    RiskOutcome,
    Side,
    StrategyVariant,
)
from tests.conftest import make_bar

T0 = datetime(2026, 10, 14, 14, 5, tzinfo=UTC)


def proposal(**kw: object) -> TradeProposal:
    base: dict[str, object] = dict(
        proposal_id="p1",
        correlation_id="c1",
        session_id="s1",
        symbol="ABC",
        as_of_utc=T0,
        strategy_id="ORB_VWAP_LONG",
        variant=StrategyVariant.A_CLOSE_CONFIRM,
        strategy_version="0.2.0-research",
        feature_snapshot_id="f1",
        regime=RegimeRef(label=RegimeLabel.UNKNOWN, rule_version="x"),
        outcome=ProposalOutcome.WAIT,
        reason_codes=(ReasonCode.NO_BREAKOUT,),
    )
    base.update(kw)
    return TradeProposal(**base)  # type: ignore[arg-type]


def candidate(**kw: object) -> TradeProposal:
    return proposal(
        outcome=ProposalOutcome.CANDIDATE,
        reason_codes=(ReasonCode.ORB_BREAKOUT_CONFIRMED,),
        entry_reference=Decimal("101.28"),
        entry_basis="next_executable_event",
        stop_reference=Decimal("100.30"),
        stop_rule="S1_ORB_LOW",
        expires_at_utc=T0 + timedelta(minutes=3),
        **kw,
    )


class TestTimestamps:
    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            proposal(as_of_utc=datetime(2026, 10, 14, 14, 5))

    def test_other_timezone_normalised_to_utc(self) -> None:
        from zoneinfo import ZoneInfo

        p = proposal(as_of_utc=datetime(2026, 10, 14, 10, 5, tzinfo=ZoneInfo("America/New_York")))
        assert p.as_of_utc == T0 and p.as_of_utc.tzinfo == UTC


class TestClosedAndFrozen:
    def test_undeclared_field_rejected(self) -> None:
        # e.g. an LLM trying to smuggle an action into a hand-off
        with pytest.raises(ValidationError, match="Extra inputs"):
            proposal(submit_order=True)

    def test_contracts_are_immutable(self) -> None:
        p = proposal()
        with pytest.raises(ValidationError):
            p.symbol = "XYZ"  # type: ignore[misc]

    def test_canonical_json_is_stable_and_uses_decimal_strings(self) -> None:
        a, b = candidate(), candidate()
        assert a.canonical_json() == b.canonical_json()
        assert '"entry_reference":"101.28"' in a.canonical_json()
        assert a.content_hash() == b.content_hash()


class TestMarket:
    def test_impossible_ohlc_rejected(self) -> None:
        with pytest.raises(ValidationError, match="impossible OHLC"):
            make_bar(T0, "100", "99", "98", "100")

    def test_final_bar_cannot_arrive_before_it_ends(self) -> None:
        bar = make_bar(T0, "100", "101", "99", "100")
        with pytest.raises(ValidationError, match="before it ends"):
            bar.model_validate({**bar.model_dump(), "received_at_utc": bar.start_utc})

    def test_crossed_quote_rejected_and_spread(self) -> None:
        kw = dict(
            symbol="ABC",
            bid_size=1,
            ask_size=1,
            quote_time_utc=T0,
            received_at_utc=T0,
            coverage="CONSOLIDATED",
            provider="t",
        )
        with pytest.raises(ValidationError, match="crossed"):
            QuoteSnapshot(bid=Decimal("10.02"), ask=Decimal("10.00"), **kw)  # type: ignore[arg-type]
        q = QuoteSnapshot(bid=Decimal("9.99"), ask=Decimal("10.01"), **kw)  # type: ignore[arg-type]
        assert q.spread_bps == Decimal(20)

    def test_symbol_pattern(self) -> None:
        with pytest.raises(ValidationError):
            proposal(symbol="abc; DROP TABLE")


class TestProposal:
    def test_candidate_requires_levels(self) -> None:
        with pytest.raises(ValidationError, match="CANDIDATE requires"):
            proposal(outcome=ProposalOutcome.CANDIDATE, reason_codes=(ReasonCode.ORB_BREAKOUT_CONFIRMED,))

    def test_long_stop_must_be_below_entry(self) -> None:
        with pytest.raises(ValidationError, match="below entry"):
            candidate().model_validate({**candidate().model_dump(), "stop_reference": Decimal("101.28")})

    def test_expiry_after_as_of(self) -> None:
        with pytest.raises(ValidationError, match="expiry"):
            candidate().model_validate({**candidate().model_dump(), "expires_at_utc": T0})

    def test_non_candidate_cannot_carry_levels(self) -> None:
        with pytest.raises(ValidationError, match="only CANDIDATE"):
            proposal(entry_reference=Decimal("101"))

    def test_reason_code_required(self) -> None:
        with pytest.raises(ValidationError, match="reason code"):
            proposal(reason_codes=())


def risk(**kw: object) -> RiskDecision:
    base: dict[str, object] = dict(
        risk_decision_id="r1",
        proposal_id="p1",
        allocation_batch_id="b1",
        allocation_rank=1,
        outcome=RiskOutcome.REJECT,
        risk_policy_version="v1",
        account_snapshot_id="a1",
        decision_time_utc=T0,
        approved_quantity=0,
        planned_risk_amount=Money(value=Decimal(0)),
        estimated_cost_amount=Money(value=Decimal(0)),
        post_trade_exposure=ExposureAfter(gross=Decimal(0), beta_adjusted=Decimal(0), sector_count=0),
        circuit_breaker_state=CircuitBreakerState(
            daily="OK", weekly="OK", drawdown="OK", consecutive_losses=0
        ),
        valid_until_utc=T0 + timedelta(seconds=30),
    )
    base.update(kw)
    return RiskDecision(**base)  # type: ignore[arg-type]


class TestRiskDecision:
    def test_approve_requires_quantity_and_hash(self) -> None:
        with pytest.raises(ValidationError, match="APPROVE requires"):
            risk(outcome=RiskOutcome.APPROVE)
        assert (
            risk(
                outcome=RiskOutcome.APPROVE, approved_quantity=10, approved_order_payload_hash="h"
            ).approved_quantity
            == 10
        )

    def test_reject_carries_no_quantity(self) -> None:
        with pytest.raises(ValidationError, match="zero quantity"):
            risk(approved_quantity=5)


def intent(**kw: object) -> OrderIntent:
    base: dict[str, object] = dict(
        intent_id="i1",
        client_order_id="aats-0001-abc",
        risk_decision_id="r1",
        symbol="ABC",
        side=Side.BUY,
        order_class=OrderClass.BRACKET,
        entry=EntryLeg(type=OrderType.LIMIT, limit_price=Decimal("101.30")),
        protective_stop=ProtectiveStop(stop_price=Decimal("100.30")),
        quantity=10,
        strategy_version="0.2.0-research",
        created_at_utc=T0,
    )
    base.update(kw)
    return OrderIntent(**base)  # type: ignore[arg-type]


class TestOrderIntent:
    def test_entries_must_be_brackets_with_stop(self) -> None:
        with pytest.raises(ValidationError, match="BRACKET"):
            intent(order_class=OrderClass.SIMPLE, protective_stop=None)

    def test_stop_below_limit(self) -> None:
        with pytest.raises(ValidationError, match="below the entry"):
            intent(protective_stop=ProtectiveStop(stop_price=Decimal("101.30")))

    def test_stop_limit_needs_stop_price(self) -> None:
        with pytest.raises(ValidationError, match="stop_price is required"):
            EntryLeg(type=OrderType.STOP_LIMIT, limit_price=Decimal("101.40"))

    def test_payload_hash_ignores_identity_fields(self) -> None:
        a = intent()
        b = intent(intent_id="other", created_at_utc=T0 + timedelta(seconds=5))
        c = intent(quantity=11)
        assert a.payload_hash() == b.payload_hash() != c.payload_hash()


class TestLLMRecord:
    def rec(self, **kw: object) -> LLMCallRecord:
        base: dict[str, object] = dict(
            call_id="l1",
            skill_id="SK-CAT-01",
            mode=LLMMode.SHADOW,
            model_id="m",
            model_digest="sha256:abcdef12",
            model_training_cutoff=date(2025, 6, 30),
            prompt_template_version="1.0.0",
            input_hash="a" * 64,
            parameters={"temperature": 0, "seed": 0},
            output={},
            validation_status=LLMValidationStatus.VALID,
            latency_ms=10,
            created_at_utc=T0,
        )
        base.update(kw)
        return LLMCallRecord(**base)  # type: ignore[arg-type]

    def test_v1_is_shadow_only(self) -> None:
        with pytest.raises(ValidationError, match="SHADOW"):
            self.rec(mode=LLMMode.VETO)

    def test_cache_key_deterministic_and_param_sensitive(self) -> None:
        assert self.rec().cache_key == self.rec(call_id="other").cache_key
        assert self.rec().cache_key != self.rec(parameters={"temperature": 0, "seed": 1}).cache_key
        assert self.rec().cache_key == llm_cache_key(
            "sha256:abcdef12", "1.0.0", "a" * 64, {"seed": 0, "temperature": 0}
        )

    @pytest.mark.parametrize(
        ("event", "contaminated"),
        [
            (date(2025, 6, 1), True),
            (date(2025, 7, 15), True),
            (date(2025, 7, 30), False),
            (date(2026, 1, 1), False),
        ],
    )
    def test_training_cutoff_contamination(self, event: date, contaminated: bool) -> None:
        assert is_contaminated(event, date(2025, 6, 30), margin_days=30) is contaminated
