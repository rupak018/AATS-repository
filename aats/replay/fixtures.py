"""Session fixture loader.

A fixture is a directory with:
  meta.yaml  - symbol, session_date, description, daily history, opening-interval volume history
  bars.csv   - 1-minute bars in ARRIVAL order (so duplicates / out-of-order arrivals can be replayed)
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


def load_fixture(path: Path) -> SessionFixture:
    meta = yaml.safe_load((path / "meta.yaml").read_text(encoding="utf-8"))
    symbol = meta["symbol"]
    bars: list[MarketBar] = []
    with (path / "bars.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            start = datetime.fromisoformat(row["start_utc"])
            end = start + timedelta(minutes=1)
            bars.append(
                MarketBar(
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
            )
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
    )


def discover_fixtures(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if (p / "meta.yaml").exists())
