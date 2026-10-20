"""Learning: the rule table, the notes file, the asker and the nightly tidy."""
from __future__ import annotations

import itertools
import os
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from taskmine.model import THRESHOLD, ModelError

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
JOINS = frozenset({"same", "supporting"})
HARD_RULES = ("idle", "case_conflict", "shared_anchor", "same_surface", "copy_paste",
              "search_then_create", "new_search")

NEAR_MARGIN = 0.1
WORK_START, WORK_END = 9, 17
MAX_OPEN = 3
ASK_SPACING = timedelta(minutes=10)

NOTES_LIMIT = 200
TIDY_HOURS = (1, 5)
TIDY_SPACING = timedelta(hours=20)
FLAG_MIN_LABELS = 50
FLAG_RATE = 0.2
TIDY_INSTRUCTIONS = (
    "These are notes on how one person's window switches group into tasks, one pattern per line, "
    "newest last. Merge duplicate lines. Where two lines disagree, keep the newer one. Keep the "
    "markdown list form, one \"- \" line per pattern, each under 120 characters. Return the list "
    "and nothing else."
)



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


# ---------------------------------------------------------------- decide

@dataclass
class Decision:
    """One switch decision: the choice, how sure, and why."""

    choice: str
    confidence: float
    reason: str
    hard: bool
    model_confidence: float | None = None
    unsure: bool = False


def decide(features: dict[str, Any]) -> Decision:
    """Hard rules decide alone; a soft row is the fallback and stays unsure for the model."""
    choice, confidence, reason, hard = rule_choice(features)
    if hard:
        return Decision(choice, confidence, reason, True)
    return Decision(choice, confidence, reason, False, None, True)


# ---------------------------------------------------------------- notes


class Notes:
    """The person's notes file: one confirmed pattern per line, newest last, mode 0600."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self) -> str:
        """The whole file, or "" when it is missing."""
        try:
            return self.path.read_text()
        except FileNotFoundError:
            return ""

    def lines(self) -> list[str]:
        """Non-blank lines without a leading "- "."""
        out = []
        for line in self.read().splitlines():
            line = line.strip()
            if line:
                out.append(line[2:].strip() if line.startswith("- ") else line)
        return out

    def count(self) -> int:
        """Number of non-blank lines."""
        return len(self.lines())

    def add(self, line: str) -> None:
        """Append one line as "- line"; create the file with mode 0600 when missing."""
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as handle:
            handle.write(f"- {line}\n")

    def replace(self, text: str) -> None:
        """Write the whole file with mode 0600."""
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(text if not text or text.endswith("\n") else text + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def keep_newest(self, limit: int = NOTES_LIMIT) -> None:
        """Keep the newest `limit` lines in list form."""
        self.replace("".join(f"- {line}\n" for line in self.lines()[-limit:]))

    @staticmethod
    def line_for(kind: str, label: str, context: dict) -> str | None:
        """One plain line for a relation or match label; app names only, never titles."""
        if kind == "relation":
            pair = f"{context['left_app']} to {context['right_app']}"
            if label in JOINS:
                return f"{pair} is usually one task"
            if label in ("break", "diversion"):
                return f"{pair} is usually a new task"
            return None
        if kind == "match":
            if label == "same":
                return f"{context['a']} and {context['b']} are the same process"
            if label == "different":
                return f"{context['a']} and {context['b']} are different processes"
        return None


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

    def context_key(self, confidence: float | None, when: datetime, pair_seen: bool) -> str:
        """Model confidence near the threshold or far (far when none), work hours, pair seen."""
        near = confidence is not None and round(abs(confidence - THRESHOLD), 9) < NEAR_MARGIN
        return _context(near, _is_work_hours(when), bool(pair_seen))

    def should_ask(self, decision: Any, open_count: int, now: datetime, *,
                   pair_seen: bool = False, last_ask_at: datetime | None = None,
                   rng: random.Random | None = None) -> bool:
        """Sample the context's answer rate; ask when above 0.5 and the gates allow."""
        if getattr(decision, "hard", False) or open_count >= MAX_OPEN:
            return False
        if last_ask_at is not None and now - last_ask_at < ASK_SPACING:
            return False
        confidence = getattr(decision, "model_confidence", None)
        row = self.stats[self.context_key(confidence, now, pair_seen)]
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


# ---------------------------------------------------------------- tidy and rule report

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


def _due(store: Any, notes: Notes, now: datetime, force: bool) -> bool:
    """Lines exist; unless forced, lines were added since the last tidy, it is 01:00 to 05:00
    local and 20 h have passed."""
    if force:
        return True
    if notes.count() == 0:
        return False
    last, added = store.setting("tidy_at"), store.setting("notes_added_at")
    if added is None or (last is not None and _parse_time(added) <= _parse_time(last)):
        return False
    if not TIDY_HOURS[0] <= now.astimezone().hour < TIDY_HOURS[1]:
        return False
    return last is None or now - _parse_time(last) >= TIDY_SPACING


def tidy(store: Any, notes: Notes, model: Any, now: datetime,
         force: bool = False) -> dict[str, Any] | None:
    """Ask the model to tidy the notes file; keep the newest 200 lines when its reply is unfit."""
    if not _due(store, notes, now, force):
        return None
    before, rewritten = notes.count(), False
    try:
        reply = model.rewrite(notes.read(), TIDY_INSTRUCTIONS)
    except ModelError:
        if before > NOTES_LIMIT:
            notes.keep_newest()
    else:
        kept = [line for line in str(reply or "").splitlines() if line.strip()]
        if kept and len(kept) <= NOTES_LIMIT:
            notes.replace(reply)
            rewritten = True
        else:
            notes.keep_newest()
    stamp = _utc_text(now)
    store.set_setting("tidy_at", stamp)
    store.set_setting("last_tidy_at", stamp)
    return {"before": before, "after": notes.count(), "rewritten": rewritten}


def rule_report(store: Any) -> list[dict[str, Any]]:
    """Labels and disagreements per hard rule; a rule is flagged at 50 labels and over 20 %."""
    counts = {rule: [0, 0] for rule in HARD_RULES}
    for row in store.hard_rule_labels():
        found = counts.get(row["reason"])
        if found is None:
            continue
        found[0] += 1
        found[1] += (row["label"] in JOINS) != (row["choice"] in JOINS)
    return [{"rule": rule, "labels": n, "disagreements": d,
             "flagged": n >= FLAG_MIN_LABELS and d / n > FLAG_RATE}
            for rule, (n, d) in counts.items()]
