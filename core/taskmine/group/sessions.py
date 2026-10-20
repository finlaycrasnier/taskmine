"""Sessions (grouper spec 3): one stretch of work in one app on one surface."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from taskmine.group.common import (
    host_path,
    is_action,
    order_key,
    seconds,
    title_of,
    untitled,
    web_page,
)

SESSION_GAP = 120.0
TRIVIAL_ACTIONS, TRIVIAL_SECONDS, TRIVIAL_CHARS = 3, 20.0, 200
MARKER = re.compile(r"^(?:\s*⟨[^⟩]*⟩\s*)+$")
EDITED = re.compile(r"[—–-]\s*edited\b", re.IGNORECASE)


@dataclass(eq=False)
class Session:
    """One stretch of work in one app on one surface."""

    index: int
    key: str
    app: str
    app_label: str
    items: list = field(default_factory=list)
    first_at: float = 0.0
    last_at: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0
    duration: float = 0.0
    chars: int = 0
    edits: int = 0
    title: str = ""
    identity: str = ""

    def add(self, item: dict) -> None:
        """Take one item and update the times."""
        at = seconds(item["at"])
        first, last = seconds(item["first_seen_at"]), seconds(item["last_seen_at"])
        if not self.items:
            self.first_at, self.last_at, self.first_seen, self.last_seen = at, at, first, last
        self.items.append(item)
        self.first_at, self.last_at = min(self.first_at, at), max(self.last_at, at)
        self.first_seen, self.last_seen = min(self.first_seen, first), max(self.last_seen, last)

    def finish(self) -> None:
        """Fill the derived fields."""
        readable = [i for i in self.items if not i.get("unreadable")]
        self.duration = max(0.0, self.last_seen - self.first_seen)
        self.chars = chars(readable)
        self.edits = sum(1 for i in readable if i.get("interaction") in ("edit", "shortcut"))
        titles = [t for t in (title_of(i) for i in self.items) if t]
        self.title = titles[-1] if titles else ""
        self.identity = self.key + "|" + self.title.casefold()

    @property
    def browser(self) -> bool:
        """True when any item is a web page."""
        return any(web_page(i) for i in self.items)


def chars(items: list[dict]) -> int:
    """Longest snapshot text plus the distinct focus names."""
    longest = max((len(i.get("text") or "") for i in items if not is_action(i)), default=0)
    names = {i.get("focus_name") for i in items if i.get("focus_name")}
    return longest + sum(len(n) for n in names)


def surface(item: dict) -> str:
    """The surface key of 3.1, or an empty string."""
    title = title_of(item)
    if item.get("document_id") and not untitled(title):
        return "doc:" + item["document_id"]
    if item.get("url"):
        host, path = host_path(item["url"])
        return "url:" + host + path
    if untitled(title) and item.get("window_ref"):
        return "new:" + item["window_ref"]
    return "title:" + title.casefold() if title else ""


def app_labels(items: list[dict]) -> dict[str, str]:
    """The most frequent display name of each bundle id, else the bundle id."""
    counts: dict[str, Counter] = {}
    for item in items:
        counts.setdefault(item["app"], Counter())
        if item.get("app_name"):
            counts[item["app"]][item["app_name"]] += 1
    return {app: (c.most_common(1)[0][0] if c else app) for app, c in counts.items()}


def _lines(item: dict, title: str) -> set[str]:
    """Content lines of a snapshot: not empty, not a marker, not the title, with a letter."""
    out = set()
    for line in (item.get("text") or "").split("\n"):
        line = line.strip()
        if line and line != title and not MARKER.match(line) and any(c.isalpha() for c in line):
            out.add(line)
    return out


def mark_growth(items: list[dict]) -> None:
    """Set `grew` and `edited` on snapshots of native windows (4.2), unless already set."""
    snaps = [i for i in sorted(items, key=order_key)
             if not is_action(i) and not web_page(i) and i.get("window_ref")]
    lines = {id(i): _lines(i, title_of(i)) for i in snaps}
    windows: dict[tuple, set] = {}
    for item in snaps:
        for line in lines[id(item)]:
            windows.setdefault((item["app"], line), set()).add(item["window_ref"])
    seen: dict[tuple, set] = {}
    for item in snaps:
        place = (item["app"], item["window_ref"])
        own = {ln for ln in lines[id(item)] if len(windows[(item["app"], ln)]) == 1}
        first = place not in seen
        known = seen.setdefault(place, set())
        item.setdefault("grew", bool(not first and own - known))
        if first:
            shown = (item.get("title") or "") + "\n" + (item.get("text") or "")
            marked = EDITED.search(shown) or "Edited" in shown.split("\n")
            item.setdefault("edited", bool(untitled(title_of(item)) and marked))
        known |= own


def sessions(actions: list[dict]) -> list[Session]:
    """Sessions in time order; withheld gaps never form or split one."""
    work = [a for a in actions if not a.get("withheld")] or list(actions)
    labels = app_labels(work)
    for item in work:
        item.setdefault("app_label", labels[item["app"]])
    mark_growth(work)
    out: list[Session] = []
    for item in sorted(work, key=order_key):
        key = surface(item)
        full = f"{item['app']}|{key}" if key else None
        current = out[-1] if out else None
        if current and seconds(item["first_seen_at"]) - current.last_seen <= SESSION_GAP and (
                full == current.key or (full is None and item["app"] == current.app)):
            current.add(item)
            continue
        session = Session(len(out), full or f"{item['app']}|unknown", item["app"],
                          labels[item["app"]])
        session.add(item)
        out.append(session)
    for session in out:
        session.finish()
    return out


def trivial(value) -> bool:
    """Fewer than 3 actions, under 20 s, no edit or shortcut and under 200 characters (3.2)."""
    if hasattr(value, "sessions"):
        items = [i for s in value.sessions for i in s.items]
    elif hasattr(value, "items") and not isinstance(value, dict | list):
        items = list(value.items)
    else:
        items = list(value)
    readable = [i for i in items if not i.get("unreadable") and not i.get("withheld")]
    if not readable:
        return False
    span = (max(seconds(i["last_seen_at"]) for i in readable)
            - min(seconds(i["first_seen_at"]) for i in readable))
    return (len(readable) < TRIVIAL_ACTIONS and span < TRIVIAL_SECONDS and chars(readable) < TRIVIAL_CHARS
            and not any(i.get("interaction") in ("edit", "shortcut") for i in readable))
