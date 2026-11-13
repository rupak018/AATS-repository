"""Generate the synthetic golden-session fixtures (seeded, fully deterministic).

The fixtures are SYNTHETIC price paths on real exchange dates. They exist to pin calculation,
session-handling and decision behaviour - not to say anything about real market performance.
Re-running this script must reproduce byte-identical files; CI does not run it.

    python tools/make_fixtures.py            # (re)write fixtures/sessions/*
"""

from __future__ import annotations

import csv
import random
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from itertools import pairwise
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aats.calendar import default_calendar  # noqa: E402

OUT = ROOT / "fixtures" / "sessions"
C = Decimal("0.01")


def d2(x: float) -> Decimal:
    return Decimal(repr(x)).quantize(C, rounding=ROUND_HALF_EVEN)


@dataclass
class Spec:
    name: str
    day: date
    description: str
    keypoints: list[tuple[int, float]]
    seed: int
    rvol_target: float = 2.0
    daily_range: float = 3.0
    expect: dict[str, object] = field(default_factory=dict)
    drop_minutes: tuple[int, ...] = ()
    duplicate_minutes: tuple[int, ...] = ()
    swap_minutes: tuple[int, ...] = ()  # deliver minute m+1 before minute m
    regime: str | None = "bull"  # benchmark shape: bull | chop | bear | highvol | None (no benchmark file)
    benchmark_until: int | None = None  # benchmark feed stops after this minute (stale inputs)
    macro_events: tuple[tuple[str, int, int], ...] = ()  # (name, tier, minute after open)


ORB = [(0, 100.50), (4, 101.05), (9, 100.30), (14, 100.80)]

SPECS = [
    Spec(
        "2026-10-14_clean_breakout",
        date(2026, 10, 14),
        "Valid range, high RVOL, steady breakout above ORB high and VWAP -> one CANDIDATE, then no re-entry.",
        [*ORB, (25, 100.90), (40, 101.50), (90, 102.60), (220, 101.90), (389, 101.60)],
        seed=11,
        expect={"candidates": 1, "regime": "BULL_TREND"},
    ),
    Spec(
        "2026-10-15_range_bound",
        date(2026, 10, 15),
        "Price stays inside the opening range -> WAIT (NO_BREAKOUT) until the last-entry time, then NO_TRADE.",
        [*ORB, (60, 100.60), (150, 100.90), (300, 100.40), (389, 100.70)],
        seed=12,
        regime="chop",
        expect={"candidates": 0, "regime": "RANGE_CHOP"},
    ),
    Spec(
        "2026-10-16_missing_orb_bar",
        date(2026, 10, 16),
        "The 09:37 bar never arrives -> GAP, data quality fails, opening range invalid -> no candidate.",
        [*ORB, (40, 101.60), (120, 102.50), (389, 101.80)],
        seed=13,
        drop_minutes=(7,),
        expect={"candidates": 0, "quality_issue": "GAP"},
    ),
    Spec(
        "2026-10-20_overextended_spike",
        date(2026, 10, 20),
        "Spike far above the range right after 09:45 -> REJECT OVEREXTENDED instead of chasing.",
        [*ORB, (15, 101.00), (16, 104.40), (40, 104.20), (120, 103.60), (389, 103.00)],
        seed=14,
        expect={"has_reason": "OVEREXTENDED"},
    ),
    Spec(
        "2026-10-21_low_rvol",
        date(2026, 10, 21),
        "Breakout on ordinary volume (opening RVOL about 0.5) -> REJECT RVOL_BELOW_MIN.",
        [*ORB, (40, 101.60), (120, 102.50), (389, 101.80)],
        seed=15,
        rvol_target=0.5,
        regime="chop",
        expect={"candidates": 0, "has_reason": "RVOL_BELOW_MIN"},
    ),
    Spec(
        "2026-10-22_duplicate_bar",
        date(2026, 10, 22),
        "A duplicated 10:05 bar arrives before any breakout -> data quality fails, no candidate afterwards.",
        [*ORB, (60, 100.95), (90, 101.80), (200, 102.40), (389, 101.90)],
        seed=16,
        duplicate_minutes=(35,),
        regime="bear",
        expect={"candidates": 0, "quality_issue": "DUPLICATE_BAR"},
    ),
    Spec(
        "2026-10-23_out_of_order",
        date(2026, 10, 23),
        "Bars for 10:20 and 10:21 arrive swapped -> OUT_OF_ORDER detected, quality fails.",
        [*ORB, (60, 100.95), (90, 101.80), (389, 101.90)],
        seed=17,
        swap_minutes=(50,),
        regime="chop",
        expect={"quality_issue": "OUT_OF_ORDER"},
    ),
    Spec(
        "2026-11-02_dst_breakout",
        date(2026, 11, 2),
        "First session after US daylight saving ends: open is 14:30 UTC, not 13:30 -> breakout CANDIDATE.",
        [*ORB, (25, 100.90), (40, 101.50), (90, 102.60), (389, 101.60)],
        seed=18,
        macro_events=(("SYNTHETIC_TIER1_RELEASE", 1, 30),),
        expect={"candidates": 1, "open_utc": "2026-11-02T14:30:00+00:00", "regime": "NEWS_SHOCK"},
    ),
    Spec(
        "2026-11-27_early_close",
        date(2026, 11, 27),
        "Early close (13:00 ET = 18:00 UTC). Breakout only after 11:30 ET -> NO_TRADE outside entry window.",
        [*ORB, (100, 100.70), (130, 100.85), (150, 101.80), (209, 102.20)],
        seed=19,
        regime="highvol",
        expect={"candidates": 0, "early_close": True, "regime": "HIGH_VOLATILITY"},
    ),
    Spec(
        "2026-10-26_benchmark_missing",
        date(2026, 10, 26),
        "Same breakout shape as the clean case but no benchmark feed -> regime UNKNOWN -> REJECT, no trade.",
        [*ORB, (25, 100.90), (40, 101.50), (90, 102.60), (220, 101.90), (389, 101.60)],
        seed=20,
        regime=None,
        expect={"candidates": 0, "has_reason": "REGIME_NOT_ELIGIBLE", "regime": "UNKNOWN"},
    ),
    Spec(
        "2026-10-27_benchmark_stale",
        date(2026, 10, 27),
        "Benchmark feed stops at 09:50 before the breakout -> inputs stale -> UNKNOWN -> REJECT, no trade.",
        [*ORB, (25, 100.90), (40, 101.50), (90, 102.60), (220, 101.90), (389, 101.60)],
        seed=21,
        benchmark_until=20,
        expect={"candidates": 0, "has_reason": "REGIME_NOT_ELIGIBLE"},
    ),
]

