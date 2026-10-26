# Developer shortcuts. Install tooling with: $(PYTHON) -m pip install -e '.[test,dev]'
PYTHON ?= .venv/bin/python
PYTEST ?= SAMBINHA_HTTP_S3_TESTS=1 $(PYTHON) -m pytest -n auto

.PHONY: lint typecheck test test-fast test-integration coverage

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy sambinha

## Everything (unit + integration) in parallel, including the local HTTP S3 server tests.
test:
	$(PYTEST)

## Unit tests only (tests/unit): no rclone, 7z or network needed; seconds.
test-fast:
	$(PYTHON) -m pytest -n auto tests/unit

## Integration tests only (tests/integration): real rclone/7z, CLI subprocess, Moto/HTTP S3.
test-integration:
	$(PYTEST) tests/integration

## Full suite with branch coverage; fails below 85%.
coverage:
	$(PYTEST) --cov=sambinha --cov-branch --cov-report=term-missing --cov-fail-under=92
