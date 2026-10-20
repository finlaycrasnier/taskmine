"""Tests for taskmine.learn: rule table, local scorer, decide, asker and nightly refit.

Sources: docs/LEARNING.md, docs/CONTRACTS.md sections 3 and 4, grouper spec 4.4 and 4.5.

Durations in a feature dict (gap, prev_seconds, context_seconds) arrive as log1p(seconds),
already transformed by group.features (LEARNING.md section 1). learn uses them as given and
never applies log1p again. Scorer.from_rules() samples gaps in seconds and transforms them itself.

Conventions these tests fix where the docs are silent:
- A feature dict holds all 20 names in FEATURES. Flags are 0.0 or 1.0.
- Times passed to learn are timezone-aware. Tests build them in the machine's local zone,
  so `when.hour` and the local hour agree. Stored times are UTC ISO 8601 with a Z.
- Scorer.fit returns (new scorer, its agreement on the fitted rows).
- Asker stats map each context key to {"alpha", "beta", "asks"}; to_dict() returns the same shape.
- The store methods refit uses: labels(kind, since=None) -> rows as dicts with `features` decoded
  to a dict; load_model(kind) -> the newest record dict or None; save_model(kind, weights,
  examples, agreement, basis); setting(key) -> str | None; set_setting(key, value).
- With no stored scorer, refit compares against Scorer.from_rules().
"""
from __future__ import annotations

import itertools
import json
import math
import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from taskmine.learn import FEATURES, Asker, Decision, Scorer, decide, refit, rule_choice

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


def parse_time(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class FixedScorer:
    """Stub scorer whose p_join is a constant."""

    def __init__(self, p: float):
        self.p = p
        self.calls = 0

    def p_join(self, features):
        self.calls += 1
        return self.p


class FakeStore:
    """The slice of Store that refit needs."""

    def __init__(self, labels=(), model=None, settings=None):
        self._labels = list(labels)
        self._model = model
        self._settings = dict(settings or {})
        self.saved: list[dict] = []

    def labels(self, kind=None, since=None):
        rows = [r for r in self._labels if kind is None or r["kind"] == kind]
        if since is not None:
            cut = parse_time(since)
            rows = [r for r in rows if parse_time(r["at"]) > cut]
        return [dict(r, features=dict(r["features"])) for r in rows]

    def load_model(self, kind):
        if self._model is not None and self._model["kind"] == kind:
            return dict(self._model)
        return None

    def save_model(self, kind, weights, examples, agreement, basis, *rest, **extra):
        record = {"kind": kind, "weights": weights, "examples": examples,
                  "agreement": agreement, "basis": basis, "version": len(self.saved) + 1,
                  "created_at": extra.get("created_at")}
        self.saved.append(record)
        self._model = record

    def setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, value):
        self._settings[key] = value


def label_rows(specs, start: datetime, kind: str = "relation") -> list[dict]:
    """specs: list of (features, label text). Rows one minute apart, oldest first."""
    rows = []
    for i, (feats, label) in enumerate(specs):
        rows.append({
            "id": i + 1,
            "at": utc_text(start + timedelta(minutes=i)),
            "kind": kind,
            "key": f"k{i:04d}",
            "source": "answer",
            "label": label,
            "features": dict(feats),
        })
    return rows


def weights_of(record) -> dict:
    weights = record["weights"]
    return json.loads(weights) if isinstance(weights, str) else weights


@pytest.fixture(scope="module")
def rules_scorer() -> Scorer:
    return Scorer.from_rules()


# Soft feature points used across tests.
JOIN_X = features(flags("shared_words", "recent", "same_app"))   # rule: same
BREAK_Y = features(flags())                                      # rule: break (gap)


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


# ---------------------------------------------------------------- Scorer

