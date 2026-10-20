"""Noise and keep stages (grouper spec 1.7, 1.8): score each line, keep the work, weigh words."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from taskmine.group import words as w
from taskmine.group.anchors import IGNORED, ISSUE
from taskmine.group.common import host_path, is_action, title_of, web_page, words

ROLE = {"furniture": 1.0, "control": 0.5, "title": 0.3, "content": -1.0, "unknown": 0.0,
        "alert": 0.0}
WEIGHTS = {"bias": -2.2, "role": 1.2, "repeats": 2.0, "spread": 1.0, "shape": 1.6, "static": 0.8,
           "anchor": -2.5, "warning": -3.0}
KEEP_MODEL = {"bias": 0.4, "noise": -2.0, "anchor": 2.5, "words": 1.5, "threshold": 0.3}
CASE_CAP, NEIGHBOUR, STEP = 0.2, (0.3, 0.7), 0.15
LOW, HIGH = 0.5, 0.8
MAX_UNITS, LINE_MAX, ELEMENT_MAX, FOLD_MAX = 400, 1000, 200, 200
SKIP = frozenset({"edited", "-", "–", "—", "modificato"})
MARKERS = re.compile(r"^(?:\s*⟨(?:clock|count|busy|edited|tracking|fragment|invisible)⟩\s*)+$")
REPEAT = re.compile(r"^\s*⟨repeat×(\d+)[^⟩]*⟩\s*$")
LABEL_VALUE = re.compile(r"[^\W\d_]{2,}\s*:\s*\S")
SENTENCE_END = tuple(".!?…:;")
MENU = frozenset(w.MENU_WORDS)
WARNING = re.compile(r"\b(?:" + "|".join(re.escape(x) for x in w.WARNING_WORDS) + r")\b", re.IGNORECASE)
STATUS = re.compile(r"\b(?:" + "|".join(re.escape(x) for x in w.STATUS_WORDS) + r")\b", re.IGNORECASE)
ANCHORS = (
    re.compile(r"(?:[€$£¥]\s?\d|\d[\d.,]*\s?(?:EUR|USD|GBP|CHF|€|\$|£)\b)"),
    re.compile(r"\b\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}\b"),
    re.compile(r"\b[\w-]+\.(?:" + "|".join(w.FILE_KINDS) + r")\b", re.IGNORECASE),
    re.compile(r"\[[A-Z_]+·|\[redacted\]"),
    re.compile(r"\b[A-Z]{2,4}\s?\d{4,8}\b|\b\d{6,12}\b"),
)
NUMBER_TOKEN = re.compile(r"\b(?=[\w-]*\d)[\w-]{3,}\b")


@dataclass(eq=False)
class Unit:
    """One title, element or line of an action."""

    role: str
    text: str
    used: bool = False
    line: int | None = None
    repeat: int = 1
    score: float = 0.0
    anchor: bool = False
    warning: bool = False

    @property
    def fold(self) -> str:
        """Collapsed, casefolded, first 200 characters."""
        return " ".join(self.text.split()).casefold()[:FOLD_MAX]


def has_case(text: str) -> bool:
    """True when the text holds an issue-style case key."""
    return any(prefix not in IGNORED for prefix, _ in ISSUE.findall(text))


def units(item: dict) -> list[Unit]:
    """Title, element and line units of one action, in screen order."""
    title = title_of(item)
    out = [Unit("title", title)] if title else []
    kind = item.get("interaction")
    if kind in ("click", "edit", "shortcut") and item.get("focus_name"):
        out.append(Unit("control" if kind == "click" else "content",
                        item["focus_name"][:ELEMENT_MAX], used=True))
    marked = set(item.get("furniture_lines") or ())
    folded_title = " ".join(title.split()).casefold()[:FOLD_MAX]
    for n, raw in enumerate((item.get("text") or "").split("\n")):
        line = raw.strip()
        if REPEAT.match(line):
            if out and out[-1].line is not None:
                out[-1].repeat = int(REPEAT.match(line).group(1))
            continue
        if not line or line.casefold() in SKIP or MARKERS.match(line):
            continue
        unit = Unit("unknown", line[:LINE_MAX], line=n)
        unit.role = ("title" if unit.fold == folded_title else
                     "furniture" if n in marked else "unknown")
        if kind == "edit":
            unit.role, unit.used = "content", True
        out.append(unit)
    return out[:MAX_UNITS]


def place(item: dict) -> str:
    """Where an item was shown: page, document, window, surface or app."""
    if web_page(item):
        host, path = host_path(item.get("url"))
        if host:
            return "url:" + host + path
        if item.get("window_ref"):
            return "window:" + item["window_ref"]
    if item.get("document_id"):
        return "doc:" + item["document_id"]
    if item.get("window_ref"):
        return "window:" + item["window_ref"]
    return item.get("surface_key") or item["app"]


def shape(text: str) -> float:
    """How much a text looks like a menu entry."""
    tokens = text.split()
    count = len(tokens)
    short = 1.0 if count <= 3 else 0.5 if count == 4 else 0.0
    no_digit = 0.0 if any(c.isdigit() for c in text) else 1.0
    no_sentence = 0.0 if text.rstrip().endswith(SENTENCE_END) or count >= 7 else 1.0
    found = [x.casefold() for x in re.findall(r"[^\W\d_]+", text)]
    menu = 1.0 if found and all(x in MENU for x in found) else 0.5 if MENU & set(found) else 0.0
    value = 0.4 * short + 0.2 * no_digit + 0.2 * no_sentence + 0.2 * menu
    return value / 2 if LABEL_VALUE.search(text) else value


class _Pass:
    """Counts over one pass that the signals need."""

    def __init__(self, items: list[dict], found: dict):
        self.places: dict[str, set] = {}
        self.docs: dict[str, set] = {}
        self.holders: dict[tuple, set] = {}
        self.doc_holders: dict[tuple, set] = {}
        self.reads: dict[str, list] = {}
        self.used: dict[str, set] = {}
        for item in items:
            app, where = item["app"], place(item)
            self.places.setdefault(app, set()).add(where)
            snapshot = not is_action(item)
            if snapshot:
                self.docs.setdefault(app, set()).add(where)
                lines = frozenset(u.fold for u in found[item["id"]] if u.line is not None)
                self.reads.setdefault(where, []).append((lines, int(item.get("sightings") or 1)))
            if item.get("interaction") in ("click", "edit") and item.get("focus_name"):
                self.used.setdefault(where, set()).add(" ".join(item["focus_name"].split()).casefold())
            for unit in found[item["id"]]:
                self.holders.setdefault((app, unit.fold), set()).add(where)
                if snapshot:
                    self.doc_holders.setdefault((app, unit.fold), set()).add(where)

    def repeats(self, app: str, unit: Unit) -> float:
        """Share of the app's other documents that show the text."""
        n = len(self.doc_holders.get((app, unit.fold), ()))
        docs = len(self.docs.get(app, ()))
        return min(1.0, max(0, n - 1) / (max(docs, 3) - 1))

    def spread(self, app: str, unit: Unit) -> float:
        """Share of the app's other places in this pass that hold the text."""
        total = len(self.places.get(app, ()))
        held = len(self.holders.get((app, unit.fold), ()))
        return (held - 1) / (total - 1) if total >= 2 else 0.0

    def static(self, where: str, unit: Unit) -> float:
        """How surely the line stays the same across the place's reads."""
        reads = self.reads.get(where, [])
        total = sum(n for _, n in reads)
        if len(reads) < 2 or len({lines for lines, _ in reads}) < 2 or total <= 0:
            return 0.0
        holding = sum(n for lines, n in reads if unit.fold in lines)
        return min(1.0, (total - 1) / 4) * holding / total


