"""Text tidy of taskmine.check (reader spec 6.1, normalisation version 2).

Oracle: data/fixtures/capture_normalization.json (18 texts, 9 urls, 15 clock lines).
"""
import json
import re
from pathlib import Path

import pytest
from taskmine import check

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"
NORM = json.loads((FIXTURES / "capture_normalization.json").read_text(encoding="utf-8"))
TEXTS = NORM["texts"]
URLS = NORM["urls"]
CLOCK_LINES = NORM["clock_lines"]


def case_ids(prefix: str, cases: list[dict]) -> list[str]:
    out = []
    for i, c in enumerate(cases):
        slug = re.sub(r"[^A-Za-z0-9]+", "_", c["input"]).strip("_")[:32] or "empty"
        out.append(f"{prefix}{i:02d}_{slug}")
    return out


def test_normalization_counts():
    assert len(TEXTS) == 18
    assert len(URLS) == 9
    assert len(CLOCK_LINES) == 15


@pytest.mark.parametrize("case", TEXTS, ids=case_ids("text", TEXTS))
def test_tidy(case):
    assert check.tidy(case["input"]) == case["expect"]


@pytest.mark.parametrize("case", TEXTS, ids=case_ids("text", TEXTS))
def test_tidy_is_idempotent(case):
    assert check.tidy(case["expect"]) == case["expect"]


@pytest.mark.parametrize("case", URLS, ids=case_ids("url", URLS))
def test_tidy_url(case):
    assert check.tidy_url(case["input"]) == case["expect"]


@pytest.mark.parametrize("case", CLOCK_LINES, ids=case_ids("clock", CLOCK_LINES))
def test_is_clock_line(case):
    assert check.is_clock_line(case["input"]) is case["expect"]
