"""Tests for taskmine.learn: rule table, decide, the notes file, the asker, the nightly tidy and
the hard-rule report.

Sources: docs/LEARNING.md, docs/CONTRACTS.md sections 3 and 4, grouper spec 4.4 and 4.5.

Conventions these tests fix where the docs are silent:
- A feature dict holds all 20 names in FEATURES. Flags are 0.0 or 1.0.
- Times passed to learn are timezone-aware. Tests build them in the machine's local zone,
  so `when.hour` and the local hour agree. Stored times are UTC ISO 8601 with a Z.
- Asker stats map each context key to {"alpha", "beta", "asks"}; to_dict() returns the same shape.
- The store methods learn uses: setting(key) -> str | None; set_setting(key, value);
  hard_rule_labels() -> rows {"reason", "choice", "label"}.
- tidy reads the settings `notes_added_at` and `tidy_at` and writes `tidy_at` and `last_tidy_at`.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import random
import stat
from datetime import UTC, datetime, timedelta

import pytest
from taskmine import learn
from taskmine.learn import (
    FEATURES,
    TIDY_INSTRUCTIONS,
    Asker,
    Decision,
    Notes,
    decide,
    rule_choice,
    rule_report,
    tidy,
)
from taskmine.model import THRESHOLD, FakeModel, ModelError, RateLimited

SIGNALS = (
    "idle", "case_conflict", "shared_anchor", "same_surface", "copy_paste",
    "search_then_create", "new_search", "context_saved", "shared_words", "recent",
    "same_app", "closed",
)
NUMBERS = (
    "gap", "prev_seconds", "context_seconds", "link_strong", "link_weak",
    "hour_sin", "hour_cos", "to_browser",
)
JOINS = {"same", "supporting"}
HARD_RULES = ("idle", "case_conflict", "shared_anchor", "same_surface", "copy_paste",
              "search_then_create", "new_search")


# ---------------------------------------------------------------- helpers

def reference_rule(f: dict[str, bool]) -> tuple[str, float, str, bool]:
    """Grouper spec 4.5, rows 1 to 13, first match wins."""
    if f["idle"]:
        return ("break", 1.00, "idle", True)
    if f["case_conflict"]:
        return ("break", 1.00, "case_conflict", True)
    if f["shared_anchor"]:
        return ("same", 0.95, "shared_anchor", True)
    if f["same_surface"]:
        return ("same", 0.90, "same_surface", True)
    if f["copy_paste"]:
        return ("supporting", 0.85, "copy_paste", True)
    if f["search_then_create"]:
        return ("supporting", 0.85, "search_then_create", True)
    if f["new_search"] and f["context_saved"]:
        return ("break", 0.85, "new_search", True)
    if f["new_search"]:
        return ("break", 0.85, "new_search", False)
    if f["shared_words"] and f["recent"] and f["same_app"]:
        return ("same", 0.70, "shared_words", False)
    if f["shared_words"] and f["recent"]:
        return ("supporting", 0.70, "shared_words", False)
    if f["closed"]:
        return ("break", 0.75, "closed", False)
    if f["recent"]:
        return ("diversion", 0.60, "switch", False)
    return ("break", 0.65, "gap", False)


def all_combos() -> list[dict[str, bool]]:
    return [dict(zip(SIGNALS, bits)) for bits in itertools.product((False, True), repeat=12)]


def flags(*on: str) -> dict[str, bool]:
    unknown = set(on) - set(SIGNALS)
    assert not unknown, unknown
    return {name: name in on for name in SIGNALS}


def gap_for(f: dict[str, bool]) -> float:
    """A gap in seconds consistent with the flags (idle wins over recent)."""
    if f["idle"]:
        return 1800.0
    if f["recent"]:
        return 45.0
    return 300.0


def features(f: dict[str, bool], **numbers: float) -> dict[str, float]:
    """A feature dict as group.features builds it: durations as log1p(seconds)."""
    out = {name: 1.0 if f[name] else 0.0 for name in SIGNALS}
    out.update({name: 0.0 for name in NUMBERS})
    out["gap"] = math.log1p(gap_for(f))
    out.update(numbers)
    return out


def local(*args: int) -> datetime:
    """An aware datetime in the machine's local zone with the given wall-clock fields."""
    return datetime(*args).astimezone()


