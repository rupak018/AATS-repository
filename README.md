# Project AATS

Deterministic, replayable research system for a long-only 15-minute Opening Range Breakout + VWAP
strategy on US equities. **Paper and replay only — there is no live-trading mode.** Design reference:
*AATS Design v1.2*.

Built so far: milestones M1–M2 (backlog AATS-001…005), the variant A rule set (AATS-007) and
stocks-in-play selection plus regime rules (AATS-006) and multi-symbol session replay (AATS-006b):

| Area | Where | Status |
|---|---|---|
| Canonical contracts (Pydantic v2) | `aats/domain/` | MarketBar, QuoteSnapshot, DailyBar, FeatureSnapshot, TradeProposal, RiskDecision, OrderIntent, LLMCallRecord, AgentResult, SessionWindow |
| Settings (live mode rejected) | `aats/config/` | done |
| NYSE session calendar | `aats/calendar/` | 2025–2027, DST and early closes; CI cross-checks against `exchange_calendars` |
| Data-quality sentinel | `aats/data/quality.py` | gaps, duplicates, out-of-order, provisional, single-venue, outside-session |
| Feature engine | `aats/features/` | ORB, VWAP (approx), ATR, opening-interval RVOL, ORB width/ATR — Decimal, versioned |
| Regime rules | `aats/regime/`, `configs/regime/` | design 8.6 precedence: UNKNOWN > NEWS_SHOCK > HIGH_VOLATILITY > BULL > BEAR > RANGE_CHOP; realised-vol fallback until VIX is licensed |
| Stocks-in-play selection | `aats/selection/`, `configs/selection/` | stage 1 pre-market watchlist (type, exchange, IPO, corporate action, price, volume, $ volume, ATR) and stage 2 RVOL top-N, with every gate result and truncation recorded |
| Strategy variant A | `aats/strategies/orb_vwap/` | close-confirmation rules incl. regime eligibility gate, every branch has a reason code |
| Replay harness | `aats/replay/` | replay clock, hash-chained event log, deterministic IDs, CLI |
| Session replay | `aats/replay/session.py`, `fixtures/universe/` | whole-session, multi-symbol replay: pre-market watchlist → only watchlist symbols subscribed → stocks in play chosen at opening-range completion → strategy decisions per selected symbol, regime shared per minute |
| Golden fixtures | `fixtures/sessions/`, `tests/golden/expected/` | 11 synthetic sessions incl. DST, early close, data faults, missing and stale benchmark feed; each with SPY benchmark + breadth for regime inputs |
| Planned packages | `risk/ execution/ llm/ orchestration/ backtest/ persistence/ monitoring/` | placeholders with boundaries already enforced by tests |

Candidates from different symbols can fire in the same minute (the universe fixture shows two at
14:06 UTC); choosing between them is the Risk Gate's allocation queue, the next step (AATS-008).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Python 3.12+
make install
make check                                              # lint + types + tests + golden
make replay FIXTURE=2026-10-14_clean_breakout           # single-symbol replay summary
make replay-session FIXTURE=2026-10-28_universe_bull    # multi-symbol session replay summary
```

## Replay viewer (for non-technical testers)

`make viewer` writes `var/viewer/aats-replay-viewer.html`: one self-contained page, no install needed.
For every test day it shows market conditions, how stocks were chosen (multi-stock day), a price
chart with the opening range, VWAP, buy signal and stop, a minute-by-minute decision strip, and a
plain-English log of what the system decided and why. Source: `viewer/template.html` +
`tools/build_viewer.py`. Simulated data only.

## How the tests are organised

| Suite | What it proves | Runs |
|---|---|---|
| `tests/unit` | Contracts reject bad data; hand-computed feature values; calendar DST/early-close; quality checks; strategy decision table; event-log tamper detection | every push |
| `tests/property` | Invariants on seeded random inputs (VWAP inside price range, ORB bounds, RVOL scale invariance, any dropped bar detected, replay invariants) | every push (60 cases), nightly (1000) |
| `tests/architecture` | Authority boundaries: LLM code can't import execution/risk/config; no wall clock, randomness or `float()` in business logic; no live mode | every push |
| `tests/golden` | Replaying each single-symbol and universe fixture reproduces the approved summary and event-log hash, twice, identically | every push |
| `tests/integration` | Committed NYSE calendar equals `exchange_calendars` for 2025–2027 | every push (skipped if not installed) |

### When a golden test fails

It means decisions changed. If you did not intend that, fix the code. If you did (for example a new
parameter value), run `make golden-update`, review `git diff tests/golden/expected` line by line, and
explain the change in the pull request. CI uploads the diff as an artifact on failure.

## Guardrails (from the design)

- Deterministic code decides; LLMs are shadow-only in v1 (`LLMCallRecord` rejects any other mode).
- Every timestamp is timezone-aware UTC; session maths uses the exchange calendar, never a fixed offset.
- Prices are `Decimal`; IDs are UUIDv5 from business keys; time comes from a clock object.
- Strategy parameters have no code defaults — a missing value disables the strategy.
- Entry `OrderIntent`s must be BRACKET orders with a broker-held stop below the limit.

Fixtures are **synthetic** price paths on real exchange dates. They pin behaviour; they say nothing
about real market performance.
