"""Unit tests for taskmine.group, written from the grouper spec (taskmine-specs/grouper-spec.md).

Section numbers in comments refer to that spec. Title cleaning belongs to taskmine.check and is not
tested here; titles in these tests are already clean.

Shapes these tests fix where the spec leaves them open:
- Actions are dicts with the fields of grouper spec 1.5 plus the four anchor fields
  (case_keys, artifact_key, surface_key, window_key) from `extract`.
- `Session`, `Episode`, `Signature`, `Name` expose their fields as attributes.
- Signature steps and artifacts are dicts: steps {kind, app, evidence, ...},
  artifacts {app, title, output, ...}. `Signature(steps=, apps=, artifacts=)` builds one.
- `segment` takes `dict[session index -> taskmine.learn.Decision]` and writes the join_saved
  change (4.7) back into that dict.
- `attach_gaps(episodes, gaps)` returns {gap id: index into episodes} (an Episode value is also accepted).
- `weigh(sessions)[session.index]` is that session's {word: link weight}.
- `build_templates(members)` returns templates as strings or as items with a `text` field.
- `build_processes(pairs)` takes [(episode a, episode b, score)] of `same` pairs and returns
  {episode id: process id}.
- `runs(members)` takes member dicts {id, task_id, started_at, ended_at, steps} and returns runs
  with `members` (ids in order), `count` and `median_seconds`.
"""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta

import pytest
from taskmine.group import (
    Signature,
    attach_gaps,
    build_processes,
    build_templates,
    case_conflict,
    compose,
    different_cases,
    extract,
    facts,
    features,
    furniture_words,
    name,
    object_rule,
    parse,
    render,
    reusable,
    runs,
    segment,
    sessions,
    signature,
    similarity,
    step_kind,
    summary,
    template_match,
    tokens,
    trivial,
    verb_rule,
    weigh,
)
from taskmine.group import text as signature_text
from taskmine.learn import Decision

SALT = b"test-salt"
FLAGS = ["idle", "case_conflict", "shared_anchor", "same_surface", "copy_paste",
         "search_then_create", "new_search", "context_saved", "shared_words", "recent",
         "same_app", "closed"]
NUMBERS = ["gap", "prev_seconds", "context_seconds", "link_strong", "link_weak",
           "hour_sin", "hour_cos", "to_browser"]
HEX32 = re.compile(r"^[0-9a-f]{32}$")
BASE = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
TEXTEDIT, NUMBERS_APP, CHROME = "com.apple.TextEdit", "com.apple.Numbers", "com.google.Chrome"
FINDER, SLACK, GHOSTTY, CALC = ("com.apple.finder", "com.tinyspeck.slackmacgap",
                                "com.mitchellh.ghostty", "org.libreoffice.script")
APP_NAMES = {TEXTEDIT: "TextEdit", NUMBERS_APP: "Numbers", CHROME: "Google Chrome",
             FINDER: "Finder", SLACK: "Slack", GHOSTTY: "Ghostty", CALC: "LibreOffice"}
SEARCH_URL = "https://www.google.com/search"


# ---------------------------------------------------------------- helpers

