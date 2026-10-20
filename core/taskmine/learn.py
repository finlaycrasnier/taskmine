"""Learning: the rule table, the local scorer, the asker and the nightly refit."""
from __future__ import annotations

import itertools
import json
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

SIGNALS = (
    "idle", "case_conflict", "shared_anchor", "same_surface", "copy_paste",
    "search_then_create", "new_search", "context_saved", "shared_words", "recent",
    "same_app", "closed",
)
NUMBERS = (
    "gap", "prev_seconds", "context_seconds", "link_strong", "link_weak",
    "hour_sin", "hour_cos", "to_browser",
)
FEATURES = SIGNALS + NUMBERS
DURATIONS = frozenset({"gap", "prev_seconds", "context_seconds"})
JOINS = frozenset({"same", "supporting"})

JOIN_AT = 0.65
BREAK_AT = 0.35
LEARNING_RATE = 0.05
RULE_EPOCHS = 60
RULE_RATE = 0.1
SOFT_WEIGHT = 10
IDLE_S = 600.0
RECENT_S = 90.0
MAX_GAP_S = 7200.0

NEAR_MARGIN = 0.1
WORK_START, WORK_END = 9, 17
MAX_OPEN = 3
ASK_SPACING = timedelta(minutes=10)

REFIT_HOURS = (1, 5)
REFIT_SPACING = timedelta(hours=20)
HOLDOUT_SHARE = 5

_RULES_CACHE: dict[str, Any] = {}


# ---------------------------------------------------------------- rule table

def rule_choice(flags: dict[str, Any]) -> tuple[str, float, str, bool]:
    """Grouper spec 4.5 rows 1 to 13, first match wins: (choice, confidence, reason, hard)."""
    on = {name: bool(flags.get(name)) for name in SIGNALS}
    for name, choice, confidence in (
        ("idle", "break", 1.00), ("case_conflict", "break", 1.00),
        ("shared_anchor", "same", 0.95), ("same_surface", "same", 0.90),
        ("copy_paste", "supporting", 0.85), ("search_then_create", "supporting", 0.85),
    ):
        if on[name]:
            return choice, confidence, name, True
    if on["new_search"]:
        return "break", 0.85, "new_search", on["context_saved"]
    if on["shared_words"] and on["recent"]:
        return ("same" if on["same_app"] else "supporting"), 0.70, "shared_words", False
    if on["closed"]:
        return "break", 0.75, "closed", False
    if on["recent"]:
        return "diversion", 0.60, "switch", False
    return "break", 0.65, "gap", False


# ---------------------------------------------------------------- scorer

