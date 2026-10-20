"""Title cleaning of taskmine.check (grouper spec 3.3, reader spec 6.2).

clean_title is the grouping key (normalize_title); tidy_title is the display form that
puts markers where clean_title removes parts. Both clip to 240.
"""
import pytest
from taskmine import check

# The examples that must hold (grouper spec 3.3): (id, title, app_name, cleaned).
GROUPER_EXAMPLES = [
    ("spinner", "⠋ npm run build", None, "npm run build"),
    ("unread_prefix", "(3) Inbox - Mail", None, "Inbox - Mail"),
    ("edited_tail", "Onboarding - Northwind Dental — Edited", None, "Onboarding - Northwind Dental"),
    ("clock", "Standup 10:45 AM", None, "Standup"),
    ("bare_date_kept", "Notes 2026-09-28", None, "Notes 2026-09-28"),
    (
        "app_suffix",
        "northwind - Google Search - Google Chrome",
        "Google Chrome",
        "northwind - Google Search",
    ),
]

# Further rules stated in grouper 3.3 steps 1 to 5.
MORE_CLEAN = [
    ("app_suffix_unknown_app_kept", "northwind - Google Search - Google Chrome", None,
     "northwind - Google Search - Google Chrome"),
    ("app_suffix_en_dash", "Plan – Notion", "Notion", "Plan"),
    ("app_suffix_em_dash", "Plan — Notion", "Notion", "Plan"),
    ("app_suffix_bar", "Plan | Notion", "Notion", "Plan"),
    ("app_suffix_case_insensitive", "Plan - notion", "Notion", "Plan"),
    ("markers_removed", "⟨busy⟩ Standup ⟨clock⟩ ⟨repeat×2⟩", None, "Standup"),
    ("zero_width_removed", "Re\u200bport\ufeff draft", None, "Report draft"),
    ("whitespace_collapsed", "  Quarterly   plan  ", None, "Quarterly plan"),
    ("unread_prefix_plus", "(99+) Inbox", None, "Inbox"),
    ("unread_prefix_brackets", "[12] Slack", None, "Slack"),
    ("unread_tail", "Inbox (3 unread)", None, "Inbox"),
    ("new_tail", "Inbox (5 new)", None, "Inbox"),
    ("count_tail", "Inbox (3)", None, "Inbox"),
    ("edited_parenthesis", "Budget (Edited)", None, "Budget"),
    ("edited_bullet_lower", "Budget • edited", None, "Budget"),
    ("edited_hyphen", "Budget - Edited", None, "Budget"),
    ("stacked_prefixes", "⠋ (3) Inbox", None, "Inbox"),
    ("lone_bar_spinner", "| Building", None, "Building"),
    ("iso_datetime", "Build 2026-09-28T10:45:00Z log", None, "Build log"),
    ("clock_seconds", "Run 14:02:11", None, "Run"),
    ("ago", "Updated 5 minutes ago", None, "Updated"),
    ("separators_trimmed", ": Report ·", None, "Report"),
]

CLEAN_CASES = GROUPER_EXAMPLES + MORE_CLEAN

# tidy_title: the same parts replaced by markers (reader spec 6.2, title_rules order).
TIDY_CASES = [
    ("spinner", "⠋ npm run build", "⟨busy⟩ npm run build"),
    ("unread_prefix", "(3) Inbox - Mail", "⟨count⟩ Inbox - Mail"),
    ("unread_tail", "Inbox (3 unread)", "Inbox ⟨count⟩"),
    ("edited_tail", "Onboarding - Northwind Dental — Edited", "Onboarding - Northwind Dental ⟨edited⟩"),
    ("clock", "Standup 10:45 AM", "Standup ⟨clock⟩"),
    ("ago", "Updated 5 minutes ago", "Updated ⟨clock⟩"),
    ("bare_date_kept", "Notes 2026-09-28", "Notes 2026-09-28"),
    ("zero_width", "Re\u200bport", "Re⟨invisible⟩port"),
    ("app_suffix_kept", "northwind - Google Search - Google Chrome",
     "northwind - Google Search - Google Chrome"),
]


@pytest.mark.parametrize(
    "title,app_name,cleaned",
    [c[1:] for c in CLEAN_CASES],
    ids=[c[0] for c in CLEAN_CASES],
)
def test_clean_title(title, app_name, cleaned):
    assert check.clean_title(title, app_name) == cleaned


@pytest.mark.parametrize(
    "title,app_name,cleaned",
    [c[1:] for c in CLEAN_CASES],
    ids=[c[0] for c in CLEAN_CASES],
)
def test_clean_title_is_stable(title, app_name, cleaned):
    assert check.clean_title(cleaned, app_name) == cleaned


@pytest.mark.parametrize("title,tidied", [c[1:] for c in TIDY_CASES], ids=[c[0] for c in TIDY_CASES])
def test_tidy_title(title, tidied):
    assert check.tidy_title(title) == tidied


@pytest.mark.parametrize("title,tidied", [c[1:] for c in TIDY_CASES], ids=[c[0] for c in TIDY_CASES])
def test_tidy_title_then_clean_matches_clean(title, tidied):
    # Markers are dropped by clean_title, so both routes give the same key.
    assert check.clean_title(tidied) == check.clean_title(title)


def test_clean_title_clips_to_240():
    assert len(check.clean_title("a" * 300)) == 240
    assert check.clean_title("a" * 300) == "a" * 240


def test_tidy_title_clips_to_240():
    assert len(check.tidy_title("b" * 300)) == 240


def test_clean_title_default_app_name():
    assert check.clean_title("(3) Inbox - Mail") == "Inbox - Mail"
