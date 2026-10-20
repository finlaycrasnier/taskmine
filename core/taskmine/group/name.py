"""Task names `Verb object in System` from fixed lists (grouper spec 5.3)."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from taskmine.group import words as w
from taskmine.group.anchors import ISSUE
from taskmine.group.common import host_path, order_key, seconds, title_of, untitled, web_page
from taskmine.group.steps import step_kind

PREFIXES = dict(w.CASE_PREFIX_OBJECTS)
PATTERNS = [(re.compile(rf"\b(?:{p})\b", re.IGNORECASE), obj) for p, obj in w.OBJECT_PATTERNS]
APPROVAL = re.compile(w.APPROVAL_LINE, re.IGNORECASE)
REPLY = re.compile(w.REPLY_LINE, re.IGNORECASE)
RECONCILE = re.compile(w.RECONCILE, re.IGNORECASE)
ORDER = [obj for _, obj in w.OBJECT_PATTERNS] + ["email", "document"]
CUTS = re.compile(r" (?:in|against|from|with|for) ")
UNSAFE = re.compile(r"\d|[@€$£¥\[\]]|https?:|www\.")
FIELDS = frozenset(w.FIELD_WORDS)
SYSTEM_CHARS = re.compile(r"^[A-Za-z][A-Za-z .&+'-]{0,38}[A-Za-z]$|^[A-Za-z]$")
MIN_STEP, MAX_STEP = 1.0, 120.0
NAME_MAX, SUMMARY_MAX = 240, 400


@dataclass
class Facts:
    """What a task did, from its kept lines only."""

    kinds: Counter = field(default_factory=Counter)
    clicked: list = field(default_factory=list)
    edits: int = 0
    fields: list = field(default_factory=list)
    typed: list = field(default_factory=list)
    titles: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    apps: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    mail: bool = False
    system: str = "an app"


@dataclass
class Name:
    """Parts and title of a task name."""

    verb: str
    object: str
    system: str
    title: str
    verb_sure: bool = False
    object_sure: bool = False


def _kept_lines(item: dict) -> list[str]:
    """Non-empty lines of an item that the keep stage did not drop."""
    dropped = set(item.get("dropped_lines") or ())
    return [ln.strip() for n, ln in enumerate((item.get("text") or "").split("\n"))
            if ln.strip() and n not in dropped]


def _place(item: dict) -> str:
    """Window key of 5.3 facts: window ref, document, title or app."""
    return item.get("window_ref") or item.get("document_id") or title_of(item) or item["app"]


def _typed(item: dict, kind: str | None, seen: dict, lines: list[str]) -> list[str]:
    """Lines counted as typed by this item."""
    place = _place(item)
    first = place not in seen
    known = seen.setdefault(place, set())
    fresh = [ln for ln in lines if ln not in known]
    known.update(lines)
    typing = kind in ("type", "paste", "create") or item.get("interaction") == "edit" \
        or item.get("grew") or item.get("edited")
    if not typing or (first and kind != "create" and not item.get("edited")):
        return []
    return fresh


def facts(actions: list[dict]) -> Facts:
    """Facts of a task from its readable, non-withheld actions in time order."""
    f, seen = Facts(), {}
    items = sorted((a for a in actions if not a.get("withheld") and not a.get("hidden")),
                   key=lambda a: (seconds(a["at"]), a["id"]))
    for item in items:
        kind, lines, title = step_kind(item), _kept_lines(item), title_of(item)
        if kind:
            f.kinds[kind] += 1
        if item.get("interaction") == "click" and item.get("focus_name"):
            f.clicked.append(item["focus_name"])
        if item.get("interaction") == "edit":
            f.edits += 1
            if item.get("focus_name") and (item.get("text") or "").strip():
                f.fields.append(item["focus_name"])
        f.typed += _typed(item, kind, seen, lines)
        if title and not untitled(title) and title not in f.titles:
            f.titles.append(title)
        place = title.casefold() if title and not web_page(item) else (
            host_path(item.get("url"))[0] if web_page(item) and not title else "")
        if place and place not in f.documents:
            f.documents.append(place)
        if item["app"] not in f.apps:
            f.apps.append(item["app"])
        f.lines += [ln for ln in lines if ln not in f.lines]
        host = host_path(item.get("url"))[0]
        f.mail = f.mail or item["app"].casefold() in w.MAIL_APPS or host in w.MAIL_HOSTS
    f.system = system(items)
    return f


def _site(host: str) -> str | None:
    """The registered domain's main label, capitalised, or None."""
    labels = [p for p in host.split(".") if p]
    if len(labels) < 2:
        return None
    main = labels[-3] if len(labels) >= 3 and len(labels[-2]) <= 3 and len(labels[-1]) == 2 \
        else labels[-2]
    if main in w.SITE_PREFIXES or not main.isalpha():
        return None
    return main[:1].upper() + main[1:]


