"""Stocks-in-play selection (design section 8.1).

Stage 1 (pre-market) fixes a watchlist from static gates and ranks it by pre-market activity.
Stage 2 (on opening-range completion) applies the opening-interval RVOL gate and keeps the top N by
RVOL. Every gate result, rank and truncation is recorded so selection can be audited and replayed.
Pure functions: no I/O, no clock.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aats.domain.base import Contract, NonNegDecimal, Price, Symbol
from aats.domain.ids import stable_id
from aats.domain.market import DailyBar
from aats.features.calc import atr_sma, q6, relative_volume

_CTX = Context(prec=34, rounding=ROUND_HALF_EVEN)

SecurityType = Literal["COMMON_STOCK", "ETF", "ADR", "SPAC", "PREFERRED", "WARRANT", "RIGHT", "UNIT", "OTHER"]


class SelectionParams(BaseModel):
    """No defaults: a missing value disables selection (design section 8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: str = Field(pattern=r"^selection-\d+\.\d+\.\d+$")
    allowed_security_types: tuple[SecurityType, ...] = Field(min_length=1)
    allowed_exchanges: tuple[str, ...] = Field(min_length=1)
    ipo_exclusion_sessions: int = Field(ge=0)
    min_price: Decimal = Field(gt=0)
    min_avg_daily_volume: int = Field(gt=0)
    min_avg_dollar_volume: Decimal = Field(gt=0)
    min_atr: Decimal = Field(gt=0)
    history_sessions: int = Field(ge=2)
    watchlist_max: int = Field(gt=0)
    rvol_min: Decimal = Field(gt=0)
    rvol_min_history_sessions: int = Field(ge=1)
    top_n: int = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.top_n > self.watchlist_max:
            raise ValueError("top_n cannot exceed watchlist_max")
        return self


def load_selection_params(path: str | Path) -> SelectionParams:
    with open(path, encoding="utf-8") as fh:
        return SelectionParams.model_validate(yaml.safe_load(fh))


class UniverseMember(Contract):
    symbol: Symbol
    security_type: SecurityType
    primary_exchange: str
    sessions_since_listing: int = Field(ge=0)
    corporate_action_in_window: bool


class PremarketStats(Contract):
    symbol: Symbol
    prior_close: Price
    reference_price: Price  # last valid pre-market trade, else prior close
    premarket_volume: int = Field(ge=0)
    daily_history: tuple[DailyBar, ...]  # oldest first, prior sessions only


class GateResult(Contract):
    gate: str
    passed: bool
    value: str | None
    threshold: str | None


class WatchlistEntry(Contract):
    symbol: Symbol
    gates: tuple[GateResult, ...]
    eligible: bool
    gap_pct: Decimal | None
    premarket_dollar_volume: NonNegDecimal | None
    rank: int | None


class Watchlist(Contract):
    watchlist_id: str
    session_date: date
    policy_version: str
    entries: tuple[WatchlistEntry, ...]
    selected: tuple[str, ...]
    truncated: tuple[str, ...]


class StocksInPlayEntry(Contract):
    symbol: Symbol
    rvol_open: NonNegDecimal | None
    gate: GateResult
    rank: int | None


class StocksInPlay(Contract):
    selection_id: str
    watchlist_id: str
    session_date: date
    policy_version: str
    entries: tuple[StocksInPlayEntry, ...]
    selected: tuple[str, ...]
    truncated: tuple[str, ...]


def _g(gate: str, passed: bool, value: object, threshold: object) -> GateResult:
    return GateResult(
        gate=gate,
        passed=passed,
        value=None if value is None else str(value),
        threshold=None if threshold is None else str(threshold),
    )


def _evaluate(m: UniverseMember, s: PremarketStats | None, p: SelectionParams) -> WatchlistEntry:
    gates = [
        _g(
            "SECURITY_TYPE",
            m.security_type in p.allowed_security_types,
            m.security_type,
            ",".join(p.allowed_security_types),
        ),
        _g(
            "EXCHANGE",
            m.primary_exchange in p.allowed_exchanges,
            m.primary_exchange,
            ",".join(p.allowed_exchanges),
        ),
        _g(
            "IPO_WINDOW",
            m.sessions_since_listing >= p.ipo_exclusion_sessions,
            m.sessions_since_listing,
            p.ipo_exclusion_sessions,
        ),
        _g("CORPORATE_ACTION", not m.corporate_action_in_window, m.corporate_action_in_window, False),
    ]
    gap = pm_dollar = None
    if s is None:
        gates.append(_g("PREMARKET_STATS", False, None, "required"))
    else:
        hist = s.daily_history[-p.history_sessions :]
        enough = len(hist) >= p.history_sessions
        gates.append(_g("HISTORY", enough, len(hist), p.history_sessions))
        with localcontext(_CTX):
            avg_vol = q6(sum((h.volume for h in hist), Decimal(0)) / Decimal(len(hist))) if hist else None
            avg_dv = (
                q6(sum((h.volume * h.close for h in hist), Decimal(0)) / Decimal(len(hist))) if hist else None
            )
            gap = q6((s.reference_price - s.prior_close) / s.prior_close)
            pm_dollar = q6(Decimal(s.premarket_volume) * s.reference_price)
        atr = atr_sma(s.daily_history, 14)
        gates += [
            _g("PRICE", s.reference_price >= p.min_price, s.reference_price, p.min_price),
            _g(
                "AVG_DAILY_VOLUME",
                avg_vol is not None and avg_vol >= p.min_avg_daily_volume,
                avg_vol,
                p.min_avg_daily_volume,
            ),
            _g(
                "AVG_DOLLAR_VOLUME",
                avg_dv is not None and avg_dv >= p.min_avg_dollar_volume,
                avg_dv,
                p.min_avg_dollar_volume,
            ),
            _g("ATR", atr is not None and atr >= p.min_atr, atr, p.min_atr),
        ]
    return WatchlistEntry(
        symbol=m.symbol,
        gates=tuple(gates),
        eligible=all(g.passed for g in gates),
        gap_pct=gap,
        premarket_dollar_volume=pm_dollar,
        rank=None,
    )


def build_watchlist(
    session_date: date,
    universe: Sequence[UniverseMember],
    stats: Mapping[str, PremarketStats],
    p: SelectionParams,
) -> Watchlist:
    """Stage 1. Rank eligible symbols by |gap| desc, pre-market dollar volume desc, symbol asc."""
    symbols = [m.symbol for m in universe]
    if len(set(symbols)) != len(symbols):
        raise ValueError("duplicate symbols in universe")
    entries = [_evaluate(m, stats.get(m.symbol), p) for m in sorted(universe, key=lambda m: m.symbol)]
    eligible = sorted(
        (e for e in entries if e.eligible),
        key=lambda e: (-abs(e.gap_pct or Decimal(0)), -(e.premarket_dollar_volume or Decimal(0)), e.symbol),
    )
    ranks = {e.symbol: i + 1 for i, e in enumerate(eligible)}
    ranked = tuple(e.model_copy(update={"rank": ranks.get(e.symbol)}) for e in entries)
    order = [e.symbol for e in eligible]
    return Watchlist(
        watchlist_id=stable_id("watchlist", session_date.isoformat(), p.policy_version, *symbols),
        session_date=session_date,
        policy_version=p.policy_version,
        entries=ranked,
        selected=tuple(order[: p.watchlist_max]),
        truncated=tuple(order[p.watchlist_max :]),
    )


def select_stocks_in_play(
    watchlist: Watchlist,
    opening_volumes: Mapping[str, int],
    open_volume_history: Mapping[str, Sequence[int]],
    p: SelectionParams,
) -> StocksInPlay:
    """Stage 2, run once the opening range is complete. Rank by RVOL desc, symbol asc; keep top N."""
    if watchlist.policy_version != p.policy_version:
        raise ValueError("watchlist and params policy versions differ")
    entries: list[StocksInPlayEntry] = []
    for sym in watchlist.selected:
        vol = opening_volumes.get(sym)
        rvol = (
            None
            if vol is None
            else relative_volume(vol, open_volume_history.get(sym, ()), p.rvol_min_history_sessions)
        )
        entries.append(
            StocksInPlayEntry(
                symbol=sym,
                rvol_open=rvol,
                rank=None,
                gate=_g("RVOL_OPEN", rvol is not None and rvol >= p.rvol_min, rvol, p.rvol_min),
            )
        )
    passing = sorted((e for e in entries if e.gate.passed), key=lambda e: (-(e.rvol_open or 0), e.symbol))
    ranks = {e.symbol: i + 1 for i, e in enumerate(passing)}
    order = [e.symbol for e in passing]
    return StocksInPlay(
        selection_id=stable_id("stocks_in_play", watchlist.watchlist_id),
        watchlist_id=watchlist.watchlist_id,
        session_date=watchlist.session_date,
        policy_version=p.policy_version,
        entries=tuple(e.model_copy(update={"rank": ranks.get(e.symbol)}) for e in entries),
        selected=tuple(order[: p.top_n]),
        truncated=tuple(order[p.top_n :]),
    )
