"""Build the AATS Replay Viewer: one self-contained HTML page for non-technical testers.

    python tools/build_viewer.py                       # writes var/viewer/aats-replay-viewer.html
    python tools/build_viewer.py --out some/file.html

Replays every fixture with the committed configs, translates decisions and selection gates into
plain English, and embeds the result as JSON in viewer/template.html. View-only: floats are used
for display and never feed back into decisions.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aats.domain.decisions import FeatureSnapshot, TradeProposal  # noqa: E402
from aats.domain.enums import ProposalOutcome, ReasonCode  # noqa: E402
from aats.domain.market import MarketBar  # noqa: E402
from aats.regime import load_regime_params  # noqa: E402
from aats.replay import discover_fixtures, load_fixture, run_replay  # noqa: E402
from aats.replay.session import run_session_replay  # noqa: E402
from aats.replay.universe import discover_universe_fixtures, load_universe_fixture  # noqa: E402
from aats.selection import load_selection_params  # noqa: E402
from aats.strategies.orb_vwap import OrbVwapParams, load_params  # noqa: E402

NY = ZoneInfo("America/New_York")
SG = ZoneInfo("Asia/Singapore")
PARAMS = ROOT / "configs" / "strategy" / "orb_vwap_long_a_0.2.0-research.yaml"
REGIME = ROOT / "configs" / "regime" / "regime-1.0.0.yaml"
SELECTION = ROOT / "configs" / "selection" / "selection-1.0.0.yaml"
TEMPLATE = ROOT / "viewer" / "template.html"

TITLES = {
    "2026-10-14_clean_breakout": "Clean breakout",
    "2026-10-15_range_bound": "Stays inside the range",
    "2026-10-16_missing_orb_bar": "Missing data in the first 15 minutes",
    "2026-10-20_overextended_spike": "Spike too far to chase",
    "2026-10-21_low_rvol": "Breakout on ordinary volume",
    "2026-10-22_duplicate_bar": "Duplicate data received",
    "2026-10-23_out_of_order": "Data arrives out of order",
    "2026-10-26_benchmark_missing": "Market feed missing",
    "2026-10-27_benchmark_stale": "Market feed stops at 09:50",
    "2026-11-02_dst_breakout": "Breakout after US clock change",
    "2026-11-27_early_close": "Early-close day (13:00)",
    "2026-10-28_universe_bull": "Full day, nine stocks",
}

PLAIN = {
    "2026-10-14_clean_breakout": "A normal day where the stock trades with unusual volume, then climbs clearly above "
    "its opening range while above VWAP. Expect one buy signal and no second entry.",
    "2026-10-15_range_bound": "The stock stays inside its opening range all morning. Expect the system to keep "
    "waiting and never buy.",
    "2026-10-16_missing_orb_bar": "The 09:37 price bar never arrives. The opening range can't be trusted, so expect "
    "the system to refuse to trade this stock all day.",
    "2026-10-20_overextended_spike": "Right after the opening range, the price jumps far above it. Expect the system "
    "to decline to chase the move.",
    "2026-10-21_low_rvol": "The price breaks out, but on ordinary volume. Expect no trade, because the move lacks "
    "unusual interest.",
    "2026-10-22_duplicate_bar": "The 10:05 price bar is received twice. Expect the system to treat the data as "
    "unreliable from that point and stop trading the stock.",
    "2026-10-23_out_of_order": "Two price bars arrive in the wrong order. Expect the system to detect it and stop "
    "trading the stock.",
    "2026-10-26_benchmark_missing": "The same breakout as the clean day, but the overall market feed is missing. "
    "Market conditions are unknown, so expect no trade.",
    "2026-10-27_benchmark_stale": "The market feed stops at 09:50, before the breakout. Expect conditions to turn "
    "unknown and no trade.",
    "2026-11-02_dst_breakout": "The first trading day after US clocks go back, so the open is an hour later in "
    "Singapore. A news release at 10:00 puts the market in a news-shock window. Expect one buy signal.",
    "2026-11-27_early_close": "The day after Thanksgiving, when the market closes at 13:00. The breakout only "
    "happens after the 11:30 cut-off, so expect no trade.",
    "2026-10-28_universe_bull": "A full morning across nine stocks. Some are excluded before the open, one is dropped "
    "for low opening volume, two give buy signals in the same minute, and one moves too far to chase.",
}

OUTCOME = {
    "CANDIDATE": "Buy signal",
    "WAIT": "Waiting",
    "REJECT": "Rejected",
    "NO_TRADE": "No trade",
}
REGIME_TEXT = {
    "BULL_TREND": "Bull trend",
    "BEAR_TREND": "Bear trend",
    "RANGE_CHOP": "Choppy",
    "HIGH_VOLATILITY": "High volatility",
    "NEWS_SHOCK": "News shock",
    "UNKNOWN": "Unknown",
}


def reason_text(p: OrbVwapParams) -> dict[str, str]:
    last = p.last_entry_time_et.strftime("%H:%M")
    return {
        ReasonCode.DATA_QUALITY_FAILED: "A data problem was found (missing, duplicate or out-of-order prices), "
        "so this stock is not traded today.",
        ReasonCode.ORB_INCOMPLETE: "The opening range could not be built because a price bar in the first "
        f"{p.opening_range_minutes} minutes is missing.",
        ReasonCode.ORB_NOT_READY: "The opening range is still forming.",
        ReasonCode.OUTSIDE_ENTRY_WINDOW: f"It is after {last} New York time, so no new entries are made.",
        ReasonCode.ALREADY_TRADED_TODAY: "This stock already gave a buy signal today. No second entry.",
        ReasonCode.ORB_WIDTH_OUT_OF_BAND: "The opening range is too narrow or too wide compared with the "
        f"stock's normal daily movement (allowed {p.orb_width_atr_min}–{p.orb_width_atr_max} × ATR).",
        ReasonCode.RVOL_BELOW_MIN: f"Opening volume is not unusual enough (needs at least {p.rvol_min}× normal).",
        ReasonCode.RVOL_HISTORY_INSUFFICIENT: "There is not enough volume history to judge whether today is unusual.",
        ReasonCode.PRICE_OUT_OF_BAND: f"The share price is below the ${p.min_price} minimum.",
        ReasonCode.ATR_BELOW_MIN: f"The stock moves too little day to day (average range below ${p.min_atr}).",
        ReasonCode.ORB_BREAKOUT_CONFIRMED: "A one-minute bar closed above the opening-range high plus a small buffer.",
        ReasonCode.ABOVE_SESSION_VWAP: "Price is above the day's average traded price (VWAP).",
        ReasonCode.BELOW_SESSION_VWAP: "Price broke out but is not above VWAP, so the system waits.",
        ReasonCode.NO_BREAKOUT: "Waiting for a close above the opening-range high plus a small buffer.",
        ReasonCode.OVEREXTENDED: "Price has already run too far above the breakout level, so the system "
        f"does not chase it (limit {p.max_extension_atr} × ATR).",
        ReasonCode.STOP_DISTANCE_TOO_SMALL: "The stop would sit too close to the entry price.",
        ReasonCode.REGIME_NOT_ELIGIBLE: "Market conditions are not allowed for this strategy. Here the market "
        "data was missing or out of date, so conditions are unknown.",
    }


GATES = {
    "SECURITY_TYPE": "Ordinary shares only (no ETFs, warrants, SPACs)",
    "EXCHANGE": "Listed on NYSE or Nasdaq",
    "IPO_WINDOW": "Listed for at least {ipo} trading days",
    "CORPORATE_ACTION": "No recent split or other corporate action",
    "PREMARKET_STATS": "Pre-market data available",
    "HISTORY": "At least {hist} days of price history",
    "PRICE": "Price at least ${price}",
    "AVG_DAILY_VOLUME": "Average volume at least {vol} shares a day",
    "AVG_DOLLAR_VOLUME": "Average traded value at least ${dv} a day",
    "ATR": "Average daily range at least ${atr}",
    "RVOL_OPEN": "Opening volume at least {rvol}× normal",
}


def gate_texts() -> dict[str, str]:
    s = load_selection_params(SELECTION)
    fmt = dict(
        ipo=s.ipo_exclusion_sessions,
        hist=s.history_sessions,
        price=s.min_price,
        vol=f"{s.min_avg_daily_volume:,}",
        dv=f"{int(s.min_avg_dollar_volume) / 1e6:g}M",
        atr=s.min_atr,
        rvol=s.rvol_min,
    )
    return {k: v.format(**fmt) for k, v in GATES.items()}


def num(x: Any) -> float | None:
    return None if x is None else round(float(x), 6)


def minute(open_utc: datetime, t: datetime) -> int:
    return int((t - open_utc).total_seconds() // 60)


def symbol_view(
    sym: str,
    bars: tuple[MarketBar, ...],
    snapshots: list[FeatureSnapshot],
    proposals: list[TradeProposal],
    open_utc: datetime,
    reasons: dict[str, str],
) -> dict[str, Any]:
    uniq = sorted({b.start_utc: b for b in bars if b.is_final}.values(), key=lambda b: b.start_utc)
    vwap = {minute(open_utc, s.as_of_utc) - 1: num(s.session_vwap) for s in snapshots}
    orb = next((s.opening_range for s in reversed(snapshots) if s.opening_range is not None), None)
    decisions = {minute(open_utc, p.as_of_utc) - 1: p for p in proposals}  # last decision per minute wins
    runs: list[dict[str, Any]] = []
    for i in sorted(decisions):
        p = decisions[i]
        codes = [r.value for r in p.reason_codes]
        key = (p.outcome.value, codes, p.regime.label.value)
        if (
            runs
            and p.outcome is not ProposalOutcome.CANDIDATE
            and runs[-1]["outcome"] != "CANDIDATE"
            and (runs[-1]["outcome"], runs[-1]["codes"], runs[-1]["regime"]) == key
            and runs[-1]["to"] == i - 1
        ):
            runs[-1]["to"] = i
            continue
        runs.append(
            {
                "from": i,
                "to": i,
                "outcome": p.outcome.value,
                "codes": codes,
                "text": [reasons.get(c, c) for c in codes],
                "regime": p.regime.label.value,
            }
        )
    cand = next((p for p in proposals if p.outcome is ProposalOutcome.CANDIDATE), None)
    candidate = None
    if cand is not None:
        assert cand.entry_reference is not None and cand.stop_reference is not None
        assert cand.expires_at_utc is not None
        candidate = {
            "minute": minute(open_utc, cand.as_of_utc) - 1,
            "entry": num(cand.entry_reference),
            "stop": num(cand.stop_reference),
            "riskPerShare": num(cand.entry_reference - cand.stop_reference),
            "expiresEt": cand.expires_at_utc.astimezone(NY).strftime("%H:%M"),
            "regime": cand.regime.label.value,
        }
    last = snapshots[-1] if snapshots else None
    return {
        "symbol": sym,
        "bars": [
            [minute(open_utc, b.start_utc), num(b.open), num(b.high), num(b.low), num(b.close), b.volume]
            for b in uniq
        ],
        "vwap": [[i, v] for i, v in sorted(vwap.items()) if v is not None],
        "orb": None if orb is None else {"high": num(orb.high), "low": num(orb.low)},
        "atr": num(last.atr14) if last else None,
        "rvol": num(next((s.rvol_open for s in reversed(snapshots) if s.rvol_open is not None), None)),
        "quality": sorted(set(last.quality_flags)) if last else [],
        "runs": runs,
        "candidate": candidate,
        "counts": {k: sum(1 for p in decisions.values() if p.outcome.value == k) for k in OUTCOME},
    }


def regime_runs(labels: dict[int, str]) -> list[list[Any]]:
    out: list[list[Any]] = []
    for i in sorted(labels):
        if out and out[-1][2] == labels[i] and out[-1][1] == i - 1:
            out[-1][1] = i
        else:
            out.append([i, i, labels[i]])
    return out


def session_meta(open_iso: str, close_iso: str, early: bool) -> dict[str, Any]:
    o, c = datetime.fromisoformat(open_iso), datetime.fromisoformat(close_iso)
    return {
        "minutes": minute(o, c),
        "earlyClose": early,
        "openEt": o.astimezone(NY).strftime("%H:%M"),
        "closeEt": c.astimezone(NY).strftime("%H:%M"),
        "openSgt": o.astimezone(SG).strftime("%H:%M"),
        "closeSgt": c.astimezone(SG).strftime("%H:%M"),
        "openEtMinutes": o.astimezone(NY).hour * 60 + o.astimezone(NY).minute,
        "utcOffsetEt": o.astimezone(NY).strftime("%z"),
    }


def build_days() -> list[dict[str, Any]]:
    params, regime_p, sel = load_params(PARAMS), load_regime_params(REGIME), load_selection_params(SELECTION)
    reasons = {k.value: v for k, v in reason_text(params).items()}
    gates = gate_texts()
    days: list[dict[str, Any]] = []

    for d in discover_fixtures(ROOT / "fixtures" / "sessions"):
        fx = load_fixture(d)
        r = run_replay(fx, params, regime_p)
        o = datetime.fromisoformat(r.session_open_utc)
        sv = symbol_view(fx.symbol, fx.bars, list(r.snapshots), list(r.proposals), o, reasons)
        sv["status"] = "traded" if sv["candidate"] else "evaluated"
        days.append(
            {
                "id": d.name,
                "date": fx.session_date.isoformat(),
                "title": TITLES.get(d.name, d.name),
                "kind": "single",
                "description": PLAIN.get(d.name, fx.description),
                "session": session_meta(r.session_open_utc, r.session_close_utc, r.early_close),
                "regime": regime_runs(
                    {minute(o, p.as_of_utc) - 1: p.regime.label.value for p in r.proposals}
                ),
                "selection": None,
                "symbols": [sv],
                "signals": 1 if sv["candidate"] else 0,
            }
        )

    for d in discover_universe_fixtures(ROOT / "fixtures" / "universe"):
        fx = load_universe_fixture(d)
        r = run_session_replay(fx, params, regime_p, sel)
        o = datetime.fromisoformat(r.session_open_utc)
        sip = r.stocks_in_play
        sip_entries = {e.symbol: e for e in sip.entries} if sip else {}
        selection = []
        for e in sorted(r.watchlist.entries, key=lambda e: (e.rank or 999, e.symbol)):
            se = sip_entries.get(e.symbol)
            if not e.eligible:
                stage = "excluded"
            elif se is None:
                stage = "not-ranked"
            elif sip and e.symbol in sip.selected:
                stage = "in-play"
            elif sip and e.symbol in sip.truncated:
                stage = "truncated"
            else:
                stage = "failed-open"
            checks = [
                {"name": gates.get(g.gate, g.gate), "code": g.gate, "passed": g.passed, "value": g.value}
                for g in e.gates
            ]
            if se is not None:
                checks.append(
                    {
                        "name": gates["RVOL_OPEN"],
                        "code": "RVOL_OPEN",
                        "passed": se.gate.passed,
                        "value": se.gate.value,
                    }
                )
            selection.append(
                {
                    "symbol": e.symbol,
                    "stage": stage,
                    "watchRank": e.rank,
                    "playRank": se.rank if se else None,
                    "gap": num(e.gap_pct),
                    "rvol": num(se.rvol_open) if se else None,
                    "checks": checks,
                }
            )
        symbols = []
        for sym, t in sorted(
            r.trackers.items(),
            key=lambda kv: (sip_entries[kv[0]].rank or 999) if kv[0] in sip_entries else 999,
        ):
            sv = symbol_view(sym, fx.bars.get(sym, ()), t.snapshots, t.proposals, o, reasons)
            sv["status"] = ("traded" if sv["candidate"] else "evaluated") if t.proposals else "watch-only"
            symbols.append(sv)
        days.append(
            {
                "id": d.name,
                "date": fx.session_date.isoformat(),
                "title": TITLES.get(d.name, d.name),
                "kind": "universe",
                "description": PLAIN.get(d.name, fx.description),
                "session": session_meta(r.session_open_utc, r.session_close_utc, r.early_close),
                "regime": regime_runs({minute(o, k) - 1: a.label.value for k, a in r.regimes.items()}),
                "selection": selection,
                "symbols": symbols,
                "signals": len(r.candidates()),
                "universeSize": len(fx.members),
            }
        )
    days.sort(key=lambda x: (x["kind"] != "universe", x["date"]))
    return days


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "var" / "viewer" / "aats-replay-viewer.html")
    args = ap.parse_args()
    params = load_params(PARAMS)
    payload = {
        "days": build_days(),
        "outcomes": OUTCOME,
        "regimes": REGIME_TEXT,
        "config": {
            "strategyVersion": params.strategy_version,
            "lastEntryEt": params.last_entry_time_et.strftime("%H:%M"),
            "openingRangeMinutes": params.opening_range_minutes,
        },
    }
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__AATS_DATA__*/null", data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"wrote {args.out} ({len(html) / 1024:.0f} KB, {len(payload['days'])} days)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
