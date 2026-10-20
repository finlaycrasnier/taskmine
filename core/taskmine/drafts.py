"""Local write-ups of one task (review-page-spec 1.12.4). They never leave this machine.

A draft holds the six workflow fields the person edits and a proposal the page shows beside
them. One draft per task revision: the id is a hash of the task id and its revision.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from taskmine import check
from taskmine.store import NotFound, StaleRevision

FIELDS = ("outcome", "steps", "inputs", "exceptions", "completion", "owner")
FIELD_MAX = {"outcome": 240, "steps": 11_500}
OTHER_MAX = 4_000
INTRO = ("These observed steps need review. Confirm the task boundaries and add any missing "
         "steps.")
STEPS_MAX = 11_500
EXCERPT_MAX = 300
STEP_GROUPS = 12
SAMPLES_PER_STEP = 2
APPS_MAX = 10
VERBS = {"edit": "Edit", "browse": "Browse", "search": "Search", "create": "Create",
         "save": "Save", "view": "View", "read": "Read", "send": "Send", "click": "Work",
         "copy": "Copy", "paste": "Paste", "open": "Open", "review": "Review"}


class Refused(Exception):
    """A request answered with an error: HTTP status, code and a message for the person."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _hid(*parts: str) -> str:
    """32 hex over parts joined by the unit separator."""
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


def draft_id(task_id: str, revision: int) -> str:
    """The draft id of one task revision."""
    return _hid("draft", task_id, str(revision))


def field_limit(name: str) -> int:
    """The longest value a workflow field takes."""
    return FIELD_MAX.get(name, OTHER_MAX)


# ---------------------------------------------------------------- building


def _app_names(actions: list[dict]) -> dict[str, str]:
    """Bundle id to display name, from the actions."""
    names: dict[str, str] = {}
    for a in actions:
        if a.get("app") and a.get("app_name"):
            names.setdefault(a["app"], a["app_name"])
    return names


def _label(episodes: list[dict], names: dict[str, str]) -> str:
    """A step label from the step kinds and apps of a group of episodes."""
    parts: list[str] = []
    for ep in episodes:
        for step in (ep.get("signature") or {}).get("steps", []):
            kind = str(step.get("kind") or "work")
            app = step.get("app") or ""
            name = names.get(app) or app.rsplit(".", 1)[-1] or "an app"
            verb = VERBS.get(kind, kind.replace("_", " ").capitalize())
            part = f"{verb} in {name}"
            if not parts or parts[-1] != part:
                parts.append(part)
    if not parts:
        return "Work"
    text = ", then ".join(parts[:4])
    return text[0].upper() + text[1:]


def _excerpts(actions: list[dict]) -> tuple[list[str], int]:
    """Excerpts of the first and last action, each passed through hide on its own.

    An excerpt that hide excludes is dropped and counted as a gap.
    """
    ordered = sorted(actions, key=lambda a: (a["observed_at"], a["event_id"]))
    sampled = ordered[:1] + ordered[-1:] if len(ordered) > 1 else ordered
    kept, gaps = [], 0
    for a in sampled[:SAMPLES_PER_STEP]:
        text = " ".join((a.get("text") or "").split())
        if not text:
            continue
        hidden = check.hide(text, "text", EXCERPT_MAX)
        if hidden.excluded or not hidden.text:
            gaps += hidden.excluded
            continue
        kept.append(hidden.text)
    return kept, gaps


def _footer(total: int, sampled: int) -> str:
    covers = (f"This draft covers all {total} saved actions." if total != 1
              else "This draft covers the 1 saved action.")
    return f"{covers} The step notes use {sampled} sampled action{'' if sampled == 1 else 's'}."


def _steps(lines: list[str], footer: str) -> str:
    body = "\n".join(lines)
    room = STEPS_MAX - len(INTRO) - len(footer) - 4
    if len(body) > room:
        body = check.clip(body, room).rstrip()
    return f"{INTRO}\n\n{body}\n\n{footer}" if body else f"{INTRO}\n\n{footer}"


def _groups(episodes: list[dict]) -> list[list[dict]]:
    """Episodes oldest first, grouped evenly into at most twelve steps."""
    ordered = sorted(episodes, key=lambda e: (e["started_at"], e["id"]))
    if len(ordered) <= STEP_GROUPS:
        return [[e] for e in ordered]
    size, extra = divmod(len(ordered), STEP_GROUPS)
    out, start = [], 0
    for i in range(STEP_GROUPS):
        end = start + size + (1 if i < extra else 0)
        out.append(ordered[start:end])
        start = end
    return out


def _seconds(start: str, end: str) -> int:
    try:
        span = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except (TypeError, ValueError):
        return 0
    return max(0, int(span.total_seconds()))


