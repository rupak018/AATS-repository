.PHONY: install lint fmt type test golden property check golden-update fixtures replay

PY ?= python

install:          ## create the dev environment (run inside a virtualenv)
	$(PY) -m pip install -e ".[dev,calendar]"

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff format .
	ruff check --fix .

type:
	mypy

test:             ## everything except golden
	pytest tests --ignore=tests/golden

golden:           ## replay regression against approved golden files
	pytest tests/golden -v

property:         ## deep property run (as nightly CI)
	AATS_PROPERTY_CASES=1000 pytest tests/property

check: lint type test golden   ## what CI runs

golden-update:    ## ONLY after an intended behaviour change; then review the diff
	$(PY) tools/update_golden.py --yes
	git diff --stat tests/golden/expected

fixtures:         ## regenerate synthetic fixtures (deterministic; normally never needed)
	$(PY) tools/make_fixtures.py

replay:           ## example: make replay FIXTURE=2026-10-14_clean_breakout
	aats-replay fixtures/sessions/$(FIXTURE) --params configs/strategy/orb_vwap_long_a_0.2.0-research.yaml
