"""Store: the core's one SQLite file, thirteen tables, one transaction per public call.

Hash keys (first 32 hex of SHA-256):
- split task id: "task" and the earliest moved action id joined by "\\x1f" (the task id rule);
  if that id is taken, the source task id is appended as a third part.
- name key: JSON ["name", sorted distinct step kinds, sorted apps] of the task's latest episode.
- match key: JSON ["match-coarse", the two [kind, app] step lists, sorted], latest episodes.
- same_process process id: "process" and the two latest episode ids, sorted, joined by "\\x1f".
JSON keys use json.dumps(parts, sort_keys=True, separators=(",", ":")).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

__all__ = ["BadOperation", "Conflict", "NotFound", "PassResult", "StaleRevision", "Store",
           "StoreError", "name_key"]

SCHEMA = """
CREATE TABLE events (
  event_id      TEXT PRIMARY KEY,
  revision      INTEGER NOT NULL,
  provenance    TEXT NOT NULL,        -- native_accessibility | native_interaction | screen_ocr | app_reader | synthetic
  kind          TEXT NOT NULL,        -- screen_text | interaction | capture_gap
  observed_at   TEXT NOT NULL,        -- UTC ISO 8601 Z, as every time column
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  app           TEXT NOT NULL,        -- bundle id
  app_name      TEXT,
  title         TEXT,                 -- as received; cleaned on read
  url           TEXT,                 -- scheme + host + path
  document_id   TEXT,
  window_ref    TEXT,
  interaction   TEXT,                 -- click | edit | shortcut | scroll | focus | window_focus | navigation | NULL
  text          TEXT NOT NULL DEFAULT '',
  metadata      TEXT NOT NULL,        -- JSON, the remaining keys
  sightings     INTEGER NOT NULL DEFAULT 1,
  duration_s    INTEGER NOT NULL DEFAULT 0,
  case_keys     TEXT NOT NULL DEFAULT '[]',   -- JSON [[namespace, value]], hashed
  artifact_key  TEXT, surface_key TEXT, window_key TEXT,
  episode_id    TEXT,                 -- NULL until a pass places it
  received_at   TEXT NOT NULL
);
CREATE INDEX events_at ON events(observed_at);
CREATE INDEX events_episode ON events(episode_id);

