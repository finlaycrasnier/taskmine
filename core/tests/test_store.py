"""Tests for taskmine.store, written from CONTRACTS.md sections 3 to 5 and grouper spec 8.1, 8.2.

Shapes the spec left open are fixed here; the implementer follows these tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from taskmine.store import (
    BadOperation,
    Conflict,
    NotFound,
    PassResult,
    StaleRevision,
    Store,
)

BASE = datetime(2026, 10, 20, 9, 0, 0, tzinfo=UTC)
HEX32 = re.compile(r"^[0-9a-f]{32}$")
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")

TABLES = {
    "events": {
        "event_id", "revision", "provenance", "kind", "observed_at", "first_seen_at",
        "last_seen_at", "app", "app_name", "title", "url", "document_id", "window_ref",
        "interaction", "text", "metadata", "sightings", "duration_s", "case_keys",
        "artifact_key", "surface_key", "window_key", "episode_id", "received_at",
    },
    "episodes": {
        "id", "task_id", "started_at", "ended_at", "first_action_at", "last_action_at",
        "close_reason", "settled", "trivial", "diversion", "signature", "process_id", "revision",
    },
    "tasks": {
        "id", "title", "name_basis", "summary", "lifecycle", "locked", "possibly_personal",
        "revision", "started_at", "last_seen_at", "case_keys", "draft_id",
    },
    "decisions": {
        "key", "left_event_id", "right_event_id", "at", "features", "p_join", "choice", "basis",
        "reason", "hard", "asked_at", "ask_context", "question_status", "answer", "answered_at",
    },
    "labels": {"id", "at", "kind", "key", "source", "label", "features"},
    "processes": {
        "id", "name", "signature", "steps", "templates", "score", "basis", "created_at",
        "updated_at",
    },
    "names": {"signature", "verb", "object", "system", "title", "basis", "reusable", "updated_at"},
    "models": {"kind", "version", "created_at", "weights", "examples", "agreement", "basis"},
    "ask_stats": {"context", "alpha", "beta", "asks", "updated_at"},
    "drafts": {
        "id", "task_id", "task_revision", "status", "workflow", "created_at", "updated_at",
    },
    "personal_days": {"day", "tasks", "minutes"},
    "batches": {"batch_id", "received_at", "ack"},
    "settings": {"key", "value"},
}


# ---------------------------------------------------------------- helpers


def ts(seconds: float) -> str:
    return (BASE + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def hid(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


def make_event(**overrides) -> dict:
    """One already-checked event: the wire shape plus the derived columns, flat at the top level.

    `text`, `kind` and `metadata` overrides go into `content`; every other key is top level.
    The text hash follows the text unless `text_sha256` is given.
    """
    text = overrides.pop("text", "Invoice INV-1042 needs approval before payment.")
    text_hash = overrides.pop("text_sha256", sha(text))
    kind = overrides.pop("kind", "screen_text")
    extra_meta = overrides.pop("metadata", {})
    event_id = overrides.pop("event_id", "desktop-e1")
    observed = overrides.pop("observed_at", ts(0))
    metadata = {
        "app_bundle_id": overrides.get("app", "com.apple.TextEdit"),
        "app_name": overrides.get("app_name", "TextEdit"),
        "window_title": overrides.get("title", "Invoice review"),
        "window_ref": "window:6f1c2e9a-0000-4000-8000-000000000001",
        "sighting_count": overrides.get("sightings", 1),
        "duration_s": overrides.get("duration_s", 0),
    }
    metadata.update(extra_meta)
    event = {
        "schema": "taskmine.event/1",
        "event_id": event_id,
        "revision": 1,
        "source_id": "mac-test",
        "observed_at": observed,
        "first_seen_at": observed,
        "last_seen_at": observed,
        "provenance": "synthetic",
        "content": {"type": kind, "text": text, "text_sha256": text_hash, "metadata": metadata},
        # derived columns
        "app": "com.apple.TextEdit",
        "app_name": "TextEdit",
        "title": "Invoice review",
        "url": None,
        "document_id": None,
        "window_ref": "window:6f1c2e9a-0000-4000-8000-000000000001",
        "interaction": None,
        "sightings": 1,
        "duration_s": 0,
        "case_keys": [],
        "artifact_key": None,
        "surface_key": None,
        "window_key": None,
    }
    event.update(overrides)
    return event


STEPS_EDIT = [{"kind": "edit", "app": "com.apple.TextEdit", "evidence": []}]
STEPS_BROWSE = [{"kind": "browse", "app": "com.apple.Safari", "evidence": []}]


def episode(task_id: str, events: list[dict], *, close_reason: str | None = "quiet",
            process_id: str | None = None, steps: list | None = None) -> dict:
    ids = sorted(e["event_id"] for e in events)
    first = min(e["observed_at"] for e in events)
    last = max(e["last_seen_at"] for e in events)
    steps = STEPS_EDIT if steps is None else steps
    return {
        "id": hashlib.sha256("\n".join(ids).encode()).hexdigest()[:32],
        "task_id": task_id,
        "started_at": first,
        "ended_at": last,
        "first_action_at": first,
        "last_action_at": last,
        "close_reason": close_reason,
        "trivial": 0,
        "diversion": 0,
        "signature": {"steps": steps, "apps": sorted({s["app"] for s in steps}), "artifacts": []},
        "process_id": process_id,
        "revision": 1,
    }


def task_row(task_id: str, title: str | None, episodes: list[dict]) -> dict:
    return {
        "id": task_id,
        "title": title,
        "name_basis": "rules",
        "summary": None,
        "lifecycle": "active",
        "locked": 0,
        "possibly_personal": 0,
        "revision": 1,
        "started_at": min(e["started_at"] for e in episodes),
        "last_seen_at": max(e["last_action_at"] for e in episodes),
        "case_keys": [],
        "draft_id": None,
    }


def decision(left: str, right: str, at: str, *, status: str | None = None,
             asked_at: str | None = None) -> dict:
    return {
        "key": hid("decision", left, right),
        "left_event_id": left,
        "right_event_id": right,
        "at": at,
        "features": {"gap_s": 42.0, "recent": 1.0, "same_app": 0.0},
        "p_join": 0.5,
        "choice": "break",
        "basis": "scorer",
        "reason": "unsure",
        "hard": 0,
        "asked_at": asked_at,
        "ask_context": "near:work:seen" if status else None,
        "question_status": status,
        "answer": None,
        "answered_at": None,
    }


def placements(*episode_event_pairs: tuple[dict, list[dict]]) -> dict[str, str]:
    out = {}
    for ep, events in episode_event_pairs:
        for e in events:
            out[e["event_id"]] = ep["id"]
    return out


def result(*, episodes=(), tasks=(), processes=(), decisions=(), places=None,
           settled=True) -> PassResult:
    return PassResult(
        episodes=list(episodes),
        tasks=list(tasks),
        processes=list(processes),
        decisions=list(decisions),
        placements=dict(places or {}),
        settled=settled,
    )


def db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def rows(path, sql, *args):
    with closing(db(path)) as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def snapshot(path) -> dict:
    out = {}
    with closing(db(path)) as conn:
        for table in TABLES:
            cur = conn.execute(f"SELECT * FROM {table}")
            out[table] = sorted(json.dumps(dict(r), sort_keys=True) for r in cur.fetchall())
    return out


def count(path, table: str) -> int:
    return rows(path, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def path(tmp_path):
    return tmp_path / "taskmine.db"


@pytest.fixture
def store(path):
    return Store(path)


@pytest.fixture
def world(store, path):
    """Three settled tasks.

    A: episodes A1 (a1, a2) and A2 (a3). B: episode B1 (b1, b2). C: episode C1 (c1).
    Decisions: a1->a3 (inside A), a3->b1, b2->a2, b2->c1, c1->a3.
    """
    ev = {
        "a1": make_event(event_id="desktop-a1", observed_at=ts(0), title="Invoice INV-1"),
        "a2": make_event(event_id="desktop-a2", observed_at=ts(60), title="Invoice INV-1"),
        "b1": make_event(event_id="desktop-b1", observed_at=ts(300), title="Order PO-7",
                         app="com.apple.Safari", app_name="Safari"),
        "b2": make_event(event_id="desktop-b2", observed_at=ts(360), title="Order PO-7",
                         app="com.apple.Safari", app_name="Safari"),
        "a3": make_event(event_id="desktop-a3", observed_at=ts(600), title="Invoice INV-1"),
        "c1": make_event(event_id="desktop-c1", observed_at=ts(900), title="Notes"),
    }
    store.put_events(list(ev.values()), now=ts(1000))
    ta, tb, tc = hid("task", "desktop-a1"), hid("task", "desktop-b1"), hid("task", "desktop-c1")
    a1 = episode(ta, [ev["a1"], ev["a2"]], close_reason="diversion")
    a2 = episode(ta, [ev["a3"]])
    b1 = episode(tb, [ev["b1"], ev["b2"]], steps=STEPS_EDIT)
    c1 = episode(tc, [ev["c1"]], steps=STEPS_BROWSE)
    d = {
        "aa": decision("desktop-a1", "desktop-a3", ts(600)),
        "ab": decision("desktop-a3", "desktop-b1", ts(610)),
        "ba": decision("desktop-b2", "desktop-a2", ts(620)),
        "bc": decision("desktop-b2", "desktop-c1", ts(900)),
        "ca": decision("desktop-c1", "desktop-a3", ts(910)),
    }
    store.write_pass(result(
        episodes=[a1, a2, b1, c1],
        tasks=[task_row(ta, "Invoice A", [a1, a2]), task_row(tb, "Order B", [b1]),
               task_row(tc, "Notes C", [c1])],
        decisions=list(d.values()),
        places=placements((a1, [ev["a1"], ev["a2"]]), (a2, [ev["a3"]]),
                          (b1, [ev["b1"], ev["b2"]]), (c1, [ev["c1"]])),
        settled=True,
    ))
    return SimpleNamespace(ev=ev, A=ta, B=tb, C=tc, a1=a1, a2=a2, b1=b1, c1=c1, d=d)


def correction_labels(path) -> list[dict]:
    return rows(path, "SELECT * FROM labels WHERE source = 'correction' ORDER BY id")


# ---------------------------------------------------------------- schema


def test_schema_has_exactly_the_thirteen_tables(store, path):
    found = {r["name"] for r in rows(
        path, "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    assert found == set(TABLES)
    for table, columns in TABLES.items():
        got = {r["name"] for r in rows(path, f"PRAGMA table_info({table})")}
        assert got == columns, table


def test_file_mode_is_0600_and_journal_is_wal(store, path):
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with closing(db(path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_reopen_keeps_data(path):
    Store(path).set_setting("k", "v")
    assert Store(path).setting("k") == "v"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# ---------------------------------------------------------------- put_events


def test_put_events_new_is_stored(store):
    ack = store.put_events([make_event(event_id="desktop-1"), make_event(event_id="desktop-2")],
                           now=ts(10))
    assert ack == {
        "accepted": [
            {"event_id": "desktop-1", "revision": 1, "status": "stored"},
            {"event_id": "desktop-2", "revision": 1, "status": "stored"},
        ],
        "rejected": [],
    }


def test_put_events_same_revision_is_duplicate(store, path):
    store.put_events([make_event(event_id="desktop-1")], now=ts(10))
    ack = store.put_events([make_event(event_id="desktop-1", text="changed")], now=ts(20))
    assert ack["accepted"] == [{"event_id": "desktop-1", "revision": 1, "status": "duplicate"}]
    assert rows(path, "SELECT text FROM events")[0]["text"] != "changed"


def test_put_events_duplicate_inside_one_call(store):
    e = make_event(event_id="desktop-1")
    ack = store.put_events([e, dict(e)], now=ts(10))
    assert [a["status"] for a in ack["accepted"]] == ["stored", "duplicate"]


def test_put_events_lower_revision_is_stale(store, path):
    store.put_events([make_event(event_id="desktop-1", revision=3, text="third")], now=ts(10))
    ack = store.put_events([make_event(event_id="desktop-1", revision=2, text="second")],
                           now=ts(20))
    assert ack["accepted"] == [{"event_id": "desktop-1", "revision": 2, "status": "stale"}]
    row = rows(path, "SELECT revision, text FROM events")[0]
    assert row == {"revision": 3, "text": "third"}


def test_put_events_writes_columns_and_received_at(store, path):
    e = make_event(event_id="desktop-1", observed_at=ts(5), last_seen_at=ts(35),
                   case_keys=[["case", "ab12"]], interaction=None, kind="screen_text")
    store.put_events([e], now=ts(100))
    row = rows(path, "SELECT * FROM events")[0]
    assert row["kind"] == "screen_text"
    assert row["provenance"] == "synthetic"
    assert row["app"] == "com.apple.TextEdit"
    assert row["title"] == "Invoice review"
    assert row["observed_at"] == ts(5) and row["last_seen_at"] == ts(35)
    assert row["received_at"] == ts(100)
    assert row["episode_id"] is None
    assert json.loads(row["case_keys"]) == [["case", "ab12"]]
    meta = json.loads(row["metadata"])
    assert meta["text_sha256"] == sha(e["content"]["text"])
    assert meta["window_title"] == "Invoice review"


def test_higher_revision_replaces_only_revision_fields(store):
    first = make_event(event_id="desktop-1", observed_at=ts(0), last_seen_at=ts(30),
                       text="first text", sightings=1, duration_s=0)
    store.put_events([first], now=ts(40))
    second = make_event(
        event_id="desktop-1", revision=2, observed_at=ts(50), first_seen_at=ts(50),
        last_seen_at=ts(120), text="second text", sightings=4, duration_s=90,
        title="Other title", app="com.other", app_name="Other",
        metadata={"window_title": "Other title", "sighting_count": 4, "duration_s": 90,
                  "text_revision": 2, "value_omitted": True},
    )
    ack = store.put_events([second], now=ts(130))
    assert ack["accepted"] == [{"event_id": "desktop-1", "revision": 2, "status": "stored"}]

    row = store.unplaced_actions()[0]
    assert row["revision"] == 2
    assert row["text"] == "second text"
    assert row["metadata"]["text_sha256"] == sha("second text")
    assert row["sightings"] == 4
    assert row["duration_s"] == 90
    assert row["last_seen_at"] == ts(120)
    assert row["metadata"]["sighting_count"] == 4
    assert row["metadata"]["duration_s"] == 90
    assert row["metadata"]["text_revision"] == 2
    assert row["metadata"]["value_omitted"] is True
    # everything else stays as first stored
    assert row["title"] == "Invoice review"
    assert row["app"] == "com.apple.TextEdit"
    assert row["app_name"] == "TextEdit"
    assert row["observed_at"] == ts(0)
    assert row["first_seen_at"] == ts(0)
    assert row["metadata"]["window_title"] == "Invoice review"
    assert row["received_at"] == ts(40)


def test_last_seen_at_never_moves_backwards(store):
    store.put_events([make_event(event_id="desktop-1", last_seen_at=ts(120))], now=ts(130))
    store.put_events([make_event(event_id="desktop-1", revision=2, last_seen_at=ts(60),
                                 text="newer")], now=ts(140))
    row = store.unplaced_actions()[0]
    assert row["revision"] == 2
    assert row["text"] == "newer"
    assert row["last_seen_at"] == ts(120)


def test_higher_revision_keeps_placement(world, store, path):
    e = make_event(event_id="desktop-a1", revision=2, text="edited", last_seen_at=ts(30))
    store.put_events([e], now=ts(2000))
    row = rows(path, "SELECT episode_id, text FROM events WHERE event_id = 'desktop-a1'")[0]
    assert row == {"episode_id": world.a1["id"], "text": "edited"}


# ---------------------------------------------------------------- unplaced and placed


def test_unplaced_actions_oldest_first_with_limit(store):
    store.put_events([
        make_event(event_id="desktop-c", observed_at=ts(30)),
        make_event(event_id="desktop-a", observed_at=ts(10)),
        make_event(event_id="desktop-b", observed_at=ts(20)),
    ], now=ts(40))
    got = store.unplaced_actions()
    assert [a["event_id"] for a in got] == ["desktop-a", "desktop-b", "desktop-c"]
    assert isinstance(got[0]["metadata"], dict)
    assert got[0]["case_keys"] == []
    assert [a["event_id"] for a in store.unplaced_actions(limit=2)] == ["desktop-a", "desktop-b"]


def test_placed_events_leave_unplaced(store):
    e1 = make_event(event_id="desktop-1", observed_at=ts(0))
    e2 = make_event(event_id="desktop-2", observed_at=ts(10))
    store.put_events([e1, e2], now=ts(20))
    t = hid("task", "desktop-1")
    ep = episode(t, [e1])
    store.write_pass(result(episodes=[ep], tasks=[task_row(t, "T", [ep])],
                            places=placements((ep, [e1])), settled=False))
    assert [a["event_id"] for a in store.unplaced_actions()] == ["desktop-2"]


# ---------------------------------------------------------------- reopenable_episode


def _settled_episodes(store, specs):
    """specs: list of (event_id, at_seconds, close_reason). One task per episode."""
    eps, tasks, places = [], [], {}
    events = [make_event(event_id=eid, observed_at=ts(at)) for eid, at, _ in specs]
    store.put_events(events, now=ts(10_000))
    for e, (_, _, reason) in zip(events, specs):
        t = hid("task", e["event_id"])
        ep = episode(t, [e], close_reason=reason)
        eps.append(ep)
        tasks.append(task_row(t, "T", [ep]))
        places[e["event_id"]] = ep["id"]
    store.write_pass(result(episodes=eps, tasks=tasks, places=places, settled=True))
    return eps


def test_reopenable_episode_quiet_within_600s(store):
    eps = _settled_episodes(store, [("desktop-1", 0, "quiet")])
    assert store.reopenable_episode(ts(600))["id"] == eps[0]["id"]
    assert store.reopenable_episode(ts(601)) is None


def test_reopenable_episode_reasons(store):
    _settled_episodes(store, [("desktop-1", 0, "idle"), ("desktop-2", 100, "break"),
                              ("desktop-3", 200, "paused")])
    assert store.reopenable_episode(ts(300)) is None


def test_reopenable_episode_newest_qualifying(store):
    eps = _settled_episodes(store, [("desktop-1", 0, "quiet"), ("desktop-2", 100, "diversion"),
                                    ("desktop-3", 200, "idle")])
    assert store.reopenable_episode(ts(300))["id"] == eps[1]["id"]


def test_reopenable_episode_ignores_provisional(store):
    e = make_event(event_id="desktop-1", observed_at=ts(0))
    store.put_events([e], now=ts(10))
    t = hid("task", "desktop-1")
    ep = episode(t, [e], close_reason="quiet")
    store.write_pass(result(episodes=[ep], tasks=[task_row(t, "T", [ep])],
                            places=placements((ep, [e])), settled=False))
    assert store.reopenable_episode(ts(60)) is None


# ---------------------------------------------------------------- write_pass


def test_write_pass_provisional_rows_are_replaced(store, path):
    evs = [make_event(event_id=f"desktop-{i}", observed_at=ts(i * 10)) for i in range(4)]
    store.put_events(evs, now=ts(100))
    ta = hid("task", "desktop-0")
    p1, p2 = episode(ta, evs[:2], close_reason=None), episode(ta, evs[2:], close_reason=None)
    store.write_pass(result(episodes=[p1, p2], tasks=[task_row(ta, "T", [p1, p2])],
                            places=placements((p1, evs[:2]), (p2, evs[2:])), settled=False))
    assert {r["id"] for r in rows(path, "SELECT id FROM episodes")} == {p1["id"], p2["id"]}
    assert all(r["settled"] == 0 for r in rows(path, "SELECT settled FROM episodes"))

    p3 = episode(ta, evs[:3], close_reason=None)
    store.write_pass(result(episodes=[p3], tasks=[task_row(ta, "T", [p3])],
                            places=placements((p3, evs[:3])), settled=False))
    assert [r["id"] for r in rows(path, "SELECT id FROM episodes")] == [p3["id"]]
    placed = {r["event_id"]: r["episode_id"] for r in rows(path, "SELECT * FROM events")}
    assert placed == {"desktop-0": p3["id"], "desktop-1": p3["id"], "desktop-2": p3["id"],
                      "desktop-3": None}
    assert [a["event_id"] for a in store.unplaced_actions()] == ["desktop-3"]


def test_write_pass_drops_task_left_without_episodes(store):
    e = make_event(event_id="desktop-1")
    store.put_events([e], now=ts(10))
    ta, tb = hid("task", "x"), hid("task", "y")
    pa, pb = episode(ta, [e], close_reason=None), episode(tb, [e], close_reason=None)
    pb["id"] = hid("other", pb["id"])
    store.write_pass(result(episodes=[pa], tasks=[task_row(ta, "A", [pa])],
                            places=placements((pa, [e])), settled=False))
    store.write_pass(result(episodes=[pb], tasks=[task_row(tb, "B", [pb])],
                            places=placements((pb, [e])), settled=False))
    assert store.task(ta) is None
    assert store.task(tb)["title"] == "B"


def test_write_pass_keeps_settled_rows(store, path):
    evs = [make_event(event_id=f"desktop-{i}", observed_at=ts(i * 10)) for i in range(4)]
    store.put_events(evs, now=ts(100))
    ta, tb = hid("task", "desktop-0"), hid("task", "desktop-2")
    s1 = episode(ta, evs[:2], close_reason="quiet")
    store.write_pass(result(episodes=[s1], tasks=[task_row(ta, "Settled", [s1])],
                            places=placements((s1, evs[:2])), settled=True))
    assert rows(path, "SELECT settled FROM episodes")[0]["settled"] == 1

    p4 = episode(tb, evs[2:], close_reason=None)
    store.write_pass(result(episodes=[p4], tasks=[task_row(tb, "Open", [p4])],
                            places=placements((p4, evs[2:])), settled=False))
    p5 = episode(tb, evs[2:3], close_reason=None)
    store.write_pass(result(episodes=[p5], tasks=[task_row(tb, "Open", [p5])],
                            places=placements((p5, evs[2:3])), settled=False))

    got = {r["id"]: r["settled"] for r in rows(path, "SELECT id, settled FROM episodes")}
    assert got == {s1["id"]: 1, p5["id"]: 0}
    placed = {r["event_id"]: r["episode_id"] for r in rows(path, "SELECT * FROM events")}
    assert placed["desktop-0"] == s1["id"] and placed["desktop-1"] == s1["id"]
    assert placed["desktop-2"] == p5["id"] and placed["desktop-3"] is None
    assert store.task(ta)["title"] == "Settled"


def test_write_pass_rerun_writes_identical_rows(store, path):
    evs = [make_event(event_id=f"desktop-{i}", observed_at=ts(i * 10)) for i in range(3)]
    store.put_events(evs, now=ts(100))
    ta = hid("task", "desktop-0")
    ep = episode(ta, evs)
    proc = {"id": hid("process", "p"), "name": None, "signature": {"steps": STEPS_EDIT},
            "steps": STEPS_EDIT, "templates": [], "score": 0.9, "basis": "rules",
            "created_at": ts(100), "updated_at": ts(100)}
    res = result(episodes=[ep], tasks=[task_row(ta, "T", [ep])], processes=[proc],
                 decisions=[decision("desktop-0", "desktop-1", ts(10))],
                 places=placements((ep, evs)), settled=True)
    store.write_pass(res)
    first = snapshot(path)
    store.write_pass(res)
    assert snapshot(path) == first

    prov = result(episodes=[ep], tasks=[task_row(ta, "T", [ep])],
                  places=placements((ep, evs)), settled=False)
    other = Store(path.parent / "second.db")
    other.put_events(evs, now=ts(100))
    other.write_pass(prov)
    other.write_pass(prov)
    assert [r["id"] for r in rows(path.parent / "second.db", "SELECT id FROM episodes")] == [
        ep["id"]]


def test_write_pass_keeps_answered_question(world, store, path):
    q = decision("desktop-a3", "desktop-b1", ts(610), status="open", asked_at=ts(700))
    store.write_pass(result(decisions=[q], settled=True))
    store.answer(q["key"], "one", now=ts(800))
    store.write_pass(result(decisions=[q], settled=True))
    row = rows(path, "SELECT question_status, answer FROM decisions WHERE key = ?", q["key"])[0]
    assert row == {"question_status": "answered", "answer": "one"}


def test_write_pass_respects_locked_task(world, store):
    store.correct(world.A, 1, {"op": "rename", "title": "My invoice"})
    again = task_row(world.A, "Rules title", [world.a1, world.a2])
    store.write_pass(result(episodes=[world.a1, world.a2], tasks=[again], settled=True))
    t = store.task(world.A)
    assert t["title"] == "My invoice"
    assert t["name_basis"] == "person"
    assert t["locked"] == 1
    assert t["revision"] == 2


# ---------------------------------------------------------------- tasks and paging


def _many_tasks(store, n):
    """n tasks, one event each; tasks 3 and 4 share a last_seen_at to test the id tiebreak."""
    ids = []
    for i in range(n):
        at = ts(i * 100) if i != 4 else ts(300)
        e = make_event(event_id=f"desktop-{i}", observed_at=at)
        store.put_events([e], now=ts(1000))
        t = hid("task", e["event_id"])
        ep = episode(t, [e])
        store.write_pass(result(episodes=[ep], tasks=[task_row(t, f"Task {i}", [ep])],
                                places=placements((ep, [e])), settled=True))
        ids.append((at, t))
    return [t for _, t in sorted(ids, reverse=True)]


def test_tasks_ordering_and_two_pages(store):
    expected = _many_tasks(store, 5)
    page1, cursor = store.tasks(limit=3)
    assert [t["id"] for t in page1] == expected[:3]
    assert isinstance(cursor, str) and cursor
    page2, cursor2 = store.tasks(limit=3, before=cursor)
    assert [t["id"] for t in page2] == expected[3:]
    assert cursor2 is None


def test_tasks_exact_page_has_no_cursor(store):
    expected = _many_tasks(store, 3)
    page, cursor = store.tasks(limit=3)
    assert [t["id"] for t in page] == expected
    assert cursor is None


def test_tasks_bad_cursor(store):
    _many_tasks(store, 2)
    with pytest.raises(ValueError):
        store.tasks(limit=1, before="not-a-cursor")


def test_task_shape(world, store):
    t = store.task(world.A)
    assert t["id"] == world.A
    assert t["title"] == "Invoice A"
    assert t["name_basis"] == "rules"
    assert t["lifecycle"] == "active"
    assert not t["locked"]
    assert t["revision"] == 1
    assert t["started_at"] == ts(0)
    assert t["last_seen_at"] == ts(600)
    assert t["case_keys"] == []
    assert t["episode_count"] == 2
    assert t["action_count"] == 3
    assert store.task(hid("task", "missing")) is None


def test_task_actions_newest_first_with_paging(world, store):
    episodes, actions, cursor = store.task_actions(world.A, limit=2)
    assert [e["id"] for e in episodes] == [world.a2["id"], world.a1["id"]]
    assert episodes[0]["signature"]["steps"] == STEPS_EDIT
    assert [a["event_id"] for a in actions] == ["desktop-a3", "desktop-a2"]
    assert all(a["episode_id"] for a in actions)
    assert cursor
    episodes2, actions2, cursor2 = store.task_actions(world.A, limit=2, before=cursor)
    assert [e["id"] for e in episodes2] == [world.a2["id"], world.a1["id"]]
    assert [a["event_id"] for a in actions2] == ["desktop-a1"]
    assert cursor2 is None


def test_task_actions_missing_task(store):
    with pytest.raises(NotFound):
        store.task_actions(hid("task", "missing"))


# ---------------------------------------------------------------- corrections


def test_rename(world, store, path):
    t = store.correct(world.A, 1, {"op": "rename", "title": "  Review   invoice\n INV-1 "})
    assert t["id"] == world.A
    assert t["title"] == "Review invoice INV-1"
    assert t["name_basis"] == "person"
    assert t["locked"] == 1
    assert t["possibly_personal"] == 0
    assert t["revision"] == 2
    labels = correction_labels(path)
    assert len(labels) == 1
    lab = labels[0]
    assert (lab["kind"], lab["label"]) == ("name", "renamed")
    assert HEX32.match(lab["key"])
    assert ISO_Z.match(lab["at"])
    assert "invoice" not in json.dumps(lab).lower()
    name = rows(path, "SELECT * FROM names WHERE signature = ?", lab["key"])
    assert len(name) == 1
    assert name[0]["title"] == "Review invoice INV-1"
    assert name[0]["basis"] == "person"
    assert name[0]["reusable"] == 1


def test_rename_rejects_empty_and_long_titles(world, store, path):
    with pytest.raises(BadOperation):
        store.correct(world.A, 1, {"op": "rename", "title": "   "})
    with pytest.raises(BadOperation):
        store.correct(world.A, 1, {"op": "rename", "title": "x" * 241})
    assert store.task(world.A)["revision"] == 1
    assert correction_labels(path) == []


def test_revision_increments_and_stale(world, store):
    store.correct(world.A, 1, {"op": "rename", "title": "One"})
    with pytest.raises(StaleRevision):
        store.correct(world.A, 1, {"op": "rename", "title": "Two"})
    t = store.correct(world.A, 2, {"op": "rename", "title": "Two"})
    assert t["revision"] == 3 and t["title"] == "Two"


def test_merge(world, store, path):
    t = store.correct(world.A, 1, {"op": "merge", "other_task_id": world.B})
    assert t["id"] == world.A
    assert t["locked"] == 1 and t["revision"] == 2
    assert t["episode_count"] == 3 and t["action_count"] == 5
    assert t["started_at"] == ts(0) and t["last_seen_at"] == ts(600)
    assert store.task(world.B) is None
    task_of = {r["id"]: r["task_id"] for r in rows(path, "SELECT id, task_id FROM episodes")}
    assert task_of[world.b1["id"]] == world.A
    labels = correction_labels(path)
    assert {(lab["kind"], lab["key"], lab["label"]) for lab in labels} == {
        ("relation", world.d["ab"]["key"], "same"),
        ("relation", world.d["ba"]["key"], "same"),
    }
    assert all(json.loads(lab["features"]) == world.d["ab"]["features"] for lab in labels)
    basis = {r["key"]: (r["basis"], r["choice"]) for r in rows(path, "SELECT * FROM decisions")}
    assert basis[world.d["ab"]["key"]] == ("person", "same")
    assert basis[world.d["bc"]["key"]] == ("scorer", "break")


def test_merge_checks_other_task(world, store):
    with pytest.raises(NotFound):
        store.correct(world.A, 1, {"op": "merge", "other_task_id": hid("task", "missing")})
    with pytest.raises(BadOperation):
        store.correct(world.A, 1, {"op": "merge", "other_task_id": world.A})
    with pytest.raises(StaleRevision):
        store.correct(world.A, 1, {"op": "merge", "other_task_id": world.B,
                                   "other_expected_revision": 5})
    assert store.task(world.B) is not None
    assert store.task(world.A)["revision"] == 1


def test_split(world, store, path):
    new = store.correct(world.A, 1, {"op": "split", "episode_ids": [world.a2["id"]]})
    assert new["id"] != world.A and HEX32.match(new["id"])
    assert new["title"] == "Split from Invoice A"
    assert new["locked"] == 1
    assert new["episode_count"] == 1
    assert new["started_at"] == ts(600) and new["last_seen_at"] == ts(600)
    source = store.task(world.A)
    assert source["locked"] == 1 and source["revision"] == 2
    assert source["episode_count"] == 1 and source["last_seen_at"] == ts(60)
    task_of = {r["id"]: r["task_id"] for r in rows(path, "SELECT id, task_id FROM episodes")}
    assert task_of[world.a2["id"]] == new["id"]
    assert task_of[world.a1["id"]] == world.A
    labels = correction_labels(path)
    assert [(lab["kind"], lab["key"], lab["label"]) for lab in labels] == [
        ("relation", world.d["aa"]["key"], "break")]


def test_split_untitled_task(store, path):
    e1 = make_event(event_id="desktop-1", observed_at=ts(0))
    e2 = make_event(event_id="desktop-2", observed_at=ts(100))
    store.put_events([e1, e2], now=ts(200))
    t = hid("task", "desktop-1")
    x, y = episode(t, [e1]), episode(t, [e2])
    store.write_pass(result(episodes=[x, y], tasks=[task_row(t, None, [x, y])],
                            places=placements((x, [e1]), (y, [e2])), settled=True))
    new = store.correct(t, 1, {"op": "split", "episode_ids": [y["id"]]})
    assert new["title"] == "Split activity"


def test_split_bad_operations(world, store, path):
    for op in (
        {"op": "split", "episode_ids": []},
        {"op": "split"},
        {"op": "split", "episode_ids": [world.a1["id"], world.a2["id"]]},
        {"op": "split", "episode_ids": [world.b1["id"]]},
    ):
        with pytest.raises(BadOperation):
            store.correct(world.A, 1, op)
    assert store.task(world.A)["revision"] == 1
    assert store.task(world.A)["locked"] == 0
    assert correction_labels(path) == []
    assert count(path, "tasks") == 3


def test_move(world, store, path):
    t = store.correct(world.A, 1, {"op": "move", "episode_ids": [world.a2["id"]],
                                   "target_task_id": world.C})
    assert t["id"] == world.A
    assert t["revision"] == 2 and t["locked"] == 1
    target = store.task(world.C)
    assert target["revision"] == 2 and target["locked"] == 1
    assert target["episode_count"] == 2
    labels = {(lab["key"], lab["label"]) for lab in correction_labels(path)}
    assert labels == {(world.d["aa"]["key"], "break"), (world.d["ca"]["key"], "same")}


def test_move_that_empties_source_returns_target(world, store, path):
    t = store.correct(world.B, 1, {"op": "move", "episode_ids": [world.b1["id"]],
                                   "target_task_id": world.C})
    assert t["id"] == world.C
    assert store.task(world.B) is None
    labels = {(lab["key"], lab["label"]) for lab in correction_labels(path)}
    assert labels == {(world.d["bc"]["key"], "same")}


def test_move_bad_target(world, store):
    with pytest.raises(NotFound):
        store.correct(world.A, 1, {"op": "move", "episode_ids": [world.a2["id"]],
                                   "target_task_id": hid("task", "missing")})
    with pytest.raises(BadOperation):
        store.correct(world.A, 1, {"op": "move", "episode_ids": [world.a2["id"]],
                                   "target_task_id": world.A})
    with pytest.raises(BadOperation):
        store.correct(world.A, 1, {"op": "move", "episode_ids": [],
                                   "target_task_id": world.C})


def test_same_process(world, store, path):
    t = store.correct(world.A, 1, {"op": "same_process", "other_task_id": world.B})
    assert t["id"] == world.A and t["locked"] == 1 and t["revision"] == 2
    other = store.task(world.B)
    assert other["locked"] == 1 and other["revision"] == 2
    assert other["episode_count"] == 1
    labels = correction_labels(path)
    assert len(labels) == 1
    assert labels[0]["kind"] == "match" and labels[0]["label"] == "same"
    assert HEX32.match(labels[0]["key"])
    procs = store.processes()
    assert len(procs) == 1
    assert procs[0]["basis"] == "person"
    assert set(procs[0]["episode_ids"]) == {world.a2["id"], world.b1["id"]}


def test_same_process_without_steps_writes_no_label(world, store, path):
    e = make_event(event_id="desktop-z", observed_at=ts(950))
    store.put_events([e], now=ts(1000))
    tz = hid("task", "desktop-z")
    ez = episode(tz, [e], steps=[])
    store.write_pass(result(episodes=[ez], tasks=[task_row(tz, "Z", [ez])],
                            places=placements((ez, [e])), settled=True))
    store.correct(world.A, 1, {"op": "same_process", "other_task_id": tz})
    assert correction_labels(path) == []
    assert store.task(tz)["locked"] == 1


def test_complete_and_reopen(world, store, path):
    t = store.correct(world.A, 1, {"op": "complete"})
    assert t["lifecycle"] == "completed" and t["locked"] == 1 and t["revision"] == 2
    t = store.correct(world.A, 2, {"op": "reopen"})
    assert t["lifecycle"] == "active" and t["revision"] == 3
    store.correct(world.A, 3, {"op": "rename", "title": "Named"})
    labels = correction_labels(path)
    assert [(lab["kind"], lab["label"]) for lab in labels] == [
        ("complete", "complete"), ("complete", "reopen"), ("name", "renamed")]
    assert labels[0]["key"] == labels[1]["key"] == labels[2]["key"]


def test_correct_not_found_and_unknown_op(world, store):
    with pytest.raises(NotFound):
        store.correct(hid("task", "missing"), 1, {"op": "rename", "title": "x"})
    with pytest.raises(BadOperation):
        store.correct(world.A, 1, {"op": "abandon_ship"})
    with pytest.raises(BadOperation):
        store.correct(world.A, 1, {})


def test_correction_expires_open_questions_of_touched_tasks(world, store):
    qab = decision("desktop-a3", "desktop-b1", ts(610), status="open", asked_at=ts(700))
    qbc = decision("desktop-b2", "desktop-c1", ts(900), status="open", asked_at=ts(950))
    store.write_pass(result(decisions=[qab, qbc], settled=True))
    store.correct(world.C, 1, {"op": "rename", "title": "Notes"})
    assert {q["key"] for q in store.questions()} == {qab["key"]}
    store.correct(world.A, 1, {"op": "complete"})
    assert store.questions() == []


# ---------------------------------------------------------------- questions and answers


@pytest.fixture
def asked(world, store):
    qs = [decision(f"desktop-a{i % 3 + 1}", "desktop-b1", ts(610 + i), status="open",
                   asked_at=ts(700 + i)) for i in range(4)]
    qs[0]["right_event_id"] = "desktop-b2"
    qs[0]["key"] = hid("decision", qs[0]["left_event_id"], "desktop-b2")
    done = decision("desktop-c1", "desktop-a3", ts(910), status="answered", asked_at=ts(800))
    done["answer"], done["answered_at"] = "one", ts(810)
    store.write_pass(result(decisions=qs + [done], settled=True))
    return qs


def test_questions_open_newest_first(world, store, asked):
    got = store.questions()
    assert [q["key"] for q in got] == [asked[3]["key"], asked[2]["key"], asked[1]["key"]]
    q = got[0]
    assert q["left_task_id"] == world.A and q["left_title"] == "Invoice A"
    assert q["right_task_id"] == world.B and q["right_title"] == "Order B"
    assert len(store.questions(limit=10)) == 4


def _label_rows(path, key):
    return rows(path, "SELECT * FROM labels WHERE key = ? ORDER BY id", key)


def test_answer_one(store, path, asked):
    key = asked[3]["key"]
    out = store.answer(key, "one", now=ts(2000))
    assert out["question_status"] == "answered" and out["answer"] == "one"
    labs = _label_rows(path, key)
    assert [(lab["kind"], lab["source"], lab["label"], lab["at"]) for lab in labs] == [
        ("relation", "answer", "same", ts(2000))]
    assert json.loads(labs[0]["features"]) == asked[3]["features"]
    row = rows(path, "SELECT * FROM decisions WHERE key = ?", key)[0]
    assert (row["basis"], row["choice"], row["answered_at"]) == ("person", "same", ts(2000))
    assert key not in {q["key"] for q in store.questions(limit=10)}


def test_answer_two(store, path, asked):
    key = asked[2]["key"]
    store.answer(key, "two", now=ts(2000))
    labs = _label_rows(path, key)
    assert [(lab["source"], lab["label"]) for lab in labs] == [("answer", "break")]
    row = rows(path, "SELECT * FROM decisions WHERE key = ?", key)[0]
    assert (row["question_status"], row["basis"], row["choice"]) == ("answered", "person", "break")


def test_answer_later_snoozes_for_30_minutes(store, path, asked):
    key = asked[1]["key"]
    out = store.answer(key, "later", now=ts(2000))
    assert out["question_status"] == "snoozed"
    row = rows(path, "SELECT * FROM decisions WHERE key = ?", key)[0]
    assert row["question_status"] == "snoozed"
    assert row["asked_at"] == ts(2000 + 1800)
    assert _label_rows(path, key) == []
    assert key not in {q["key"] for q in store.questions(limit=10)}


def test_answer_never_dismisses(store, path, asked):
    key = asked[0]["key"]
    out = store.answer(key, "never", now=ts(2000))
    assert out["question_status"] == "dismissed"
    assert _label_rows(path, key) == []
    assert key not in {q["key"] for q in store.questions(limit=10)}


def test_answer_errors(store, path, asked):
    key = asked[3]["key"]
    with pytest.raises(BadOperation):
        store.answer(key, "maybe", now=ts(2000))
    with pytest.raises(NotFound):
        store.answer(hid("decision", "x", "y"), "one", now=ts(2000))
    store.answer(key, "one", now=ts(2000))
    with pytest.raises(Conflict):
        store.answer(key, "two", now=ts(2001))
    assert [lab["label"] for lab in _label_rows(path, key)] == ["same"]


def test_snoozed_question_can_still_be_answered(store, asked):
    key = asked[1]["key"]
    store.answer(key, "later", now=ts(2000))
    assert store.answer(key, "one", now=ts(4000))["question_status"] == "answered"


# ---------------------------------------------------------------- processes


def test_processes_need_two_episodes(store):
    evs = [make_event(event_id=f"desktop-{i}", observed_at=ts(i * 100)) for i in range(3)]
    store.put_events(evs, now=ts(1000))
    p1, p2 = hid("process", "1"), hid("process", "2")
    eps = [episode(hid("task", e["event_id"]), [e], process_id=pid)
           for e, pid in zip(evs, [p1, p1, p2])]
    procs = [{"id": pid, "name": "Review invoice", "signature": {"steps": STEPS_EDIT},
              "steps": STEPS_EDIT, "templates": ["Invoice {id}"], "score": 0.9,
              "basis": "rules", "created_at": ts(1000), "updated_at": ts(1000)}
             for pid in (p1, p2)]
    store.write_pass(result(
        episodes=eps, tasks=[task_row(ep["task_id"], "T", [ep]) for ep in eps],
        processes=procs, places={e["event_id"]: ep["id"] for e, ep in zip(evs, eps)},
        settled=True))
    got = store.processes()
    assert [p["id"] for p in got] == [p1]
    assert set(got[0]["episode_ids"]) == {eps[0]["id"], eps[1]["id"]}
    assert got[0]["templates"] == ["Invoice {id}"]
    assert got[0]["steps"] == STEPS_EDIT


# ---------------------------------------------------------------- labels, models, ask stats, settings


def test_labels_add_and_filter(store):
    first = store.add_label("relation", "k1", "scripted", "same", {"gap_s": 1.0}, ts(0))
    second = store.add_label("relation", "k2", "answer", "break", None, ts(100))
    store.add_label("match", "k3", "correction", "same", None, ts(200))
    assert isinstance(first, int) and second > first
    got = store.labels("relation")
    assert [(lab["key"], lab["label"]) for lab in got] == [("k1", "same"), ("k2", "break")]
    assert got[0]["features"] == {"gap_s": 1.0}
    assert got[1]["features"] is None
    assert [lab["key"] for lab in store.labels("relation", since=ts(0))] == ["k2"]
    assert [lab["key"] for lab in store.labels("match")] == ["k3"]


def test_labels_are_append_only(store):
    store.add_label("relation", "k1", "answer", "same", None, ts(0))
    store.add_label("relation", "k1", "answer", "break", None, ts(10))
    assert [lab["label"] for lab in store.labels("relation")] == ["same", "break"]


def test_models_keep_last_five(store, path):
    versions = [store.save_model("relation", {"w": [i]}, examples=i, agreement=0.99,
                                 basis="refit", now=ts(i)) for i in range(7)]
    assert versions == [1, 2, 3, 4, 5, 6, 7]
    kept = [r["version"] for r in rows(
        path, "SELECT version FROM models WHERE kind = 'relation' ORDER BY version")]
    assert kept == [3, 4, 5, 6, 7]
    m = store.load_model("relation")
    assert m["version"] == 7 and m["weights"] == {"w": [6]}
    assert m["examples"] == 6 and m["agreement"] == 0.99 and m["basis"] == "refit"
    assert store.load_model("naming") is None
    assert store.save_model("naming", {}, examples=0, agreement=1.0, basis="rules",
                            now=ts(9)) == 1


def test_ask_stats(store):
    assert store.ask_stats() == {}
    store.update_ask_stats("near:work:seen", 2.0, 1.0, 3, ts(0))
    store.update_ask_stats("near:work:seen", 3.0, 1.0, 4, ts(10))
    store.update_ask_stats("far:off:new", 1.0, 2.0, 1, ts(20))
    stats = store.ask_stats()
    assert stats["near:work:seen"] == {"alpha": 3.0, "beta": 1.0, "asks": 4, "updated_at": ts(10)}
    assert set(stats) == {"near:work:seen", "far:off:new"}


def test_settings(store):
    assert store.setting("paused") is None
    store.set_setting("paused", "1")
    store.set_setting("paused", "0")
    assert store.setting("paused") == "0"


# ---------------------------------------------------------------- prune


def test_prune_order_and_retention(store, path):
    now = "2026-10-20T12:00:00Z"
    old = make_event(event_id="desktop-old", observed_at="2026-09-01T10:00:00Z")
    edge_old = make_event(event_id="desktop-edge-old", observed_at="2026-09-20T11:59:59Z")
    boundary = make_event(event_id="desktop-boundary", observed_at="2026-09-20T12:00:00Z")
    fresh = make_event(event_id="desktop-fresh", observed_at="2026-10-19T10:00:00Z")
    loose = make_event(event_id="desktop-loose", observed_at="2026-08-01T10:00:00Z")
    store.put_events([old, edge_old, boundary, fresh, loose], now=now)
    t_old, t_mixed = hid("task", "old"), hid("task", "mixed")
    e_old = episode(t_old, [old])
    e_mixed = episode(t_mixed, [edge_old, fresh])
    e_edge = episode(t_mixed, [boundary])
    store.write_pass(result(
        episodes=[e_old, e_mixed, e_edge],
        tasks=[task_row(t_old, "Old", [e_old]), task_row(t_mixed, "Mixed", [e_mixed, e_edge])],
        places=placements((e_old, [old]), (e_mixed, [edge_old, fresh]), (e_edge, [boundary])),
        settled=True))
    store.add_label("relation", "k1", "answer", "same", None, "2026-01-01T00:00:00Z")
    store.save_model("relation", {"w": 1}, examples=1, agreement=1.0, basis="rules",
                     now="2026-01-01T00:00:00Z")
    store.update_ask_stats("near:work:seen", 1.0, 1.0, 0, "2026-01-01T00:00:00Z")

    counts = store.prune(now, 30)
    assert counts == {"events": 3, "episodes": 1, "tasks": 1}
    assert {r["event_id"] for r in rows(path, "SELECT event_id FROM events")} == {
        "desktop-boundary", "desktop-fresh"}
    assert {r["id"] for r in rows(path, "SELECT id FROM episodes")} == {
        e_mixed["id"], e_edge["id"]}
    assert store.task(t_old) is None and store.task(t_mixed) is not None
    assert count(path, "labels") == 1
    assert count(path, "models") == 1
    assert count(path, "ask_stats") == 1
    assert store.prune(now, 30) == {"events": 0, "episodes": 0, "tasks": 0}


# ---------------------------------------------------------------- status


def test_status(world, store):
    s = store.status()
    assert s == {"events": 6, "tasks": 3, "open_questions": 0, "last_pass_at": None,
                 "last_refit_at": None}
    q = decision("desktop-a3", "desktop-b1", ts(610), status="open", asked_at=ts(700))
    store.write_pass(result(decisions=[q], settled=True))
    store.set_setting("last_pass_at", ts(1000))
    store.set_setting("last_refit_at", ts(500))
    s = store.status()
    assert s["open_questions"] == 1
    assert s["last_pass_at"] == ts(1000)
    assert s["last_refit_at"] == ts(500)