def utc_text(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class FakeStore:
    """The slice of Store that learn needs."""

    def __init__(self, labels=(), settings=None, hard=()):
        self._labels = list(labels)
        self._settings = dict(settings or {})
        self._hard = list(hard)

    def labels(self, kind=None, since=None):
        return [dict(r) for r in self._labels if kind is None or r["kind"] == kind]

    def hard_rule_labels(self):
        return [dict(r) for r in self._hard]

    def setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, value):
        self._settings[key] = value


def test_learn_threshold_is_the_model_threshold():
    assert THRESHOLD == 0.8
    assert learn.THRESHOLD == THRESHOLD
    assert learn.NEAR_MARGIN == pytest.approx(0.1)
    assert learn.NOTES_LIMIT == 200
    assert learn.TIDY_HOURS == (1, 5)
    assert learn.TIDY_SPACING == timedelta(hours=20)
    assert (learn.FLAG_MIN_LABELS, learn.FLAG_RATE) == (50, pytest.approx(0.2))


# ---------------------------------------------------------------- FEATURES and rule_choice

def test_features_are_the_twenty_names_in_stored_order():
    assert FEATURES == SIGNALS + NUMBERS
    assert isinstance(FEATURES, tuple)
    assert len(FEATURES) == 20


@pytest.mark.parametrize("on, expected", [
    ((), ("break", 0.65, "gap", False)),
    (("idle", "shared_anchor", "recent"), ("break", 1.00, "idle", True)),
    (("case_conflict", "shared_anchor"), ("break", 1.00, "case_conflict", True)),
    (("shared_anchor", "copy_paste"), ("same", 0.95, "shared_anchor", True)),
    (("same_surface", "new_search"), ("same", 0.90, "same_surface", True)),
    (("copy_paste", "new_search", "context_saved"), ("supporting", 0.85, "copy_paste", True)),
    (("search_then_create",), ("supporting", 0.85, "search_then_create", True)),
    (("new_search", "context_saved", "shared_words", "recent"), ("break", 0.85, "new_search", True)),
    (("new_search", "shared_words", "recent"), ("break", 0.85, "new_search", False)),
    (("shared_words", "recent", "same_app"), ("same", 0.70, "shared_words", False)),
    (("shared_words", "recent"), ("supporting", 0.70, "shared_words", False)),
    (("shared_words", "closed"), ("break", 0.75, "closed", False)),
    (("recent", "closed"), ("break", 0.75, "closed", False)),
    (("recent", "same_app"), ("diversion", 0.60, "switch", False)),
    (("shared_words", "same_app"), ("break", 0.65, "gap", False)),
])
def test_rule_choice_rows(on, expected):
    choice, confidence, reason, hard = rule_choice(flags(*on))
    assert (choice, reason, hard) == (expected[0], expected[2], expected[3])
    assert confidence == pytest.approx(expected[1])


def test_rule_choice_matches_the_table_on_all_4096_combinations():
    for f in all_combos():
        got = rule_choice(f)
        want = reference_rule(f)
        assert (got[0], got[2], got[3]) == (want[0], want[2], want[3]), f
        assert got[1] == pytest.approx(want[1]), f


def test_hard_rows_are_rows_one_to_seven():
    hard = [f for f in all_combos() if rule_choice(f)[3]]
    soft = [f for f in all_combos() if not rule_choice(f)[3]]
    # Soft: first six flags off and not (new_search and context_saved): 64 - 16 = 48.
    assert len(soft) == 48
    assert len(hard) == 4096 - 48


# ---------------------------------------------------------------- decide

def test_decision_defaults_to_no_model_confidence_and_sure():
    d = Decision(choice="break", confidence=0.65, reason="gap", hard=False)
    assert d.model_confidence is None
    assert d.unsure is False


