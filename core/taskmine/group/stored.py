"""A pass over the store (grouper spec 4.9): read unplaced actions, resume, write the result."""
from __future__ import annotations

import os
from pathlib import Path

from taskmine.contracts import anchor_key
from taskmine.group.anchors import extract
from taskmine.group.common import iso, seconds, title_of
from taskmine.group.matching import stored_member
from taskmine.group.run import run_batch, to_seconds
from taskmine.group.tasks import anchors_of
from taskmine.store import PassResult

REOPEN = 600.0
ARTIFACT_JOIN = 7200.0
RECENT_TASKS = 50
MATCH_WINDOW = 200
TASK_ACTIONS = 3000


def salt_of(store) -> bytes:
    """The anchor key beside the database when present, else a salt kept in settings."""
    path = getattr(store, "path", None)
    if path and (Path(path).parent / "anchor.key").is_file():
        return anchor_key(Path(path).parent)
    value = store.setting("salt")
    if not value:
        value = os.urandom(16).hex()
        store.set_setting("salt", value)
    return bytes.fromhex(value)


def to_action(row: dict, salt: bytes) -> dict:
    """A stored event row as a grouper action, with anchors under this salt."""
    meta = row.get("metadata") or {}
    action = {
        "id": row["event_id"], "at": row["observed_at"], "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"], "app": row["app"], "app_name": row.get("app_name"),
        "interaction": row.get("interaction"), "title": row.get("title"), "text": row.get("text") or "",
        "url": row.get("url"), "document_id": row.get("document_id"), "window_ref": row.get("window_ref"),
        "browser": bool(meta.get("browser_domain")),
        "withheld": row.get("kind") == "capture_gap" or meta.get("capture_method") == "withheld",
        "sightings": int(row.get("sightings") or 1), "duration_s": int(row.get("duration_s") or 0),
        "focus_name": meta.get("focus_name"), "shortcut": meta.get("shortcut"),
        "furniture_lines": meta.get("furniture") or [], "case_ref": meta.get("case_ref"),
        "external_ok": not meta.get("private_browsing"), "episode_id": row.get("episode_id"),
    }
    found = extract(action, salt)
    action.update(case_keys=[list(p) for p in found.case_keys], artifact_key=found.artifact_key,
                  surface_key=found.surface_key, window_key=found.window_key)
    return action


def _reopen(store, actions: list[dict], salt: bytes) -> tuple[list[dict], dict | None]:
    """Load back a quiet or diversion episode the first new action resumes within 600 s."""
    if not actions:
        return [], None
    first = min(seconds(a["at"]) for a in actions)
    old = store.reopenable_episode(iso(first))
    if not old or first - seconds(old["last_action_at"]) > REOPEN:
        return [], None
    _, rows, _ = store.task_actions(old["task_id"], limit=TASK_ACTIONS)
    loaded = [to_action(r, salt) for r in rows if r.get("episode_id") == old["id"]]
    reopened = {"id": old["id"], "task_id": old["task_id"], "revision": old.get("revision", 1),
                "action_ids": [a["id"] for a in loaded]}
    return loaded, reopened


def _recent_tasks(store, since: float, salt: bytes, keep: str | None) -> list[dict]:
    """Tasks seen within the artifact window, with the anchors of their actions."""
    rows, _ = store.tasks(limit=RECENT_TASKS)
    out = []
    for task in rows:
        if seconds(task["last_seen_at"]) < since - ARTIFACT_JOIN and task["id"] != keep:
            continue
        _, actions, _ = store.task_actions(task["id"], limit=TASK_ACTIONS)
        found = anchors_of([to_action(r, salt) for r in actions])
        out.append({**task, "artifact_keys": sorted(found["artifacts"]),
                    "window_keys": {k: [v[0], v[3]] for k, v in found["window_span"].items()}})
    return out


def _stored_episodes(store) -> list[dict]:
    """The match window: recent stored episodes with artifact titles re-read from evidence."""
    rows = store.recent_episodes(limit=MATCH_WINDOW)
    refs = {a.get("ref") for r in rows for a in (r["signature"] or {}).get("artifacts", [])}
    found = store.actions_by_id(r for r in refs if r)
    titles = {i: title_of({"title": a.get("title"), "app_name": a.get("app_name")})
              for i, a in found.items()}
    processes = {p["id"]: p for p in store.processes()}
    return [stored_member({**r, "process": processes.get(r.get("process_id"))}, titles)
            for r in rows]


def run(store, asker, model, now, *, force: bool = False, notes: str = "") -> PassResult:
    """One pass over the store's unplaced actions; the result is written and returned."""
    stamp = iso(to_seconds(now))
    salt = salt_of(store)
    actions = [to_action(r, salt) for r in store.unplaced_actions()]
    if not actions:
        store.set_setting("last_pass_at", stamp)
        return PassResult(settled=bool(force))
    loaded, reopened = _reopen(store, actions, salt)
    first = min(seconds(a["at"]) for a in actions)
    existing = _recent_tasks(store, first, salt, reopened and reopened["task_id"])
    open_count = len(store.questions(limit=3, now=stamp))
    result = run_batch(loaded + actions, asker, model, stamp, force=force, salt=salt, notes=notes,
                       existing=existing, reopened=reopened, stored=_stored_episodes(store),
                       names=store.names(), open_questions=open_count)
    store.write_pass(result)
    store.set_setting("last_pass_at", stamp)
    return result
