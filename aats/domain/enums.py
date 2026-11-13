"""Central enums. Every enum value that crosses a module boundary is defined here."""

from enum import StrEnum


class Mode(StrEnum):
    """Runtime modes recognised by v1. There is deliberately no LIVE member."""

    TEST = "test"
    REPLAY = "replay"
    PAPER = "paper"


class ProposalOutcome(StrEnum):
    CANDIDATE = "CANDIDATE"
    WAIT = "WAIT"
    REJECT = "REJECT"
    NO_TRADE = "NO_TRADE"


class StrategyVariant(StrEnum):
    A_CLOSE_CONFIRM = "A_CLOSE_CONFIRM"
    B_BUY_STOP = "B_BUY_STOP"


class RiskOutcome(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    LOCKED = "LOCKED"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderClass(StrEnum):
    BRACKET = "BRACKET"
    SIMPLE = "SIMPLE"  # exits only; entries must be BRACKET in v1


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    STOP_LIMIT = "STOP_LIMIT"
    STOP = "STOP"


class TimeInForce(StrEnum):
    DAY = "DAY"


class AgentStatus(StrEnum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    REFUSED = "REFUSED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class QualityStatus(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    STALE = "STALE"
    DELAYED = "DELAYED"
    INVALID = "INVALID"
    CORRECTED = "CORRECTED"
    CONTRADICTORY = "CONTRADICTORY"


class RegimeLabel(StrEnum):
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    RANGE_CHOP = "RANGE_CHOP"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    NEWS_SHOCK = "NEWS_SHOCK"
    UNKNOWN = "UNKNOWN"


class LLMMode(StrEnum):
    SHADOW = "SHADOW"
    ADVISORY = "ADVISORY"
    VETO = "VETO"


class LLMValidationStatus(StrEnum):
    VALID = "VALID"
    SCHEMA_FAIL = "SCHEMA_FAIL"
    EVIDENCE_FAIL = "EVIDENCE_FAIL"
    TIMEOUT = "TIMEOUT"


class ReasonCode(StrEnum):
    """Stable reason codes. Add new members; never rename or reuse existing ones."""

    # data / session
    DATA_QUALITY_FAILED = "DATA_QUALITY_FAILED"
    ORB_INCOMPLETE = "ORB_INCOMPLETE"
    ORB_NOT_READY = "ORB_NOT_READY"
    OUTSIDE_ENTRY_WINDOW = "OUTSIDE_ENTRY_WINDOW"
    ALREADY_TRADED_TODAY = "ALREADY_TRADED_TODAY"
    # eligibility
    ORB_WIDTH_OUT_OF_BAND = "ORB_WIDTH_OUT_OF_BAND"
    RVOL_BELOW_MIN = "RVOL_BELOW_MIN"
    RVOL_HISTORY_INSUFFICIENT = "RVOL_HISTORY_INSUFFICIENT"
    PRICE_OUT_OF_BAND = "PRICE_OUT_OF_BAND"
    ATR_BELOW_MIN = "ATR_BELOW_MIN"
    # setup
    ORB_BREAKOUT_CONFIRMED = "ORB_BREAKOUT_CONFIRMED"
    ABOVE_SESSION_VWAP = "ABOVE_SESSION_VWAP"
    BELOW_SESSION_VWAP = "BELOW_SESSION_VWAP"
    NO_BREAKOUT = "NO_BREAKOUT"
    OVEREXTENDED = "OVEREXTENDED"
    STOP_DISTANCE_TOO_SMALL = "STOP_DISTANCE_TOO_SMALL"
