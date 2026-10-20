"""The two recorded days through the store path: Receive's check, Store, then `group.run`.

Each event goes old shape -> taskmine.event/1 (`to_event`, counts as strings, as on the wire)
-> `intake.check_event` (as `review.receive` calls it) -> `Store.put_events` -> `group.run`.
The store must end with the same episodes, tasks and processes as `run_batch` gives on the same
day in test_group_replay.py (grouper spec 9.1, 9.2), and a second `run` must change nothing (4.9).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

pytest.importorskip("taskmine.learn", reason="taskmine.learn (rule_choice, decide) is not built yet")
pytest.importorskip("taskmine.model", reason="taskmine.model (FakeModel) is not built yet")

from replay_support import (
    SALT,
    NeverAsk,
    iso,
    load,
    replay_actions,
    run_replay,
    silent_model,
    to_event,
)
from taskmine import intake
from taskmine.contracts import Policy, validate_event
from taskmine.group import run
from taskmine.store import Store

CODING_KEY = bytes.fromhex("11" * 32)
DAYS = {  # file: (episodes, tasks, processes), from 9.1 and 9.2
    "stage2_replay.json": (3, 3, 1),
    "timesheet_replay.json": (2, 2, 1),
}


def wire_events(name: str) -> list[dict]:
    """The day's events in the taskmine.event/1 wire shape."""
    events = []
    for old in load(name)["evidence"]:
        event = to_event(old, source_id="mac-replay")
        meta = event["content"]["metadata"]
        meta.pop("url", None)  # the wire carries the host only (CONTRACTS section 2)
        for count in ("sighting_count", "duration_s"):
            if count in meta:
                meta[count] = str(meta[count])
        assert validate_event(event) == [], event["event_id"]
        events.append(event)
    return events


def receive(store: Store, events: list[dict], now: datetime) -> None:
    """Check and store a batch the way review.receive does, without HTTP."""
    keep, rejected = [], []
    for event in events:
        checked, rejection = intake.check_event(event, policy=Policy.default(), key=CODING_KEY,
                                                salt=SALT, now=now, retention_days=30)
        keep += [checked] if checked else []
        rejected += [rejection] if rejection else []
    assert rejected == []
    store.put_events(keep, now)


def snapshot(path) -> dict:
    """What the store holds after a pass: episode members and the grouping rows."""
    conn = sqlite3.connect(path)
    try:
        members: dict[str, set[str]] = {}
        for event_id, episode_id in conn.execute("SELECT event_id, episode_id FROM events"):
            members.setdefault(episode_id, set()).add(event_id)
        return {
            "members": members,
            "episodes": sorted(conn.execute(
                "SELECT id, task_id, process_id, close_reason, settled, trivial, "
                "started_at, ended_at FROM episodes")),
            "tasks": sorted(r[0] for r in conn.execute("SELECT id FROM tasks")),
            "processes": sorted(r[0] for r in conn.execute("SELECT id FROM processes")),
        }
    finally:
        conn.close()


@pytest.mark.parametrize("name", list(DAYS))
def test_store_path_matches_run_batch(tmp_path, name):
    episodes, tasks, processes = DAYS[name]
    events = wire_events(name)
    last = max(e["last_seen_at"] for e in events)
    now = iso(last, 600)
    path = tmp_path / "taskmine.db"
    store = Store(path)
    receive(store, events, datetime.fromisoformat(now))

    run(store, NeverAsk(), silent_model(), now, force=True)
    held = snapshot(path)

    assert None not in held["members"]  # 9.1, 9.2 Test: no unplaced action
    assert len(held["episodes"]) == episodes  # 9.1: 3 episodes; 9.2: 2 episodes
    assert len(held["tasks"]) == tasks  # 9.1: 3 instances; 9.2: 2 instances
    assert len(store.processes()) == processes  # 9.1, 9.2: one process
    process_of = {row[0]: row[2] for row in held["episodes"]}
    assert len(set(process_of.values())) == 1 and None not in process_of.values()

    # Same episode boundaries as the in-memory pass on the same day.
    batch = run_replay(replay_actions(name))
    expected: dict[str, set[str]] = {}
    for event_id, episode_id in batch.placements.items():
        expected.setdefault(episode_id, set()).add(event_id)
    assert {frozenset(ids) for ids in held["members"].values()} == {
        frozenset(ids) for ids in expected.values()}

    # 4.9: a second run on the same store changes nothing.
    run(store, NeverAsk(), silent_model(), iso(last, 660), force=True)
    assert snapshot(path) == held