def test_from_rules_agrees_with_rule_table_on_soft_combinations(rules_scorer):
    soft = [f for f in all_combos() if not rule_choice(f)[3]]
    agree = 0
    misses = []
    for f in soft:
        rule_join = rule_choice(f)[0] in JOINS
        scorer_join = rules_scorer.p_join(features(f)) >= 0.5
        if rule_join == scorer_join:
            agree += 1
        else:
            misses.append({k for k, v in f.items() if v})
    assert agree / len(soft) >= 0.99, misses


def test_p_join_is_a_probability_on_all_combinations(rules_scorer):
    for f in all_combos():
        p = rules_scorer.p_join(features(f))
        assert isinstance(p, float)
        assert 0.0 <= p <= 1.0
        assert not math.isnan(p)


def test_from_rules_is_deterministic():
    assert Scorer.from_rules().to_dict() == Scorer.from_rules().to_dict()


def _mid_point(scorer: Scorer) -> dict[str, float]:
    """The combination whose p is closest to 0.5, so one step is visible in float precision."""
    return min((features(f) for f in all_combos()), key=lambda x: abs(scorer.p_join(x) - 0.5))


def test_update_towards_one_raises_p():
    scorer = Scorer.from_rules()
    x = _mid_point(scorer)
    before = scorer.p_join(x)
    scorer.update(x, 1)
    after = scorer.p_join(x)
    assert after > before
    scorer.update(x, 1, lr=0.5)
    assert scorer.p_join(x) > after


def test_update_towards_zero_lowers_p():
    scorer = Scorer.from_rules()
    x = _mid_point(scorer)
    before = scorer.p_join(x)
    scorer.update(x, 0)
    after = scorer.p_join(x)
    assert after < before
    scorer.update(x, 0, lr=0.5)
    assert scorer.p_join(x) < after


def test_larger_learning_rate_moves_p_further():
    small, large = Scorer.from_rules(), Scorer.from_rules()
    x = _mid_point(small)
    start = small.p_join(x)
    small.update(x, 1, lr=0.05)
    large.update(x, 1, lr=0.5)
    assert large.p_join(x) - start > small.p_join(x) - start > 0


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


@pytest.mark.parametrize("name", ["gap", "prev_seconds", "context_seconds"])
def test_durations_are_used_as_given(name):
    """Durations arrive as log1p(seconds). The logit of a logistic regression is affine in each
    input as given, so equal steps in the value give equal steps in the logit. A second log1p
    inside learn would bend it."""
    base = flags("recent", "same_app")
    short = {k: math.log1p(5) for k in ("gap", "prev_seconds", "context_seconds")}
    long = {k: math.log1p(3000) for k in ("gap", "prev_seconds", "context_seconds")}
    rows = [(features(base, **short), 1), (features(base, **long), 0)] * 5
    scorer, _ = Scorer.from_rules().fit(rows, epochs=20)

    logits = []
    for value in (1.0, 3.0, 5.0):
        p = scorer.p_join(features(base, **{**short, name: value}))
        assert 1e-9 < p < 1 - 1e-9
        logits.append(_logit(p))
    first, second = logits[1] - logits[0], logits[2] - logits[1]
    assert abs(first) > 1e-6, "the fit left this duration without weight"
    assert second == pytest.approx(first, rel=1e-6, abs=1e-9)


def _toy_rows() -> list[tuple[dict, int]]:
    """Separable on to_browser alone; the rule table calls every row a break."""
    rows = []
    for on in [(), ("same_app",), ("closed",), ("same_app", "closed")]:
        f = flags(*on)
        rows.append((features(f, to_browser=1.0), 1))
        rows.append((features(f, to_browser=0.0), 0))
    return rows


def test_fit_reaches_full_agreement_on_a_separable_toy_set():
    rows = _toy_rows()
    base = Scorer.from_rules()
    before = base.to_dict()
    assert base.agreement(rows) < 1.0
    result = base.fit(rows, epochs=200)
    assert isinstance(result, tuple) and len(result) == 2
    fitted, agreement = result
    assert isinstance(fitted, Scorer)
    assert fitted is not base
    assert agreement == 1.0
    assert fitted.agreement(rows) == 1.0
    assert base.to_dict() == before, "fit must return a new scorer and leave the old one alone"


