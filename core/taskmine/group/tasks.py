"""Task placement at pass time (grouper spec 2.4) and the possibly-personal mark (1.8)."""
from __future__ import annotations

import re

from taskmine.group import words as w
from taskmine.group.anchors import different_cases
from taskmine.group.common import EXTENSION, digest, host_path, iso, seconds, title_of

JOINABLE = frozenset({"active", "suspended", "unconfirmed"})
ARTIFACT_LIFECYCLES = frozenset({"active", "unconfirmed"})
ARTIFACT_JOIN, WINDOW_GAP, WINDOW_SPAN = 7200.0, 120.0, 900.0
TITLE_SPLIT = re.compile(r"\s[-–—|·]\s")
SITE_EXTRAS = ("applemusic", "steam", "chesscom", "epicgames")


def task_id(first_action_id: str) -> str:
    """A task id from its earliest action id."""
    return digest("task", first_action_id)


def _pairs(value) -> set[tuple[str, str]]:
    """Case keys as a set of pairs."""
    return {tuple(p) for p in value or ()}


def _record(row: dict) -> dict:
    """A working copy of a task row."""
    out = dict(row)
    out["case_keys"] = sorted(list(p) for p in _pairs(row.get("case_keys")))
    out["artifact_keys"] = sorted(set(row.get("artifact_keys") or ()))
    out["window_keys"] = {k: list(v) for k, v in (row.get("window_keys") or {}).items()}
    out.setdefault("lifecycle", "active")
    out.setdefault("locked", 0)
    return out


def _by_case(ep: dict, tasks: list[dict]) -> dict | None:
    """Rule 1: exactly one joinable task carries one of the episode's explicit cases."""
    cases = _pairs(ep["cases"])
    namespaces = [n for n, _ in cases]
    if not cases or len(namespaces) != len(set(namespaces)):
        return None
    found = [t for t in tasks if t["lifecycle"] in JOINABLE and _pairs(t["case_keys"]) & cases]
    if len(found) == 1 and not different_cases(found[0]["case_keys"], cases):
        return found[0]
    return None


def _by_artifact(ep: dict, tasks: list[dict]) -> dict | None:
    """Rule 2: same artifact, unlocked, active, seen within 7200 s, no different case."""
    found = [t for t in tasks if set(t["artifact_keys"]) & ep["artifacts"] and not t["locked"]
             and t["lifecycle"] in ARTIFACT_LIFECYCLES
             and ep["start"] - seconds(t["last_seen_at"]) <= ARTIFACT_JOIN
             and not different_cases(t["case_keys"], ep["cases"])]
    return max(found, key=lambda t: (t["last_seen_at"], t["id"])) if found else None


def _window_ok(ep: dict, task: dict) -> bool:
    """Rule 3 limits: anchored, unlocked, joinable task without a conflicting case or document."""
    if task["locked"] or task["lifecycle"] not in JOINABLE:
        return False
    if not task["case_keys"] and not task["artifact_keys"]:
        return False
    if different_cases(task["case_keys"], ep["cases"]):
        return False
    shared_case = bool(_pairs(task["case_keys"]) & _pairs(ep["cases"]))
    arts = set(task["artifact_keys"])
    return shared_case or not (arts and ep["artifacts"] and not arts & ep["artifacts"])


def _by_window(ep: dict, tasks: list[dict]) -> dict | None:
    """Rule 3: the window's previous action is in a task within 120 s and a 900 s span."""
    best = None
    for window, (first_at, first_seen) in ep["windows"].items():
        for task in tasks:
            seen = task["window_keys"].get(window)
            if not seen or seen[0] > first_at or not _window_ok(ep, task):
                continue
            near = first_seen - seen[1] <= WINDOW_GAP
            short = first_at - seconds(task["started_at"]) <= WINDOW_SPAN
            if near and short and (best is None or seen[1] > best[0]):
                best = (seen[1], task)
    return best[1] if best else None