def build(task: dict, episodes: list[dict], actions: list[dict]) -> tuple[dict, dict]:
    """The workflow fields and the proposal for one task.

    Each excerpt passes hide alone, then the credential check again in its place in the
    assembled steps: a label value that is safe alone can read as unterminated once more
    lines follow it. An excerpt that fails either check is dropped and counted.
    """
    names = _app_names(actions)
    by_episode: dict[str, list[dict]] = {}
    for a in actions:
        by_episode.setdefault(a.get("episode_id"), []).append(a)
    probe = _footer(len(actions), 0)
    lines, sampled, gaps = [], 0, 0
    for number, group in enumerate(_groups(episodes), start=1):
        excerpts, gaps_here = _excerpts([a for ep in group for a in by_episode.get(ep["id"], [])])
        line = head = f"{number}. {_label(group, names)}."
        for excerpt in excerpts:
            candidate = f"{line} {excerpt}" if line == head else f"{line} … {excerpt}"
            if check.contains_credential(_steps([*lines, candidate], probe)):
                gaps_here += 1
                continue
            line, sampled = candidate, sampled + 1
        lines.append(line)
        gaps += gaps_here
    steps = _steps(lines, _footer(len(actions), sampled))
    workflow = {"outcome": (task.get("title") or "Unlabelled task")[:240], "steps": steps,
                "inputs": "", "exceptions": "", "completion": "", "owner": ""}
    uses: dict[str, int] = {}
    for a in actions:
        label = a.get("app_name") or a.get("app") or "Application"
        uses[label] = uses.get(label, 0) + 1
    manifest = "\n".join(sorted(f"{a['event_id']}:{a.get('revision', 1)}" for a in actions))
    proposal = {
        "apps": sorted(uses, key=lambda k: (-uses[k], k))[:APPS_MAX],
        "observed": {"started_at": task.get("started_at"), "last_seen_at": task.get("last_seen_at"),
                     "active_seconds": sum(_seconds(e["first_action_at"], e["last_action_at"])
                                           for e in episodes),
                     "intervals": len(episodes)},
        "evidence": {"total_actions": len(actions), "sampled": sampled, "omitted_count": gaps,
                     "manifest_digest": hashlib.sha256(manifest.encode()).hexdigest()},
        "questions": ([] if task.get("lifecycle") == "completed"
                      else ["Is this case finished, or is work still to come?"]),
        "coverage_gaps": {"credential_surface": gaps},
    }
    return workflow, proposal


def has_credential(fields: dict) -> bool:
    """True when any field holds a password or key."""
    return any(check.contains_credential(v) for v in fields.values() if isinstance(v, str))


# ---------------------------------------------------------------- storage and route work


def _out(row: dict) -> dict:
    """A store draft as the API shape."""
    stored = row["workflow"]
    workflow = {k: stored.get(k, "") for k in FIELDS}
    source = {"task_id": row["task_id"], "revision": row["task_revision"]}
    digest = hashlib.sha256(json.dumps({"workflow": workflow, "source": source},
                                       sort_keys=True).encode()).hexdigest()
    return {"id": row["id"], "status": row["status"], "digest": digest, "workflow": workflow,
            "source": source, "proposal": stored.get("proposal", {}),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def get(store, did: str) -> dict | None:
    row = store.draft(did)
    return None if row is None else _out(row)


def all_drafts(store) -> list[dict]:
    return [_out(r) for r in store.drafts()]


def update(store, did: str, changes: dict) -> dict | None:
    """Replace some workflow fields; None when the draft does not exist."""
    row = store.draft(did)
    if row is None:
        return None
    store.put_draft({**row, "workflow": {**row["workflow"], **changes}})
    return get(store, did)


def _all_actions(store, task_id: str) -> tuple[list[dict], list[dict]]:
    """A task's episodes and every action it holds, up to 10 000."""
    episodes, actions, cursor = store.task_actions(task_id, 1000)
    while cursor and len(actions) < 10_000:
        _, more, cursor = store.task_actions(task_id, 1000, cursor)
        actions += more
    return episodes, actions


def create(store, task_id: str) -> tuple[int, dict]:
    """Make the draft of the task's current revision, or name the one that exists."""
    task = store.task(task_id)
    if task is None:
        raise Refused(404, "not_found", "Task was not found")
    did = draft_id(task_id, task["revision"])
    if store.draft(did) is not None:
        return 200, {"draft_id": did, "status": "existing"}
    try:
        episodes, actions = _all_actions(store, task_id)
    except NotFound:
        raise Refused(404, "not_found", "Task was not found") from None
    workflow, proposal = build(task, episodes, actions)
    if has_credential(workflow):
        raise Refused(422, "credential_detected",
                      "A password or key was found in this review. Correct the task before sharing")
    try:
        created = store.put_draft({"id": did, "task_id": task_id, "task_revision": task["revision"],
                                   "workflow": {**workflow, "proposal": proposal}})
    except (NotFound, StaleRevision):
        raise Refused(409, "instance_changed",
                      "Task changed while you were opening it. Choose Review again.") from None
    return (201 if created else 200), {"draft_id": did,
                                       "status": "created" if created else "existing"}


def changes(body) -> dict:
    """The checked fields of a PATCH body."""
    if (not isinstance(body, dict) or not body or not set(body) <= set(FIELDS)
            or not all(isinstance(v, str) for v in body.values())):
        raise Refused(400, "bad_draft",
                      "Send text for outcome, steps, inputs, exceptions, completion or owner")
    for name, value in body.items():
        if len(value) > field_limit(name):
            raise Refused(400, "bad_draft", f"Keep {name} to {field_limit(name)} characters")
    if has_credential(body):
        raise Refused(422, "credential_detected", "Remove the password or key from this draft")
    return body