def test_agreement_is_the_share_predicted_correctly_at_one_half():
    scorer = Scorer.from_rules()
    rows = [(JOIN_X, 1), (BREAK_Y, 0), (JOIN_X, 0), (BREAK_Y, 1)]
    assert scorer.p_join(JOIN_X) >= 0.5 > scorer.p_join(BREAK_Y)
    assert scorer.agreement(rows) == pytest.approx(0.5)
    assert scorer.agreement(rows[:2]) == pytest.approx(1.0)
    assert scorer.agreement(rows[2:]) == pytest.approx(0.0)


def test_to_dict_from_dict_round_trip():
    scorer, _ = Scorer.from_rules().fit(_toy_rows(), epochs=50)
    data = scorer.to_dict()
    assert isinstance(data, dict)
    copy = Scorer.from_dict(json.loads(json.dumps(data)))
    assert copy.to_dict() == data
    for f in all_combos()[::97]:
        for to_browser in (0.0, 1.0):
            feats = features(f, to_browser=to_browser)
            assert copy.p_join(feats) == pytest.approx(scorer.p_join(feats), abs=1e-12)


# ---------------------------------------------------------------- decide

def test_decide_returns_hard_rules_unchanged_for_every_hard_combination(rules_scorer):
    for f in all_combos():
        choice, confidence, reason, hard = rule_choice(f)
        if not hard:
            continue
        d = decide(features(f), rules_scorer)
        assert isinstance(d, Decision)
        assert (d.choice, d.reason, d.hard) == (choice, reason, True), f
        assert d.confidence == pytest.approx(confidence)
        assert d.p_join is None
        assert d.unsure is False


def test_decide_does_not_consult_the_scorer_on_a_hard_rule():
    stub = FixedScorer(0.99)
    d = decide(features(flags("idle", "shared_words", "recent")), stub)
    assert d.choice == "break" and d.hard and d.p_join is None
    assert stub.calls == 0


@pytest.mark.parametrize("p", [0.65, 0.8, 1.0])
@pytest.mark.parametrize("same_app, expected", [(True, "same"), (False, "supporting")])
def test_decide_joins_at_or_above_0_65(p, same_app, expected):
    on = ("closed", "same_app") if same_app else ("closed",)
    d = decide(features(flags(*on)), FixedScorer(p))
    assert d.choice == expected
    assert d.reason == "scorer"
    assert d.hard is False
    assert d.unsure is False
    assert d.p_join == pytest.approx(p)
    assert 0.0 <= d.confidence <= 1.0


@pytest.mark.parametrize("p", [0.35, 0.2, 0.0])
@pytest.mark.parametrize("recent, expected", [(True, "diversion"), (False, "break")])
def test_decide_breaks_at_or_below_0_35(p, recent, expected):
    on = ("shared_words", "same_app", "recent") if recent else ("shared_words", "same_app")
    d = decide(features(flags(*on)), FixedScorer(p))
    assert d.choice == expected
    assert d.reason == "scorer"
    assert d.hard is False
    assert d.unsure is False
    assert d.p_join == pytest.approx(p)
    assert 0.0 <= d.confidence <= 1.0


@pytest.mark.parametrize("p", [0.36, 0.5, 0.64])
@pytest.mark.parametrize("on", [
    (),
    ("recent",),
    ("closed",),
    ("new_search",),
    ("shared_words", "recent", "same_app"),
    ("shared_words", "recent"),
])
def test_decide_between_thresholds_is_unsure_with_rule_fallback(p, on):
    f = flags(*on)
    d = decide(features(f), FixedScorer(p))
    assert d.unsure is True
    assert d.reason == "unsure"
    assert d.choice == rule_choice(f)[0]
    assert d.hard is False
    assert d.p_join == pytest.approx(p)


# ---------------------------------------------------------------- Asker

