PY := .venv/bin/python
BIN := .venv/bin

.PHONY: install lint format typecheck test test-unit test-scenarios test-determinism check

# Install dev dependencies and the package (editable) into the project venv
install:
	$(PY) -m pip install -r requirements/dev.txt && $(PY) -m pip install -e .

# Lint with ruff
lint:
	$(BIN)/ruff check .

# Format with ruff
format:
	$(BIN)/ruff format .

# Strict type check of src/
typecheck:
	$(BIN)/mypy src

# Run the full test suite
test:
	$(BIN)/pytest -q

# Run unit tests only
test-unit:
	$(BIN)/pytest -q tests/unit

# Run golden scenario tests only
test-scenarios:
	$(BIN)/pytest -q tests/scenarios

# Run determinism and invariant tests only
test-determinism:
	$(BIN)/pytest -q tests/determinism

# Lint, type check and test
check: lint typecheck test
