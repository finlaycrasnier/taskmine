# Working on taskmine

For any agent or person changing this repo.

## Commands

```sh
make setup      # .venv, dev dependencies, Swift build, .env from .env.example
make test       # Python and Swift tests; fixture counts are asserted
make run        # the core and the menu-bar app
taskmine doctor # checks the install: paths, permissions, key, database, last pass, last refit
```

## Layout

```
core/taskmine/   contracts.py check.py store.py group/ learn.py model.py review.py cli.py page/
core/tests/
reader/          Swift package: Sources/Taskmine/{Readers,Pipeline,Queue,Upload,Menu} Tests/
data/rules/      the lists behind check
data/fixtures/   credential cases (495 + 57), golden (494), tidy (18/9/15), surfaces (24/18/8), schemas
data/replay/     four recorded days and expected_outputs.json
docs/            ARCHITECTURE.md LEARNING.md CONTRACTS.md images/
```

Dependencies point one way: `review` and `cli` at the top, `contracts` and `check` at the bottom. `group` never imports `review`; `learn` never imports `group`. The core has no runtime dependencies beyond the standard library.

## Rules

- The agent that implements a module does not write its tests. Tests are written first, from the spec, by someone else, and the implementer does not edit them.
- Every change runs `make test`. A fixture count that drops is a failure.
- No text, titles or window content in logs, labels, models or fingerprints. Hashes only.
- No new runtime dependency without a one-line justification in the pull request.
- Prose in docs and comments: short sentences, facts, no filler.