MONDAY_10 = local(2026, 10, 19, 10, 0)     # work hours
MONDAY_09 = local(2026, 10, 19, 9, 0)      # work hours
MONDAY_08 = local(2026, 10, 19, 8, 0)      # before work
MONDAY_20 = local(2026, 10, 19, 20, 0)     # after work
SATURDAY_10 = local(2026, 10, 24, 10, 0)   # weekend


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
    for p, when, seen in itertools.product((0.5, 0.95), (MONDAY_10, MONDAY_20), (True, False)):
        key = asker.context_key(p, when, seen)
        assert key in set(asker.contexts)
        keys[(p, when, seen)] = key
    assert len(set(keys.values())) == 8

    def same(a, b):
        return asker.context_key(*a) == asker.context_key(*b)

    # Margin: |p - 0.5| < 0.1 is near.
    assert same((0.45, MONDAY_10, True), (0.55, MONDAY_10, True))
    assert same((0.42, MONDAY_10, True), (0.5, MONDAY_10, True))
    assert same((0.05, MONDAY_10, True), (0.95, MONDAY_10, True))
    assert same((0.25, MONDAY_10, True), (0.75, MONDAY_10, True))
    assert not same((0.55, MONDAY_10, True), (0.7, MONDAY_10, True))
    assert not same((0.45, MONDAY_10, True), (0.3, MONDAY_10, True))
    # Work hours: 9 to 17 local, Monday to Friday.
    assert same((0.5, MONDAY_09, True), (0.5, MONDAY_10, True))
    assert same((0.5, MONDAY_08, True), (0.5, MONDAY_20, True))
    assert same((0.5, SATURDAY_10, True), (0.5, MONDAY_20, True))
    assert not same((0.5, MONDAY_10, True), (0.5, SATURDAY_10, True))
    assert not same((0.5, MONDAY_10, True), (0.5, MONDAY_08, True))
    # Pair seen.
    assert not same((0.5, MONDAY_10, True), (0.5, MONDAY_10, False))


def _stats(alpha: float, beta: float) -> dict:
    keys = Asker.load({}).contexts
    return {key: {"alpha": alpha, "beta": beta, "asks": 0} for key in keys}


CASES = list(itertools.product((0.5, 0.9), (MONDAY_10, SATURDAY_10), (True, False)))


def ask(asker: Asker, p: float, when: datetime, seen: bool, open_count: int = 0,
        last=None, rng=None) -> bool:
    return asker.should_ask(SimpleNamespace(p_join=p), open_count, when,
                            pair_seen=seen, last_ask_at=last, rng=rng)


def test_beta_50_1_asks():
    asker = Asker.load(_stats(50, 1))
    rng = random.Random(1234)
    for _ in range(25):
        for p, when, seen in CASES:
            assert ask(asker, p, when, seen, rng=rng) is True


def test_beta_1_50_holds():
    asker = Asker.load(_stats(1, 50))
    rng = random.Random(1234)
    for _ in range(25):
        for p, when, seen in CASES:
            assert ask(asker, p, when, seen, rng=rng) is False


def test_should_ask_accepts_a_decision():
    asker = Asker.load(_stats(50, 1))
    d = Decision(choice="break", confidence=0.6, reason="unsure", hard=False,
                 p_join=0.5, unsure=True)
    assert asker.should_ask(d, 0, MONDAY_10, rng=random.Random(3)) is True
    assert asker.should_ask(d, 3, MONDAY_10, rng=random.Random(3)) is False


def test_should_ask_defaults_pair_seen_false():
    asker = Asker.load({})
    keen = asker.context_key(0.5, MONDAY_10, False)
    stats = _stats(1, 50)
    stats[keen] = {"alpha": 50, "beta": 1, "asks": 0}
    asker = Asker.load(stats)
    rng = random.Random(11)
    for _ in range(20):
        assert asker.should_ask(SimpleNamespace(p_join=0.5), 0, MONDAY_10, rng=rng) is True


