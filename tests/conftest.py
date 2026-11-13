from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from aats.calendar import default_calendar
from aats.domain.decisions import SessionWindow
from aats.domain.market import MarketBar
from aats.strategies.orb_vwap import OrbVwapParams, load_params

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "sessions"
GOLDEN = ROOT / "tests" / "golden" / "expected"
PARAMS_FILE = ROOT / "configs" / "strategy" / "orb_vwap_long_a_0.2.0-research.yaml"


@pytest.fixture(scope="session")
def params() -> OrbVwapParams:
    return load_params(PARAMS_FILE)


@pytest.fixture(scope="session")
def session() -> SessionWindow:
    return default_calendar().session(datetime(2026, 10, 14).date())


def make_bar(
    start: datetime,
    o: str,
    h: str,
    lo: str,
    c: str,
    v: int = 1000,
    *,
    symbol: str = "ABC",
    final: bool = True,
    coverage: str = "CONSOLIDATED",
) -> MarketBar:
    end = start + timedelta(minutes=1)
    return MarketBar(
        symbol=symbol,
        interval_seconds=60,
        start_utc=start,
        end_utc=end,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(c),
        volume=v,
        provider="test",
        received_at_utc=end + timedelta(seconds=1) if final else end - timedelta(seconds=30),
        is_final=final,
        coverage=coverage,  # type: ignore[arg-type]
    )


def minute(session: SessionWindow, m: int) -> datetime:
    return session.open_utc + timedelta(minutes=m)
