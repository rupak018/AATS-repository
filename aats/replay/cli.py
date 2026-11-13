"""Command line: replay one fixture and print (or write) its golden summary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aats.regime import load_regime_params
from aats.replay.engine import run_replay
from aats.replay.fixtures import load_fixture
from aats.replay.session import run_session_replay
from aats.replay.universe import load_universe_fixture
from aats.selection import load_selection_params
from aats.strategies.orb_vwap.params import load_params


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aats-replay", description=__doc__)
    ap.add_argument("fixture", type=Path, help="fixture directory (contains meta.yaml and bars.csv)")
    ap.add_argument("--params", type=Path, required=True, help="strategy parameter YAML")
    ap.add_argument("--regime-params", type=Path, required=True, help="regime rules YAML")
    ap.add_argument("--out", type=Path, help="write summary JSON here instead of stdout")
    ap.add_argument("--event-log", type=Path, help="also write the hash-chained event log (JSONL)")
    args = ap.parse_args(argv)

    result = run_replay(
        load_fixture(args.fixture),
        load_params(args.params),
        load_regime_params(args.regime_params),
        log_path=args.event_log,
    )
    text = json.dumps(result.summary(), indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def session_main(argv: list[str] | None = None) -> int:
    """Replay a multi-symbol universe session and print (or write) its golden summary."""
    ap = argparse.ArgumentParser(prog="aats-replay-session", description=session_main.__doc__)
    ap.add_argument("fixture", type=Path, help="universe fixture directory (contains session.yaml)")
    ap.add_argument("--params", type=Path, required=True, help="strategy parameter YAML")
    ap.add_argument("--regime-params", type=Path, required=True, help="regime rules YAML")
    ap.add_argument("--selection-params", type=Path, required=True, help="selection policy YAML")
    ap.add_argument("--out", type=Path, help="write summary JSON here instead of stdout")
    ap.add_argument("--event-log", type=Path, help="also write the hash-chained event log (JSONL)")
    args = ap.parse_args(argv)

    result = run_session_replay(
        load_universe_fixture(args.fixture),
        load_params(args.params),
        load_regime_params(args.regime_params),
        load_selection_params(args.selection_params),
        log_path=args.event_log,
    )
    text = json.dumps(result.summary(), indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
