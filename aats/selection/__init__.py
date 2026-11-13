"""Stocks-in-play selection across symbols (design section 8.1, backlog AATS-006)."""

from aats.selection.stocks_in_play import (
    GateResult,
    PremarketStats,
    SelectionParams,
    StocksInPlay,
    StocksInPlayEntry,
    UniverseMember,
    Watchlist,
    WatchlistEntry,
    build_watchlist,
    load_selection_params,
    select_stocks_in_play,
)

__all__ = [
    "GateResult",
    "PremarketStats",
    "SelectionParams",
    "StocksInPlay",
    "StocksInPlayEntry",
    "UniverseMember",
    "Watchlist",
    "WatchlistEntry",
    "build_watchlist",
    "load_selection_params",
    "select_stocks_in_play",
]
