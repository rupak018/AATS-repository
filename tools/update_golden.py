"""Regenerate golden replay files after an INTENDED behaviour change.

    python tools/update_golden.py --yes

Then review `git diff tests/golden/expected` line by line and explain the change in the PR.
Never run this just to make a failing test pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aats.replay import discover_fixtures, load_fixture, run_replay  # noqa: E402
from aats.strategies.orb_vwap import load_params  # noqa: E402

PARAMS = ROOT / "configs" / "strategy" / "orb_vwap_long_a_0.2.0-research.yaml"
EXPECTED = ROOT / "tests" / "golden" / "expected"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true", help="confirm you intend to overwrite golden files")
    args = ap.parse_args()
    if not args.yes:
        print("Refusing to overwrite golden files without --yes", file=sys.stderr)
        return 2
    EXPECTED.mkdir(parents=True, exist_ok=True)
    params = load_params(PARAMS)
    for d in discover_fixtures(ROOT / "fixtures" / "sessions"):
        summary = run_replay(load_fixture(d), params).summary()
        (EXPECTED / f"{d.name}.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", "utf-8"
        )
        print("updated", d.name, summary["event_log_head_hash"][:12])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