def clean_system(label: str, app: str) -> str:
    """A safe display name for an app or site."""
    value = " ".join((label or "").split())
    if re.fullmatch(r"[a-z][\w-]*(?:\.[\w-]+){2,}", value):
        value = value.split(".")[-1]
    value = re.sub(r"^Microsoft ", "", value)
    if SYSTEM_CHARS.match(value):
        return value
    tail = (app or "").split(".")[-1]
    return tail if SYSTEM_CHARS.match(tail) else "an app"


def system(items: list[dict]) -> str:
    """The app or site with most of the task's time."""
    items = sorted(items, key=order_key)
    time: Counter = Counter()
    labels: dict[str, str] = {}
    for i, item in enumerate(items):
        if i + 1 < len(items):
            spent = seconds(items[i + 1]["at"]) - seconds(item["at"])
        else:
            spent = seconds(item["last_seen_at"]) - seconds(item["first_seen_at"])
        site = _site(host_path(item.get("url"))[0]) if web_page(item) and item.get("url") else None
        key = site or item.get("app_label") or item.get("app_name") or item["app"]
        labels[key] = item["app"]
        time[key] += min(MAX_STEP, max(MIN_STEP, spent))
    if not time:
        return "an app"
    best = max(time, key=lambda k: (time[k], k))
    return clean_system(best, labels[best])


def object_rule(f: Facts) -> tuple[str, bool]:
    """The task's object by weighted votes from titles, lines and mail (5.3)."""
    votes: Counter = Counter()

    def vote(text: str, prefix_weight: int, word_weight: int) -> None:
        for prefix, _ in ISSUE.findall(text):
            if prefix in PREFIXES:
                votes[PREFIXES[prefix]] += prefix_weight
        hit = next((obj for pattern, obj in PATTERNS if pattern.search(text)), None)
        if hit:
            votes[hit] += word_weight

    for title in f.titles:
        vote(title, 3, 2)
    for line in f.lines[:400]:
        vote(line, 1, 1)
    if f.mail:
        votes["email"] += 2
    if not votes:
        return "document", False
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], ORDER.index(kv[0])))
    top, weight = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0
    sure = weight != runner and weight >= 3 and (len(ranked) == 1 or weight >= 2 * runner)
    return top, sure


def _starts(labels: list[str], prefixes) -> bool:
    """True when any label starts with one of the prefixes."""
    return any(label.casefold().strip().startswith(p) for label in labels for p in prefixes)


