"""Generate the synthetic multi-symbol session fixture(s) (seeded, fully deterministic).

    python tools/make_universe_fixtures.py

Synthetic price paths on a real exchange date. They pin selection, session-replay and decision
behaviour; they say nothing about real market performance.
"""

from __future__ import annotations

import csv
import random
import shutil
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aats.calendar import default_calendar  # noqa: E402
from tools.make_fixtures import ORB, SPY_SHAPES, d2, interp  # noqa: E402

OUT = ROOT / "fixtures" / "universe"

BREAKOUT = [*ORB, (25, 100.90), (40, 101.50), (90, 102.60), (220, 101.90), (389, 101.60)]
LATE_BREAKOUT = [*ORB, (30, 100.95), (50, 101.70), (120, 102.40), (389, 101.80)]
RANGE = [*ORB, (60, 100.60), (150, 100.90), (300, 100.40), (389, 100.70)]
SPIKE = [*ORB, (15, 101.00), (16, 104.40), (40, 104.20), (120, 103.60), (389, 103.00)]


@dataclass
class Member:
    symbol: str
    scale: float  # price level relative to the 100-based shapes
    shape: list[tuple[int, float]]
    rvol_target: float
    gap: float
    premarket_volume: int
    security_type: str = "COMMON_STOCK"
    exchange: str = "XNAS"
    sessions_since_listing: int = 900
    history_days: int = 15
    has_premarket: bool = True
    note: str = ""


MEMBERS = [
    Member("AAA", 1.00, BREAKOUT, 3.0, 0.040, 300_000, note="breakout, highest RVOL -> CANDIDATE"),
    Member("BBB", 0.45, LATE_BREAKOUT, 2.0, 0.030, 400_000, note="later breakout -> CANDIDATE"),
    Member("CCC", 2.50, RANGE, 1.8, 0.020, 120_000, note="stock in play but no breakout"),
    Member("DDD", 0.30, BREAKOUT, 0.6, 0.050, 500_000, note="on watchlist, fails stage-2 RVOL"),
    Member("EEE", 4.50, BREAKOUT, 2.5, 0.010, 900_000, security_type="ETF", note="ETF -> excluded"),
    Member("FFF", 0.03, BREAKOUT, 2.5, 0.080, 2_000_000, note="$3 stock -> excluded"),
    Member(
        "GGG",
        0.80,
        BREAKOUT,
        2.5,
        0.060,
        300_000,
        sessions_since_listing=6,
        history_days=6,
        note="6-session-old IPO -> excluded",
    ),
    Member("HHH", 0.60, SPIKE, 1.4, 0.035, 250_000, note="spike -> REJECT OVEREXTENDED"),
    Member("JJJ", 0.70, BREAKOUT, 2.0, 0.020, 100_000, has_premarket=False, note="no pre-market stats"),
]


def bars_for(m: Member, open_utc: object, minutes: int, rng: random.Random) -> list[dict[str, object]]:
    k = m.scale
    kp = [(t, p * k) for t, p in m.shape]
    rows, prev = [], d2(kp[0][1])
    for i in range(minutes):
        close = max(d2(0.05), d2(interp(kp, i) + rng.gauss(0, 0.03 * k)))
        o = prev if i else d2(kp[0][1] - 0.05 * k)
        hi = max(o, close) + d2(abs(rng.gauss(0, 0.04 * k)))
        lo = max(d2(0.01), min(o, close) - d2(abs(rng.gauss(0, 0.04 * k))))
        shape = 3.0 if i < 15 else (1.6 if i < 60 else (0.8 if i < minutes - 30 else 1.5))
        rows.append(
            {
                "start_utc": (open_utc + timedelta(minutes=i)).isoformat(),  # type: ignore[operator]
                "open": str(o),
                "high": str(hi),
                "low": str(lo),
                "close": str(close),
                "volume": int(20_000 * shape * (0.8 + 0.4 * rng.random())),
                "is_final": "true",
            }
        )
        prev = close
    return rows


