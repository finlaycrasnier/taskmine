"""Shared paths for the test suite. Fixture data lives under data/ at the repo root."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA


@pytest.fixture(scope="session")
def rules_dir() -> Path:
    return DATA / "rules"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return DATA / "fixtures"


@pytest.fixture(scope="session")
def replay_dir() -> Path:
    return DATA / "replay"