SPY_SHAPES = {
    "bull": ([(0, 580.0), (389, 584.0)], 0.70),
    "highvol": ([(0, 580.0), (389, 584.0)], 0.70),
    "chop": ([(0, 580.0), (60, 580.6), (120, 579.6), (200, 580.5), (280, 579.5), (389, 580.0)], 0.50),
    "bear": ([(0, 580.0), (389, 576.0)], 0.30),
}


def interp(kp: list[tuple[int, float]], m: int) -> float:
    for (m0, p0), (m1, p1) in pairwise(kp):
        if m0 <= m <= m1:
            return p0 + (p1 - p0) * (m - m0) / max(1, m1 - m0)
    return kp[-1][1]


def build_benchmark(spec: Spec, sess_open: object, minutes: int) -> dict[str, object] | None:
    """SPY bars + breadth column, from a separate RNG so the symbol's own bars never change."""
    if spec.regime is None:
        return None
    rng = random.Random(spec.seed + 1000)
    kp, breadth = SPY_SHAPES[spec.regime]
    scale = 0.2 if spec.regime in ("chop",) else 1.0
    rows, prev = [], d2(kp[0][1])
    last_minute = minutes if spec.benchmark_until is None else spec.benchmark_until + 1
    for m in range(last_minute):
        close = d2(interp(kp, m) + rng.gauss(0, 0.05 * scale))
        o = prev
        rows.append(
            {
                "start_utc": (sess_open + timedelta(minutes=m)).isoformat(),  # type: ignore[operator]
                "open": str(o),
                "high": str(max(o, close) + d2(abs(rng.gauss(0, 0.04)))),
                "low": str(min(o, close) - d2(abs(rng.gauss(0, 0.04)))),
                "close": str(close),
                "volume": int(400_000 * (0.8 + 0.4 * rng.random())),
                "is_final": "true",
                "breadth_above_vwap": str(
                    Decimal(repr(min(1.0, max(0.0, breadth + rng.gauss(0, 0.02))))).quantize(
                        Decimal("0.0001")
                    )
                ),
            }
        )
        prev = close
    daily_sd = 0.025 if spec.regime == "highvol" else 0.006
    closes, c = [], 575.0
    for _ in range(21):
        c *= 1 + rng.gauss(0, daily_sd)
        closes.append(str(d2(c)))
    events = [
        {"name": n, "tier": t, "scheduled_utc": (sess_open + timedelta(minutes=om)).isoformat()}  # type: ignore[operator]
        for n, t, om in spec.macro_events
    ]
    return {
        "rows": rows,
        "regime": {"spy_daily_closes": closes, "spy_5m_abs_return_norm": "0.0012", "macro_events": events},
    }