def test_should_ask_uses_the_asker_seed_when_no_rng_is_given():
    def run(seed):
        asker = Asker.load(_stats(1, 1), seed=seed)
        return [asker.should_ask(SimpleNamespace(p_join=p), 0, when, pair_seen=seen)
                for _ in range(10) for p, when, seen in CASES]

    first = run(42)
    assert first == run(42)
    assert True in first and False in first
    assert all(Asker.load(_stats(50, 1), seed=1).should_ask(
        SimpleNamespace(p_join=0.5), 0, MONDAY_10) for _ in range(20))


def test_should_ask_respects_open_count():
    asker = Asker.load(_stats(50, 1))
    rng = random.Random(7)
    assert ask(asker, 0.5, MONDAY_10, True, 2, rng=rng) is True
    assert ask(asker, 0.5, MONDAY_10, True, 3, rng=rng) is False
    assert ask(asker, 0.5, MONDAY_10, True, 5, rng=rng) is False


def test_should_ask_waits_ten_minutes_after_the_last_ask():
    asker = Asker.load(_stats(50, 1))
    rng = random.Random(7)
    now = MONDAY_10
    assert ask(asker, 0.5, now, True, last=now - timedelta(minutes=1), rng=rng) is False
    assert ask(asker, 0.5, now, True, last=now - timedelta(minutes=9, seconds=59), rng=rng) is False
    assert ask(asker, 0.5, now, True, last=now - timedelta(minutes=10, seconds=1), rng=rng) is True
    assert ask(asker, 0.5, now, True, last=now - timedelta(hours=3), rng=rng) is True
    assert ask(asker, 0.5, now, True, last=None, rng=rng) is True


def test_should_ask_uses_the_context_posterior():
    asker = Asker.load({})
    keen = asker.context_key(0.5, MONDAY_10, True)
    stats = _stats(1, 50)
    stats[keen] = {"alpha": 50, "beta": 1, "asks": 0}
    asker = Asker.load(stats)
    rng = random.Random(99)
    for _ in range(20):
        assert ask(asker, 0.5, MONDAY_10, True, rng=rng) is True
        assert ask(asker, 0.5, MONDAY_10, False, rng=rng) is False
        assert ask(asker, 0.9, MONDAY_10, True, rng=rng) is False


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


# ---------------------------------------------------------------- refit

NIGHT = local(2026, 10, 20, 2, 30)


def _store_with_new_labels(now: datetime, refit_at: datetime | None = None, n: int = 10,
                           **kwargs) -> FakeStore:
    specs = [(JOIN_X, "break")] * n
    rows = label_rows(specs, now - timedelta(minutes=30))
    settings = {"refit_at": utc_text(refit_at)} if refit_at else {}
    return FakeStore(rows, settings=settings, **kwargs)


@pytest.mark.parametrize("wall", [
    (0, 59), (5, 0), (5, 30), (12, 0), (23, 59),
])
def test_refit_not_due_outside_one_to_five(wall):
    now = local(2026, 10, 20, *wall)
    store = _store_with_new_labels(now)
    assert refit(store, now) is None
    assert store.saved == []


@pytest.mark.parametrize("wall", [(1, 0), (2, 30), (4, 59)])
def test_refit_due_between_one_and_five(wall):
    now = local(2026, 10, 20, *wall)
    store = _store_with_new_labels(now)
    result = refit(store, now)
    assert result is not None
    assert result["examples"] == 10


def test_refit_not_due_within_twenty_hours_of_the_last():
    store = _store_with_new_labels(NIGHT, refit_at=NIGHT - timedelta(hours=19, minutes=59))
    assert refit(store, NIGHT) is None
    assert store.saved == []


def test_refit_due_twenty_hours_after_the_last():
    store = _store_with_new_labels(NIGHT, refit_at=NIGHT - timedelta(hours=21))
    assert refit(store, NIGHT) is not None