def ts(seconds: float) -> str:
    return (BASE + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def raw(id: str, t: float = 0, app: str = TEXTEDIT, dur: float = 0, **fields) -> dict:
    """One action with every field of grouper spec 1.5; no anchors."""
    action = {"id": id, "at": ts(t), "first_seen_at": ts(t), "last_seen_at": ts(t + dur),
              "app": app, "app_name": APP_NAMES.get(app, app), "interaction": None, "title": None,
              "text": "", "url": None, "document_id": None, "window_ref": None, "browser": False,
              "withheld": False, "sightings": 1, "duration_s": int(dur), "focus_name": None,
              "shortcut": None, "furniture_lines": [], "case_ref": None, "external_ok": True}
    action.update(fields)
    if action["url"] and "browser" not in fields:
        action["browser"] = True
    return action


def act(id: str, t: float = 0, app: str = TEXTEDIT, dur: float = 0, **fields) -> dict:
    """One action with its anchors, as the store hands it to the grouper."""
    action = raw(id, t, app, dur, **fields)
    found = extract(action, SALT)
    action.update(case_keys=list(found.case_keys), artifact_key=found.artifact_key,
                  surface_key=found.surface_key, window_key=found.window_key)
    return action


def page(id: str, t: float, title: str, url: str = SEARCH_URL, **fields) -> dict:
    """A browser page action."""
    fields.setdefault("interaction", "navigation")
    return act(id, t, CHROME, title=title, url=url, **fields)


def keys(**fields) -> list:
    return list(extract(raw("x", **fields), SALT).case_keys)


def ids(session) -> list[str]:
    return [item["id"] for item in session.items]


def get(item, key):
    return item[key] if isinstance(item, dict) else getattr(item, key)


def switch(actions: list[dict]) -> dict:
    """Features of the switch into the last session, with every earlier session as context."""
    found = sessions(actions)
    assert len(found) >= 2
    return features(found[:-1], found[-2], found[-1], furniture_words(found), weigh(found))


def same(reason: str = "shared_anchor") -> Decision:
    return Decision("same", 0.95, reason, True)


def broke(reason: str = "gap", hard: bool = False) -> Decision:
    return Decision("break", 1.0 if hard else 0.65, reason, hard)


def diverted() -> Decision:
    return Decision("diversion", 0.60, "switch", False)


def episode_of(episodes, index: int):
    return next(e for e in episodes if index in [s.index for s in e.sessions])


def indexes(episode) -> list[int]:
    return [s.index for s in episode.sessions]


# ---------------------------------------------------------------- anchors (2.2)

def test_issue_key_in_title_gives_one_hashed_case():
    found = extract(raw("a", title="Fix CS-1042 billing"), SALT)
    assert len(found.case_keys) == 1
    namespace, value = found.case_keys[0]
    assert HEX32.match(namespace) and HEX32.match(value)


def test_leading_zeros_are_removed_from_issue_keys():
    assert keys(title="CS-01042 refund") == keys(title="CS-1042 refund")


def test_same_prefix_shares_namespace_and_differs_in_value():
    (ns_a, value_a), = keys(title="CS-1042")
    (ns_b, value_b), = keys(title="CS-1057")
    assert ns_a == ns_b and value_a != value_b


@pytest.mark.parametrize("title", [
    "ISO-9001 audit", "UTF-8 encoding", "SHA-256 digest", "GPT-4 notes", "RFC-2822 headers",
    "COVID-19 policy", "HTTP-2 upgrade", "FY-2026 budget", "MACOS-15 release", "KPI-3 review",
])
def test_ignored_prefixes_never_count(title):
    assert keys(title=title) == []


@pytest.mark.parametrize("title", ["xCS-1042", "CS-1042a", "cs-1042", "C-1042", "CS-12345678"])
def test_issue_key_needs_clean_boundaries(title):
    assert keys(title=title) == []


def test_page_text_never_gives_a_case():
    assert keys(title="Inbox", text="CS-1042\nCS-1057\nSUP-1187") == []


def test_focused_element_gives_an_issue_key():
    # 2.2 step 4 and 9.2: "Displaying timesheet CS-1057" in the focused element.
    focused = keys(title="Inbox (1) - Gmail", focus_name="Displaying timesheet CS-1057")
    assert focused == keys(title="CS-1057")


def test_case_ref_wins_over_title_and_is_stripped():
    from_ref = keys(title="CS-1042", case_ref=" case-a ")
    assert len(from_ref) == 1
    assert from_ref == keys(case_ref="case-a")
    assert from_ref != keys(title="CS-1042")


def test_url_case_path():
    one = keys(url="https://support.example.com/tickets/48213")
    assert len(one) == 1
    assert one == keys(url="https://support.example.com/tickets/48213/notes")
    assert keys(url="https://support.example.com/tickets/48214")[0][0] == one[0][0]
    assert keys(url="https://other.example.com/tickets/48213")[0][0] != one[0][0]
    assert keys(url="https://example.com/about/team") == []


def test_url_case_path_with_issue_key_equals_title_key():
    assert keys(url="https://jira.example.com/browse/CS-1042") == keys(title="CS-1042")


def test_artifact_from_document_and_url():
    one = extract(raw("a", title="Report", document_id="doc-1"), SALT).artifact_key
    assert HEX32.match(one)
    assert one == extract(raw("b", title="Report v2", document_id="doc-1"), SALT).artifact_key
    assert one != extract(raw("c", title="Report", document_id="doc-2"), SALT).artifact_key
    assert extract(raw("d", url="https://docs.example.com/d/abc123/edit"), SALT).artifact_key
    assert extract(raw("e", url="https://example.com/about/team"), SALT).artifact_key is None


@pytest.mark.parametrize("title", ["Untitled", "untitled 3", "UNTITLED — Edited"])
def test_untitled_document_gives_no_artifact(title):
    assert extract(raw("a", title=title, document_id="doc-1"), SALT).artifact_key is None


def test_surface_needs_a_title_or_url():
    assert HEX32.match(extract(raw("a", title="Report"), SALT).surface_key)
    assert HEX32.match(extract(raw("b", url="https://example.com/a"), SALT).surface_key)
    assert extract(raw("c"), SALT).surface_key is None


def test_window_is_scoped_to_app_and_ref():
    one = extract(raw("a", window_ref="window:w1"), SALT).window_key
    assert HEX32.match(one)
    assert one == extract(raw("b", window_ref="window:w1", title="Other"), SALT).window_key
    assert one != extract(raw("c", 0, NUMBERS_APP, window_ref="window:w1"), SALT).window_key
    assert extract(raw("d", window_ref="bad ref"), SALT).window_key is None
    assert extract(raw("e"), SALT).window_key is None


def test_salt_changes_every_hash():
    action = raw("a", title="CS-1042", document_id="doc-1", window_ref="w1")
    one, two = extract(action, SALT), extract(action, b"other-salt")
    assert one.case_keys != two.case_keys
    assert one.artifact_key != two.artifact_key
    assert one.surface_key != two.surface_key
    assert one.window_key != two.window_key


def test_different_cases_and_case_conflict():
    cs1042, cs1057, sup = keys(title="CS-1042"), keys(title="CS-1057"), keys(title="SUP-1187")
    both = keys(title="CS-1042 vs CS-1057")
    assert different_cases(cs1042, cs1057) and case_conflict(cs1042, cs1057)
    assert not different_cases(cs1042, cs1042) and not case_conflict(cs1042, cs1042)
    # Unrelated namespaces are different cases (2.2) but not a conflict in one namespace.
    assert different_cases(cs1042, sup) and not case_conflict(cs1042, sup)
    # Shared pair, but the CS value sets differ.
    assert different_cases(both, cs1042) and case_conflict(both, cs1042)
    assert not different_cases([], cs1042) and not case_conflict([], cs1042)


# ---------------------------------------------------------------- sessions (3.1)

def test_surface_keys():
    found = sessions([
        act("doc", 0, title="Report", document_id="doc-1"),
        act("url", 10, CHROME, title="Team", url="https://Docs.Example.com//a//b/"),
        act("new", 20, title="Untitled", window_ref="w9"),
        act("title", 30, GHOSTTY, title="Release Checks"),
    ])
    assert [s.key for s in found] == [
        f"{TEXTEDIT}|doc:doc-1", f"{CHROME}|url:docs.example.com/a/b",
        f"{TEXTEDIT}|new:w9", f"{GHOSTTY}|title:release checks",
    ]
    assert [s.index for s in found] == [0, 1, 2, 3]


def test_session_gap_is_120_seconds_inclusive():
    found = sessions([act("a", 0, title="Plan"), act("b", 120, title="Plan"), act("c", 241, title="Plan")])
    assert [ids(s) for s in found] == [["a", "b"], ["c"]]


def test_a_b_a_makes_three_sessions():
    found = sessions([act("a", 0, title="A", document_id="doc-a"),
                      act("b", 5, title="B", document_id="doc-b"),
                      act("a2", 10, title="A", document_id="doc-a")])
    assert [ids(s) for s in found] == [["a"], ["b"], ["a2"]]


def test_item_without_key_joins_its_app_session():
    found = sessions([act("a", 0, title="Report", document_id="doc-1"),
                      act("click", 5, interaction="click"),
                      act("other", 6, GHOSTTY, interaction="click")])
    assert [ids(s) for s in found] == [["a", "click"], ["other"]]
    assert found[1].key == f"{GHOSTTY}|unknown"


def test_items_sort_snapshot_before_action_in_one_second():
    # 1.2: sort by (at, is_action, id).
    found = sessions([act("a1", 5, title="Report", interaction="click"),
                      act("z9", 5, title="Report"),
                      act("b0", 0, title="Report")])
    assert ids(found[0]) == ["b0", "z9", "a1"]


def test_session_fields():
    found = sessions([act("a", 0, dur=10, title="Report", text="x" * 50),
                      act("b", 12, title="Report", interaction="edit", focus_name="Body"),
                      act("c", 20, dur=5, title="Report", interaction="shortcut", shortcut="cmd+s")])
    s, = found
    assert s.app == TEXTEDIT
    assert s.duration == 25
    assert s.edits == 2
    assert s.chars == 50 + len("Body")
    assert s.title == "Report"
    assert s.identity == s.key + "|report"


def test_withheld_gap_never_forms_or_splits_a_session():
    found = sessions([act("a", 0, title="Report", document_id="doc-1"),
                      act("gap", 5, withheld=True),
                      act("b", 10, title="Report", document_id="doc-1")])
    assert [ids(s) for s in found] == [["a", "b"]]


# ---------------------------------------------------------------- trivial (3.2)

def one_session(*actions):
    found = sessions(list(actions))
    assert len(found) == 1
    return found[0]


def test_trivial_session():
    assert trivial(one_session(act("a", 0, title="Note", text="short"), act("b", 5, title="Note")))


@pytest.mark.parametrize("actions", [
    [act("a", 0, title="Note"), act("b", 5, title="Note"), act("c", 8, title="Note")],
    [act("a", 0, title="Note"), act("b", 20, title="Note")],
    [act("a", 0, title="Note"), act("b", 5, title="Note", interaction="edit", focus_name="Body")],
    [act("a", 0, title="Note"), act("b", 5, title="Note", interaction="shortcut", shortcut="cmd+z")],
    [act("a", 0, title="Note", text="x" * 200)],
    [act("a", 0, title="Note", text="x" * 150), act("b", 5, title="Note", interaction="click", focus_name="y" * 60)],
], ids=["three actions", "20 seconds", "edit", "shortcut", "200 chars", "chars with focus names"])
def test_not_trivial(actions):
    assert not trivial(one_session(*actions))


# ---------------------------------------------------------------- features (4.4)

def test_feature_names_and_order():
    f = switch([act("a", 0, title="Alpha", document_id="doc-1"),
                act("b", 30, NUMBERS_APP, title="Beta", document_id="doc-2")])
    assert list(f) == FLAGS + NUMBERS
    assert all(f[name] in (0, 1) for name in FLAGS)
    assert f["hour_sin"] ** 2 + f["hour_cos"] ** 2 == pytest.approx(1.0)


def test_idle():
    f = switch([act("a", 0, title="Alpha", document_id="doc-1"),
                act("b", 700, NUMBERS_APP, title="Beta", document_id="doc-2")])
    assert f["idle"] == 1 and f["recent"] == 0
    assert f["gap"] == pytest.approx(math.log1p(700))
    edge = switch([act("a", 0, title="Alpha", document_id="doc-1"),
                   act("b", 600, NUMBERS_APP, title="Beta", document_id="doc-2")])
    assert edge["idle"] == 0


def test_case_conflict():
    f = switch([act("a", 0, title="Invoice INV-4827"), act("b", 10, title="Ticket SUP-1187")])
    assert f["case_conflict"] == 1
    g = switch([act("a", 0, title="Invoice INV-4827"), act("b", 10, title="Approval INV-4827")])
    assert g["case_conflict"] == 0 and g["shared_anchor"] == 1


def test_shared_anchor_by_document_and_by_window():
    f = switch([act("f", 0, FINDER, title="Documents", document_id="opaque-doc-a"),
                act("t", 3, title="Checklist", document_id="opaque-doc-a")])
    assert f["shared_anchor"] == 1
    g = switch([act("a", 0, title="Alpha", document_id="doc-1", window_ref="w1"),
                act("b", 5, title="Beta", document_id="doc-2", window_ref="w1")])
    assert g["shared_anchor"] == 1
    h = switch([act("a", 0, title="Alpha", document_id="doc-1"),
                act("b", 5, NUMBERS_APP, title="Beta", document_id="doc-2")])
    assert h["shared_anchor"] == 0


def test_same_surface():
    team = "https://example.com/about/team"
    f = switch([page("p1", 0, "Team", url=team), act("n", 10, title="Notes", document_id="doc-n"),
                page("p2", 20, "Team", url=team)])
    assert f["same_surface"] == 1
    late = switch([page("p1", 0, "Team", url=team), act("n", 10, title="Notes", document_id="doc-n"),
                   page("p2", 140, "Team", url=team)])
    assert late["same_surface"] == 0
    by_title = switch([act("g1", 0, GHOSTTY, title="build"), act("n", 10, title="Notes", document_id="doc-n"),
                       act("g2", 20, GHOSTTY, title="build")])
    assert by_title["same_surface"] == 0


def test_copy_paste():
    def run(paste_at):
        return switch([act("s", 0, title="Source", document_id="doc-s"),
                       act("c", 5, title="Source", document_id="doc-s", interaction="shortcut", shortcut="cmd+c"),
                       act("v", paste_at, NUMBERS_APP, title="Target", document_id="doc-t",
                           interaction="shortcut", shortcut="cmd+v")])
    assert run(20)["copy_paste"] == 1
    assert run(200)["copy_paste"] == 0


def test_search_then_create():
    def run(new_at):
        return switch([page("p", 0, "acme - Google Search"),
                       act("n", new_at, title="Untitled", window_ref="w-new",
                           interaction="shortcut", shortcut="cmd+n")])
    assert run(30)["search_then_create"] == 1
    assert run(100)["search_then_create"] == 0


def test_new_search_and_context_saved():
    def run(saved):
        actions = [act("e", 0, title="Report draft", document_id="doc-r", interaction="edit", focus_name="Body")]
        if saved:
            actions.append(act("s", 10, title="Report draft", document_id="doc-r",
                               interaction="shortcut", shortcut="cmd+s"))
        return switch(actions + [page("p", 30, "harbour cafe - Google Search")])
    f = run(saved=True)
    assert f["new_search"] == 1 and f["context_saved"] == 1 and f["to_browser"] == 1
    g = run(saved=False)
    assert g["new_search"] == 1 and g["context_saved"] == 0


def test_new_search_needs_a_new_subject():
    f = switch([act("e", 0, title="Harbour cafe onboarding", document_id="doc-r",
                    interaction="edit", focus_name="Body"),
                page("p", 30, "harbour cafe - Google Search")])
    assert f["new_search"] == 0


def test_shared_words():
    f = switch([act("a", 0, title="Northwind onboarding", document_id="doc-1"),
                act("b", 10, NUMBERS_APP, title="Northwind onboarding budget", document_id="doc-2")])
    assert f["shared_words"] == 1 and f["recent"] == 1
    assert f["link_strong"] == pytest.approx(2.0)  # two words, rarity 1.0 with fewer than 3 sessions (4.3)
    g = switch([act("a", 0, title="Northwind onboarding", document_id="doc-1"),
                act("b", 10, NUMBERS_APP, title="Quarterly budget", document_id="doc-2")])
    assert g["shared_words"] == 0


def test_same_app():
    f = switch([act("a", 0, title="Alpha", document_id="doc-1"),
                act("b", 5, NUMBERS_APP, title="Beta", document_id="doc-2"),
                act("c", 10, title="Gamma", document_id="doc-3")])
    assert f["same_app"] == 1
    g = switch([act("a", 0, title="Alpha", document_id="doc-1"),
                act("b", 5, NUMBERS_APP, title="Beta", document_id="doc-2")])
    assert g["same_app"] == 0


def test_closed():
    def run(close):
        actions = [act("e", 0, title="Alpha", document_id="doc-1", interaction="edit", focus_name="Body")]
        if close:
            actions.append(act("w", 5, title="Alpha", document_id="doc-1", interaction="shortcut", shortcut="cmd+w"))
        return switch(actions + [act("b", 10, NUMBERS_APP, title="Beta", document_id="doc-2")])
    assert run(True)["closed"] == 1
    assert run(False)["closed"] == 0


def test_durations_enter_as_log():
    f = switch([act("a", 0, dur=5, title="Alpha", document_id="doc-1"),
                act("a2", 20, TEXTEDIT, 10, title="Alpha", document_id="doc-1"),
                act("b", 40, NUMBERS_APP, title="Beta", document_id="doc-2")])
    assert f["prev_seconds"] == pytest.approx(math.log1p(30))
    assert f["context_seconds"] == pytest.approx(math.log1p(30))
    assert f["gap"] == pytest.approx(math.log1p(10))


def test_furniture_words():
    # 4.3: "Google Search" in two separate searches with other work between is furniture.
    found = sessions([page("p1", 0, "northwind dental - Google Search"),
                      act("t", 10, title="Onboarding - Northwind Dental", document_id="doc-n",
                          text="Onboarding note for Northwind Dental."),
                      page("p2", 30, "harbour cafe - Google Search")])
    words = furniture_words(found)
    assert {"google", "search"} <= words
    assert "northwind" not in words


def test_furniture_word_shown_between_is_not_furniture():
    found = sessions([page("p1", 0, "acme invoice - Google Search"),
                      act("t", 10, title="Acme note", document_id="doc-a"),
                      page("p2", 30, "acme ticket - Google Search")])
    words = furniture_words(found)
    assert "acme" not in words and "search" in words


def test_weigh_rarity():
    # 4.3: with N >= 3 sessions, a word in every session has rarity ln 2 / ln 3.
    found = sessions([act("a", 0, title="Shared alpha", document_id="d1"),
                      act("b", 5, NUMBERS_APP, title="Shared beta", document_id="d2"),
                      act("c", 10, title="Shared gamma", document_id="d3"),
                      act("d", 15, NUMBERS_APP, title="Shared delta", document_id="d4")])
    weights = weigh(found)
    first = weights[found[0].index]
    assert first["shared"] == pytest.approx(math.log(2) / math.log(3), abs=1e-3)
    assert first["alpha"] == pytest.approx(1.0)


# ---------------------------------------------------------------- segment (4.6.2, 4.7)

def test_joins_make_one_open_episode():
    found = sessions([act("a", 0, title="A", document_id="d1"), act("b", 5, NUMBERS_APP, title="B", document_id="d1")])
    episodes = segment(found, {1: same()})
    assert [indexes(e) for e in episodes] == [[0, 1]]
    assert episodes[0].close_reason is None


def test_break_and_idle_close_reasons():
    found = sessions([act("a", 0, title="A", document_id="d1"),
                      act("b", 5, NUMBERS_APP, title="B", document_id="d2"),
                      act("c", 900, title="C", document_id="d3")])
    episodes = segment(found, {1: broke("gap"), 2: broke("idle", hard=True)})
    assert [indexes(e) for e in episodes] == [[0], [1], [2]]
    assert [e.close_reason for e in episodes] == ["break", "idle", None]


def test_short_diversion_resumes():
    # 9.4 "A short diversion resumes".
    found = sessions([act("t1", 10, dur=25, title="Onboarding - Northwind Dental", document_id="doc-n", window_ref="w1"),
                      act("s", 40, SLACK, dur=25, title="general - Team chat"),
                      act("t2", 70, dur=25, title="Onboarding - Northwind Dental", document_id="doc-n", window_ref="w1")])
    episodes = segment(found, {1: diverted(), 2: diverted()})
    assert sorted(indexes(e) for e in episodes) == [[0, 2], [1]]
    slack = episode_of(episodes, 1)
    assert slack.close_reason == "diversion" and slack.diversion


def test_diversion_longer_than_120_seconds_does_not_resume():
    found = sessions([act("t1", 0, dur=10, title="Report", document_id="doc-r", window_ref="w1"),
                      act("s", 15, SLACK, dur=160, title="general - Team chat"),
                      act("t2", 180, title="Report", document_id="doc-r", window_ref="w1")])
    episodes = segment(found, {1: diverted(), 2: broke("gap")})
    assert [indexes(e) for e in episodes] == [[0], [1], [2]]
    assert episodes[0].close_reason == "diversion"
    assert episodes[1].close_reason == "break" and not episodes[1].diversion


def test_case_conflict_within_90_seconds_is_an_interruption_that_resumes():
    # 9.3: invoice, ticket, invoice again.
    found = sessions([act("i1", 0, title="Invoice INV-4827", document_id="doc-a", window_ref="w-a"),
                      act("i2", 13, title="Invoice INV-4827", document_id="doc-a", window_ref="w-a"),
                      act("k1", 15, dur=13, title="Ticket SUP-1187", document_id="doc-t", window_ref="w-t"),
                      act("i3", 30, dur=3, title="Invoice INV-4827", document_id="doc-a", window_ref="w-a")])
    conflict = broke("case_conflict", hard=True)
    episodes = segment(found, {1: conflict, 2: conflict})
    assert sorted(indexes(e) for e in episodes) == [[0, 2], [1]]
    assert episode_of(episodes, 1).close_reason == "break"


def test_case_conflict_after_90_seconds_does_not_resume():
    found = sessions([act("i1", 0, dur=13, title="Invoice INV-4827", document_id="doc-a", window_ref="w-a"),
                      act("k1", 120, dur=13, title="Ticket SUP-1187", document_id="doc-t", window_ref="w-t"),
                      act("i3", 135, dur=3, title="Invoice INV-4827", document_id="doc-a", window_ref="w-a")])
    conflict = broke("case_conflict", hard=True)
    episodes = segment(found, {1: conflict, 2: conflict})
    assert [indexes(e) for e in episodes] == [[0], [1], [2]]
    assert episodes[1].own_case  # 4.9: a hard case_conflict starts it


def test_episode_is_capped_at_7200_seconds():
    found = sessions([act("a", 0, dur=10, title="A", document_id="d1"),
                      act("b", 3600, NUMBERS_APP, title="B", document_id="d1"),
                      act("c", 7190, dur=10, title="C", document_id="d1"),
                      act("d", 7300, NUMBERS_APP, title="D", document_id="d1")])
    episodes = segment(found, {1: same(), 2: same(), 3: same()})
    assert [indexes(e) for e in episodes] == [[0, 1, 2], [3]]


def harbour(saved_title: str, search: str = "harbour cafe group - Google Search"):
    return sessions([page("p1", 0, search), page("p2", 10, search, interaction=None, text="Harbour results"),
                     act("u1", 160, title="Untitled", window_ref="w9"),
                     act("u2", 170, title="Untitled", window_ref="w9", interaction="edit", focus_name="Text"),
                     act("u3", 180, title="Untitled", window_ref="w9", interaction="click", focus_name="Save"),
                     act("d1", 190, title=saved_title, document_id="doc-h", window_ref="w9")])


def test_join_saved_merges_search_and_late_new_document():
    # 4.7 example: search "harbour cafe group", saved name "onboarding - harbou cafe".
    found = harbour("onboarding - harbou cafe")
    assert len(found) == 3
    decisions = {1: broke("gap"), 2: same()}
    episodes = segment(found, decisions)
    assert [indexes(e) for e in episodes] == [[0, 1, 2]]
    assert decisions[1].choice == "supporting"
    assert decisions[1].reason == "saved_as_search"
    assert decisions[1].hard


def test_join_saved_relates_by_five_letter_prefix():
    found = harbour("onboarding - harbou", search="harbour - Google Search")
    assert [indexes(e) for e in segment(found, {1: broke("gap"), 2: same()})] == [[0, 1, 2]]


def test_join_saved_needs_a_related_name():
    found = harbour("meeting notes")
    decisions = {1: broke("gap"), 2: same()}
    assert [indexes(e) for e in segment(found, decisions)] == [[0], [1, 2]]
    assert decisions[1].reason == "gap"


def test_join_saved_never_crosses_idle():
    found = harbour("onboarding - harbou cafe")
    assert len(segment(found, {1: broke("idle", hard=True), 2: same()})) == 2


# ---------------------------------------------------------------- withheld gaps (1.4)

def test_attach_gaps():
    found = sessions([act("a1", 100, title="A", document_id="d1"), act("a2", 130, title="A", document_id="d1"),
                      act("b1", 1000, NUMBERS_APP, title="B", document_id="d2"),
                      act("b2", 1030, NUMBERS_APP, title="B", document_id="d2")])
    episodes = segment(found, {1: broke("idle", hard=True)})
    gaps = [act(g, t, withheld=True, dur=5) for g, t in
            [("inside", 110), ("before", 0), ("mid", 500), ("after", 1500), ("far", 5000), ("late", 300)]]
    placed = attach_gaps(episodes, gaps)
    where = {g: (v if isinstance(v, int) else next(i for i, e in enumerate(episodes) if e is v))
             for g, v in placed.items()}
    first, second = (episodes.index(episode_of(episodes, i)) for i in (0, 1))
    assert where == {"inside": first,   # inside the span
                     "before": first,   # first episode starting within 600 s after
                     "mid": second,     # first episode starting within 600 s after
                     "after": second,   # last episode ending within 600 s before
                     "far": second,     # nearest start
                     "late": first}     # last episode ending within 600 s before


# ---------------------------------------------------------------- step kinds (5.1)

@pytest.mark.parametrize("shortcut, kind", [
    ("cmd+s", "save"), ("Cmd+Shift+S", "save"), ("cmd + option + s", "save"), ("command+n", "create"),
    ("cmd+w", "close"), ("cmd+c", "copy"), ("cmd+x", "copy"), ("cmd+v", "paste"),
    ("cmd+opt+shift+v", "paste"), ("cmd+t", "browse"), ("cmd+l", "browse"), ("cmd+return", "send"),
    ("cmd+enter", "send"), ("cmd+shift+d", "send"), ("cmd+z", None),
])
def test_shortcut_kinds(shortcut, kind):
    assert step_kind(raw("a", title="Note", interaction="shortcut", shortcut=shortcut)) == kind


def test_shortcut_parsed_from_text_and_missing_shortcut():
    assert step_kind(raw("a", title="Note", interaction="shortcut", text="Pressed Cmd+Shift+V")) == "paste"
    assert step_kind(raw("b", title="Note", interaction="shortcut")) is None


def test_edit_is_search_on_a_search_page_else_type():
    assert step_kind(raw("a", 0, CHROME, interaction="edit", url=SEARCH_URL, title="acme - Google Search")) == "search"
    assert step_kind(raw("b", 0, CHROME, interaction="edit", url="https://mail.google.com/mail/u/0",
                         title="Inbox - Gmail")) == "type"
    assert step_kind(raw("c", interaction="edit", title="Report")) == "type"


@pytest.mark.parametrize("label, kind", [
    ("Save...", "save"), ("Save…", "save"), ("Send now", "send"), ("Submit form", "send"),
    ("Create invoice", "create"), ("New message", "create"), ("Close", "close"), ("Done", "close"),
    ("Search", "search"), ("Copy link", "copy"), ("Paste", "paste"), ("Approve", "click"),
    ("", "click"), (None, "click"),
])
def test_click_kind_by_first_word(label, kind):
    assert step_kind(raw("a", title="Note", interaction="click", focus_name=label)) == kind


@pytest.mark.parametrize("interaction", ["scroll", "focus", "window_focus", "element_focus"])
def test_focus_and_scroll_are_not_steps(interaction):
    assert step_kind(raw("a", title="Note", interaction=interaction)) is None


def test_web_pages():
    assert step_kind(raw("a", 0, CHROME, url="https://example.com/results/1", title="Results")) == "search"
    assert step_kind(raw("b", 0, CHROME, url="https://example.com/find", title="Find")) == "search"
    assert step_kind(raw("c", 0, CHROME, url="https://example.com/about", title="About")) == "browse"
    assert step_kind(raw("d", 0, CHROME, browser=True, title="acme - Google Search")) == "search"
    assert step_kind(raw("e", 0, CHROME, interaction="navigation", title="Acme home")) == "browse"


def test_grew_untitled_and_view():
    assert step_kind(raw("a", title="Report", grew=True)) == "type"
    assert step_kind(raw("b", title="Untitled")) == "create"
    assert step_kind(raw("c", title="Research notes")) == "view"


# ---------------------------------------------------------------- signature (5.2)

def one_episode(actions):
    found = sessions(actions)
    episodes = segment(found, {s.index: same() for s in found[1:]})
    assert len(episodes) == 1
    return episodes[0]


def test_rate_card_then_billing_are_two_steps():
    rate = [act("r1", 0, CALC, title="Client rate card.ods", document_id="doc-r", window_ref="w-r"),
            act("r2", 5, CALC, title="Client rate card.ods", document_id="doc-r", window_ref="w-r",
                interaction="edit", focus_name="D5")]
    billing = [act("b1", 10, CALC, title="Billing – week 39.ods", document_id="doc-b", window_ref="w-b"),
               act("b2", 15, CALC, title="Billing – week 39.ods", document_id="doc-b", window_ref="w-b",
                   interaction="edit", focus_name="A9"),
               act("b3", 16, CALC, title="Billing – week 39.ods", document_id="doc-b", window_ref="w-b",
                   interaction="edit", focus_name="B9"),
               act("b4", 20, CALC, title="Billing – week 39.ods", document_id="doc-b", window_ref="w-b",
                   interaction="shortcut", shortcut="cmd+s")]
    sig = signature(one_episode(rate + billing))
    assert [get(s, "kind") for s in sig.steps] == ["type", "type", "save"]
    assert set(get(sig.steps[0], "evidence")) <= {"r1", "r2"}
    assert {"b2", "b3"} <= set(get(sig.steps[1], "evidence")) <= {"b1", "b2", "b3"}
    assert sig.apps == [CALC]


def test_new_document_signature_and_text():
    sig = signature(one_episode([
        act("n", 0, title="Untitled", window_ref="w1", interaction="shortcut", shortcut="cmd+n"),
        act("e", 5, title="Untitled", window_ref="w1", interaction="edit", focus_name="Text"),
        act("u", 8, title="Untitled", window_ref="w1"),  # a create after typing is skipped
        act("s", 10, title="Untitled", window_ref="w1", interaction="shortcut", shortcut="cmd+s"),
        act("w", 15, title="Untitled", window_ref="w1", interaction="shortcut", shortcut="cmd+w"),
    ]))
    assert [get(s, "kind") for s in sig.steps] == ["create", "type", "save", "close"]
    assert sig.artifacts == []  # Untitled names no artifact
    assert signature_text(sig) == "create(TextEdit) → type(TextEdit) → save(TextEdit) → close(TextEdit)"


def test_low_signal_session_keeps_its_first_step_only():
    sig = signature(one_episode([act("v1", 0, title="Policy"), act("c1", 3, title="Policy", interaction="click"),
                                 act("v2", 6, title="Policy")]))
    assert [get(s, "kind") for s in sig.steps] == ["view"]


# ---------------------------------------------------------------- names (5.3)

INVOICE = [act("i1", 0, title="Invoice INV-4827 Acme Supplies.rtf", document_id="doc-i", window_ref="w-i",
               text="Invoice INV-4827\nAmount: 1,240.00 EUR\nStatus: awaiting approval"),
           act("i2", 10, title="Invoice INV-4827 Acme Supplies.rtf", document_id="doc-i", window_ref="w-i",
               interaction="edit", focus_name="Status", text="Status: approved for payment"),
           act("i3", 20, title="Invoice INV-4827 Acme Supplies.rtf", document_id="doc-i", window_ref="w-i",
               text="Invoice INV-4827\nAmount: 1,240.00 EUR\nStatus: awaiting approval\nStatus: approved for payment")]
TICKET = [act("t1", 0, title="Ticket SUP-1187 reply.rtf", document_id="doc-t", window_ref="w-t",
              text="Ticket SUP-1187\nCustomer: Bluebird Bakery\nProblem: the delivery note is missing."),
          act("t2", 10, title="Ticket SUP-1187 reply.rtf", document_id="doc-t", window_ref="w-t",
              interaction="edit", focus_name="Reply", text="Reply: We have sent the delivery note again.")]


def test_approve_invoice():
    n = name(facts(INVOICE))
    assert (n.verb, n.object, n.system) == ("approve", "invoice", "TextEdit")
    assert n.title == "Approve invoice in TextEdit"


def test_reply_to_ticket():
    n = name(facts(TICKET))
    assert (n.verb, n.object) == ("reply", "ticket")
    assert n.title == "Reply to ticket in TextEdit"


def test_object_and_verb_rules():
    f = facts(INVOICE)
    assert object_rule(f) == ("invoice", True)
    assert verb_rule(f, "invoice") == ("approve", False)
    assert object_rule(facts([act("n", 0, title="Notes")])) == ("document", False)
    mail = facts([act("m", 0, CHROME, title="Inbox - Gmail", url="https://mail.google.com/mail/u/0")])
    assert object_rule(mail) == ("email", False)


@pytest.mark.parametrize("actions, verb", [
    ([act("a", 0, title="Invoice 7"), act("b", 3, title="Invoice 7", interaction="click", focus_name="Approve invoice")],
     ("approve", True)),
    ([act("a", 0, title="Supplier list", document_id="d1"), act("b", 5, title="Delivery notes", document_id="d2")],
     ("check", True)),
    ([act("a", 0, title="Policy")], ("review", False)),
    ([act("a", 0, title="Untitled", window_ref="w1", interaction="shortcut", shortcut="cmd+n"),
      act("b", 5, title="Untitled", window_ref="w1", interaction="shortcut", shortcut="cmd+s")], ("create", True)),
    ([act("a", 0, CALC, title="Billing week", document_id="d1"),
      act("b", 3, CALC, title="Billing week", document_id="d1", interaction="edit", focus_name="A9", text="8"),
      act("c", 6, CALC, title="Billing week", document_id="d1", interaction="shortcut", shortcut="cmd+s")],
     ("enter", True)),
    ([act("a", 0, title="Bank statement March")], ("reconcile", False)),
], ids=["approve click", "two documents", "review", "create and save", "edits and save", "reconcile"])
def test_verb_ladder(actions, verb):
    f = facts(actions)
    assert verb_rule(f, object_rule(f)[0]) == verb


def test_compose():
    assert compose("approve", "invoice", "TextEdit") == "Approve invoice in TextEdit"
    assert compose("reply", "ticket", "TextEdit") == "Reply to ticket in TextEdit"
    assert compose("enter", "timesheet", "LibreOffice", count=2) == "Enter timesheets in LibreOffice · 2 timesheets"
    assert compose("enter", "employee record", "Xero", count=3) == "Enter employee records in Xero · 3 employee records"


@pytest.mark.parametrize("text, parts", [
    ("Reply to customer ticket in TextEdit", ("reply", "ticket")),
    ("Approve invoices in Xero", ("approve", "invoice")),
    ("Enter purchase orders against budget", ("enter", "purchase order")),
    ("check", None),
    ("Lunch with Sam", None),
])
def test_parse(text, parts):
    assert parse(text) == parts


@pytest.mark.parametrize("text, labels, ok", [
    ("Reply to customer ticket in TextEdit", ["TextEdit"], True),
    ("Approve invoice in Xero", ["Xero"], True),
    ("Approve invoice in Xero", [], False),
    ("Reply to Maya in TextEdit", ["TextEdit"], False),
    ("Invoice 4827 for Acme", [], False),
    ("Pay supplier €", [], False),
    ("See https://example.com", [], False),
    ("Reply to [ticket]", [], False),
    ("", [], False),
    ("review " + "x" * 120, [], False),
])
def test_reusable(text, labels, ok):
    assert reusable(text, labels) is ok


def test_summary():
    text = summary(facts(INVOICE))
    assert text.startswith("Worked in TextEdit")
    assert text.endswith(".") and len(text) <= 400
    assert "Fields: Status" in text
    assert not re.search(r"\d", text)


# ---------------------------------------------------------------- similarity and templates (6.3, 6.7)

def steps(*pairs):
    return [{"kind": kind, "app": app} for kind, app in pairs]


ONBOARD = steps(("search", CHROME), ("create", TEXTEDIT), ("type", TEXTEDIT), ("save", TEXTEDIT),
                ("close", TEXTEDIT))


def sig(step_list, artifacts=()):
    apps = list(dict.fromkeys(s["app"] for s in step_list))
    return Signature(steps=step_list, apps=apps, artifacts=list(artifacts))


def art(app, title, output=False):
    return {"app": app, "title": title, "output": output}


def test_identical_and_empty_signatures():
    one = sig(ONBOARD, [art(CHROME, "northwind dental - Google Search")])
    assert similarity(one, one) == 1.0
    assert similarity(sig([]), one) == 0


def test_similarity_stage2_pair():
    # 9.1 derived: 0.933 between the first company (no output captured) and a later one.
    first = sig(ONBOARD[:4], [art(CHROME, "northwind dental - Google Search")])
    later = sig(ONBOARD, [art(CHROME, "brightline logistics - Google Search"),
                          art(TEXTEDIT, "onboarding - brightline", output=True)])
    assert similarity(first, later) == 0.933


def test_different_outputs_cap_at_0_7():
    # 6.3 and 9.4: the meeting notes are not an onboarding note.
    meeting = sig(ONBOARD, [art(CHROME, "quarterly figures template - Google Search"),
                            art(TEXTEDIT, "Meeting notes 2026-09-25", output=True)])
    company = sig(ONBOARD, [art(CHROME, "northwind dental - Google Search"),
                            art(TEXTEDIT, "Onboarding - Northwind Dental", output=True)])
    assert similarity(meeting, company) == 0.7
    other = sig(ONBOARD, [art(CHROME, "harbour cafe - Google Search"),
                          art(TEXTEDIT, "Onboarding - Harbour Café Group", output=True)])
    assert similarity(company, other) == 1.0


def test_shape_is_half_without_same_app_artifacts():
    assert similarity(sig(ONBOARD), sig(ONBOARD)) == 0.9


def test_similarity_is_rounded():
    a = sig(steps(("search", CHROME), ("type", TEXTEDIT), ("save", TEXTEDIT)))
    b = sig(steps(("search", CHROME), ("save", TEXTEDIT), ("close", NUMBERS_APP)))
    value = similarity(a, b)
    assert 0 < value < 1 and value == round(value, 3)


def test_tokens_and_render():
    assert tokens("Onboarding - Northwind Dental.rtf") == ["Onboarding", "-", "Northwind", "Dental"]
    assert tokens("Invoice 1041 for Acme") == ["Invoice", "1041", "for", "Acme"]
    assert tokens("O'Brien report") == ["O'Brien", "report"]
    assert tokens("Harbour Café Group") == ["Harbour", "Café", "Group"]
    assert render(["Invoice", "1041", ",", "paid", "(", "late", ")"]) == "Invoice 1041, paid (late)"
    assert render(["w", "/", "e", "25"]) == "w /e 25"


def test_template_match():
    assert template_match("Onboarding - Northwind Dental", "Onboarding - Harbour Café Group")
    assert template_match("northwind dental - Google Search", "brightline logistics - Google Search")
    assert not template_match("Meeting notes 2026-09-25", "Onboarding - Northwind Dental")


def texts(templates) -> set[str]:
    return {t if isinstance(t, str) else get(t, "text") for t in templates}


COMPANIES = [("northwind dental", "Northwind Dental"), ("harbour café group", "Harbour Café Group"),
             ("bluebird bakery", "Bluebird Bakery")]


def test_onboarding_templates():
    # 6.7 and 9.4 examples.
    members = [[art(CHROME, f"{low} - Google Search"), art(TEXTEDIT, f"Onboarding - {name_}.rtf", output=True)]
               for low, name_ in COMPANIES]
    assert texts(build_templates(members)) == {"Onboarding - {name}", "{name} - Google Search"}


def test_invoice_template():
    members = [[art(TEXTEDIT, "Invoice 1041 for Acme", output=True)],
               [art(TEXTEDIT, "Invoice 1042 for Globex", output=True)]]
    assert texts(build_templates(members)) == {"Invoice {number} for {value}"}


def test_meeting_and_onboarding_give_no_template():
    members = [[art(TEXTEDIT, "Meeting notes 2026-09-25", output=True)],
               [art(TEXTEDIT, "Onboarding - Northwind Dental", output=True)]]
    assert texts(build_templates(members)) == set()


def test_fixed_name_needs_an_output():
    fixed = [[art(TEXTEDIT, "Weekly report")], [art(TEXTEDIT, "Weekly report")]]
    assert texts(build_templates(fixed)) == set()
    saved = [[art(TEXTEDIT, "Weekly report", output=True)], [art(TEXTEDIT, "Weekly report", output=True)]]
    assert texts(build_templates(saved)) == {"Weekly report"}


def test_templates_need_two_members():
    assert texts(build_templates([[art(TEXTEDIT, "Invoice 1041 for Acme", output=True)]])) == set()


# ---------------------------------------------------------------- processes and runs (6.6, 6.9)

def test_build_processes_chains_pairs():
    found = build_processes([("e1", "e2", 0.9), ("e2", "e3", 0.85)])
    assert set(found) == {"e1", "e2", "e3"} and len(set(found.values())) == 1


def test_build_processes_never_merges_two_processes():
    pairs = [("e1", "e2", 0.9), ("e3", "e4", 0.95), ("e2", "e3", 0.8)]
    found = build_processes(pairs)
    assert found["e1"] == found["e2"] and found["e3"] == found["e4"]
    assert found["e1"] != found["e3"]
    assert build_processes(list(reversed(pairs))) == found  # ids depend on content only


def member(id, start, task, length=60, step_list=(("type", CALC), ("save", CALC))):
    return {"id": id, "task_id": task, "started_at": ts(start), "ended_at": ts(start + length),
            "steps": [list(s) for s in step_list]}


def run_ids(found) -> list[list[str]]:
    return [list(get(r, "members")) for r in found]


def test_runs():
    found = runs([member("m3", 300, "t3", 120), member("m1", 0, "t1", 60), member("m2", 90, "t2", 90)])
    assert run_ids(found) == [["m1", "m2", "m3"]]
    assert get(found[0], "count") == 3
    assert get(found[0], "median_seconds") == 90.0


def test_runs_break_on_steps_gap_and_same_task():
    other = (("type", CALC), ("send", CHROME))
    assert run_ids(runs([member("m1", 0, "t1"), member("m2", 90, "t2", step_list=other),
                         member("m3", 180, "t3")])) == []
    assert run_ids(runs([member("m1", 0, "t1"), member("m2", 90, "t2"), member("m3", 850, "t3")])) == [["m1", "m2"]]
    assert run_ids(runs([member("m1", 0, "t1"), member("m2", 90, "t1"), member("m3", 180, "t3")])) == [["m2", "m3"]]
    assert run_ids(runs([member("m1", 0, "t1", 200), member("m2", 100, "t2")])) == [["m1", "m2"]]
    assert run_ids(runs([member("m1", 0, "t1", 300), member("m2", 100, "t2")])) == []
