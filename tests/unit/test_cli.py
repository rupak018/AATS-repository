from __future__ import annotations

import json
from pathlib import Path

from aats.replay import EventLog
from aats.replay.cli import main
from tests.conftest import FIXTURES, GOLDEN, PARAMS_FILE, REGIME_FILE


def test_cli_writes_summary_matching_golden_and_event_log(tmp_path: Path) -> None:
    out, log = tmp_path / "s.json", tmp_path / "events.jsonl"
    name = "2026-10-14_clean_breakout"
    rc = main(
        [
            str(FIXTURES / name),
            "--params",
            str(PARAMS_FILE),
            "--regime-params",
            str(REGIME_FILE),
            "--out",
            str(out),
            "--event-log",
            str(log),
        ]
    )
    assert rc == 0
    summary = json.loads(out.read_text())
    assert summary == json.loads((GOLDEN / f"{name}.json").read_text())
    assert EventLog.load(log).head_hash == summary["event_log_head_hash"]