def test_refit_not_due_without_new_relation_labels():
    rows = label_rows([(JOIN_X, "break")] * 10, NIGHT - timedelta(hours=30))
    rows += label_rows([(JOIN_X, "same")] * 3, NIGHT - timedelta(hours=1), kind="name")
    for i, row in enumerate(rows):
        row["id"], row["key"] = i + 1, f"r{i}"
    store = FakeStore(rows, settings={"refit_at": utc_text(NIGHT - timedelta(hours=24))})
    assert refit(store, NIGHT) is None
    assert refit(store, NIGHT, force=True) is None
    assert store.saved == []


def test_refit_not_due_with_no_labels_at_all():
    store = FakeStore()
    assert refit(store, NIGHT) is None
    assert store.saved == []


def test_refit_force_skips_the_time_gate():
    noon = local(2026, 10, 20, 12, 0)
    store = _store_with_new_labels(noon, refit_at=noon - timedelta(hours=1))
    assert refit(store, noon) is None
    result = refit(store, noon, force=True)
    assert result is not None
    assert result["examples"] == 10


def test_refit_accepts_a_better_fit_and_saves_the_record():
    # The rule table joins JOIN_X; every label says it was a break.
    specs = [(JOIN_X, "break")] * 15 + [(JOIN_X, "diversion")] * 10
    rows = label_rows(specs, NIGHT - timedelta(hours=3))
    rows += label_rows([(BREAK_Y, "same")] * 4, NIGHT - timedelta(hours=5), kind="name")
    store = FakeStore(rows)
    assert Scorer.from_rules().p_join(JOIN_X) >= 0.5

    result = refit(store, NIGHT)

    assert set(result) >= {"examples", "agreement", "accepted"}
    assert result["accepted"] is True
    assert result["examples"] == 25
    assert result["agreement"] == pytest.approx(1.0)
    assert len(store.saved) == 1
    record = store.saved[0]
    assert record["kind"] == "scorer"
    assert record["basis"] == "labels"
    assert record["examples"] == 25
    assert record["agreement"] == pytest.approx(result["agreement"])
    saved = Scorer.from_dict(weights_of(record))
    assert saved.p_join(JOIN_X) < 0.5
    # The refit time is recorded, so a second run the same night is not due.
    assert store.setting("refit_at") is not None
    assert refit(store, NIGHT + timedelta(minutes=30)) is None
    assert len(store.saved) == 1


class FittedStub:
    """Stands in for the scorer that Scorer.fit returns, so the gate is tested apart from SGD."""

    def __init__(self, p: float):
        self.p = p

    def p_join(self, features):
        return self.p

    def agreement(self, rows):
        rows = list(rows)
        return sum((self.p >= 0.5) == bool(label) for _, label in rows) / len(rows)

    def update(self, features, label, lr=0.05):
        pass

    def to_dict(self):
        return {"stub_p": self.p}


def _patch_fit(monkeypatch, p: float) -> list:
    """Make Scorer.fit return (FittedStub(p), its agreement on the rows).

    Returns the list of row lists fit received.
    """
    seen = []

    def fake_fit(*args, **kwargs):
        rows = kwargs.get("rows")
        if rows is None:
            rows = next(a for a in args if isinstance(a, (list, tuple)))
        seen.append(list(rows))
        stub = FittedStub(p)
        return stub, stub.agreement(rows)

    monkeypatch.setattr(Scorer, "fit", fake_fit)
    return seen


def test_refit_fits_on_every_relation_label_with_joins_as_one(monkeypatch):
    specs = ([(JOIN_X, "same")] * 4 + [(BREAK_Y, "supporting")] * 3
             + [(BREAK_Y, "break")] * 2 + [(JOIN_X, "diversion")] * 1)
    rows = label_rows(specs, NIGHT - timedelta(hours=3))
    rows += label_rows([(BREAK_Y, "same")] * 5, NIGHT - timedelta(hours=2), kind="name")
    seen = _patch_fit(monkeypatch, 0.9)
    result = refit(FakeStore(rows), NIGHT)
    assert result["examples"] == 10
    assert len(seen) == 1
    fitted_rows = seen[0]
    assert len(fitted_rows) == 10
    assert sorted(int(label) for _, label in fitted_rows) == [0] * 3 + [1] * 7
    for feats, label in fitted_rows:
        assert feats == JOIN_X or feats == BREAK_Y


