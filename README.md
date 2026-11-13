# Project AATS

Deterministic, replayable research system for a long-only 15-minute Opening Range Breakout + VWAP
strategy on US equities. **Paper and replay only — there is no live-trading mode.** Design reference:
*AATS Design v1.2*.

This repository is the first vertical slice (milestones M1–M2, backlog AATS-001…005 and the variant A
rule set from AATS-007):

| Area | Where | Status |
|---|---|---|
| Canonical contracts (Pydantic v2) | `aats/domain/` | MarketBar, QuoteSnapshot, DailyBar, FeatureSnapshot, TradeProposal, RiskDecision, OrderIntent, LLMCallRecord, AgentResult, SessionWindow |
| Settings (live mode rejected) | `aats/config/` | done |
| NYSE session calendar | `aats/calendar/` | 2025–2027, DST and early closes; CI cross-checks against `exchange_calendars` |
| Data-quality sentinel | `aats/data/quality.py` | gaps, duplicates, out-of-order, provisional, single-venue, outside-session |
| Feature engine | `aats/features/` | ORB, VWAP (approx), ATR, opening-interval RVOL, ORB width/ATR — Decimal, versioned |
| Strategy variant A | `aats/strategies/orb_vwap/` | close-confirmation rules, every branch has a reason code |
| Replay harness | `aats/replay/` | replay clock, hash-chained event log, deterministic IDs, CLI |
| Golden fixtures | `fixtures/sessions/`, `tests/golden/expected/` | 9 synthetic sessions incl. DST, early close, faults |
| Planned packages | `selection/ risk/ execution/ llm/ orchestration/ backtest/ persistence/ monitoring/` | placeholders with boundaries already enforced by tests |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Python 3.12+
make install
make check                                              # lint + types + tests + golden
make replay FIXTURE=2026-10-14_clean_breakout           # print a replay summary
```

## How the tests are organised

| Suite | What it proves | Runs |
|---|---|---|
| `tests/unit` | Contracts reject bad data; hand-computed feature values; calendar DST/early-close; quality checks; strategy decision table; event-log tamper detection | every push |
| `tests/property` | Invariants on seeded random inputs (VWAP inside price range, ORB bounds, RVOL scale invariance, any dropped bar detected, replay invariants) | every push (60 cases), nightly (1000) |
| `tests/architecture` | Authority boundaries: LLM code can't import execution/risk/config; no wall clock, randomness or `float()` in business logic; no live mode | every push |
| `tests/golden` | Replaying each fixture reproduces the approved summary and event-log hash, twice, identically | every push |
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
