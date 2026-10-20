"""Helpers for the group tests: the old-shape converter, asker and model stubs, result readers.

The replay files in data/replay use older shapes (`evidence[]` with `content.metadata`, flat rows
with `app_bundle_id`, synthetic rows with `t_offset_s`, layer-1 steps with `at_seconds`).
`to_event` maps any of them to the taskmine.event/1 shape of docs/CONTRACTS.md section 2, and
`to_action` maps that event to the flat action the grouper consumes (grouper spec 1.5).
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

REPLAY = Path(__file__).resolve().parents[2] / "data" / "replay"
SALT = b"test-salt"
T0 = "2026-10-20T09:00:00Z"
ACTION_KINDS = {"click", "edit", "shortcut", "scroll", "focus", "window_focus", "element_focus",
                "navigation", "content_change"}
PROVENANCES = {"native_accessibility", "native_interaction", "screen_ocr", "app_reader"}
SOURCES = {"accessibility": "native_accessibility", "interaction": "native_interaction"}


def iso(base: str, seconds: float = 0) -> str:
    """`base` (ISO 8601 Z) plus `seconds`, as ISO 8601 Z."""
    when = datetime.fromisoformat(base) + timedelta(seconds=seconds)
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def furniture(lines) -> list[int]:
    """`"3-25"` (inclusive range), a list of line numbers, or nothing."""
    if isinstance(lines, str):
        low, high = lines.split("-")
        return list(range(int(low), int(high) + 1))
    return sorted(int(n) for n in lines or [])


# ---------------------------------------------------------------- converter (old shape -> event -> action)

def to_event(old: dict, source_id: str = "mac-replay") -> dict:
    f = {**old, **old.get("content", {}), **old.get("content", {}).get("metadata", {})}
    seen = f.get("observed_at") or iso(T0, f.get("t_offset_s", f.get("at_seconds", 0)))
    url = f.get("url") or f.get("document_url") or None
    url = url and urlsplit(url)._replace(query="", fragment="").geturl()  # scheme, host, path (1.1)
    act = f.get("interaction_type") or f.get("interaction")
    act = act if act in ACTION_KINDS else None  # accessibility_event, interval, screen_text: snapshot (1.2)
    gap = f.get("type") == "capture_gap" or f.get("capture_method") == "withheld"
    web = f.get("source") == "browser" or f.get("source_type") == "browser"
    text = f.get("text") or ""
    meta = {"app_bundle_id": f.get("app_bundle_id") or f.get("app"), "app_name": f.get("app_name"),
            "window_title": f.get("window_title", f.get("title")), "window_ref": f.get("window_ref"),
            "document_id": f.get("document_id"), "url": url,
            "browser_domain": f.get("browser_domain") or (urlsplit(url).hostname if web and url else None),
            "interaction_type": act, "focus_name": f.get("focus_name"), "shortcut": f.get("shortcut"),
            "sighting_count": int(f.get("sighting_count") or 1), "duration_s": int(f.get("duration_s") or 0),
            "gap_reason": f.get("gap_reason"), "capture_method": f.get("capture_method"),
            "case_ref": f.get("case_ref"), "furniture": furniture(f.get("furniture_lines"))}
    prov = f.get("provenance") if f.get("provenance") in PROVENANCES else SOURCES.get(f.get("source"), "synthetic")
    return {"schema": "taskmine.event/1", "event_id": f.get("event_id") or f["id"],
            "revision": f.get("revision", 1), "source_id": source_id, "observed_at": seen,
            "first_seen_at": f.get("first_seen_at") or seen,
            "last_seen_at": f.get("last_seen_at") or iso(seen, f.get("last_seen_offset_s", 0)),
            "provenance": prov,
            "content": {"type": "capture_gap" if gap else "interaction" if act else "screen_text",
                        "text": text, "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "metadata": {k: v for k, v in meta.items() if v not in (None, "", [])}}}


def to_action(event: dict) -> dict:
    m = event["content"]["metadata"]
    return {"id": event["event_id"], "at": event["observed_at"], "first_seen_at": event["first_seen_at"],
            "last_seen_at": event["last_seen_at"], "app": m["app_bundle_id"], "app_name": m.get("app_name"),
            "interaction": m.get("interaction_type"), "title": m.get("window_title"),
            "text": event["content"]["text"], "url": m.get("url"), "document_id": m.get("document_id"),
            "window_ref": m.get("window_ref"), "browser": bool(m.get("browser_domain")),
            "withheld": event["content"]["type"] == "capture_gap", "sightings": m["sighting_count"],
            "duration_s": m["duration_s"], "focus_name": m.get("focus_name"), "shortcut": m.get("shortcut"),
            "furniture_lines": m.get("furniture", []), "case_ref": m.get("case_ref"), "external_ok": True}


def anchored(action: dict, salt: bytes = SALT) -> dict:
    """Add the four anchor fields the store keeps beside each event (CONTRACTS section 3)."""
    from taskmine.group.anchors import extract

    found = extract(action, salt)
    action.update(case_keys=[tuple(pair) for pair in found.case_keys], artifact_key=found.artifact_key,
                  surface_key=found.surface_key, window_key=found.window_key)
    return action


def load(name: str) -> dict | list:
    return json.loads((REPLAY / name).read_text())


def replay_actions(name: str, variant: str | None = None) -> list[dict]:
    """Anchored actions of one replay file, in file order."""
    data = load(name)
    if name == "three_tasks.json":
        rows = data["scenarios"][variant or "three_tasks"]["events"]
    elif name == "three_companies.json":
        rows = [e for e in data["events"] if variant == "plus_meeting" or not e["id"].startswith("meet-")]
    else:
        rows = data["evidence"]
    return [anchored(to_action(to_event(row))) for row in rows]


def case_actions(steps: list[dict]) -> list[dict]:
    """Anchored actions from the `observe` steps of a native_window_cases case."""
    return [anchored(to_action(to_event(s))) for s in steps if s["op"] == "observe"]


# ---------------------------------------------------------------- asker and model stubs

class NeverAsk:
    """An asker that never asks the person."""

    def should_ask(self, *args, **kwargs) -> bool:
        return False

    def reward(self, *args, **kwargs) -> None:
        return None


def silent_model():
    """A decision model that answers nothing."""
    from taskmine.model import FakeModel

    return FakeModel({})


def run_replay(actions: list[dict], *, existing: list[dict] | None = None, force: bool = True,
               now: str | None = None, model=None, asker=None, notes: str = ""):
    """One in-memory pass over `actions`, 600 s after the last of them."""
    from taskmine.group import run_batch

    now = now or iso(max(a["last_seen_at"] for a in actions), 600)
    return run_batch(copy.deepcopy(actions), asker or NeverAsk(), model or silent_model(), now,
                     force=force, salt=SALT, notes=notes, existing=existing)


# ---------------------------------------------------------------- result readers

def row(item) -> dict:
    """A PassResult entry as a dict, whether it is a dict, a dataclass or a plain object."""
    if isinstance(item, dict):
        return item
    if dataclasses.is_dataclass(item):
        return dataclasses.asdict(item)
    return dict(vars(item))


def rows(items) -> list[dict]:
    return [row(item) for item in items or []]


def parsed(value):
    """A JSON column as a Python value; values already decoded pass through."""
    if isinstance(value, str) and value[:1] in "[{":
        return json.loads(value)
    return value


def field(item, name: str):
    """Read `name` from a dict or an object."""
    return item[name] if isinstance(item, dict) else getattr(item, name)


def members(result) -> dict[str, set[str]]:
    """Episode id -> the event ids placed in it."""
    out: dict[str, set[str]] = {}
    for event_id, episode_id in result.placements.items():
        out.setdefault(episode_id, set()).add(event_id)
    return out


def task_groups(result) -> dict[str, set[str]]:
    """Task id -> the event ids of its episodes."""
    task_of = {e["id"]: e["task_id"] for e in rows(result.episodes)}
    out: dict[str, set[str]] = {}
    for event_id, episode_id in result.placements.items():
        out.setdefault(task_of[episode_id], set()).add(event_id)
    return out
