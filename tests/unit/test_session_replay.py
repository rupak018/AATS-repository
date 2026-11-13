"""Behaviour of the multi-symbol session replay beyond the golden file."""

from __future__ import annotations

import dataclasses
import json
from datetime import timedelta
from pathlib import Path

import pytest

from aats.regime import RegimeParams
from aats.replay import run_replay
from aats.replay.cli import session_main
from aats.replay.fixtures import SessionFixture
from aats.replay.session import SessionReplayResult, run_session_replay
from aats.replay.universe import UniverseFixture, load_universe_fixture
from aats.selection import SelectionParams
from aats.strategies.orb_vwap import OrbVwapParams
from tests.conftest import PARAMS_FILE, REGIME_FILE, ROOT, SELECTION_FILE, UNIVERSE_FIXTURES

NAME = "2026-10-28_universe_bull"


@pytest.fixture(scope="module")
def fx() -> UniverseFixture:
    return load_universe_fixture(UNIVERSE_FIXTURES / NAME)


@pytest.fixture(scope="module")
def res(
    fx: UniverseFixture, params: OrbVwapParams, regime_params: RegimeParams, selection_params: SelectionParams
) -> SessionReplayResult:
    return run_session_replay(fx, params, regime_params, selection_params)


def _events(r: SessionReplayResult, kind: str) -> list[dict]:  # type: ignore[type-arg]
    return [json.loads(e.payload) | {"_t": e.recorded_at_utc} for e in r.log if e.event_type == kind]


def test_only_watchlist_symbols_are_subscribed(res: SessionReplayResult, fx: UniverseFixture) -> None:
    received = {e["symbol"] for e in _events(res, "BarReceived")}
    assert received == set(res.watchlist.selected)
    excluded = set(fx.bars) - received
    assert res.not_subscribed_bars == sum(len(fx.bars[s]) for s in excluded)


def test_stage2_runs_once_at_orb_completion_before_any_decision(res: SessionReplayResult) -> None:
    order = [e.event_type for e in res.log]
    assert order.count("Watchlist") == 1 and order[0] == "Watchlist"
    assert order.count("StocksInPlay") == 1
    assert order.index("StocksInPlay") < order.index("TradeProposal")
    first = min(p.as_of_utc for t in res.trackers.values() for p in t.proposals)
    open_utc = res.trackers["AAA"].session.open_utc
    assert first == open_utc + timedelta(minutes=15)


def test_only_stocks_in_play_get_decisions(res: SessionReplayResult) -> None:
    assert res.stocks_in_play is not None
    decided = {sym for sym, t in res.trackers.items() if t.proposals}
    assert decided == set(res.stocks_in_play.selected)


def test_regime_assessed_once_per_minute_and_shared(res: SessionReplayResult) -> None:
    regime_events = _events(res, "RegimeAssessment")
    minutes = {p.as_of_utc for t in res.trackers.values() for p in t.proposals}
    assert len(regime_events) == len(minutes) == len(res.regimes)
    for t in res.trackers.values():
        for p in t.proposals:
            assert res.regimes[p.as_of_utc].assessment_id in p.supporting_evidence_refs


def test_simultaneous_candidates_are_both_recorded(res: SessionReplayResult) -> None:
    # Allocation between them is the Risk Gate's job (AATS-008); replay must surface both.
    times = [c["as_of_utc"] for c in res.candidates()]
    assert len(times) == 2 and len(set(times)) == 1


def test_parity_with_single_symbol_replay(
    res: SessionReplayResult, fx: UniverseFixture, params: OrbVwapParams, regime_params: RegimeParams
) -> None:
    """A selected symbol decides exactly as the single-symbol replay of the same data would."""
    for sym in res.stocks_in_play.selected if res.stocks_in_play else ():
        single = SessionFixture(
            name=sym,
            symbol=sym,
            session_date=fx.session_date,
            description="",
            bars=fx.bars[sym],
            daily_history=fx.stats[sym].daily_history,
            open_volume_history=fx.open_volume_history[sym],
            expectations={},
            benchmark_bars=fx.benchmark.bars,
            breadth=fx.benchmark.breadth,
            spy_daily_closes=fx.benchmark.spy_daily_closes,
            spy_5m_abs_return_norm=fx.benchmark.spy_5m_abs_return_norm,
            macro_events=fx.benchmark.macro_events,
        )
        a = [
            (p.as_of_utc, p.outcome, p.reason_codes, p.regime, p.entry_reference, p.stop_reference)
            for p in res.trackers[sym].proposals
        ]
        b = [
            (p.as_of_utc, p.outcome, p.reason_codes, p.regime, p.entry_reference, p.stop_reference)
            for p in run_replay(single, params, regime_params).proposals
        ]
        assert a == b, sym


def test_top_n_truncation_stops_decisions(
    fx: UniverseFixture, params: OrbVwapParams, regime_params: RegimeParams, selection_params: SelectionParams
) -> None:
    r = run_session_replay(fx, params, regime_params, selection_params.model_copy(update={"top_n": 2}))
    assert r.stocks_in_play is not None
    assert r.stocks_in_play.selected == ("AAA", "BBB")
    assert r.stocks_in_play.truncated == ("CCC", "HHH")
    assert not r.trackers["CCC"].proposals and not r.trackers["HHH"].proposals


def test_missing_opening_bars_fail_stage2_point_in_time(
    fx: UniverseFixture, params: OrbVwapParams, regime_params: RegimeParams, selection_params: SelectionParams
) -> None:
    late = dataclasses.replace(
        fx, bars={**fx.bars, "AAA": fx.bars["AAA"][20:]}
    )  # first 20 minutes never arrive
    r = run_session_replay(late, params, regime_params, selection_params)
    assert r.stocks_in_play is not None and "AAA" not in r.stocks_in_play.selected
    assert r.summary()["stocks_in_play"]["failed_rvol"]["AAA"] == "0.000000"


def test_config_mismatches_are_refused(
    fx: UniverseFixture, params: OrbVwapParams, regime_params: RegimeParams, selection_params: SelectionParams
) -> None:
    with pytest.raises(ValueError, match="regime rule versions"):
        run_session_replay(
            fx,
            params.model_copy(update={"regime_rule_version": "regime-9.0.0"}),
            regime_params,
            selection_params,
        )
    with pytest.raises(ValueError, match="rvol_min_history_sessions"):
        run_session_replay(
            fx, params, regime_params, selection_params.model_copy(update={"rvol_min_history_sessions": 5})
        )


def test_session_cli_matches_golden(tmp_path: Path) -> None:
    out = tmp_path / "s.json"
    rc = session_main(
        [
            str(UNIVERSE_FIXTURES / NAME),
            "--params",
            str(PARAMS_FILE),
            "--regime-params",
            str(REGIME_FILE),
            "--selection-params",
            str(SELECTION_FILE),
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    golden = ROOT / "tests" / "golden" / "expected_universe" / f"{NAME}.json"
    assert json.loads(out.read_text()) == json.loads(golden.read_text())