def _sigmoid(z: float) -> float:
    """Logistic function."""
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


def _score(unit: Unit, item: dict, counts: _Pass) -> float:
    """The noise score of one unit, before the neighbour pass."""
    app, where = item["app"], place(item)
    unit.anchor = any(p.search(unit.text) for p in ANCHORS) or has_case(unit.text)
    unit.warning = unit.role == "alert" or bool(WARNING.search(unit.text))
    unit.used = unit.used or (unit.line is not None and unit.fold in counts.used.get(where, ()))
    element = unit.used and unit.line is None and unit.role != "title"
    z = (WEIGHTS["bias"] + WEIGHTS["role"] * ROLE[unit.role]
         + WEIGHTS["repeats"] * (0.0 if element else counts.repeats(app, unit))
         + WEIGHTS["spread"] * counts.spread(app, unit) + WEIGHTS["shape"] * shape(unit.text)
         + WEIGHTS["static"] * (counts.static(where, unit) if unit.line is not None else 0.0)
         + WEIGHTS["anchor"] * unit.anchor + WEIGHTS["warning"] * unit.warning)
    return _cap(unit, _sigmoid(z))


def _cap(unit: Unit, score: float) -> float:
    """A used unit scores 0; a unit with a case key at most 0.2; rounded to 3 places."""
    if unit.used:
        return 0.0
    if has_case(unit.text):
        score = min(score, CASE_CAP)
    return round(score, 3)


