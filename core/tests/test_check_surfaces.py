"""Exclusion rules of taskmine.check (reader spec 4): app blocklist, URL blocklist,
private windows. A null expectation means the surface stays eligible.

Oracle: data/fixtures/sensitive_surfaces.json (24 apps, 18 urls, 8 private windows).
"""
import json
from pathlib import Path

import pytest
from taskmine import check

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"
SURFACES = json.loads((FIXTURES / "sensitive_surfaces.json").read_text(encoding="utf-8"))
APPS = SURFACES["apps"]
URLS = SURFACES["urls"]
WINDOWS = SURFACES["private_windows"]


def test_surface_counts():
    assert len(APPS) == 24
    assert len(URLS) == 18
    assert len(WINDOWS) == 8


@pytest.mark.parametrize("case", APPS, ids=[c["bundle_id"] for c in APPS])
def test_app_reason(case):
    assert check.app_reason(case["bundle_id"]) == case["expect"]


@pytest.mark.parametrize("case", URLS, ids=[c["url"] for c in URLS])
def test_url_reason(case):
    assert check.url_reason(case["url"]) == case["expect"]


@pytest.mark.parametrize(
    "case",
    WINDOWS,
    ids=[f"{i}_{c['title']}|{','.join(c['labels'])}" for i, c in enumerate(WINDOWS)],
)
def test_is_private_window(case):
    assert check.is_private_window(case["title"], case["labels"]) is case["expect"]