def _sigmoid(z: float) -> float:
    """Logistic function without overflow."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _raw(features: dict[str, Any], name: str) -> float:
    """One feature value, with log(1 + seconds) applied to durations."""
    value = float(features.get(name, 0.0) or 0.0)
    return math.log1p(max(0.0, value)) if name in DURATIONS else value


class Scorer:
    """Logistic regression over FEATURES; p_join is the chance the task continues."""

    def __init__(self, weights: dict[str, float], bias: float,
                 mean: dict[str, float], scale: dict[str, float]):
        self.weights = {name: float(weights.get(name, 0.0)) for name in FEATURES}
        self.bias = float(bias)
        self.mean = {name: float(mean.get(name, 0.0)) for name in NUMBERS}
        self.scale = {name: float(scale.get(name, 1.0)) or 1.0 for name in NUMBERS}

    @classmethod
    def from_rules(cls) -> Scorer:
        """Starting weights fitted on the 4096 rule-table combinations."""
        if not _RULES_CACHE:
            _RULES_CACHE.update(_fit_rules().to_dict())
        return cls.from_dict(_RULES_CACHE)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scorer:
        """Rebuild a scorer from to_dict output."""
        return cls(data["weights"], data["bias"], data["mean"], data["scale"])

    @classmethod
    def load(cls, store: Any) -> Scorer:
        """The newest stored scorer, or the rule scorer when none is stored."""
        record = store.load_model("scorer")
        return cls.from_dict(_weights(record)) if record else cls.from_rules()

    def to_dict(self) -> dict[str, Any]:
        """Plain data for storage."""
        return {"weights": dict(self.weights), "bias": self.bias,
                "mean": dict(self.mean), "scale": dict(self.scale)}

    def vector(self, features: dict[str, Any]) -> list[float]:
        """Inputs in FEATURES order, numbers standardised."""
        out = [_raw(features, name) for name in SIGNALS]
        out += [(_raw(features, n) - self.mean[n]) / self.scale[n] for n in NUMBERS]
        return out

    def _p(self, x: list[float]) -> float:
        """Probability for a prepared vector."""
        z = self.bias + sum(self.weights[n] * v for n, v in zip(FEATURES, x))
        return _sigmoid(z)

    def _step(self, x: list[float], label: float, lr: float) -> None:
        """One gradient step on a prepared vector."""
        err = lr * (label - self._p(x))
        self.bias += err
        for name, v in zip(FEATURES, x):
            self.weights[name] += err * v

    def p_join(self, features: dict[str, Any]) -> float:
        """Probability that the switch continues the task."""
        return float(self._p(self.vector(features)))

    def update(self, features: dict[str, Any], label: Any, lr: float = LEARNING_RATE) -> None:
        """One step of stochastic gradient descent towards label (1 join, 0 break)."""
        self._step(self.vector(features), float(bool(label)), lr)

    def fit(self, rows: list[tuple[dict, Any]], epochs: int = 20,
            lr: float = LEARNING_RATE) -> tuple[Scorer, float]:
        """A new scorer trained from this one on rows; returns it and its agreement."""
        fitted = Scorer.from_dict(self.to_dict())
        prepared = [(fitted.vector(f), float(bool(y))) for f, y in rows]
        fitted._train(prepared, epochs, lr, random.Random(0))
        return fitted, fitted.agreement(rows)

    def _train(self, prepared: list[tuple[list[float], float]], epochs: int,
               lr: float, rng: random.Random) -> None:
        """Shuffled SGD over prepared rows."""
        order = list(range(len(prepared)))
        for _ in range(epochs):
            rng.shuffle(order)
            for i in order:
                self._step(prepared[i][0], prepared[i][1], lr)

    def agreement(self, rows: list[tuple[dict, Any]]) -> float:
        """Share of rows whose label matches p_join >= 0.5."""
        rows = list(rows)
        if not rows:
            return 0.0
        hits = sum((self.p_join(f) >= 0.5) == bool(y) for f, y in rows)
        return hits / len(rows)


def _rule_rows() -> list[tuple[dict[str, float], int]]:
    """The 4096 flag combinations with sampled gaps, labelled by the rule table."""
    rng = random.Random(0)
    rows = []
    for bits in itertools.product((0.0, 1.0), repeat=len(SIGNALS)):
        feats = dict(zip(SIGNALS, bits)) | {name: 0.0 for name in NUMBERS}
        if feats["idle"]:
            feats["gap"] = rng.uniform(IDLE_S + 1, MAX_GAP_S)
        elif feats["recent"]:
            feats["gap"] = rng.uniform(0.0, RECENT_S)
        else:
            feats["gap"] = rng.uniform(RECENT_S + 1, IDLE_S)
        rows.append((feats, int(rule_choice(feats)[0] in JOINS)))
    return rows


def _standardise(rows: list[tuple[dict, int]]) -> tuple[dict, dict]:
    """Mean and scale of each number over rows; scale 1 when a number does not vary."""
    mean, scale = {}, {}
    for name in NUMBERS:
        values = [_raw(f, name) for f, _ in rows]
        m = sum(values) / len(values)
        sd = math.sqrt(sum((v - m) ** 2 for v in values) / len(values))
        mean[name], scale[name] = m, sd if sd > 1e-12 else 1.0
    return mean, scale


def _fit_rules() -> Scorer:
    """Fit the starting scorer on the rule rows; soft rows count ten times, hard ones bypass it."""
    rows = _rule_rows()
    mean, scale = _standardise(rows)
    scorer = Scorer({}, 0.0, mean, scale)
    prepared = []
    for f, y in rows:
        copies = 1 if rule_choice(f)[3] else SOFT_WEIGHT
        prepared += [(scorer.vector(f), float(y))] * copies
    scorer._train(prepared, RULE_EPOCHS, RULE_RATE, random.Random(0))
    return scorer


# ---------------------------------------------------------------- decide

@dataclass
class Decision:
    """One switch decision: the choice, how sure, and why."""

    choice: str
    confidence: float
    reason: str
    hard: bool
    p_join: float | None = None
    unsure: bool = False


def decide(features: dict[str, Any], scorer: Any) -> Decision:
    """Hard rules decide alone; otherwise the scorer, with the rule table when unsure."""
    choice, confidence, reason, hard = rule_choice(features)
    if hard:
        return Decision(choice, confidence, reason, True)
    p = float(scorer.p_join(features))
    if p >= JOIN_AT:
        joined = "same" if features.get("same_app") else "supporting"
        return Decision(joined, p, "scorer", False, p)
    if p <= BREAK_AT:
        broken = "diversion" if features.get("recent") else "break"
        return Decision(broken, 1.0 - p, "scorer", False, p)
    return Decision(choice, confidence, "unsure", False, p, True)


# ---------------------------------------------------------------- asker

def _context(near: bool, work: bool, seen: bool) -> str:
    """Context key from its three bits."""
    return "-".join(("near" if near else "far", "work" if work else "off",
                     "seen" if seen else "new"))


CONTEXTS = tuple(_context(*bits) for bits in itertools.product((True, False), repeat=3))


def _is_work_hours(when: datetime) -> bool:
    """Monday to Friday, 9 to 17 local."""
    local = when.astimezone()
    return local.weekday() < 5 and WORK_START <= local.hour < WORK_END


class Asker:
    """Thompson sampling over eight contexts: is a question worth asking now?"""

    contexts = CONTEXTS

    def __init__(self, stats: dict[str, dict], seed: int | None = None):
        self.stats = stats
        self.rng = random.Random(seed)

    @classmethod
    def load(cls, stats: Any, seed: int | None = None) -> Asker:
        """An asker from stored stats, or from a store's ask_stats(); missing contexts start at Beta(1, 1)."""
        if hasattr(stats, "ask_stats"):
            stats = stats.ask_stats()
        stats = stats or {}
        clean = {}
        for key in CONTEXTS:
            row = stats.get(key) or {}
            clean[key] = {"alpha": row.get("alpha", 1), "beta": row.get("beta", 1),
                          "asks": row.get("asks", 0)}
        return cls(clean, seed)

    def context_key(self, p: float, when: datetime, pair_seen: bool) -> str:
        """Near or far from 0.5, work hours or not, app pair seen or not."""
        return _context(abs(p - 0.5) < NEAR_MARGIN, _is_work_hours(when), bool(pair_seen))

    def should_ask(self, decision: Any, open_count: int, now: datetime, *,
                   pair_seen: bool = False, last_ask_at: datetime | None = None,
                   rng: random.Random | None = None) -> bool:
        """Sample the context's answer rate; ask when above 0.5 and the gates allow."""
        p = getattr(decision, "p_join", None)
        if p is None or getattr(decision, "hard", False) or open_count >= MAX_OPEN:
            return False
        if last_ask_at is not None and now - last_ask_at < ASK_SPACING:
            return False
        row = self.stats[self.context_key(p, now, pair_seen)]
        if (rng or self.rng).betavariate(row["alpha"], row["beta"]) <= 0.5:
            return False
        row["asks"] += 1
        return True

    def reward(self, context: str, outcome: str) -> None:
        """Answered adds to alpha; dismissed or expired add to beta; later changes nothing."""
        row = self.stats[context]
        if outcome == "answered":
            row["alpha"] += 1
        elif outcome in ("dismissed", "expired"):
            row["beta"] += 1
        elif outcome != "later":
            raise ValueError(f"unknown outcome: {outcome}")

    def to_dict(self) -> dict[str, dict]:
        """Stats per context, as load takes them."""
        return {key: dict(row) for key, row in self.stats.items()}