def verb_rule(f: Facts, obj: str) -> tuple[str, bool]:
    """The task's verb by the first matching rule of 5.3."""
    if _starts(f.clicked, w.APPROVE_CLICKS):
        return "approve", True
    if _starts(f.clicked, w.REPLY_CLICKS):
        return "reply", True
    if f.kinds["send"]:
        if f.edits and obj not in w.REPLY_OBJECTS and not f.mail:
            return "enter", True
        return "reply", True
    if any(APPROVAL.search(line) for line in f.typed):
        return "approve", False
    if any(REPLY.search(line) for line in f.typed) and (obj in w.REPLY_OBJECTS or f.mail):
        return "reply", False
    if any(RECONCILE.search(t) for t in f.titles + f.lines[:200]):
        return "reconcile", False
    if f.kinds["create"]:
        return "create", bool(f.kinds["save"])
    if f.edits:
        fields = {x.casefold() for x in f.fields}
        return "enter", bool(f.kinds["save"] or f.kinds["send"] or len(fields) >= 2)
    if f.kinds["type"] or f.kinds["paste"]:
        return ("enter" if f.kinds["copy"] or f.kinds["paste"] else "update"), False
    if f.kinds["save"] or _starts(f.clicked, w.FILE_CLICKS):
        return "file", False
    if len(f.documents) >= 2:
        return "check", True
    return "review", False


def plural(obj: str) -> str:
    """The plural of a listed object."""
    return obj + "s"


def compose(verb: str, obj: str, system_name: str, count: int = 1) -> str:
    """`Verb object in System`, with ` · N objects` when it repeats."""
    noun = plural(obj) if count > 1 else obj
    head = f"Reply to {noun}" if verb == "reply" else f"{verb.capitalize()} {noun}"
    out = f"{head} in {system_name}"
    if count > 1:
        out += f" · {count} {plural(obj)}"
    return out[:NAME_MAX]


def name(f: Facts) -> Name:
    """The rule name of a task from its facts."""
    obj, obj_sure = object_rule(f)
    verb, verb_sure = verb_rule(f, obj)
    return Name(verb, obj, f.system, compose(verb, obj, f.system), verb_sure, obj_sure)


def parse(text: str) -> tuple[str, str] | None:
    """(verb, object) of a name, or None when it does not read as one."""
    value = " ".join((text or "").casefold().split())
    verb = next((v for v in w.VERBS if value == v or value.startswith(v + " ")), None)
    if verb is None:
        return None
    rest = CUTS.split(" " + value[len(verb):].strip() + " ", maxsplit=1)[0]
    best = None
    for obj in w.OBJECTS:
        for form in (obj, plural(obj)):
            for match in re.finditer(rf"\b{re.escape(form)}\b", rest):
                rank = (match.end(), len(form))
                if best is None or rank > best[0]:
                    best = (rank, obj)
    return (verb, best[1]) if best else None


def reusable(text: str, app_labels) -> bool:
    """True when a name holds no digits, money, links or unknown capitalised words."""
    if not text or len(text) > 120 or UNSAFE.search(text):
        return False
    allowed = set(w.VERBS) | set(w.CONNECTORS)
    for obj in w.OBJECTS:
        allowed |= set(obj.split()) | set(plural(obj).split())
    for label in app_labels or ():
        allowed |= {part.casefold() for part in label.split()}
    for word in text.split()[1:]:
        bare = word.strip(".,;:!?()\"'")
        if bare[:1].isupper() and bare.casefold() not in allowed:
            return False
    return True


def _field_label(label: str) -> bool:
    """A form label of 1 to 3 words, all in the allow-list."""
    parts = label.casefold().split()
    return 1 <= len(parts) <= 3 and all(p.strip(":") in FIELDS for p in parts)


def summary(f: Facts) -> str:
    """Rule summary: where, what was done and which fields."""
    where = f"{len(f.documents)} documents in {f.system}" if len(f.documents) > 1 else f.system
    done = [text for kind, text in w.SUMMARY_WORDS if f.kinds[kind]][:5]
    out = f"Worked in {where}" + (": " + ", ".join(done) if done else "") + "."
    labels = list(dict.fromkeys(x.strip() for x in f.fields if _field_label(x)))[:6]
    if labels:
        out += " Fields: " + ", ".join(labels) + "."
    return out[:SUMMARY_MAX]
