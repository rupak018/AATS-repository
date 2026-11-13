"""Market data contracts: bars and NBBO quote snapshots."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from aats.domain.base import Contract, NonNegDecimal, Price, Symbol, UtcDatetime


class MarketBar(Contract):
    """One OHLCV bar. `start_utc` is inclusive, `end_utc` exclusive."""

    symbol: Symbol
    interval_seconds: int = Field(gt=0)
    start_utc: UtcDatetime
    end_utc: UtcDatetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: int = Field(ge=0)
    provider: str = Field(min_length=1)
    received_at_utc: UtcDatetime
    is_final: bool
    coverage: Literal["CONSOLIDATED", "SINGLE_VENUE"]
    adjustment: Literal["RAW", "SPLIT_ADJUSTED", "SPLIT_DIV_ADJUSTED"] = "RAW"

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end_utc - self.start_utc != timedelta(seconds=self.interval_seconds):
            raise ValueError("end_utc - start_utc must equal interval_seconds")
        if not (self.low <= min(self.open, self.close) and self.high >= max(self.open, self.close)):
            raise ValueError("impossible OHLC: low must be <= open/close <= high")
        if self.received_at_utc < self.end_utc and self.is_final:
            raise ValueError("a final bar cannot be received before it ends")
        return self

    @property
    def typical_price(self) -> Decimal:
        return (self.high + self.low + self.close) / Decimal(3)


class QuoteSnapshot(Contract):
    """NBBO (or labelled single-venue) quote. Never substitute a last trade for this."""

    symbol: Symbol
    bid: Price
    ask: Price
    bid_size: int = Field(ge=0)
    ask_size: int = Field(ge=0)
    quote_time_utc: UtcDatetime
    received_at_utc: UtcDatetime
    coverage: Literal["CONSOLIDATED", "SINGLE_VENUE"]
    provider: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.bid > self.ask:
            raise ValueError("crossed quote: bid > ask")
        return self

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal(2)

    @property
    def spread_bps(self) -> Decimal:
        return (self.ask - self.bid) / self.mid * Decimal(10_000)


class DailyBar(Contract):
    """Prior-session daily bar used for ATR and liquidity history."""

    symbol: Symbol
    session_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    open: Price
    high: Price
    low: Price
    close: Price
    volume: NonNegDecimal

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not (self.low <= min(self.open, self.close) and self.high >= max(self.open, self.close)):
            raise ValueError("impossible OHLC")
        return self