def _neighbours(found: list[Unit]) -> None:
    """Move a middling line toward the mean of its two screen neighbours, by at most 0.15."""
    lines = [u for u in found if u.line is not None]
    before = [u.score for u in lines]
    for i, unit in enumerate(lines):
        if not NEIGHBOUR[0] <= before[i] <= NEIGHBOUR[1]:
            continue
        near = [before[j] for j in (i - 1, i + 1) if 0 <= j < len(lines)]
        if near:
            target = sum(near) / len(near)
            moved = before[i] + max(-STEP, min(STEP, target - before[i]))
            unit.score = _cap(unit, moved)


def _weights(item: dict, found: list[Unit]) -> None:
    """Action noise, word weights (1 - score, highest per word) and used words."""
    body = [u for u in found if u.role != "title"] or found
    size = sum(len(u.text) for u in body)
    item["noise"] = round(sum(u.score * len(u.text) for u in body) / size, 3) if size else 0.0
    weights: dict[str, float] = {}
    for unit in found:
        for word in words(unit.text):
            weights[word] = max(1.0 - unit.score, weights.get(word, 0.0))
    item["noise_words"] = weights
    item["used_words"] = sorted({x for u in found if u.used for x in words(u.text)})


def score(items: list[dict]) -> dict[str, list[Unit]]:
    """Score every unit of every item; set noise, noise_words and used_words on the items."""
    found = {item["id"]: units(item) for item in items}
    counts = _Pass(items, found)
    for item in items:
        for unit in found[item["id"]]:
            unit.score = _score(unit, item, counts)
        _neighbours(found[item["id"]])
        _weights(item, found[item["id"]])
    return found


def _clear(found: list[Unit]) -> tuple[set, set]:
    """Case keys, number tokens and words of the task's clear content."""
    clear = [u for u in found if u.score < LOW]
    tokens = {t.casefold() for u in clear for t in NUMBER_TOKEN.findall(u.text)}
    return tokens, {x for u in clear for x in words(u.text)}


def _kept(unit: Unit, tokens: set, vocabulary: set) -> bool:
    """Keep-stage verdict for one unit (1.8)."""
    if unit.used or unit.warning or (len(unit.text.split()) <= 8 and STATUS.search(unit.text)):
        return True
    if unit.score < LOW:
        return True
    if unit.score >= HIGH:
        return False
    p = min(1 - 1e-6, max(1e-6, unit.score))
    own = set(words(unit.text))
    anchor = bool({t.casefold() for t in NUMBER_TOKEN.findall(unit.text)} & tokens)
    share = len(own & vocabulary) / len(own) if own else 0.0
    z = (KEEP_MODEL["bias"] + KEEP_MODEL["noise"] * math.log(p / (1 - p))
         + KEEP_MODEL["anchor"] * anchor + KEEP_MODEL["words"] * share)
    return _sigmoid(z) >= KEEP_MODEL["threshold"]


def keep(items: list[dict], found: dict[str, list[Unit]]) -> None:
    """Mark dropped line numbers on the items of one task (`dropped_lines`)."""
    every = [u for item in items for u in found.get(item["id"], ())]
    tokens, vocabulary = _clear(every)
    for item in items:
        item["dropped_lines"] = sorted(u.line for u in found.get(item["id"], ())
                                       if u.line is not None and not _kept(u, tokens, vocabulary))
