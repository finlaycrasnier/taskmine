"""Step kinds and episode signatures (grouper spec 5.1, 5.2)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from taskmine.group.common import ENGINE_SPLIT, host_path, seconds, title_of, untitled, web_page

SHORTCUTS = {
    "cmd+s": "save", "cmd+shift+s": "save", "cmd+opt+s": "save", "cmd+n": "create",
    "cmd+w": "close", "cmd+c": "copy", "cmd+x": "copy", "cmd+v": "paste", "cmd+shift+v": "paste",
    "cmd+opt+shift+v": "paste", "cmd+t": "browse", "cmd+l": "browse", "cmd+return": "send",
    "cmd+enter": "send", "cmd+shift+d": "send",
}
CLICKS = {"save": "save", "send": "send", "submit": "send", "create": "create", "new": "create",
          "close": "close", "done": "close", "search": "search", "copy": "copy", "paste": "paste"}
NOT_STEPS = frozenset({"scroll", "focus", "window_focus", "element_focus"})
PRESSED = re.compile(r"Pressed\s+((?:(?:Cmd|Command|Ctrl|Control|Opt|Option|Alt|Shift)\+)+\S+)",
                     re.IGNORECASE)
SEARCH_SEGMENTS = frozenset({"search", "results", "find"})
SEARCH_WORD = re.compile(r"\bsearch\b", re.IGNORECASE)
LOW_SIGNAL = frozenset({"view", "click"})
DOCUMENT_WORK = frozenset({"create", "type", "paste", "save"})
OUTPUT = frozenset({"save", "send"})
EVIDENCE_MAX = 20


@dataclass
class Signature:
    """Steps, apps and artifacts of one episode."""

    steps: list = field(default_factory=list)
    apps: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)


def shortcut(item: dict) -> str | None:
    """The normalised shortcut of an action, from metadata or its text."""
    value = item.get("shortcut")
    if not value and item.get("interaction"):
        found = PRESSED.search(item.get("text") or "")
        value = found.group(1) if found else None
    if not value:
        return None
    value = value.lower().replace("command", "cmd").replace("option", "opt").replace(" ", "")
    return value.replace("control", "ctrl").replace("alt+", "opt+")


def search_page(item: dict) -> bool:
    """A URL path segment search, results or find, or a title whose last part says search."""
    _, path = host_path(item.get("url"))
    if SEARCH_SEGMENTS & {s.casefold() for s in path.split("/") if s}:
        return True
    parts = ENGINE_SPLIT.split(title_of(item))
    return len(parts) >= 2 and bool(SEARCH_WORD.search(parts[-1]))


def _click(item: dict) -> str:
    """The kind a clicked label names, else click."""
    label = (item.get("focus_name") or "").casefold().split()
    first = label[0].strip(".…") if label else ""
    return CLICKS.get(first, "click")


def step_kind(item: dict) -> str | None:
    """The step kind of one action, or None when it is not a step (5.1)."""
    keys = shortcut(item)
    interaction = item.get("interaction")
    if keys:
        return SHORTCUTS.get(keys)
    if interaction == "shortcut" or interaction in NOT_STEPS:
        return None
    if interaction == "edit":
        return "search" if web_page(item) and search_page(item) else "type"
    if interaction == "click":
        return _click(item)
    if web_page(item):
        return "search" if search_page(item) else "browse"
    if item.get("grew"):
        return "type"
    return "create" if untitled(title_of(item)) else "view"


def _recorded(session) -> list[tuple[str, dict]]:
    """Kinds of one session's items, with the create-after-typing and edited rules."""
    out, started = [], False
    for item in session.items:
        kind = step_kind(item)
        if kind == "create" and started:
            continue
        started = started or kind in DOCUMENT_WORK
        if kind:
            out.append((kind, item))
            if kind == "create" and item.get("edited"):
                out.append(("type", item))
    kept = [pair for pair in out if pair[0] not in LOW_SIGNAL]
    return kept or out[:1]


def _document(session) -> str:
    """The named document of a session, or empty for pages and unsaved documents."""
    if session.browser or not session.title or untitled(session.title):
        return ""
    return session.title.casefold()


def _steps(sessions) -> list[dict]:
    """Merged steps of 5.2 over the sessions in order."""
    steps: list[dict] = []
    for session in sessions:
        document = _document(session)
        for kind, item in _recorded(session):
            last = steps[-1] if steps else None
            if (last and last["kind"] == kind and last["app"] == item["app"]
                    and (not document or not last["document"] or document == last["document"])):
                if len(last["evidence"]) < EVIDENCE_MAX:
                    last["evidence"].append(item["id"])
                continue
            steps.append({"kind": kind, "app": item["app"], "label": session.app_label,
                          "evidence": [item["id"]], "session": session.index,
                          "document": document})
    return steps


def _artifacts(sessions, steps: list[dict]) -> list[dict]:
    """Named artifacts per session, with output marks."""
    out, seen = [], set()
    for session in sessions:
        if not session.title or untitled(session.title):
            continue
        key = (session.app, session.title.casefold())
        if key in seen:
            continue
        seen.add(key)
        same = [i for i in session.items if title_of(i) == session.title]
        ref = (same or session.items)[-1]["id"]
        out.append({"app": session.app, "label": session.app_label, "title": session.title,
                    "ref": ref, "output": False, "session": session.index,
                    "start": session.first_at})
    _mark_outputs(sessions, steps, out)
    return out


def _mark_outputs(sessions, steps: list[dict], artifacts: list[dict]) -> None:
    """Output: shown after the first save in an app that saved; else saved in its own session."""
    saving = {s["app"] for s in steps if s["kind"] in OUTPUT}
    first = min((seconds(i["at"]) for s in sessions for i in s.items if step_kind(i) in OUTPUT),
                default=None)
    for art in artifacts:
        art["output"] = first is not None and art["start"] > first and art["app"] in saving
    if not any(a["output"] for a in artifacts):
        own = {s["session"] for s in steps if s["kind"] in OUTPUT}
        for art in artifacts:
            art["output"] = art["session"] in own


def signature(episode) -> Signature:
    """Steps, apps and artifacts of an episode (5.2)."""
    sessions = sorted(episode.sessions, key=lambda s: s.index)
    steps = _steps(sessions)
    apps = list(dict.fromkeys(s.app for s in sessions))
    artifacts = _artifacts(sessions, steps)
    for art in artifacts:
        del art["start"]
    return Signature(steps, apps, artifacts)


def public(sig: Signature) -> dict:
    """The stored form: no titles, sessions relative to the first one."""
    base = min((s["session"] for s in sig.steps), default=0)
    base = min([base] + [a["session"] for a in sig.artifacts])
    steps = [{"kind": s["kind"], "app": s["app"], "label": s["label"],
              "evidence": list(s["evidence"]), "session": s["session"] - base} for s in sig.steps]
    artifacts = [{"app": a["app"], "label": a["label"], "ref": a["ref"], "output": a["output"],
                  "session": a["session"] - base} for a in sig.artifacts]
    return {"steps": steps, "apps": list(sig.apps), "artifacts": artifacts}


def text(sig: Signature, templates: list | None = None) -> str:
    """Signature text `kind(App[ template])` joined by arrows."""
    parts = []
    for i, step in enumerate(sig.steps):
        template = templates[i] if templates and i < len(templates) else None
        parts.append(f"{step['kind']}({step['label']}{' ' + template if template else ''})")
    return " → ".join(parts)
