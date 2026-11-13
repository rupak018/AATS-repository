"""Multi-symbol session fixture loader.

A universe fixture is a directory with:
  session.yaml   - session_date, description, expect, regime block, and one entry per universe member
                   (security info, pre-market stats, daily history, opening-volume history)
  bars/<SYM>.csv - 1-minute bars per symbol in ARRIVAL order (same columns as single-symbol fixtures)
  benchmark.csv  - optional SPY bars + breadth_above_vwap for regime inputs
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from aats.domain.market import DailyBar, MarketBar
from aats.domain.regime import MacroEvent
from aats.replay.fixtures import _bar
from aats.replay.tracker import BenchmarkFeed
from aats.selection import PremarketStats, UniverseMember


@dataclass(frozen=True)
class UniverseFixture:
    name: str
    session_date: date
    description: str
    members: tuple[UniverseMember, ...]
    stats: dict[str, PremarketStats]
    bars: dict[str, tuple[MarketBar, ...]]
    open_volume_history: dict[str, tuple[int, ...]]
    benchmark: BenchmarkFeed
    expectations: dict[str, Any]


def _daily(symbol: str, rows: list[dict[str, Any]]) -> tuple[DailyBar, ...]:
    return tuple(
        DailyBar(symbol=symbol, **{k: (Decimal(str(v)) if k != "session_date" else v) for k, v in r.items()})
        for r in rows
    )


def load_universe_fixture(path: Path) -> UniverseFixture:
    meta = yaml.safe_load((path / "session.yaml").read_text(encoding="utf-8"))
    members, stats, bars, hist = [], {}, {}, {}
    for m in meta["members"]:
        sym = m["symbol"]
        members.append(
            UniverseMember(
                symbol=sym,
                security_type=m["security_type"],
                primary_exchange=m["primary_exchange"],
                sessions_since_listing=m["sessions_since_listing"],
                corporate_action_in_window=m["corporate_action_in_window"],
            )
        )
        pm = m.get("premarket")
        if pm is not None:
            stats[sym] = PremarketStats(
                symbol=sym,
                prior_close=Decimal(str(pm["prior_close"])),
                reference_price=Decimal(str(pm["reference_price"])),
                premarket_volume=int(pm["premarket_volume"]),
                daily_history=_daily(sym, m["daily_history"]),
            )
        hist[sym] = tuple(int(v) for v in m.get("open_volume_history", ()))
        f = path / "bars" / f"{sym}.csv"
        if f.exists():
            with f.open(newline="", encoding="utf-8") as fh:
                bars[sym] = tuple(_bar(sym, row) for row in csv.DictReader(fh))

    bench: list[MarketBar] = []
    breadth: list[tuple[datetime, Decimal]] = []
    if (path / "benchmark.csv").exists():
        with (path / "benchmark.csv").open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                b = _bar("SPY", row)
                bench.append(b)
                breadth.append((b.start_utc, Decimal(row["breadth_above_vwap"])))
    reg = meta.get("regime") or {}
    feed = BenchmarkFeed(
        bars=tuple(bench),
        breadth=tuple(breadth),
        spy_daily_closes=tuple(Decimal(str(c)) for c in reg.get("spy_daily_closes", ())),
        spy_5m_abs_return_norm=(
            Decimal(str(reg["spy_5m_abs_return_norm"])) if "spy_5m_abs_return_norm" in reg else None
        ),
        macro_events=tuple(MacroEvent.model_validate(e) for e in reg.get("macro_events", ())),
    )
    return UniverseFixture(
        name=path.name,
        session_date=date.fromisoformat(str(meta["session_date"])),
        description=meta["description"],
        members=tuple(members),
        stats=stats,
        bars=bars,
        open_volume_history=hist,
        benchmark=feed,
        expectations=meta.get("expect", {}),
    )


def discover_universe_fixtures(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if (p / "session.yaml").exists()) if root.exists() else []
