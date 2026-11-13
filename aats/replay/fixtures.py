"""Session fixture loader.

A fixture is a directory with:
  meta.yaml  - symbol, session_date, description, daily history, opening-interval volume history
  bars.csv   - 1-minute bars in ARRIVAL order (so duplicates / out-of-order arrivals can be replayed)
  benchmark.csv (optional) - SPY 1-minute bars plus `breadth_above_vwap` for regime inputs; when it is
               absent the regime is UNKNOWN for the whole session
  meta.yaml `regime` block (optional) - spy_daily_closes, spy_5m_abs_return_norm, macro_events
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from aats.domain.market import DailyBar, MarketBar
from aats.domain.regime import MacroEvent


@dataclass(frozen=True)
class SessionFixture:
    name: str
    symbol: str
    session_date: date
    description: str
    bars: tuple[MarketBar, ...]
    daily_history: tuple[DailyBar, ...]
    open_volume_history: tuple[int, ...]
    expectations: dict[str, Any]
    benchmark_bars: tuple[MarketBar, ...] = ()
    breadth: tuple[tuple[datetime, Decimal], ...] = ()  # keyed by benchmark bar start
    spy_daily_closes: tuple[Decimal, ...] = ()
    spy_5m_abs_return_norm: Decimal | None = None
    macro_events: tuple[MacroEvent, ...] = ()


def _bar(symbol: str, row: dict[str, str]) -> MarketBar:
    start = datetime.fromisoformat(row["start_utc"])
    end = start + timedelta(minutes=1)
    return MarketBar(
        symbol=symbol,
        interval_seconds=60,
        start_utc=start,
        end_utc=end,
        open=Decimal(row["open"]),
        high=Decimal(row["high"]),
        low=Decimal(row["low"]),
        close=Decimal(row["close"]),
        volume=int(row["volume"]),
        provider="fixture",
        received_at_utc=end + timedelta(seconds=1),
        is_final=row["is_final"].strip().lower() == "true",
        coverage="CONSOLIDATED",
    )


def load_fixture(path: Path) -> SessionFixture:
    meta = yaml.safe_load((path / "meta.yaml").read_text(encoding="utf-8"))
    symbol = meta["symbol"]
    with (path / "bars.csv").open(newline="", encoding="utf-8") as fh:
        bars = [_bar(symbol, row) for row in csv.DictReader(fh)]
    bench: list[MarketBar] = []
    breadth: list[tuple[datetime, Decimal]] = []
    if (path / "benchmark.csv").exists():
        with (path / "benchmark.csv").open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                b = _bar("SPY", row)
                bench.append(b)
                breadth.append((b.start_utc, Decimal(row["breadth_above_vwap"])))
    reg = meta.get("regime") or {}
    daily = tuple(
        DailyBar(symbol=symbol, **{k: (Decimal(str(v)) if k != "session_date" else v) for k, v in d.items()})
        for d in meta["daily_history"]
    )
    return SessionFixture(
        name=path.name,
        symbol=symbol,
        session_date=date.fromisoformat(str(meta["session_date"])),
        description=meta["description"],
        bars=tuple(bars),
        daily_history=daily,
        open_volume_history=tuple(int(v) for v in meta["open_volume_history"]),
        expectations=meta.get("expect", {}),
        benchmark_bars=tuple(bench),
        breadth=tuple(breadth),
        spy_daily_closes=tuple(Decimal(str(c)) for c in reg.get("spy_daily_closes", ())),
        spy_5m_abs_return_norm=(
            Decimal(str(reg["spy_5m_abs_return_norm"])) if "spy_5m_abs_return_norm" in reg else None
        ),
        macro_events=tuple(MacroEvent.model_validate(e) for e in reg.get("macro_events", ())),
    )


def discover_fixtures(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if (p / "meta.yaml").exists())
