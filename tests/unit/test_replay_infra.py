from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aats.domain.decisions import AgentResult
from aats.domain.enums import AgentStatus
from aats.replay import EventLog, ReplayClock

T0 = datetime(2026, 10, 14, 13, 30, tzinfo=UTC)


def ev(i: int) -> AgentResult:
    return AgentResult(task_id=f"t{i}", correlation_id="c", role="A1", status=AgentStatus.OK)


def test_hash_chain_verifies_and_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    log = EventLog(path)
    for i in range(5):
        log.append("AgentResult", ev(i), T0 + timedelta(seconds=i))
    assert log.verify()
    reloaded = EventLog.load(path)
    assert reloaded.verify() and reloaded.head_hash == log.head_hash

    lines = path.read_text().splitlines()
    rec = json.loads(lines[2])
    rec["payload"] = rec["payload"].replace("t2", "tX")
    lines[2] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    assert not EventLog.load(path).verify()


def test_deleting_a_record_breaks_chain(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    log = EventLog(path)
    for i in range(3):
        log.append("AgentResult", ev(i), T0)
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert not EventLog.load(path).verify()


def test_clock_is_monotonic() -> None:
    c = ReplayClock(T0)
    c.advance_to(T0 + timedelta(minutes=2))
    c.advance_to(T0 + timedelta(minutes=1))  # late event
    assert c.now() == T0 + timedelta(minutes=2)


def test_clock_requires_aware_start() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ReplayClock(datetime(2026, 10, 14, 13, 30))
