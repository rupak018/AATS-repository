"""Golden regression for multi-symbol session replay (selection -> strategy, AATS-006b)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aats.domain.enums import ProposalOutcome
from aats.regime import RegimeParams
from aats.replay import EventLog
from aats.replay.session import run_session_replay
from aats.replay.universe import discover_universe_fixtures, load_universe_fixture
from aats.selection import SelectionParams
from aats.strategies.orb_vwap import OrbVwapParams
from tests.conftest import ROOT

UNIVERSE = ROOT / "fixtures" / "universe"
GOLDEN_U = ROOT / "tests" / "golden" / "expected_universe"
DIRS = discover_universe_fixtures(UNIVERSE)
pytestmark = pytest.mark.golden


def test_every_universe_fixture_has_a_golden_file() -> None:
    assert DIRS, "no universe fixtures found"
    missing = [d.name for d in DIRS if not (GOLDEN_U / f"{d.name}.json").exists()]
    orphans = [p.stem for p in GOLDEN_U.glob("*.json") if not (UNIVERSE / p.stem).exists()]
    assert not missing and not orphans, f"missing={missing} orphans={orphans}"


@pytest.mark.parametrize("d", DIRS, ids=lambda p: p.name)
def test_session_matches_golden(
    d: Path, params: OrbVwapParams, regime_params: RegimeParams, selection_params: SelectionParams
) -> None:
    expected = json.loads((GOLDEN_U / f"{d.name}.json").read_text())
    actual = json.loads(
        json.dumps(
            run_session_replay(load_universe_fixture(d), params, regime_params, selection_params).summary()
        )
    )
    for key in expected:
        assert actual[key] == expected[key], f"{d.name}: '{key}' differs from golden"
    assert actual.keys() == expected.keys()


@pytest.mark.parametrize("d", DIRS, ids=lambda p: p.name)
def test_session_expectations(
    d: Path, params: OrbVwapParams, regime_params: RegimeParams, selection_params: SelectionParams
) -> None:
    fx = load_universe_fixture(d)
    res = run_session_replay(fx, params, regime_params, selection_params)
    s = res.summary()
    exp = fx.expectations
    for sym, gate in exp.get("watchlist_rejected", {}).items():
        assert gate in s["watchlist"]["rejected"][sym]
    assert sorted(s["stocks_in_play"]["failed_rvol"]) == sorted(exp.get("failed_rvol", []))
    assert sorted({c["symbol"] for c in s["candidates"]}) == sorted(exp.get("candidate_symbols", []))
    for sym, reason in exp.get("has_reason", {}).items():
        assert any(reason in [r.value for r in p.reason_codes] for p in res.trackers[sym].proposals)
    for t in res.trackers.values():
        assert sum(p.outcome is ProposalOutcome.CANDIDATE for p in t.proposals) <= 1


@pytest.mark.parametrize("d", DIRS, ids=lambda p: p.name)
def test_session_is_deterministic(
    d: Path,
    params: OrbVwapParams,
    regime_params: RegimeParams,
    selection_params: SelectionParams,
    tmp_path: Path,
) -> None:
    fx = load_universe_fixture(d)
    a = run_session_replay(fx, params, regime_params, selection_params, log_path=tmp_path / "s.jsonl")
    b = run_session_replay(fx, params, regime_params, selection_params)
    assert a.digest == b.digest and a.log.verify()
    assert EventLog.load(tmp_path / "s.jsonl").head_hash == a.digest
