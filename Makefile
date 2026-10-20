PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: setup test test-python test-swift run lint

setup: .venv .env
	cd reader && swift build -c release

.venv:
	python3 -m venv .venv
	$(PIP) install -q -e ".[dev]"

.env:
	cp .env.example .env

test: test-python test-swift

test-python: .venv
	$(PY) -m pytest -q

test-swift:
	cd reader && swift test

lint: .venv
	.venv/bin/ruff check core

run: .venv .env
	$(PY) -m taskmine serve & \
	open reader/.build/release/Taskmine.app 2>/dev/null || true; \
	wait