# ---------------------------------------------------------------- refit

def _parse_time(value: Any) -> datetime:
    """An aware datetime from a datetime or ISO text; naive text is UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _utc_text(when: datetime) -> str:
    """UTC ISO 8601 with a Z."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _weights(record: dict[str, Any]) -> dict[str, Any]:
    """The weights of a stored model record, decoded when stored as text."""
    weights = record["weights"]
    return json.loads(weights) if isinstance(weights, str) else weights


def _features(row: dict[str, Any]) -> dict[str, Any]:
    """A label row's features as a dict."""
    feats = row["features"]
    return json.loads(feats) if isinstance(feats, str) else feats


def _due(store: Any, now: datetime, force: bool) -> bool:
    """New relation labels exist and, unless forced, it is 01:00 to 05:00 and 20 h since."""
    last = store.setting("refit_at")
    if not store.labels("relation", since=last):
        return False
    if force:
        return True
    if not REFIT_HOURS[0] <= now.astimezone().hour < REFIT_HOURS[1]:
        return False
    return last is None or now - _parse_time(last) >= REFIT_SPACING


def refit(store: Any, now: datetime, force: bool = False) -> dict[str, Any] | None:
    """Refit on every relation label; keep it if the newest fifth is predicted at least as well."""
    if not _due(store, now, force):
        return None
    labels = sorted(store.labels("relation"), key=lambda r: (_parse_time(r["at"]), r["id"]))
    rows = [(_features(r), int(r["label"] in JOINS)) for r in labels]
    holdout = rows[-max(1, len(rows) // HOLDOUT_SHARE):]
    record = store.load_model("scorer")
    current = Scorer.from_dict(_weights(record)) if record else Scorer.from_rules()
    fitted, _ = Scorer.from_rules().fit(rows)
    agreement = fitted.agreement(holdout)
    previous = current.agreement(holdout)
    accepted = agreement >= previous
    if accepted:
        store.save_model("scorer", fitted.to_dict(), len(rows), agreement, basis="labels")
    store.set_setting("refit_at", _utc_text(now))
    return {"examples": len(rows), "agreement": agreement, "previous": previous,
            "accepted": accepted}
