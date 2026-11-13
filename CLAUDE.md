# AATS - working notes for AI coding assistants

Read `README.md` first. Design source of truth: AATS Design v1.2 (section numbers below refer to it).

## Non-negotiables
- No live trading code paths. `Mode` has only test/replay/paper; do not add others.
- Only `aats/execution` may talk to a broker; only the Risk Gate (`aats/risk`) may approve an order.
- `aats/llm` must never import `aats.execution`, `aats.risk` or `aats.config`. LLM output is shadow-only.
- No `datetime.now()`, `random`, `uuid4()` or `float()` in `aats/` (except `replay/clock.py`). Use the clock,
  `stable_id()` and `Decimal`. `tests/architecture` enforces this.
- Contracts are frozen and `extra="forbid"`. Add fields deliberately and bump `schema_version` when breaking.
- Strategy parameters never get code defaults (design 8). Config values are research hypotheses.

## Workflow
- `make check` must pass before every commit.
- Never run `make golden-update` to silence a failing test. Only after an intended behaviour change,
  and explain the diff in the PR.
- New behaviour = new unit test + (if it changes decisions) new or updated fixture and golden file.
- New reason codes: append to `ReasonCode`; never rename or reuse a value.

## Next backlog items
AATS-006 stocks-in-play selection + regime rules · AATS-008 Risk Gate + allocation queue ·
AATS-009 simulated OMS with bracket orders · AATS-010 cost/fill model · AATS-012 LLM shadow pipeline.