def test_refit_refuses_a_worse_fit(monkeypatch):
    # The newest 20 % say same on JOIN_X, as the rule scorer does; the new fit says break.
    specs = [(JOIN_X, "break")] * 40 + [(JOIN_X, "same")] * 10
    store = FakeStore(label_rows(specs, NIGHT - timedelta(hours=3)))
    assert Scorer.from_rules().p_join(JOIN_X) >= 0.5
    _patch_fit(monkeypatch, 0.1)

    result = refit(store, NIGHT)

    assert set(result) >= {"examples", "agreement", "accepted"}
    assert result["accepted"] is False
    assert result["examples"] == 50
    assert result["agreement"] == pytest.approx(0.0)
    assert store.saved == []


def test_refit_accepts_a_better_fit_through_the_gate(monkeypatch):
    specs = [(JOIN_X, "break")] * 50
    store = FakeStore(label_rows(specs, NIGHT - timedelta(hours=3)))
    _patch_fit(monkeypatch, 0.1)
    result = refit(store, NIGHT)
    assert result["accepted"] is True
    assert result["agreement"] == pytest.approx(1.0)
    assert len(store.saved) == 1
    record = store.saved[0]
    assert (record["kind"], record["basis"], record["examples"]) == ("scorer", "labels", 50)
    assert record["agreement"] == pytest.approx(1.0)
    assert weights_of(record) == {"stub_p": 0.1}


def test_refit_accepts_a_tie(monkeypatch):
    # Both the rule scorer and the new fit get the newest rows right.
    specs = [(JOIN_X, "same")] * 20
    store = FakeStore(label_rows(specs, NIGHT - timedelta(hours=3)))
    _patch_fit(monkeypatch, 0.9)
    result = refit(store, NIGHT)
    assert result["accepted"] is True
    assert len(store.saved) == 1


def test_refit_holdout_is_the_newest_fifth(monkeypatch):
    # 9 rows: the newest 20 % is at least 1 row. Only the newest row says break.
    specs = [(JOIN_X, "same")] * 8 + [(JOIN_X, "break")]
    store = FakeStore(label_rows(specs, NIGHT - timedelta(hours=3)))
    _patch_fit(monkeypatch, 0.1)   # predicts break everywhere
    result = refit(store, NIGHT)
    # Rule scorer joins JOIN_X: 0 on the newest row. The stub: 1. Accepted.
    assert result["accepted"] is True
    assert result["agreement"] == pytest.approx(1.0)


def test_refit_compares_against_the_stored_scorer(monkeypatch):
    # Newest labels say BREAK_Y is the same task. The rule scorer breaks it, as does the new fit.
    specs = [(BREAK_Y, "break")] * 40 + [(BREAK_Y, "same")] * 10
    rows = label_rows(specs, NIGHT - timedelta(hours=3))
    stored, _ = Scorer.from_rules().fit([(BREAK_Y, 1)] * 20, epochs=100)
    assert stored.p_join(BREAK_Y) >= 0.5
    model = {"kind": "scorer", "version": 1, "created_at": utc_text(NIGHT - timedelta(days=2)),
             "weights": stored.to_dict(), "examples": 20, "agreement": 1.0, "basis": "labels"}
    _patch_fit(monkeypatch, 0.1)

    # No stored scorer: the rule scorer scores 0 on the newest rows, a tie, so the fit is kept.
    assert refit(FakeStore(rows), NIGHT)["accepted"] is True

    # The stored scorer scores 1 on the newest rows, so the new fit is refused.
    store = FakeStore(rows, model=model)
    result = refit(store, NIGHT)
    assert result["accepted"] is False
    assert store.saved == []