def test_decide_returns_hard_rules_unchanged_for_every_hard_combination():
    for f in all_combos():
        choice, confidence, reason, hard = rule_choice(f)
        if not hard:
            continue
        d = decide(features(f))
        assert isinstance(d, Decision)
        assert (d.choice, d.reason, d.hard) == (choice, reason, True), f
        assert d.confidence == pytest.approx(confidence)
        assert d.model_confidence is None
        assert d.unsure is False


def test_decide_marks_every_soft_combination_unsure_with_the_rule_fallback():
    soft = 0
    for f in all_combos():
        choice, confidence, reason, hard = rule_choice(f)
        if hard:
            continue
        soft += 1
        d = decide(features(f))
        assert isinstance(d, Decision)
        assert (d.choice, d.reason, d.hard) == (choice, reason, False), f
        assert d.confidence == pytest.approx(confidence)
        assert d.model_confidence is None
        assert d.unsure is True
    assert soft == 48


# ---------------------------------------------------------------- Notes

def test_notes_read_is_empty_when_the_file_is_missing(tmp_path):
    notes = Notes(tmp_path / "notes.md")
    assert notes.read() == ""
    assert notes.lines() == []
    assert notes.count() == 0
    assert not (tmp_path / "notes.md").exists()


def test_notes_add_creates_the_file_with_mode_0600(tmp_path):
    path = tmp_path / "notes.md"
    Notes(path).add("Preview to Excel is usually one task")
    assert path.read_text() == "- Preview to Excel is usually one task\n"
    assert mode(path) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.md"]


def test_notes_add_appends_newest_last(tmp_path):
    notes = Notes(tmp_path / "notes.md")
    notes.add("Preview to Excel is usually one task")
    notes.add("Twitter to YouTube is usually a new task")
    assert notes.read() == ("- Preview to Excel is usually one task\n"
                            "- Twitter to YouTube is usually a new task\n")
    assert notes.lines() == ["Preview to Excel is usually one task",
                             "Twitter to YouTube is usually a new task"]
    assert notes.count() == 2
    assert mode(tmp_path / "notes.md") == 0o600


