# Developer shortcuts. Install tooling with: $(PYTHON) -m pip install -e '.[test,dev]'
PYTHON ?= .venv/bin/python
PYTEST ?= SAMBINHA_HTTP_S3_TESTS=1 $(PYTHON) -m pytest -n auto

.PHONY: lint typecheck test test-fast coverage

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy sambinha

## Full suite in parallel, including the local HTTP S3 server tests.
test:
	$(PYTEST)

## Full suite without the HTTP S3 server tests.
test-fast:
	$(PYTHON) -m pytest -n auto

## Full suite with branch coverage; fails below 85%.
coverage:
	$(PYTEST) --cov=sambinha --cov-branch --cov-report=term-missing --cov-fail-under=85
