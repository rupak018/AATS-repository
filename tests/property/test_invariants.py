"""Seeded property tests: invariants that must hold for any valid input.

Uses a seeded stdlib RNG rather than an extra dependency, so failures are reproducible from the seed
printed in the test id. Set AATS_PROPERTY_CASES to raise the case count in nightly runs.
"""

from __future__ import annotations

import os
import random
from datetime import timedelta
from decimal import Decimal

import pytest

from aats.data.quality import check_intraday_bars
from aats.domain.decisions import SessionWindow
from aats.domain.enums import ProposalOutcome
from aats.domain.market import MarketBar
from aats.features import opening_range, relative_volume, session_vwap_approx
from aats.replay import load_fixture, run_replay
from aats.replay.fixtures import SessionFixture
from aats.strategies.orb_vwap import OrbVwapParams
from tests.conftest import FIXTURES, make_bar, minute

CASES = int(os.environ.get("AATS_PROPERTY_CASES", "60"))


def random_bars(session: SessionWindow, rng: random.Random, n: int) -> list[MarketBar]:
    out, px = [], Decimal("50") + Decimal(rng.randint(0, 5000)) / 100
    for i in range(n):
        o = px
        c = max(Decimal("1.00"), o + Decimal(rng.randint(-50, 50)) / 100)
        h = max(o, c) + Decimal(rng.randint(0, 30)) / 100
        lo = max(Decimal("0.50"), min(o, c) - Decimal(rng.randint(0, 30)) / 100)
        out.append(make_bar(minute(session, i), str(o), str(h), str(lo), str(c), rng.randint(0, 100_000)))
        px = c
    return out


@pytest.mark.parametrize("seed", range(CASES))
def test_vwap_within_price_envelope(session: SessionWindow, seed: int) -> None:
    rng = random.Random(seed)
    bars = random_bars(session, rng, rng.randint(1, 120))
    v = session_vwap_approx(bars)
    if sum(b.volume for b in bars) == 0:
        assert v is None
    else:
        assert v is not None
        assert min(b.low for b in bars) <= v <= max(b.high for b in bars)


@pytest.mark.parametrize("seed", range(CASES))
def test_orb_bounds_contain_window_and_ignore_later_bars(session: SessionWindow, seed: int) -> None:
    rng = random.Random(seed)
    bars = random_bars(session, rng, 30)
    r = opening_range(bars, session, 15)
    assert r.opening_range is not None
    window = bars[:15]
    assert r.opening_range.high == max(b.high for b in window)
    assert r.opening_range.low == min(b.low for b in window)
    assert opening_range(bars[:16], session, 15) == r  # later bars never change the range


@pytest.mark.parametrize("seed", range(CASES))
def test_rvol_scale_invariant(seed: int) -> None:
    rng = random.Random(seed)
    hist = [rng.randint(1, 10_000) for _ in range(14)]
    cur, k = rng.randint(1, 10_000), rng.randint(2, 9)
    assert relative_volume(cur, hist, 10) == relative_volume(cur * k, [h * k for h in hist], 10)


@pytest.mark.parametrize("seed", range(CASES))
def test_any_dropped_bar_is_detected(session: SessionWindow, seed: int) -> None:
    rng = random.Random(seed)
    bars = random_bars(session, rng, 40)
    drop = rng.randint(0, 38)  # never the last bar (a trailing gap is only visible once the next bar arrives)
    assert not check_intraday_bars(bars[:drop] + bars[drop + 1 :], session).ok


def _perturbed(fx: SessionFixture, rng: random.Random) -> SessionFixture:
    """Shift every price by a random constant and scale volumes: decisions must stay internally valid."""
    shift = Decimal(rng.randint(-300, 300)) / 100
    bars = tuple(
        b.model_copy(
            update={
                "open": b.open + shift,
                "high": b.high + shift,
                "low": b.low + shift,
                "close": b.close + shift,
                "volume": b.volume * 2,
            }
        )
        for b in fx.bars
    )
    return SessionFixture(
        fx.name,
        fx.symbol,
        fx.session_date,
        fx.description,
        bars,
        fx.daily_history,
        tuple(v * 2 for v in fx.open_volume_history),
        fx.expectations,
    )


@pytest.mark.parametrize("seed", range(max(3, CASES // 10)))
def test_replay_invariants_under_perturbation(params: OrbVwapParams, seed: int) -> None:
    rng = random.Random(seed)
    fx = _perturbed(load_fixture(FIXTURES / "2026-10-14_clean_breakout"), rng)
    res = run_replay(fx, params)
    cands = [p for p in res.proposals if p.outcome is ProposalOutcome.CANDIDATE]
    assert len(cands) <= 1  # no re-entry
    for p in cands:
        assert p.stop_reference is not None and p.entry_reference is not None
        assert p.stop_reference < p.entry_reference
        assert p.expires_at_utc is not None and p.expires_at_utc > p.as_of_utc
    # no decision before the opening range is complete
    orb_end = (
        res.snapshots[0].as_of_utc - timedelta(minutes=1) + timedelta(minutes=params.opening_range_minutes)
    )
    assert all(p.as_of_utc >= orb_end for p in res.proposals)
    assert res.log.verify()