def test_notes_lines_strip_the_leading_dash_and_skip_blank_lines(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("- Mail to Numbers is usually one task\n\n   \n- Slack to Xero is usually a new task\n"
                    "my idle gap is about fifteen minutes\n")
    notes = Notes(path)
    assert notes.lines() == ["Mail to Numbers is usually one task",
                             "Slack to Xero is usually a new task",
                             "my idle gap is about fifteen minutes"]
    assert notes.count() == 3


def test_notes_replace_writes_the_whole_file_and_keeps_mode_0600(tmp_path):
    path = tmp_path / "notes.md"
    notes = Notes(path)
    for i in range(3):
        notes.add(f"App{i} to Excel is usually one task")
    notes.replace("- Mail to Excel is usually one task\n")
    assert path.read_text() == "- Mail to Excel is usually one task\n"
    assert notes.count() == 1
    assert mode(path) == 0o600


def test_notes_replace_on_a_missing_file_writes_mode_0600(tmp_path):
    path = tmp_path / "notes.md"
    Notes(path).replace("- Mail to Excel is usually one task\n")
    assert path.read_text() == "- Mail to Excel is usually one task\n"
    assert mode(path) == 0o600


@pytest.mark.parametrize("label, expected", [
    ("same", "Preview to Excel is usually one task"),
    ("supporting", "Preview to Excel is usually one task"),
    ("break", "Preview to Excel is usually a new task"),
    ("diversion", "Preview to Excel is usually a new task"),
])
def test_line_for_relation(label, expected):
    context = {"left_app": "Preview", "right_app": "Excel"}
    assert Notes.line_for("relation", label, context) == expected


@pytest.mark.parametrize("label, expected", [
    ("same", "Mail then Excel and Mail then Numbers are the same process"),
    ("different", "Mail then Excel and Mail then Numbers are different processes"),
])
def test_line_for_match(label, expected):
    context = {"a": "Mail then Excel", "b": "Mail then Numbers"}
    assert Notes.line_for("match", label, context) == expected


@pytest.mark.parametrize("kind, label, context", [
    ("name", "Pay invoices", {}),
    ("rename", "Quarterly report", {}),
    ("verb", "review", {}),
    ("complete", "done", {"left_app": "Mail", "right_app": "Excel"}),
])
def test_line_for_other_kinds_is_none(kind, label, context):
    assert Notes.line_for(kind, label, context) is None


# ---------------------------------------------------------------- Asker

MONDAY_10 = local(2026, 10, 19, 10, 0)     # work hours
MONDAY_09 = local(2026, 10, 19, 9, 0)      # work hours
MONDAY_08 = local(2026, 10, 19, 8, 0)      # before work
MONDAY_20 = local(2026, 10, 19, 20, 0)     # after work
SATURDAY_10 = local(2026, 10, 24, 10, 0)   # weekend
NEAR, FAR = 0.75, 0.3


def test_weekday_fixtures():
    assert MONDAY_10.weekday() == 0 and SATURDAY_10.weekday() == 5


def test_asker_has_eight_contexts():
    asker = Asker.load({})
    assert len(set(asker.contexts)) == 8
    assert len(list(asker.contexts)) == 8


def test_fresh_asker_starts_every_context_at_beta_one_one():
    data = Asker.load({}).to_dict()
    assert set(data) == set(Asker.load({}).contexts)
    for stats in data.values():
        assert stats["alpha"] == 1
        assert stats["beta"] == 1


def test_context_key_buckets_by_margin_hours_and_pair():
    asker = Asker.load({})
    keys = {}
    for c, when, seen in itertools.product((NEAR, FAR), (MONDAY_10, MONDAY_20), (True, False)):
        key = asker.context_key(c, when, seen)
        assert key in set(asker.contexts)
        keys[(c, when, seen)] = key
    assert len(set(keys.values())) == 8

    def same(a, b):
        return asker.context_key(*a) == asker.context_key(*b)

    # Work hours: 9 to 17 local, Monday to Friday.
    assert same((NEAR, MONDAY_09, True), (NEAR, MONDAY_10, True))
    assert same((NEAR, MONDAY_08, True), (NEAR, MONDAY_20, True))
    assert same((NEAR, SATURDAY_10, True), (NEAR, MONDAY_20, True))
    assert not same((NEAR, MONDAY_10, True), (NEAR, SATURDAY_10, True))
    assert not same((NEAR, MONDAY_10, True), (NEAR, MONDAY_08, True))
    # Pair seen.
    assert not same((NEAR, MONDAY_10, True), (NEAR, MONDAY_10, False))


@pytest.mark.parametrize("confidence", [0.72, 0.75, 0.8, 0.85, 0.88])
def test_context_key_is_near_within_a_tenth_of_the_threshold(confidence):
    asker = Asker.load({})
    near = asker.context_key(0.8, MONDAY_10, True)
    assert asker.context_key(confidence, MONDAY_10, True) == near
    assert asker.context_key(None, MONDAY_10, True) != near


@pytest.mark.parametrize("confidence", [None, 0.0, 0.3, 0.69, 0.91, 1.0])
def test_context_key_is_far_outside_a_tenth_or_without_an_answer(confidence):
    asker = Asker.load({})
    assert asker.context_key(confidence, MONDAY_10, True) != asker.context_key(0.8, MONDAY_10, True)
    assert asker.context_key(confidence, MONDAY_10, True) == asker.context_key(FAR, MONDAY_10, True)


@pytest.mark.parametrize("confidence", [0.7, 0.9])
def test_context_key_exactly_a_tenth_away_is_far(confidence):
    asker = Asker.load({})
    assert asker.context_key(confidence, MONDAY_10, True) == asker.context_key(FAR, MONDAY_10, True)


def _stats(alpha: float, beta: float) -> dict:
    keys = Asker.load({}).contexts
    return {key: {"alpha": alpha, "beta": beta, "asks": 0} for key in keys}


def unsure(confidence: float | None) -> Decision:
    """A soft decision that kept the rule-table choice, with Jev's reported confidence."""
    return Decision("break", 0.65, "gap", False, confidence, True)


CASES = list(itertools.product((NEAR, FAR, None), (MONDAY_10, SATURDAY_10), (True, False)))


def ask(asker: Asker, confidence: float | None, when: datetime, seen: bool, open_count: int = 0,
        last=None, rng=None) -> bool:
    return asker.should_ask(unsure(confidence), open_count, when,
                            pair_seen=seen, last_ask_at=last, rng=rng)


def test_beta_50_1_asks():
    asker = Asker.load(_stats(50, 1))
    rng = random.Random(1234)
    for _ in range(25):
        for c, when, seen in CASES:
            assert ask(asker, c, when, seen, rng=rng) is True


def test_beta_1_50_holds():
    asker = Asker.load(_stats(1, 50))
    rng = random.Random(1234)
    for _ in range(25):
        for c, when, seen in CASES:
            assert ask(asker, c, when, seen, rng=rng) is False


def test_should_ask_refuses_hard_decisions():
    asker = Asker.load(_stats(50, 1))
    for c in (None, NEAR):
        d = Decision("same", 0.95, "shared_anchor", True, c, False)
        assert asker.should_ask(d, 0, MONDAY_10, rng=random.Random(3)) is False


def test_should_ask_accepts_a_decision_without_a_model_answer():
    asker = Asker.load(_stats(50, 1))
    assert asker.should_ask(unsure(None), 0, MONDAY_10, rng=random.Random(3)) is True
    assert asker.should_ask(unsure(None), 3, MONDAY_10, rng=random.Random(3)) is False


def test_should_ask_defaults_pair_seen_false():
    asker = Asker.load({})
    keen = asker.context_key(NEAR, MONDAY_10, False)
    stats = _stats(1, 50)
    stats[keen] = {"alpha": 50, "beta": 1, "asks": 0}
    asker = Asker.load(stats)
    rng = random.Random(11)
    for _ in range(20):
        assert asker.should_ask(unsure(NEAR), 0, MONDAY_10, rng=rng) is True


def test_should_ask_uses_the_asker_seed_when_no_rng_is_given():
    def run(seed):
        asker = Asker.load(_stats(1, 1), seed=seed)
        return [asker.should_ask(unsure(c), 0, when, pair_seen=seen)
                for _ in range(10) for c, when, seen in CASES]

    first = run(42)
    assert first == run(42)
    assert True in first and False in first
    assert all(Asker.load(_stats(50, 1), seed=1).should_ask(unsure(NEAR), 0, MONDAY_10)
               for _ in range(20))


def test_should_ask_respects_open_count():
    asker = Asker.load(_stats(50, 1))
    rng = random.Random(7)
    assert ask(asker, NEAR, MONDAY_10, True, 2, rng=rng) is True
    assert ask(asker, NEAR, MONDAY_10, True, 3, rng=rng) is False
    assert ask(asker, NEAR, MONDAY_10, True, 5, rng=rng) is False


def test_should_ask_waits_ten_minutes_after_the_last_ask():
    asker = Asker.load(_stats(50, 1))
    rng = random.Random(7)
    now = MONDAY_10
    assert ask(asker, NEAR, now, True, last=now - timedelta(minutes=1), rng=rng) is False
    assert ask(asker, NEAR, now, True, last=now - timedelta(minutes=9, seconds=59), rng=rng) is False
    assert ask(asker, NEAR, now, True, last=now - timedelta(minutes=10, seconds=1), rng=rng) is True
    assert ask(asker, NEAR, now, True, last=now - timedelta(hours=3), rng=rng) is True
    assert ask(asker, NEAR, now, True, last=None, rng=rng) is True


def test_should_ask_uses_the_context_posterior():
    asker = Asker.load({})
    keen = asker.context_key(NEAR, MONDAY_10, True)
    stats = _stats(1, 50)
    stats[keen] = {"alpha": 50, "beta": 1, "asks": 0}
    asker = Asker.load(stats)
    rng = random.Random(99)
    for _ in range(20):
        assert ask(asker, NEAR, MONDAY_10, True, rng=rng) is True
        assert ask(asker, NEAR, MONDAY_10, False, rng=rng) is False
        assert ask(asker, FAR, MONDAY_10, True, rng=rng) is False
        assert ask(asker, None, MONDAY_10, True, rng=rng) is False


def test_should_ask_puts_no_model_answer_in_the_far_context():
    asker = Asker.load({})
    keen = asker.context_key(FAR, MONDAY_10, True)
    stats = _stats(1, 50)
    stats[keen] = {"alpha": 50, "beta": 1, "asks": 0}
    asker = Asker.load(stats)
    rng = random.Random(5)
    for _ in range(20):
        assert ask(asker, None, MONDAY_10, True, rng=rng) is True
        assert ask(asker, NEAR, MONDAY_10, True, rng=rng) is False


def _ab(asker: Asker, key: str) -> tuple[float, float]:
    stats = asker.to_dict()[key]
    return stats["alpha"], stats["beta"]


@pytest.mark.parametrize("outcome, d_alpha, d_beta", [
    ("answered", 1, 0),
    ("dismissed", 0, 1),
    ("expired", 0, 1),
])
def test_reward_moves_alpha_and_beta(outcome, d_alpha, d_beta):
    asker = Asker.load(_stats(3, 4))
    key = min(asker.contexts)
    others_before = {k: v for k, v in asker.to_dict().items() if k != key}
    asker.reward(key, outcome)
    assert _ab(asker, key) == (3 + d_alpha, 4 + d_beta)
    others_after = {k: v for k, v in asker.to_dict().items() if k != key}
    assert others_after == others_before


def test_reward_later_changes_nothing():
    asker = Asker.load(_stats(3, 4))
    before = asker.to_dict()
    for key in asker.contexts:
        asker.reward(key, "later")
    assert asker.to_dict() == before


def test_rewards_accumulate():
    asker = Asker.load({})
    key = max(asker.contexts)
    for outcome in ("answered", "answered", "dismissed", "later", "expired"):
        asker.reward(key, outcome)
    assert _ab(asker, key) == (3, 3)


def test_asker_round_trips_through_to_dict():
    asker = Asker.load(_stats(2, 5))
    asker.reward(min(asker.contexts), "answered")
    data = asker.to_dict()
    assert Asker.load(json.loads(json.dumps(data))).to_dict() == data


# ---------------------------------------------------------------- tidy

NIGHT = local(2026, 10, 20, 2, 30)


def _notes(tmp_path, n: int) -> Notes:
    notes = Notes(tmp_path / "notes.md")
    for i in range(n):
        notes.add(f"App{i:03d} to Excel is usually one task")
    return notes


def _store(now: datetime, *, tidy_at: datetime | None = None,
           added_at: datetime | None = None) -> FakeStore:
    settings = {"notes_added_at": utc_text(added_at or now - timedelta(minutes=30))}
    if tidy_at is not None:
        settings["tidy_at"] = utc_text(tidy_at)
    return FakeStore(settings=settings)


def _reply(n: int) -> str:
    return "".join(f"- Merged{i:03d} to Excel is usually one task\n" for i in range(n))


class FailingModel:
    """A model whose rewrite fails."""

    def __init__(self, error: Exception):
        self.error = error
        self.rewrite_calls = []

    def rewrite(self, text, instructions):
        self.rewrite_calls.append((text, instructions))
        raise self.error


def test_tidy_instructions_ask_for_short_merged_lines():
    assert isinstance(TIDY_INSTRUCTIONS, str) and TIDY_INSTRUCTIONS.strip()
    assert "120" in TIDY_INSTRUCTIONS


@pytest.mark.parametrize("wall", [(0, 59), (5, 0), (5, 30), (12, 0), (23, 59)])
def test_tidy_not_due_outside_one_to_five(tmp_path, wall):
    now = local(2026, 10, 20, *wall)
    notes, store, model = _notes(tmp_path, 5), _store(now), FakeModel({})
    before = notes.read()
    assert tidy(store, notes, model, now) is None
    assert model.rewrite_calls == []
    assert store.setting("tidy_at") is None
    assert notes.read() == before


@pytest.mark.parametrize("wall", [(1, 0), (2, 30), (4, 59)])
def test_tidy_due_between_one_and_five(tmp_path, wall):
    now = local(2026, 10, 20, *wall)
    notes, model = _notes(tmp_path, 5), FakeModel({})
    result = tidy(_store(now), notes, model, now)
    assert result is not None
    assert result["before"] == 5
    assert len(model.rewrite_calls) == 1


def test_tidy_not_due_within_twenty_hours_of_the_last(tmp_path):
    store = _store(NIGHT, tidy_at=NIGHT - timedelta(hours=19, minutes=59),
                   added_at=NIGHT - timedelta(minutes=10))
    model = FakeModel({})
    assert tidy(store, _notes(tmp_path, 5), model, NIGHT) is None
    assert model.rewrite_calls == []


def test_tidy_due_twenty_hours_after_the_last(tmp_path):
    store = _store(NIGHT, tidy_at=NIGHT - timedelta(hours=21),
                   added_at=NIGHT - timedelta(minutes=10))
    assert tidy(store, _notes(tmp_path, 5), FakeModel({}), NIGHT) is not None


def test_tidy_not_due_without_new_lines(tmp_path):
    store = _store(NIGHT, tidy_at=NIGHT - timedelta(hours=24),
                   added_at=NIGHT - timedelta(hours=25))
    model = FakeModel({})
    assert tidy(store, _notes(tmp_path, 5), model, NIGHT) is None
    assert model.rewrite_calls == []


def test_tidy_not_due_with_an_empty_notes_file(tmp_path):
    model = FakeModel({})
    assert tidy(_store(NIGHT), Notes(tmp_path / "notes.md"), model, NIGHT) is None
    assert model.rewrite_calls == []


def test_tidy_force_skips_the_gates(tmp_path):
    noon = local(2026, 10, 20, 12, 0)
    store = _store(noon, tidy_at=noon - timedelta(hours=1), added_at=noon - timedelta(minutes=10))
    notes, model = _notes(tmp_path, 5), FakeModel({}, rewrites={"tidy": _reply(2)})
    assert tidy(store, notes, model, noon) is None
    result = tidy(store, notes, model, noon, force=True)
    assert result == {"before": 5, "after": 2, "rewritten": True}
    assert notes.read() == _reply(2)


def test_tidy_rewrites_the_file_from_the_model_reply(tmp_path):
    notes, store = _notes(tmp_path, 5), _store(NIGHT)
    text = notes.read()
    model = FakeModel({}, rewrites={"tidy": _reply(2)})
    result = tidy(store, notes, model, NIGHT)
    assert result == {"before": 5, "after": 2, "rewritten": True}
    assert notes.read() == _reply(2)
    assert mode(tmp_path / "notes.md") == 0o600
    [call] = model.rewrite_calls
    values = list(call.values()) if isinstance(call, dict) else list(call)
    assert text in values and TIDY_INSTRUCTIONS in values
    assert store.setting("tidy_at") == utc_text(NIGHT)
    assert store.setting("last_tidy_at") == utc_text(NIGHT)


def test_tidy_once_a_night(tmp_path):
    notes, store = _notes(tmp_path, 5), _store(NIGHT)
    assert tidy(store, notes, FakeModel({}), NIGHT) is not None
    assert tidy(store, notes, FakeModel({}), NIGHT + timedelta(minutes=30)) is None


@pytest.mark.parametrize("reply", ["", "\n  \n", _reply(201)])
def test_tidy_keeps_the_newest_200_lines_when_the_reply_is_empty_or_too_long(tmp_path, reply):
    notes = _notes(tmp_path, 250)
    lines = notes.lines()
    result = tidy(_store(NIGHT), notes, FakeModel({}, rewrites={"tidy": reply}), NIGHT)
    assert (result["before"], result["after"]) == (250, 200)
    assert notes.lines() == lines[-200:]
    assert mode(tmp_path / "notes.md") == 0o600


def test_tidy_accepts_a_reply_of_exactly_200_lines(tmp_path):
    notes = _notes(tmp_path, 250)
    result = tidy(_store(NIGHT), notes, FakeModel({}, rewrites={"tidy": _reply(200)}), NIGHT)
    assert result == {"before": 250, "after": 200, "rewritten": True}
    assert notes.read() == _reply(200)


@pytest.mark.parametrize("error", [ModelError("down"), RateLimited("429")])
def test_tidy_still_sets_tidy_at_on_a_model_error(tmp_path, error):
    notes, store = _notes(tmp_path, 250), _store(NIGHT)
    lines = notes.lines()
    result = tidy(store, notes, FailingModel(error), NIGHT)
    assert result == {"before": 250, "after": 200, "rewritten": False}
    assert notes.lines() == lines[-200:]
    assert store.setting("tidy_at") == utc_text(NIGHT)
    assert store.setting("last_tidy_at") == utc_text(NIGHT)


def test_tidy_leaves_a_short_file_alone_on_a_model_error(tmp_path):
    notes, store = _notes(tmp_path, 10), _store(NIGHT)
    text = notes.read()
    result = tidy(store, notes, FailingModel(ModelError("down")), NIGHT)
    assert result == {"before": 10, "after": 10, "rewritten": False}
    assert notes.read() == text
    assert store.setting("tidy_at") == utc_text(NIGHT)


# ---------------------------------------------------------------- rule_report

def _hard(reason: str, choice: str, agree: int, disagree: int) -> list[dict]:
    same = "supporting" if choice in JOINS else "diversion"
    other = "break" if choice in JOINS else "same"
    return ([{"reason": reason, "choice": choice, "label": same}] * agree
            + [{"reason": reason, "choice": choice, "label": other}] * disagree)


def test_rule_report_lists_every_hard_rule_in_order():
    report = rule_report(FakeStore())
    assert [r["rule"] for r in report] == list(HARD_RULES)
    for row in report:
        assert row == {"rule": row["rule"], "labels": 0, "disagreements": 0, "flagged": False}


def test_rule_report_counts_disagreements_per_hard_rule():
    rows = (_hard("idle", "break", 3, 2) + _hard("shared_anchor", "same", 4, 1)
            + _hard("copy_paste", "supporting", 0, 6)
            + [{"reason": "idle", "choice": "break", "label": "break"}])
    report = {r["rule"]: r for r in rule_report(FakeStore(hard=rows))}
    assert (report["idle"]["labels"], report["idle"]["disagreements"]) == (6, 2)
    assert (report["shared_anchor"]["labels"], report["shared_anchor"]["disagreements"]) == (5, 1)
    assert (report["copy_paste"]["labels"], report["copy_paste"]["disagreements"]) == (6, 6)
    assert (report["new_search"]["labels"], report["new_search"]["disagreements"]) == (0, 0)
    assert not any(r["flagged"] for r in report.values())


@pytest.mark.parametrize("agree, disagree, flagged", [
    (39, 11, True),     # 50 labels, 22 %
    (79, 21, True),     # 100 labels, 21 %
    (0, 49, False),     # 49 labels
    (40, 10, False),    # 50 labels, exactly 20 %
    (49, 0, False),
])
def test_rule_report_flags_at_fifty_labels_above_twenty_percent(agree, disagree, flagged):
    rows = _hard("same_surface", "same", agree, disagree) + _hard("idle", "break", 50, 0)
    report = {r["rule"]: r for r in rule_report(FakeStore(hard=rows))}
    assert report["same_surface"]["labels"] == agree + disagree
    assert report["same_surface"]["disagreements"] == disagree
    assert report["same_surface"]["flagged"] is flagged
    assert report["idle"]["flagged"] is False
