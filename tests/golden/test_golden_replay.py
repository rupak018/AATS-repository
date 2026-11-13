"""Golden replay regression tests.

Each fixture session is replayed and its summary compared field-by-field with the approved file in
tests/golden/expected. A difference means behaviour changed. If the change is intended, run
`make golden-update`, review the diff and explain it in the pull request.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aats.domain.enums import ProposalOutcome
from aats.regime import RegimeParams
from aats.replay import EventLog, discover_fixtures, load_fixture, run_replay
from aats.strategies.orb_vwap import OrbVwapParams
from tests.conftest import FIXTURES, GOLDEN

FIXTURE_DIRS = discover_fixtures(FIXTURES)
pytestmark = pytest.mark.golden


def test_every_fixture_has_a_golden_file() -> None:
    assert FIXTURE_DIRS, "no fixtures found"
    missing = [d.name for d in FIXTURE_DIRS if not (GOLDEN / f"{d.name}.json").exists()]
    orphans = [p.stem for p in GOLDEN.glob("*.json") if not (FIXTURES / p.stem).exists()]
    assert not missing and not orphans, f"missing={missing} orphans={orphans}"


@pytest.mark.parametrize("fixture_dir", FIXTURE_DIRS, ids=lambda p: p.name)
def test_replay_matches_golden(fixture_dir: Path, params: OrbVwapParams, regime_params: RegimeParams) -> None:
    expected = json.loads((GOLDEN / f"{fixture_dir.name}.json").read_text())
    actual = json.loads(json.dumps(run_replay(load_fixture(fixture_dir), params, regime_params).summary()))
    for key in expected:
        assert actual[key] == expected[key], f"{fixture_dir.name}: '{key}' differs from golden"
    assert actual.keys() == expected.keys()


@pytest.mark.parametrize("fixture_dir", FIXTURE_DIRS, ids=lambda p: p.name)
def test_fixture_expectations(fixture_dir: Path, params: OrbVwapParams, regime_params: RegimeParams) -> None:
    """Human-stated intent in meta.yaml, independent of the golden file."""
    fx = load_fixture(fixture_dir)
    res = run_replay(fx, params, regime_params)
    s = res.summary()
    exp = fx.expectations
    if "candidates" in exp:
        assert sum(p.outcome is ProposalOutcome.CANDIDATE for p in res.proposals) == exp["candidates"]
    if "has_reason" in exp:
        assert any(exp["has_reason"] in [r.value for r in p.reason_codes] for p in res.proposals)
    if "quality_issue" in exp:
        assert exp["quality_issue"] in s["quality_issue_codes"]
    if "open_utc" in exp:
        assert s["session"]["open_utc"] == exp["open_utc"]
    if "regime" in exp:
        assert exp["regime"] in s["regime_counts"]
    if "early_close" in exp:
        assert s["session"]["early_close"] is exp["early_close"]


@pytest.mark.parametrize("fixture_dir", FIXTURE_DIRS, ids=lambda p: p.name)
def test_replay_is_deterministic(
    fixture_dir: Path, params: OrbVwapParams, regime_params: RegimeParams, tmp_path: Path
) -> None:
    fx = load_fixture(fixture_dir)
    a = run_replay(fx, params, regime_params, log_path=tmp_path / "a.jsonl")
    b = run_replay(fx, params, regime_params)
    assert a.digest == b.digest
    assert a.log.verify()
    reloaded = EventLog.load(tmp_path / "a.jsonl")
    assert reloaded.verify() and reloaded.head_hash == a.digest
