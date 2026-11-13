"""Append-only, hash-chained event log.

Each record stores the hash of the previous record, so any edit, deletion or reordering of history
is detectable (`verify()`). The head hash of a replay is its decision digest: two replays with the
same inputs and versions must produce the same head hash.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aats.domain.base import Contract, canonical_dumps, sha256_hex

GENESIS = "0" * 64


@dataclass(frozen=True)
class EventRecord:
    seq: int
    event_type: str
    recorded_at_utc: str
    payload: str  # canonical JSON
    prev_hash: str
    hash: str

    def to_json(self) -> str:
        return canonical_dumps(self.__dict__)


def _record_hash(seq: int, event_type: str, recorded_at: str, payload: str, prev_hash: str) -> str:
    return sha256_hex(canonical_dumps([seq, event_type, recorded_at, payload, prev_hash]))


class EventLog:
    def __init__(self, path: Path | None = None) -> None:
        self._records: list[EventRecord] = []
        self._path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    @property
    def head_hash(self) -> str:
        return self._records[-1].hash if self._records else GENESIS

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[EventRecord]:
        return iter(self._records)

    def append(self, event_type: str, payload: Contract, recorded_at: datetime) -> EventRecord:
        seq = len(self._records)
        body = payload.canonical_json()
        ts = recorded_at.isoformat()
        rec = EventRecord(
            seq, event_type, ts, body, self.head_hash, _record_hash(seq, event_type, ts, body, self.head_hash)
        )
        self._records.append(rec)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(rec.to_json() + "\n")
        return rec

    def verify(self) -> bool:
        prev = GENESIS
        for i, r in enumerate(self._records):
            if r.seq != i or r.prev_hash != prev:
                return False
            if r.hash != _record_hash(r.seq, r.event_type, r.recorded_at_utc, r.payload, r.prev_hash):
                return False
            prev = r.hash
        return True

    @classmethod
    def load(cls, path: Path) -> EventLog:
        log = cls()
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                log._records.append(EventRecord(**json.loads(line)))
        return log
