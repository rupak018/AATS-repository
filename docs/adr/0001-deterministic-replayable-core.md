# ADR 0001 - Deterministic, replayable core

**Status:** accepted · **Date:** 2026-10-23

## Context
The design (v1.2, sections 4, 12.3, 15.1) requires that the same inputs and versions always produce
the same decisions, so that golden regression tests, audits and backtests are trustworthy.

## Decision
1. All prices and derived features use `Decimal` with a fixed context and explicit quantisation.
2. All identifiers are UUIDv5 values derived from business keys (`aats.domain.ids.stable_id`).
3. Business logic reads time only from a `Clock`; replays use `ReplayClock`.
4. Every input and output is appended to a hash-chained `EventLog`; the head hash is the replay digest.
5. Contracts are frozen Pydantic models with `extra="forbid"` and timezone-aware UTC timestamps.
6. LLM outputs (from AATS-012) are cached by (model digest, prompt version, input hash, parameters) and
   replays read the cache; a cache miss is recorded, never re-generated.
7. Static architecture tests enforce 1–3 and the authority boundaries.

## Consequences
- Golden files can be compared exactly; any behaviour change is visible in review.
- Slightly more verbose arithmetic than floats; acceptable for correctness.
- Changing a feature formula or rounding rule requires a new `FEATURE_VERSION` and golden update.