def build(spec: Spec) -> None:
    cal = default_calendar()
    sess = cal.session(spec.day)
    minutes = int((sess.close_utc - sess.open_utc).total_seconds() // 60)
    rng = random.Random(spec.seed)
    kp = sorted(spec.keypoints)

    def target(m: int) -> float:
        for (m0, p0), (m1, p1) in pairwise(kp):
            if m0 <= m <= m1:
                return p0 + (p1 - p0) * (m - m0) / max(1, m1 - m0)
        return kp[-1][1]

    rows = []
    prev_close = d2(kp[0][1])
    for m in range(minutes):
        close = d2(target(m) + rng.gauss(0, 0.03))
        o = prev_close if m else d2(kp[0][1] - 0.05)
        hi = max(o, close) + d2(abs(rng.gauss(0, 0.04)))
        lo = min(o, close) - d2(abs(rng.gauss(0, 0.04)))
        shape = 3.0 if m < 15 else (1.6 if m < 60 else (0.8 if m < minutes - 30 else 1.5))
        vol = int(20_000 * shape * (0.8 + 0.4 * rng.random()))
        start = sess.open_utc + timedelta(minutes=m)
        rows.append(
            {
                "start_utc": start.isoformat(),
                "open": str(o),
                "high": str(hi),
                "low": str(lo),
                "close": str(close),
                "volume": vol,
                "is_final": "true",
            }
        )
        prev_close = close

    open_vol = sum(int(r["volume"]) for r in rows[:15])
    hist_base = open_vol / spec.rvol_target
    history = [int(hist_base * (0.85 + 0.3 * rng.random())) for _ in range(14)]

    daily, c = [], 99.0
    for i in range(15):
        c += rng.gauss(0, 0.6)
        r = spec.daily_range * (0.8 + 0.4 * rng.random())
        o = c + rng.gauss(0, 0.4)
        daily.append(
            {
                "session_date": (spec.day - timedelta(days=22 - i)).isoformat(),
                "open": str(d2(o)),
                "high": str(d2(max(o, c) + r / 2)),
                "low": str(d2(min(o, c) - r / 2)),
                "close": str(d2(c)),
                "volume": str(int(3_000_000 * (0.8 + 0.4 * rng.random()))),
            }
        )

    # Inject faults in arrival order.
    arrival = [r for i, r in enumerate(rows) if i not in spec.drop_minutes]
    for m in spec.duplicate_minutes:
        idx = next(i for i, r in enumerate(arrival) if r["start_utc"] == rows[m]["start_utc"])
        arrival.insert(idx + 1, dict(arrival[idx]))
    for m in spec.swap_minutes:
        a = next(i for i, r in enumerate(arrival) if r["start_utc"] == rows[m]["start_utc"])
        arrival[a], arrival[a + 1] = arrival[a + 1], arrival[a]

    d = OUT / spec.name
    d.mkdir(parents=True, exist_ok=True)
    with (d / "bars.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(arrival)
    bench = build_benchmark(spec, sess.open_utc, minutes)
    if bench is not None:
        brows = bench["rows"]
        assert isinstance(brows, list)
        with (d / "benchmark.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(brows[0].keys()), lineterminator="\n")
            w.writeheader()
            w.writerows(brows)
    elif (d / "benchmark.csv").exists():
        (d / "benchmark.csv").unlink()
    meta = {
        "symbol": "ABC",
        "session_date": spec.day.isoformat(),
        "description": spec.description,
        "synthetic": True,
        "seed": spec.seed,
        "open_volume_history": history,
        "daily_history": daily,
        "expect": spec.expect,
    }
    if bench is not None:
        meta["regime"] = bench["regime"]
    (d / "meta.yaml").write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    for s in SPECS:
        build(s)
        print("wrote", s.name)