def _absorb(task: dict, ep: dict) -> None:
    """Add an episode's anchors and times to a task."""
    task["case_keys"] = sorted(list(p) for p in _pairs(task["case_keys"]) | _pairs(ep["cases"]))
    task["artifact_keys"] = sorted(set(task["artifact_keys"]) | ep["artifacts"])
    for window, span in ep["window_span"].items():
        old = task["window_keys"].get(window)
        task["window_keys"][window] = [min(span[0], old[0]) if old else span[0],
                                       max(span[3], old[1]) if old else span[3]]
    task["started_at"] = min(task["started_at"], iso(ep["start"]), key=seconds)
    task["last_seen_at"] = max(task["last_seen_at"], iso(ep["end"]), key=seconds)


def join_tasks(episodes: list[dict], tasks: list[dict]) -> tuple[dict[str, str], list[dict]]:
    """Episode id -> task id, first rule that matches; and the task records, new ones added."""
    records = [_record(t) for t in tasks]
    placed = {}
    for ep in sorted(episodes, key=lambda e: (e["start"], e["id"])):
        task = ep.get("task_id") and next((t for t in records if t["id"] == ep["task_id"]), None)
        task = task or _by_case(ep, records) or _by_artifact(ep, records) or _by_window(ep, records)
        if task is None:
            task = {"id": task_id(ep["first_id"]), "lifecycle": "active", "locked": 0,
                    "case_keys": [], "artifact_keys": [], "window_keys": {},
                    "started_at": iso(ep["start"]), "last_seen_at": iso(ep["end"]), "new": True}
            existing = next((t for t in records if t["id"] == task["id"]), None)
            task = existing or task
            if existing is None:
                records.append(task)
        _absorb(task, ep)
        placed[ep["id"]] = task["id"]
    return placed, records


def anchors_of(actions: list[dict]) -> dict:
    """Cases, artifacts and per-window times of a set of actions."""
    cases, artifacts, spans = set(), set(), {}
    for a in actions:
        cases |= _pairs(a.get("case_keys"))
        if a.get("artifact_key"):
            artifacts.add(a["artifact_key"])
        if a.get("window_key"):
            at, first, last = seconds(a["at"]), seconds(a["first_seen_at"]), seconds(a["last_seen_at"])
            old = spans.get(a["window_key"])
            spans[a["window_key"]] = (min(at, old[0]), min(first, old[1]), max(at, old[2]),
                                      max(last, old[3])) if old else (at, first, at, last)
    return {"cases": sorted(cases), "artifacts": artifacts, "window_span": spans,
            "windows": {k: (v[0], v[1]) for k, v in spans.items()}}


def _site_labels() -> set[str]:
    """Letters-only names of the personal sites, as a title would show them."""
    out = set(SITE_EXTRAS)
    for _, hosts in w.PERSONAL_SITES:
        out |= {h.split(".")[0] for h in hosts if h.count(".") == 1}
    return out


SITES = _site_labels()


def _personal_host(host: str) -> bool:
    """True when a host is one of the personal sites or a subdomain of one."""
    for _, hosts in w.PERSONAL_SITES:
        for pattern in hosts:
            if pattern.endswith(".*"):
                stem = pattern[:-2]
                labels = host.split(".")
                if stem in labels[:-1]:
                    return True
            elif host == pattern or host.endswith("." + pattern):
                return True
    return False


def _personal_title(title: str) -> bool:
    """True when the last part of a title names a personal site."""
    parts = TITLE_SPLIT.split(title)
    if len(parts) < 2:
        return False
    last = re.sub(r"[^a-z]", "", EXTENSION.sub("", parts[-1].strip()).casefold())
    return last in SITES


def possibly_personal(actions: list[dict]) -> bool:
    """Every readable action shows a personal site and none carries a case."""
    readable = [a for a in actions if not a.get("withheld") and not a.get("hidden")]
    if not readable or any(a.get("case_keys") for a in readable):
        return False
    return all(_personal_host(host_path(a.get("url"))[0]) or _personal_title(title_of(a))
               for a in readable)