CREATE TABLE episodes (               -- a run of sessions that is one piece of work
  id              TEXT PRIMARY KEY,   -- sha256 of the sorted action ids, 32 hex: a rerun writes the same row
  task_id         TEXT NOT NULL,
  started_at      TEXT NOT NULL, ended_at TEXT NOT NULL,
  first_action_at TEXT NOT NULL, last_action_at TEXT NOT NULL,
  close_reason    TEXT,               -- idle | break | diversion | quiet | paused | NULL while open
  settled         INTEGER NOT NULL DEFAULT 0,   -- 0 provisional, 1 closed by a quiet or forced pass
  trivial         INTEGER NOT NULL DEFAULT 0,
  diversion       INTEGER NOT NULL DEFAULT 0,
  signature       TEXT NOT NULL,      -- JSON {steps:[{kind, app, evidence[]}], apps[], artifacts[]}
  process_id      TEXT,
  revision        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE tasks (                  -- one row on the page
  id                TEXT PRIMARY KEY, -- hash of the earliest action id; survives merges
  title             TEXT,
  name_basis        TEXT NOT NULL DEFAULT 'rules',   -- rules | model | person
  summary           TEXT,
  lifecycle         TEXT NOT NULL DEFAULT 'active',  -- active | completed | abandoned
  locked            INTEGER NOT NULL DEFAULT 0,      -- set by any correction
  possibly_personal INTEGER NOT NULL DEFAULT 0,
  revision          INTEGER NOT NULL DEFAULT 1,      -- corrections send expected_revision; mismatch = 409
  started_at        TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  case_keys         TEXT NOT NULL DEFAULT '[]',
  draft_id          TEXT
);

CREATE TABLE decisions (              -- one per switch between two sessions
  key             TEXT PRIMARY KEY,   -- hash(first action id of previous session, first action id of next)
  left_event_id   TEXT NOT NULL, right_event_id TEXT NOT NULL,
  at              TEXT NOT NULL,
  features        TEXT NOT NULL,      -- JSON {name: number}
  p_join          REAL,               -- scorer output; NULL when a hard rule decided
  choice          TEXT NOT NULL,      -- same | supporting | break | diversion
  basis           TEXT NOT NULL,      -- rule | scorer | model | person
  reason          TEXT NOT NULL,
  hard            INTEGER NOT NULL,
  asked_at TEXT, ask_context TEXT, question_status TEXT,   -- open | answered | snoozed | dismissed | expired
  answer TEXT, answered_at TEXT
);

CREATE TABLE labels (                 -- append only; the current label is the newest row per key
  id       INTEGER PRIMARY KEY,
  at       TEXT NOT NULL,
  kind     TEXT NOT NULL,             -- relation | match | name | complete
  key      TEXT NOT NULL,
  source   TEXT NOT NULL,             -- answer | correction | scripted
  label    TEXT NOT NULL,
  features TEXT                       -- JSON copy of decisions.features at label time
);

CREATE TABLE processes (id TEXT PRIMARY KEY, name TEXT, signature TEXT NOT NULL, steps TEXT NOT NULL,
  templates TEXT NOT NULL, score REAL NOT NULL, basis TEXT NOT NULL,   -- rules | model | person
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE names (signature TEXT PRIMARY KEY, verb TEXT, object TEXT, system TEXT, title TEXT,
  basis TEXT NOT NULL, reusable INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE models (kind TEXT NOT NULL, version INTEGER NOT NULL, created_at TEXT NOT NULL,
  weights TEXT NOT NULL, examples INTEGER NOT NULL, agreement REAL NOT NULL, basis TEXT NOT NULL,
  PRIMARY KEY (kind, version));
CREATE TABLE ask_stats (context TEXT PRIMARY KEY, alpha REAL NOT NULL, beta REAL NOT NULL,
  asks INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE drafts (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_revision INTEGER NOT NULL,
  status TEXT NOT NULL, workflow TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE personal_days (day TEXT PRIMARY KEY, tasks INTEGER NOT NULL, minutes INTEGER NOT NULL);
CREATE TABLE batches (batch_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, ack TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

REOPEN_S = 600
SNOOZE = timedelta(minutes=30)
TITLE_MAX = 240
SPLIT_TITLE_MAX = 220
EPISODES_MAX = 500
MODELS_KEPT = 5
REVISION_KEYS = ("text_sha256", "sighting_count", "duration_s", "text_revision", "value_omitted")
JSON_COLUMNS = frozenset({"metadata", "case_keys", "signature", "features", "steps", "templates",
                          "weights", "ack"})
RANK = {"model": 1, "person": 2}
ANSWERS = {"one": ("answered", "same"), "two": ("answered", "break"),
           "later": ("snoozed", None), "never": ("dismissed", None)}
EVENT_COPY = ("event_id", "revision", "provenance", "observed_at", "first_seen_at", "last_seen_at",
              "url", "interaction", "artifact_key", "surface_key", "window_key")
EVENT_FROM_META = {"app": "app_bundle_id", "app_name": "app_name", "title": "window_title",
                   "window_ref": "window_ref", "document_id": "document_id"}
EPISODE_COLUMNS = ("id", "task_id", "started_at", "ended_at", "first_action_at", "last_action_at",
                   "close_reason", "signature", "process_id")
TASK_PASS = ("title", "name_basis", "summary", "lifecycle", "possibly_personal", "started_at",
             "last_seen_at", "case_keys")
TASK_COLUMNS = ["id", *TASK_PASS, "locked", "revision", "draft_id"]
TASK_DEFAULTS = {"name_basis": "rules", "lifecycle": "active", "locked": 0, "possibly_personal": 0,
                 "revision": 1, "case_keys": []}
DECISION_CORE = ("features", "p_join", "choice", "basis", "reason", "hard")
QUESTION_COLUMNS = ("asked_at", "ask_context", "question_status", "answer", "answered_at")
DECISION_COLUMNS = ["key", "left_event_id", "right_event_id", "at", *DECISION_CORE,
                    *QUESTION_COLUMNS]
NAME_COLUMNS = ("signature", "verb", "object", "system", "title", "basis", "reusable",
                "updated_at")
PROCESS_COLUMNS = ("id", "name", "signature", "steps", "templates", "score", "basis", "created_at",
                   "updated_at")
TASK_SQL = """SELECT t.*,
  (SELECT COUNT(*) FROM episodes e WHERE e.task_id = t.id) AS episode_count,
  (SELECT COUNT(*) FROM events v JOIN episodes e ON e.id = v.episode_id
    WHERE e.task_id = t.id) AS action_count
FROM tasks t"""
PLACE_SQL = """UPDATE events SET episode_id = ? WHERE event_id = ? AND (
  episode_id IS NULL OR episode_id = ? OR episode_id NOT IN (
    SELECT e.id FROM episodes e JOIN tasks t ON t.id = e.task_id WHERE t.locked = 1))"""
QUESTIONS_SQL = """SELECT d.*, lt.id AS left_task_id, lt.title AS left_title,
  rt.id AS right_task_id, rt.title AS right_title
FROM decisions d
JOIN events l ON l.event_id = d.left_event_id JOIN episodes le ON le.id = l.episode_id
JOIN tasks lt ON lt.id = le.task_id
JOIN events r ON r.event_id = d.right_event_id JOIN episodes re ON re.id = r.episode_id
JOIN tasks rt ON rt.id = re.task_id
WHERE d.question_status = 'open' OR (d.question_status = 'snoozed' AND d.asked_at <= ?)
ORDER BY d.asked_at DESC, d.key DESC LIMIT ?"""
STATUS_SQL = """SELECT (SELECT COUNT(*) FROM events) AS events,
  (SELECT COUNT(*) FROM tasks) AS tasks,
  (SELECT COUNT(*) FROM decisions WHERE question_status = 'open') AS open_questions,
  (SELECT value FROM settings WHERE key = 'last_pass_at') AS last_pass_at,
  (SELECT value FROM settings WHERE key = 'last_refit_at') AS last_refit_at"""


class StoreError(Exception):
    """Base class for store errors."""


class NotFound(StoreError):
    """A task or question does not exist."""


class StaleRevision(StoreError):
    """The expected revision is not the stored one."""


class BadOperation(StoreError):
    """A correction or answer is malformed."""


class Conflict(StoreError):
    """The question is no longer open."""


@dataclass
class PassResult:
    """What one grouping pass writes."""

    episodes: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    processes: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    placements: dict[str, str] = field(default_factory=dict)
    settled: bool = False
    names: list[dict] = field(default_factory=list)
    links: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- time, keys, rows


def _fmt(moment: datetime) -> str:
    """UTC ISO 8601 with Z, whole seconds."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(now: str | datetime | None) -> str:
    """A time string from a string, a datetime or the clock."""
    if now is None:
        return _fmt(datetime.now(UTC))
    return _fmt(now) if isinstance(now, datetime) else now


def _hid(*parts: str) -> str:
    """32 hex over parts joined by the unit separator."""
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


def _key(*parts) -> str:
    """32 hex over the compact, sorted JSON of parts."""
    text = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def _dump(value):
    """JSON text for a JSON column; strings and None pass through."""
    return value if value is None or isinstance(value, str) else json.dumps(value, sort_keys=True)


def _row(row) -> dict | None:
    """A sqlite row as a dict with JSON columns decoded."""
    if row is None:
        return None
    out = dict(row)
    for k in JSON_COLUMNS & out.keys():
        if isinstance(out[k], str):
            out[k] = json.loads(out[k])
    return out


def _marks(n: int) -> str:
    """n comma-separated placeholders."""
    return ", ".join("?" * n)


def _put(c, table: str, row: dict, verb: str = "INSERT OR REPLACE", tail: str = "") -> int:
    """Write one row; return its rowid."""
    values = [_dump(v) if k in JSON_COLUMNS else v for k, v in row.items()]
    sql = f"{verb} INTO {table} ({', '.join(row)}) VALUES ({_marks(len(row))}){tail}"
    return c.execute(sql, values).lastrowid


def _after(before: str | None, at_col: str, id_col: str) -> tuple[str, list]:
    """SQL and arguments for rows past a cursor; ValueError when the cursor is malformed."""
    if before is None:
        return "", []
    at, sep, key = before.partition("_")
    if not sep or not key:
        raise ValueError(f"bad cursor: {before!r}")
    datetime.fromisoformat(at)
    return f" AND ({at_col} < ? OR ({at_col} = ? AND {id_col} < ?))", [at, at, key]


def _page(rows: list, limit: int, at: str, key: str) -> tuple[list[dict], str | None]:
    """The first `limit` rows and a cursor when more remain."""
    items = [_row(r) for r in rows[:limit]]
    more = len(rows) > limit and items
    return items, f"{items[-1][at]}_{items[-1][key]}" if more else None


def _drop_orphans(c) -> tuple[int, int]:
    """Delete episodes with no events, then tasks with no episodes."""
    episodes = c.execute("DELETE FROM episodes WHERE id NOT IN "
                         "(SELECT episode_id FROM events WHERE episode_id IS NOT NULL)").rowcount
    tasks = c.execute("DELETE FROM tasks WHERE id NOT IN (SELECT task_id FROM episodes)").rowcount
    return episodes, tasks


# ---------------------------------------------------------------- pass writers


def _put_event(c, event: dict, received: str) -> str:
    """Store one event or one newer revision of it; return the ack status."""
    content = event.get("content", {})
    meta = dict(content.get("metadata", {}))
    if "text_sha256" in content:
        meta["text_sha256"] = content["text_sha256"]
    old = c.execute("SELECT revision, last_seen_at, metadata FROM events WHERE event_id = ?",
                    (event["event_id"],)).fetchone()
    if old is None:
        row = {k: event.get(k) for k in EVENT_COPY}
        row.update({col: event[col] if col in event else meta.get(key)
                    for col, key in EVENT_FROM_META.items()})
        row.update(kind=content.get("type"), text=content.get("text", ""), metadata=meta,
                   sightings=event.get("sightings", 1), duration_s=event.get("duration_s", 0),
                   case_keys=event.get("case_keys", []), received_at=received)
        _put(c, "events", row, "INSERT")
        return "stored"
    if event["revision"] == old["revision"]:
        return "duplicate"
    if event["revision"] < old["revision"]:
        return "stale"
    merged = json.loads(old["metadata"])
    merged.update({k: meta[k] for k in REVISION_KEYS if k in meta})
    newer = event.get("last_seen_at") or old["last_seen_at"]
    last = max(old["last_seen_at"], newer, key=datetime.fromisoformat)
    c.execute("UPDATE events SET revision = ?, text = ?, metadata = ?, sightings = ?, "
              "duration_s = ?, last_seen_at = ? WHERE event_id = ?",
              (event["revision"], content.get("text", ""), _dump(merged),
               event.get("sightings", 1), event.get("duration_s", 0), last, event["event_id"]))
    return "stored"


def _write_decision(c, d: dict) -> None:
    """Upsert a decision; person beats model beats rules, and question state is kept."""
    row = {k: d.get(k) for k in DECISION_COLUMNS}
    old = c.execute("SELECT * FROM decisions WHERE key = ?", (row["key"],)).fetchone()
    if old is not None:
        if RANK.get(old["basis"], 0) > RANK.get(row["basis"], 0):
            row.update({k: old[k] for k in DECISION_CORE})
        if old["question_status"] is not None:
            row.update({k: old[k] for k in QUESTION_COLUMNS})
    _put(c, "decisions", row)


def _write_task(c, t: dict) -> None:
    """Upsert a task row unless the task is locked."""
    row = {k: t.get(k, TASK_DEFAULTS.get(k)) for k in TASK_COLUMNS}
    update = ", ".join(f"{k} = excluded.{k}" for k in TASK_PASS)
    _put(c, "tasks", row, "INSERT",
         f" ON CONFLICT(id) DO UPDATE SET {update} WHERE tasks.locked = 0")


def _write_episode(c, ep: dict, settled: bool) -> None:
    """Upsert an episode; one in a locked task keeps its task and process."""
    old = c.execute("SELECT e.task_id, e.process_id, t.locked FROM episodes e "
                    "LEFT JOIN tasks t ON t.id = e.task_id WHERE e.id = ?",
                    (ep["id"],)).fetchone()
    row = {k: ep.get(k) for k in EPISODE_COLUMNS}
    row.update(settled=int(settled or old is not None), trivial=ep.get("trivial", 0),
               diversion=ep.get("diversion", 0), revision=ep.get("revision", 1))
    if old is not None and old["locked"]:
        row.update(task_id=old["task_id"], process_id=old["process_id"])
    _put(c, "episodes", row)


def _write_process(c, p: dict) -> None:
    """Upsert a process unless a person confirmed it."""
    old = c.execute("SELECT basis FROM processes WHERE id = ?", (p["id"],)).fetchone()
    if old is None or old["basis"] != "person":
        _put(c, "processes", {k: p.get(k) for k in PROCESS_COLUMNS})


# ---------------------------------------------------------------- correction helpers


def _task(c, task_id: str) -> dict | None:
    """One task with its episode and action counts."""
    return _row(c.execute(TASK_SQL + " WHERE t.id = ?", (task_id,)).fetchone())


def _episode_ids(c, task_id: str) -> set[str]:
    """Ids of a task's episodes."""
    return {r[0] for r in c.execute("SELECT id FROM episodes WHERE task_id = ?", (task_id,))}


def _latest(c, task_id: str) -> dict:
    """A task's newest episode."""
    return _row(c.execute("SELECT * FROM episodes WHERE task_id = ? "
                          "ORDER BY last_action_at DESC, id DESC LIMIT 1", (task_id,)).fetchone())


def name_key(signature: dict) -> str:
    """Content-free name key of an episode signature: its step kinds and apps."""
    steps = signature.get("steps", [])
    apps = signature.get("apps") or {s["app"] for s in steps}
    return _key("name", sorted({s["kind"] for s in steps}), sorted(apps))


def _name_key(c, task_id: str) -> str:
    """Content-free name key of a task's latest episode."""
    return name_key(_latest(c, task_id)["signature"])


def _write_name(c, n: dict) -> None:
    """Upsert a name record; a lower basis never replaces a higher one."""
    old = c.execute("SELECT basis FROM names WHERE signature = ?", (n["signature"],)).fetchone()
    if old is not None and RANK.get(old["basis"], 0) >= RANK.get(n.get("basis"), 0):
        return
    _put(c, "names", {k: n.get(k) for k in NAME_COLUMNS} | {"reusable": int(n.get("reusable") or 0)})


def _label(c, kind: str, key: str, source: str, label: str, features, at: str) -> int:
    """Append one label row."""
    return _put(c, "labels", {"at": at, "kind": kind, "key": key, "source": source,
                              "label": label, "features": features}, "INSERT")


def _lock(c, task_ids) -> None:
    """Lock tasks and bump their revision."""
    ids = list(task_ids)
    c.execute("UPDATE tasks SET locked = 1, possibly_personal = 0, revision = revision + 1 "
              f"WHERE id IN ({_marks(len(ids))})", ids)


def _refresh(c, *task_ids: str) -> None:
    """Recompute tasks' spans from their episodes."""
    c.executemany("UPDATE tasks SET started_at = (SELECT MIN(started_at) FROM episodes "
              "WHERE task_id = ?1), last_seen_at = (SELECT MAX(last_action_at) FROM episodes "
              "WHERE task_id = ?1) WHERE id = ?1", [(t,) for t in task_ids])


def _expire(c, task_ids) -> None:
    """Expire open or snoozed questions that touch the given tasks."""
    ids = list(task_ids)
    c.execute("UPDATE decisions SET question_status = 'expired' "
              "WHERE question_status IN ('open', 'snoozed') AND key IN ("
              "SELECT d.key FROM decisions d "
              "JOIN events v ON v.event_id IN (d.left_event_id, d.right_event_id) "
              "JOIN episodes e ON e.id = v.episode_id "
              f"WHERE e.task_id IN ({_marks(len(ids))}))", ids)


def _relabel(c, side_a: set[str], side_b: set[str], label: str, at: str) -> None:
    """Label every decision between two sets of episodes and record it as the person's."""
    pairs = c.execute("SELECT d.key, d.features, l.episode_id AS le, r.episode_id AS re "
                      "FROM decisions d JOIN events l ON l.event_id = d.left_event_id "
                      "JOIN events r ON r.event_id = d.right_event_id ORDER BY d.at, d.key")
    for p in pairs.fetchall():
        if (p["le"] in side_a and p["re"] in side_b) or (p["le"] in side_b and p["re"] in side_a):
            _label(c, "relation", p["key"], "correction", label, p["features"], at)
            c.execute("UPDATE decisions SET basis = 'person', choice = ? WHERE key = ?",
                      (label, p["key"]))


def _other(c, task: dict, op: dict, name: str) -> dict:
    """The second task an operation names, checked."""
    other_id = op.get(name)
    if not isinstance(other_id, str) or other_id == task["id"]:
        raise BadOperation(f"{name} must name another task")
    other = _task(c, other_id)
    if other is None:
        raise NotFound(other_id)
    expected = op.get("other_expected_revision")
    if expected is not None and expected != other["revision"]:
        raise StaleRevision(other_id)
    return other


def _chosen(c, task: dict, op: dict, *, keep_one: bool) -> tuple[set[str], set[str]]:
    """The chosen episodes of a task and the ones left."""
    ids = op.get("episode_ids")
    if (not isinstance(ids, list) or not ids or len(ids) > EPISODES_MAX
            or not all(isinstance(i, str) for i in ids)):
        raise BadOperation("episode_ids must list 1 to 500 episodes")
    own, chosen = _episode_ids(c, task["id"]), set(ids)
    if not chosen <= own:
        raise BadOperation("an episode is not in this task")
    if keep_one and chosen == own:
        raise BadOperation("nothing would remain in the task")
    return chosen, own - chosen


def _assign(c, column: str, value: str, episode_ids: set[str]) -> None:
    """Set task_id or process_id on episodes."""
    ids = sorted(episode_ids)
    c.execute(f"UPDATE episodes SET {column} = ? WHERE id IN ({_marks(len(ids))})", [value, *ids])


# ---------------------------------------------------------------- corrections


def _rename(c, task: dict, op: dict, at: str) -> str:
    """Set the person's title and keep it in names under the task's name key."""
    title = op.get("title")
    title = " ".join(title.split()) if isinstance(title, str) else ""
    if not title or len(title) > TITLE_MAX:
        raise BadOperation("title must be 1 to 240 characters")
    _expire(c, [task["id"]])
    c.execute("UPDATE tasks SET title = ?, name_basis = 'person' WHERE id = ?", (title, task["id"]))
    _lock(c, [task["id"]])
    key = _name_key(c, task["id"])
    _label(c, "name", key, "correction", "renamed", None, at)
    _put(c, "names", {"signature": key, "verb": None, "object": None, "system": None,
                      "title": title, "basis": "person", "reusable": 1, "updated_at": at})
    return task["id"]


def _merge(c, task: dict, op: dict, at: str) -> str:
    """Move every episode of the other task into this one."""
    other = _other(c, task, op, "other_task_id")
    _expire(c, [task["id"], other["id"]])
    mine, theirs = _episode_ids(c, task["id"]), _episode_ids(c, other["id"])
    cases = task["case_keys"] + [k for k in other["case_keys"] if k not in task["case_keys"]]
    _assign(c, "task_id", task["id"], theirs)
    c.execute("DELETE FROM tasks WHERE id = ?", (other["id"],))
    c.execute("UPDATE tasks SET case_keys = ? WHERE id = ?", (_dump(cases), task["id"]))
    _lock(c, [task["id"]])
    _refresh(c, task["id"])
    _relabel(c, mine, theirs, "same", at)
    return task["id"]


def _split(c, task: dict, op: dict, at: str) -> str:
    """Move chosen episodes to a new locked task; return the new task."""
    chosen, rest = _chosen(c, task, op, keep_one=True)
    ids = sorted(chosen)
    earliest = c.execute(f"SELECT event_id FROM events WHERE episode_id IN ({_marks(len(ids))}) "
                         "ORDER BY observed_at, event_id LIMIT 1", ids).fetchone()
    first = earliest[0] if earliest else ids[0]
    new_id = _hid("task", first) if _task(c, _hid("task", first)) is None else _hid(
        "task", first, task["id"])
    title = f"Split from {task['title'][:SPLIT_TITLE_MAX]}" if task["title"] else "Split activity"
    _expire(c, [task["id"]])
    c.execute("INSERT INTO tasks (id, title, locked, started_at, last_seen_at) "
              "SELECT ?, ?, 1, MIN(started_at), MAX(last_action_at) FROM episodes "
              f"WHERE id IN ({_marks(len(ids))})", [new_id, title, *ids])
    _assign(c, "task_id", new_id, chosen)
    _lock(c, [task["id"]])
    _refresh(c, task["id"])
    _relabel(c, chosen, rest, "break", at)
    return new_id


def _move(c, task: dict, op: dict, at: str) -> str:
    """Move chosen episodes to the target; return the target if the source empties."""
    chosen, rest = _chosen(c, task, op, keep_one=False)
    target = _other(c, task, op, "target_task_id")
    _expire(c, [task["id"], target["id"]])
    target_eps = _episode_ids(c, target["id"])
    _assign(c, "task_id", target["id"], chosen)
    _relabel(c, chosen, rest, "break", at)
    _relabel(c, chosen, target_eps, "same", at)
    if not rest:
        c.execute("DELETE FROM tasks WHERE id = ?", (task["id"],))
        _lock(c, [target["id"]])
        _refresh(c, target["id"])
        return target["id"]
    _lock(c, [task["id"], target["id"]])
    _refresh(c, task["id"], target["id"])
    return task["id"]


def _same_process(c, task: dict, op: dict, at: str) -> str:
    """Put both tasks' latest episodes in one person-confirmed process."""
    other = _other(c, task, op, "other_task_id")
    _expire(c, [task["id"], other["id"]])
    _lock(c, [task["id"], other["id"]])
    ea, eb = _latest(c, task["id"]), _latest(c, other["id"])
    sa, sb = ([[s["kind"], s["app"]] for s in e["signature"].get("steps", [])] for e in (ea, eb))
    pid = ea["process_id"] or eb["process_id"] or _hid("process", *sorted([ea["id"], eb["id"]]))
    if c.execute("SELECT 1 FROM processes WHERE id = ?", (pid,)).fetchone():
        c.execute("UPDATE processes SET basis = 'person', updated_at = ? WHERE id = ?", (at, pid))
    else:
        _put(c, "processes", {"id": pid, "name": None, "signature": {"steps": sa}, "steps": sa,
                              "templates": [], "score": 1.0, "basis": "person",
                              "created_at": at, "updated_at": at}, "INSERT")
    _assign(c, "process_id", pid, {ea["id"], eb["id"]})
    if sa and sb:
        _label(c, "match", _key("match-coarse", sorted([sa, sb])), "correction", "same", None, at)
    return task["id"]


def _lifecycle(state: str, label: str):
    """A correction that sets the lifecycle and writes a complete label."""
    def apply(c, task: dict, op: dict, at: str) -> str:
        _expire(c, [task["id"]])
        c.execute("UPDATE tasks SET lifecycle = ? WHERE id = ?", (state, task["id"]))
        _lock(c, [task["id"]])
        _label(c, "complete", _name_key(c, task["id"]), "correction", label, None, at)
        return task["id"]
    return apply


OPERATIONS = {"rename": _rename, "merge": _merge, "split": _split, "move": _move,
              "same_process": _same_process, "complete": _lifecycle("completed", "complete"),
              "reopen": _lifecycle("active", "reopen")}


def _draft(row) -> dict | None:
    """A drafts row with its workflow JSON decoded."""
    if row is None:
        return None
    out = dict(row)
    out["workflow"] = json.loads(out["workflow"])
    return out


def _add_personal(c, day: str, tasks: int, minutes: int) -> None:
    """Add to one day's personal-time row."""
    c.execute("INSERT INTO personal_days (day, tasks, minutes) VALUES (?, ?, ?) ON CONFLICT(day) "
              "DO UPDATE SET tasks = tasks + excluded.tasks, minutes = minutes + excluded.minutes",
              (day, tasks, minutes))


# ---------------------------------------------------------------- store


class Store:
    """The core database at `path`."""

    def __init__(self, path) -> None:
        self.path = os.fspath(path)
        if not os.path.exists(self.path):
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        os.chmod(self.path, 0o600)
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'events'").fetchone() is None:
                conn.executescript(f"BEGIN;\n{SCHEMA}\nCOMMIT;")
        finally:
            conn.close()

    @contextmanager
    def _tx(self, write: bool = True) -> Iterator[sqlite3.Connection]:
        """One connection and one transaction; rolled back on any error."""
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def put_events(self, events: list[dict], now=None) -> dict:
        """Store checked events; the ack names each one stored, duplicate or stale."""
        received = _stamp(now)
        with self._tx() as c:
            accepted = [{"event_id": e["event_id"], "revision": e["revision"],
                         "status": _put_event(c, e, received)} for e in events]
        return {"accepted": accepted, "rejected": []}

    def unplaced_actions(self, limit: int = 3000) -> list[dict]:
        """Events no episode holds, oldest first."""
        with self._tx(False) as c:
            rows = c.execute("SELECT * FROM events WHERE episode_id IS NULL "
                             "ORDER BY observed_at, event_id LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]

    def reopenable_episode(self, now=None) -> dict | None:
        """The newest settled quiet or diversion episode that ended at most 600 s ago."""
        with self._tx(False) as c:
            row = c.execute("SELECT * FROM episodes WHERE settled = 1 AND close_reason IN "
                            "('quiet', 'diversion') ORDER BY last_action_at DESC, id DESC "
                            "LIMIT 1").fetchone()
        if row is None:
            return None
        gap = datetime.fromisoformat(_stamp(now)) - datetime.fromisoformat(row["last_action_at"])
        return _row(row) if gap.total_seconds() <= REOPEN_S else None

    def write_pass(self, result: PassResult) -> None:
        """Write one pass: provisional rows are replaced; settled, locked and answered kept."""
        with self._tx() as c:
            c.execute("UPDATE events SET episode_id = NULL WHERE episode_id IN "
                      "(SELECT id FROM episodes WHERE settled = 0)")
            c.execute("DELETE FROM episodes WHERE settled = 0")
            for d in result.decisions:
                _write_decision(c, d)
            for t in result.tasks:
                _write_task(c, t)
            for ep in result.episodes:
                _write_episode(c, ep, result.settled)
            c.executemany(PLACE_SQL, [(ep, ev, ep) for ev, ep in result.placements.items()])
            for p in result.processes:
                _write_process(c, p)
            c.executemany("UPDATE episodes SET process_id = ? WHERE id = ? AND process_id IS NULL",
                          [(pid, eid) for eid, pid in result.links.items()])
            for n in result.names:
                _write_name(c, n)
            _drop_orphans(c)

    def recent_episodes(self, limit: int = 200) -> list[dict]:
        """The newest settled, non-trivial episodes by ended_at, with signature and process_id."""
        with self._tx(False) as c:
            rows = c.execute("SELECT * FROM episodes WHERE settled = 1 AND trivial = 0 "
                             "ORDER BY ended_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]

    def actions_by_id(self, event_ids) -> dict[str, dict]:
        """Event rows by id, for the ids that still exist."""
        ids, out = sorted(set(event_ids)), {}
        with self._tx(False) as c:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                for r in c.execute(f"SELECT * FROM events WHERE event_id IN ({_marks(len(chunk))})",
                                   chunk):
                    out[r["event_id"]] = _row(r)
        return out

    def names(self, signatures=None) -> dict[str, dict]:
        """Stored name records by name key, all or only the given keys."""
        with self._tx(False) as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM names")]  # no JSON columns
        wanted = None if signatures is None else set(signatures)
        return {r["signature"]: r for r in rows if wanted is None or r["signature"] in wanted}

    def task(self, task_id: str) -> dict | None:
        """One task with episode_count and action_count, or None."""
        with self._tx(False) as c:
            return _task(c, task_id)

    def tasks(self, limit: int = 50, before: str | None = None) -> tuple[list[dict], str | None]:
        """Tasks newest first by last_seen_at then id, and a cursor when more remain."""
        where, args = _after(before, "t.last_seen_at", "t.id")
        with self._tx(False) as c:
            rows = c.execute(f"{TASK_SQL} WHERE 1{where} ORDER BY t.last_seen_at DESC, t.id DESC "
                             "LIMIT ?", [*args, limit + 1]).fetchall()
        return _page(rows, limit, "last_seen_at", "id")

    def task_actions(self, task_id: str, limit: int = 100, before: str | None = None
                     ) -> tuple[list[dict], list[dict], str | None]:
        """A task's episodes and one page of its actions, newest first."""
        where, args = _after(before, "v.observed_at", "v.event_id")
        with self._tx(False) as c:
            if c.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
                raise NotFound(task_id)
            episodes = [_row(r) for r in c.execute(
                "SELECT * FROM episodes WHERE task_id = ? ORDER BY last_action_at DESC, id DESC",
                (task_id,))]
            rows = c.execute("SELECT v.* FROM events v JOIN episodes e ON e.id = v.episode_id "
                             "WHERE e.task_id = ?" + where +
                             " ORDER BY v.observed_at DESC, v.event_id DESC LIMIT ?",
                             [task_id, *args, limit + 1]).fetchall()
        actions, cursor = _page(rows, limit, "observed_at", "event_id")
        return episodes, actions, cursor

    def correct(self, task_id: str, expected_revision: int, op: dict, now=None) -> dict:
        """Apply one correction, lock the touched tasks, write labels; return the result task."""
        at = _stamp(now)
        with self._tx() as c:
            task = _task(c, task_id)
            if task is None:
                raise NotFound(task_id)
            if task["revision"] != expected_revision:
                raise StaleRevision(task_id)
            name = op.get("op") if isinstance(op, dict) else None
            apply = OPERATIONS.get(name) if isinstance(name, str) else None
            if apply is None:
                raise BadOperation(f"unknown operation: {name!r}")
            result_id = apply(c, task, op, at)
            c.execute("UPDATE episodes SET settled = 1 "
                      "WHERE task_id IN (SELECT id FROM tasks WHERE locked = 1)")
            return _task(c, result_id)

    def questions(self, limit: int = 3, now=None) -> list[dict]:
        """Open questions newest first; snoozed ones return once `now` passes their time."""
        with self._tx(False) as c:
            rows = c.execute(QUESTIONS_SQL, (_stamp(now) if now else "", limit)).fetchall()
        return [_row(r) for r in rows]

    def answer(self, key: str, answer: str, now=None) -> dict:
        """Record the person's answer to an open or snoozed question."""
        if not isinstance(answer, str) or answer not in ANSWERS:
            raise BadOperation(f"answer must be one of {sorted(ANSWERS)}")
        at = _stamp(now)
        status, label = ANSWERS[answer]
        with self._tx() as c:
            row = c.execute("SELECT * FROM decisions WHERE key = ?", (key,)).fetchone()
            if row is None:
                raise NotFound(key)
            if row["question_status"] not in ("open", "snoozed"):
                raise Conflict(key)
            if label is not None:
                c.execute("UPDATE decisions SET question_status = ?, answer = ?, answered_at = ?, "
                          "basis = 'person', choice = ? WHERE key = ?",
                          (status, answer, at, label, key))
                _label(c, "relation", key, "answer", label, row["features"], at)
            elif status == "snoozed":
                c.execute("UPDATE decisions SET question_status = ?, asked_at = ? WHERE key = ?",
                          (status, _fmt(datetime.fromisoformat(at) + SNOOZE), key))
            else:
                c.execute("UPDATE decisions SET question_status = ?, answer = ?, answered_at = ? "
                          "WHERE key = ?", (status, answer, at, key))
            return _row(c.execute("SELECT * FROM decisions WHERE key = ?", (key,)).fetchone())

    def processes(self) -> list[dict]:
        """Processes with two or more episodes, each with its episode_ids."""
        with self._tx(False) as c:
            procs = [_row(r) for r in c.execute(
                "SELECT * FROM processes ORDER BY updated_at DESC, id")]
            members: dict[str, list[str]] = {}
            for pid, eid in c.execute("SELECT process_id, id FROM episodes WHERE process_id "
                                      "IS NOT NULL ORDER BY started_at, id"):
                members.setdefault(pid, []).append(eid)
        return [{**p, "episode_ids": members[p["id"]]} for p in procs
                if len(members.get(p["id"], [])) >= 2]

    def add_label(self, kind: str, key: str, source: str, label: str, features=None,
                  at=None) -> int:
        """Append one label; return its id."""
        with self._tx() as c:
            return _label(c, kind, key, source, label, features, _stamp(at))

    def labels(self, kind: str, since: str | None = None) -> list[dict]:
        """Labels of one kind in insertion order, after `since` when given."""
        with self._tx(False) as c:
            rows = c.execute("SELECT * FROM labels WHERE kind = ? AND at > ? ORDER BY id",
                             (kind, since or "")).fetchall()
        return [_row(r) for r in rows]

    def save_model(self, kind: str, weights, *, examples: int, agreement: float, basis: str,
                   now=None) -> int:
        """Store the next model version of a kind, keep the last five; return the version."""
        with self._tx() as c:
            version = c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM models WHERE kind = ?",
                                (kind,)).fetchone()[0]
            _put(c, "models", {"kind": kind, "version": version, "created_at": _stamp(now),
                               "weights": weights, "examples": examples, "agreement": agreement,
                               "basis": basis}, "INSERT")
            c.execute("DELETE FROM models WHERE kind = ? AND version <= ?",
                      (kind, version - MODELS_KEPT))
        return version

    def load_model(self, kind: str) -> dict | None:
        """The newest model of a kind, or None."""
        with self._tx(False) as c:
            return _row(c.execute("SELECT * FROM models WHERE kind = ? ORDER BY version DESC "
                                  "LIMIT 1", (kind,)).fetchone())

    def ask_stats(self) -> dict[str, dict]:
        """Asking posteriors keyed by context."""
        with self._tx(False) as c:
            rows = c.execute("SELECT * FROM ask_stats").fetchall()
        return {r["context"]: {k: r[k] for k in ("alpha", "beta", "asks", "updated_at")}
                for r in rows}

    def update_ask_stats(self, context: str, alpha: float, beta: float, asks: int,
                         at=None) -> None:
        """Replace the posterior of one context."""
        with self._tx() as c:
            _put(c, "ask_stats", {"context": context, "alpha": alpha, "beta": beta, "asks": asks,
                                  "updated_at": _stamp(at)})

    def setting(self, key: str) -> str | None:
        """One setting, or None."""
        with self._tx(False) as c:
            row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_setting(self, key: str, value: str) -> None:
        """Write one setting."""
        with self._tx() as c:
            _put(c, "settings", {"key": key, "value": value})

    def prune(self, now=None, retention_days: int = 30) -> dict[str, int]:
        """Delete old events, then empty episodes, then empty tasks; return the counts."""
        cutoff = _fmt(datetime.fromisoformat(_stamp(now)) - timedelta(days=retention_days))
        with self._tx() as c:
            events = c.execute("DELETE FROM events WHERE observed_at < ?", (cutoff,)).rowcount
            episodes, tasks = _drop_orphans(c)
        return {"events": events, "episodes": episodes, "tasks": tasks}

    def status(self) -> dict:
        """Counts, open questions and the last pass and refit times."""
        with self._tx(False) as c:
            row = c.execute(STATUS_SQL).fetchone()
        return dict(row)

    # ------------------------------------------------------------ review page and receive

    def batch_ack(self, batch_id: str) -> dict | None:
        """The ack stored for a batch, or None."""
        with self._tx(False) as c:
            row = c.execute("SELECT ack FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return None if row is None else json.loads(row["ack"])

    def record_batch(self, batch_id: str, ack: dict, now=None) -> None:
        """Keep a batch's ack for replay; the first ack for an id wins."""
        with self._tx() as c:
            _put(c, "batches", {"batch_id": batch_id, "received_at": _stamp(now), "ack": ack},
                 "INSERT OR IGNORE")

    def draft(self, draft_id: str) -> dict | None:
        """One draft with its workflow decoded, or None."""
        with self._tx(False) as c:
            return _draft(c.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone())

    def drafts(self) -> list[dict]:
        """Every draft, newest first."""
        with self._tx(False) as c:
            rows = c.execute("SELECT * FROM drafts ORDER BY created_at DESC, id DESC").fetchall()
        return [_draft(r) for r in rows]

    def put_draft(self, draft: dict, now=None) -> bool:
        """Insert a draft or replace its workflow; return True when it is new.

        A new draft needs its task at task_revision (NotFound, StaleRevision) and becomes the
        task's draft_id.
        """
        at = _stamp(now)
        with self._tx() as c:
            if c.execute("SELECT 1 FROM drafts WHERE id = ?", (draft["id"],)).fetchone():
                c.execute("UPDATE drafts SET workflow = ?, updated_at = ? WHERE id = ?",
                          (_dump(draft["workflow"]), at, draft["id"]))
                return False
            task = c.execute("SELECT revision FROM tasks WHERE id = ?",
                             (draft["task_id"],)).fetchone()
            if task is None:
                raise NotFound(draft["task_id"])
            if task["revision"] != draft["task_revision"]:
                raise StaleRevision(draft["task_id"])
            _put(c, "drafts", {"id": draft["id"], "task_id": draft["task_id"],
                               "task_revision": draft["task_revision"],
                               "status": draft.get("status", "pending_review"),
                               "workflow": _dump(draft["workflow"]), "created_at": at,
                               "updated_at": at}, "INSERT")
            c.execute("UPDATE tasks SET draft_id = ? WHERE id = ?", (draft["id"], draft["task_id"]))
            return True

    def delete_draft(self, draft_id: str) -> bool:
        """Delete a draft and clear the task's link to it; False when it did not exist."""
        with self._tx() as c:
            gone = c.execute("DELETE FROM drafts WHERE id = ?", (draft_id,)).rowcount
            c.execute("UPDATE tasks SET draft_id = NULL WHERE draft_id = ?", (draft_id,))
        return gone == 1

    def add_personal_day(self, day: str, tasks: int, minutes: int) -> None:
        """Add to one day's personal-time total."""
        with self._tx() as c:
            _add_personal(c, day, tasks, minutes)

    def personal_time(self, day: str) -> dict:
        """One day's personal-time total: {tasks, minutes}."""
        with self._tx(False) as c:
            row = c.execute("SELECT tasks, minutes FROM personal_days WHERE day = ?",
                            (day,)).fetchone()
        return {"tasks": row["tasks"] if row else 0, "minutes": row["minutes"] if row else 0}

    def delete_personal_task(self, task_id: str, day: str) -> int:
        """Delete a possibly personal task with its actions, episodes, decisions and drafts.

        Only its minutes are kept, added to `day`. Raises NotFound, or Conflict when the task
        is not marked possibly personal. Returns the minutes added.
        """
        mine = ("SELECT event_id FROM events WHERE episode_id IN "
                "(SELECT id FROM episodes WHERE task_id = ?)")
        with self._tx() as c:
            task = c.execute("SELECT possibly_personal FROM tasks WHERE id = ?",
                             (task_id,)).fetchone()
            if task is None:
                raise NotFound(task_id)
            if not task["possibly_personal"]:
                raise Conflict(task_id)
            seconds = sum(max(0.0, (datetime.fromisoformat(b) - datetime.fromisoformat(a))
                              .total_seconds()) for a, b in c.execute(
                "SELECT first_action_at, last_action_at FROM episodes WHERE task_id = ?",
                (task_id,)))
            c.execute(f"DELETE FROM decisions WHERE left_event_id IN ({mine}) "
                      f"OR right_event_id IN ({mine})", (task_id, task_id))
            c.execute(f"DELETE FROM events WHERE event_id IN ({mine})", (task_id,))
            for table, column in (("episodes", "task_id"), ("drafts", "task_id"), ("tasks", "id")):
                c.execute(f"DELETE FROM {table} WHERE {column} = ?", (task_id,))
            minutes = int(seconds // 60)
            _add_personal(c, day, 1, minutes)
        return minutes

    def task_apps(self, task_ids: list[str]) -> dict[str, list[str]]:
        """One display name per app of each task, most used app first.

        The name is the most frequent non-empty app_name of the bundle id, else the bundle id.
        """
        if not task_ids:
            return {}
        uses: dict[str, dict[str, int]] = {}
        names: dict[tuple[str, str], dict[str, int]] = {}
        with self._tx(False) as c:
            for r in c.execute("SELECT e.task_id, v.app, v.app_name, COUNT(*) AS n FROM events v "
                               "JOIN episodes e ON e.id = v.episode_id "
                               f"WHERE e.task_id IN ({_marks(len(task_ids))}) "
                               "GROUP BY e.task_id, v.app, v.app_name", task_ids):
                per_task = uses.setdefault(r["task_id"], {})
                per_task[r["app"]] = per_task.get(r["app"], 0) + r["n"]
                if r["app_name"] and r["app_name"].strip():
                    named = names.setdefault((r["task_id"], r["app"]), {})
                    named[r["app_name"]] = named.get(r["app_name"], 0) + r["n"]
        out: dict[str, list[str]] = {}
        for task_id, apps in uses.items():
            for app in sorted(apps, key=lambda a: (-apps[a], a)):
                named = names.get((task_id, app))
                out.setdefault(task_id, []).append(
                    min(named, key=lambda n: (-named[n], n)) if named else app)
        return out

    def task_processes(self, task_ids: list[str]) -> dict[str, dict]:
        """The newest process with two or more episodes that each task takes part in."""
        if not task_ids:
            return {}
        out: dict[str, dict] = {}
        with self._tx(False) as c:
            for r in c.execute("SELECT e.task_id, p.id, p.name FROM episodes e JOIN processes p "
                               f"ON p.id = e.process_id WHERE e.task_id IN ({_marks(len(task_ids))})"
                               " AND (SELECT COUNT(*) FROM episodes x WHERE x.process_id = p.id) "
                               ">= 2 ORDER BY e.last_action_at DESC", task_ids):
                out.setdefault(r["task_id"], {"id": r["id"], "name": r["name"]})
        return out

    def episode_cases(self, episode_ids: list[str]) -> dict[str, dict]:
        """Each episode's task id, task title, span and close reason."""
        if not episode_ids:
            return {}
        with self._tx(False) as c:
            rows = c.execute("SELECT e.id AS episode_id, e.task_id, t.title, e.started_at, "
                             "e.ended_at, e.close_reason FROM episodes e JOIN tasks t "
                             f"ON t.id = e.task_id WHERE e.id IN ({_marks(len(episode_ids))})",
                             episode_ids).fetchall()
        return {r["episode_id"]: dict(r) for r in rows}

    def label_counts(self) -> dict[str, dict[str, int]]:
        """Label counts by kind, then source."""
        out: dict[str, dict[str, int]] = {}
        with self._tx(False) as c:
            for r in c.execute("SELECT kind, source, COUNT(*) AS n FROM labels "
                               "GROUP BY kind, source"):
                out.setdefault(r["kind"], {})[r["source"]] = r["n"]
        return out