def build(name: str, day: date, seed: int) -> None:
    sess = default_calendar().session(day)
    minutes = int((sess.close_utc - sess.open_utc).total_seconds() // 60)
    d = OUT / name
    if d.exists():
        shutil.rmtree(d)
    (d / "bars").mkdir(parents=True)
    members_meta = []
    for idx, m in enumerate(MEMBERS):
        rng = random.Random(seed * 100 + idx)
        rows = bars_for(m, sess.open_utc, minutes, rng)
        with (d / "bars" / f"{m.symbol}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        open_vol = sum(int(r["volume"]) for r in rows[:15])  # type: ignore[call-overload]
        history = [int(open_vol / m.rvol_target * (0.85 + 0.3 * rng.random())) for _ in range(14)]
        daily, c = [], 99.0 * m.scale
        for i in range(m.history_days):
            c += rng.gauss(0, 0.6 * m.scale)
            r = 3.0 * m.scale * (0.8 + 0.4 * rng.random())
            o = c + rng.gauss(0, 0.4 * m.scale)
            daily.append(
                {
                    "session_date": (day - timedelta(days=22 - i)).isoformat(),
                    "open": str(d2(o)),
                    "high": str(d2(max(o, c) + r / 2)),
                    "low": str(d2(min(o, c) - r / 2)),
                    "close": str(d2(c)),
                    "volume": str(int(3_000_000 * (0.8 + 0.4 * rng.random()))),
                }
            )
        prior = d2(c)
        entry: dict[str, object] = {
            "symbol": m.symbol,
            "note": m.note,
            "security_type": m.security_type,
            "primary_exchange": m.exchange,
            "sessions_since_listing": m.sessions_since_listing,
            "corporate_action_in_window": False,
            "open_volume_history": history,
            "daily_history": daily,
        }
        if m.has_premarket:
            entry["premarket"] = {
                "prior_close": str(prior),
                "reference_price": str(d2(float(prior) * (1 + m.gap))),
                "premarket_volume": m.premarket_volume,
            }
        members_meta.append(entry)

    rng = random.Random(seed + 1000)
    kp, breadth = SPY_SHAPES["bull"]
    brows, prev = [], d2(kp[0][1])
    for i in range(minutes):
        close = d2(interp(kp, i) + rng.gauss(0, 0.05))
        brows.append(
            {
                "start_utc": (sess.open_utc + timedelta(minutes=i)).isoformat(),
                "open": str(prev),
                "high": str(max(prev, close) + d2(abs(rng.gauss(0, 0.04)))),
                "low": str(min(prev, close) - d2(abs(rng.gauss(0, 0.04)))),
                "close": str(close),
                "volume": int(400_000 * (0.8 + 0.4 * rng.random())),
                "is_final": "true",
                "breadth_above_vwap": f"{min(1.0, max(0.0, breadth + rng.gauss(0, 0.02))):.4f}",
            }
        )
        prev = close
    with (d / "benchmark.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(brows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(brows)
    closes, c = [], 575.0
    for _ in range(21):
        c *= 1 + rng.gauss(0, 0.006)
        closes.append(str(d2(c)))

    meta = {
        "session_date": day.isoformat(),
        "description": "Nine-symbol universe covering every stage-1 and stage-2 selection outcome, "
        "two breakout candidates and one overextended rejection, in a bull regime.",
        "synthetic": True,
        "seed": seed,
        "expect": {
            "watchlist_rejected": {
                "EEE": "SECURITY_TYPE",
                "FFF": "PRICE",
                "GGG": "IPO_WINDOW",
                "JJJ": "PREMARKET_STATS",
            },
            "failed_rvol": ["DDD"],
            "candidate_symbols": ["AAA", "BBB"],
            "has_reason": {"HHH": "OVEREXTENDED"},
        },
        "regime": {"spy_daily_closes": closes, "spy_5m_abs_return_norm": "0.0012", "macro_events": []},
        "members": members_meta,
    }
    (d / "session.yaml").write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    build("2026-10-28_universe_bull", date(2026, 10, 28), seed=31)
    print("wrote 2026-10-28_universe_bull")
