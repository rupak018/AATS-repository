"""Multi-symbol session replay driven by stocks-in-play selection (backlog AATS-006b).

Order of work, all point in time and recorded in one hash-chained event log:

1. Pre-market (open - 5 min): stage 1 builds the watchlist from the universe. Only watchlist symbols
   are "subscribed"; bars for other symbols are never ingested (counted, not logged).
2. Bars are processed in arrival batches (same receipt timestamp), ordered by symbol then file order,
   so the event stream is deterministic.
3. On the first batch at or after the opening-range end, stage 2 selects stocks in play from the
   opening-interval volume that has actually arrived.
4. From then on, each selected symbol's bars are evaluated by the strategy. Regime is assessed once
   per decision minute and shared by every symbol in that minute.

Proposals are still replay-only: allocation between simultaneous candidates and order approval
belong to the Risk Gate (AATS-008).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import groupby
from pathlib import Path
from typing import Any

from aats.calendar import SessionCalendar, default_calendar
from aats.domain.base import canonical_dumps, sha256_hex
from aats.domain.enums import ProposalOutcome
from aats.domain.ids import stable_id
from aats.domain.market import MarketBar
from aats.domain.regime import RegimeAssessment
from aats.features.calc import FEATURE_VERSION, atr_sma
from aats.regime import RegimeParams, classify
from aats.replay.clock import ReplayClock
from aats.replay.engine import decision_runs
from aats.replay.event_log import EventLog
from aats.replay.tracker import SymbolTracker, regime_inputs_at
from aats.replay.universe import UniverseFixture
from aats.selection import SelectionParams, StocksInPlay, Watchlist, build_watchlist, select_stocks_in_play
from aats.strategies.orb_vwap.params import OrbVwapParams

PREMARKET_LEAD = timedelta(minutes=5)


def _hash(model: Any) -> str:
    return sha256_hex(canonical_dumps(model.model_dump(mode="json")))


@dataclass(frozen=True)
class SessionReplayResult:
    fixture: str
    session_open_utc: str
    session_close_utc: str
    early_close: bool
    watchlist: Watchlist
    stocks_in_play: StocksInPlay | None
    trackers: dict[str, SymbolTracker]
    regimes: dict[datetime, RegimeAssessment]
    not_subscribed_bars: int
    log: EventLog
    versions: dict[str, str]

    @property
    def digest(self) -> str:
        return self.log.head_hash

    def candidates(self) -> list[dict[str, Any]]:
        out = []
        for sym, t in self.trackers.items():
            for p in t.proposals:
                if p.outcome is ProposalOutcome.CANDIDATE:
                    out.append(
                        {
                            "as_of_utc": p.as_of_utc.isoformat(),
                            "symbol": sym,
                            "entry_reference": str(p.entry_reference),
                            "stop_reference": str(p.stop_reference),
                            "regime": p.regime.label.value,
                        }
                    )
        return sorted(out, key=lambda c: (c["as_of_utc"], c["symbol"]))

    def summary(self) -> dict[str, Any]:
        wl = self.watchlist
        sip = self.stocks_in_play
        return {
            "fixture": self.fixture,
            "session": {
                "open_utc": self.session_open_utc,
                "close_utc": self.session_close_utc,
                "early_close": self.early_close,
            },
            "versions": dict(sorted(self.versions.items())),
            "watchlist": {
                "selected": list(wl.selected),
                "truncated": list(wl.truncated),
                "rejected": {
                    e.symbol: sorted(g.gate for g in e.gates if not g.passed)
                    for e in wl.entries
                    if not e.eligible
                },
            },
            "stocks_in_play": None
            if sip is None
            else {
                "selected": [
                    {"symbol": e.symbol, "rank": e.rank, "rvol_open": str(e.rvol_open)}
                    for e in sorted(sip.entries, key=lambda e: e.rank or 10**9)
                    if e.symbol in sip.selected
                ],
                "truncated": list(sip.truncated),
                "failed_rvol": {
                    e.symbol: None if e.rvol_open is None else str(e.rvol_open)
                    for e in sip.entries
                    if not e.gate.passed
                },
            },
            "not_subscribed_bars": self.not_subscribed_bars,
            "symbols": {
                sym: {
                    "outcome_counts": dict(sorted(Counter(p.outcome.value for p in t.proposals).items())),
                    "decision_runs": decision_runs(tuple(t.proposals)),
                }
                for sym, t in sorted(self.trackers.items())
                if t.proposals
            },
            "candidates": self.candidates(),
            "regime_counts": dict(sorted(Counter(a.label.value for a in self.regimes.values()).items())),
            "event_count": len(self.log),
            "event_log_head_hash": self.digest,
        }


def run_session_replay(
    fx: UniverseFixture,
    params: OrbVwapParams,
    regime_params: RegimeParams,
    selection_params: SelectionParams,
    *,
    calendar: SessionCalendar | None = None,
    log_path: Path | None = None,
) -> SessionReplayResult:
    if regime_params.rule_version != params.regime_rule_version:
        raise ValueError("strategy params and regime params reference different regime rule versions")
    if selection_params.rvol_min_history_sessions != params.rvol_min_history_sessions:
        raise ValueError("selection and strategy disagree on rvol_min_history_sessions")
    cal = calendar or default_calendar()
    session = cal.session(fx.session_date)
    session_id = stable_id("session", fx.session_date.isoformat(), params.strategy_version)
    orb_end = session.open_utc + timedelta(minutes=params.opening_range_minutes)
    clock = ReplayClock(session.open_utc - PREMARKET_LEAD)
    log = EventLog(log_path)

    # Stage 1 - pre-market watchlist.
    watchlist = build_watchlist(fx.session_date, fx.members, fx.stats, selection_params)
    log.append("Watchlist", watchlist, clock.now())
    trackers = {
        sym: SymbolTracker(
            symbol=sym,
            session=session,
            session_id=session_id,
            params=params,
            atr14=atr_sma(fx.stats[sym].daily_history, 14),
            open_volume_history=fx.open_volume_history.get(sym, ()),
        )
        for sym in watchlist.selected
    }
    not_subscribed = sum(len(b) for s, b in fx.bars.items() if s not in trackers)

    events: list[tuple[datetime, str, int, MarketBar]] = sorted(
        (
            (bar.received_at_utc, sym, i, bar)
            for sym in trackers
            for i, bar in enumerate(fx.bars.get(sym, ()))
        ),
        key=lambda e: (e[0], e[1], e[2]),
    )
    sip: StocksInPlay | None = None
    regimes: dict[datetime, RegimeAssessment] = {}

    for received, group in groupby(events, key=lambda e: e[0]):
        now = clock.advance_to(received)
        batch = []
        for _, sym, _, bar in group:
            ev = log.append("BarReceived", bar, now)
            snap = trackers[sym].ingest(bar, ev.seq)
            log.append("FeatureSnapshot", snap, now)
            batch.append((sym, bar, ev.seq, snap))

        # Stage 2 - stocks in play, once, from the opening volume that has arrived by now.
        if sip is None and now >= orb_end:
            opening = {s: t.opening_volume() for s, t in trackers.items()}
            sip = select_stocks_in_play(watchlist, opening, fx.open_volume_history, selection_params)
            log.append("StocksInPlay", sip, now)
        if sip is None:
            continue

        for sym, bar, seq, snap in batch:
            if sym not in sip.selected or bar.end_utc < orb_end:
                continue
            regime = regimes.get(bar.end_utc)
            if regime is None:
                regime = classify(regime_inputs_at(fx.benchmark, bar.end_utc, now), regime_params)
                regimes[bar.end_utc] = regime
                log.append("RegimeAssessment", regime, now)
            proposal = trackers[sym].decide(snap, str(seq), regime, cal.to_exchange_time(bar.end_utc))
            log.append("TradeProposal", proposal, now)

    return SessionReplayResult(
        fixture=fx.name,
        session_open_utc=session.open_utc.isoformat(),
        session_close_utc=session.close_utc.isoformat(),
        early_close=session.early_close,
        watchlist=watchlist,
        stocks_in_play=sip,
        trackers=trackers,
        regimes=regimes,
        not_subscribed_bars=not_subscribed,
        log=log,
        versions={
            "feature_version": FEATURE_VERSION,
            "params_hash": params.parameter_hash(),
            "regime_rule_version": regime_params.rule_version,
            "regime_params_hash": _hash(regime_params),
            "selection_policy_version": selection_params.policy_version,
            "selection_params_hash": _hash(selection_params),
        },
    )
