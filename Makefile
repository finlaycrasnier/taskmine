PY := .venv/bin/python
PIP := .venv/bin/pip
# The Command Line Tools ship Swift Testing outside the default search path; Xcode does not need the flag.
SWIFT_TEST_FLAGS := $(shell xcode-select -p 2>/dev/null | grep -q CommandLineTools && echo -Xswiftc -F/Library/Developer/CommandLineTools/Library/Developer/Frameworks)

.PHONY: setup test test-python test-swift run lint doctor

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
	cd reader && swift test $(SWIFT_TEST_FLAGS)

lint: .venv
	.venv/bin/ruff check core

doctor: .venv
	$(PY) -m taskmine doctor

run: .venv .env
	@set -a; . ./.env; set +a; \
	./reader/.build/release/Taskmine & \
	$(PY) -m taskmine serve
