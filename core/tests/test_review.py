"""Route tests for taskmine.review, written from CONTRACTS.md sections 2 and 4,
ARCHITECTURE.md (Core: Receive, Check; Page) and review-page-spec 1.2 to 1.13, 2, 2.1, 2.2, 5.

Shapes the specs left open are fixed here; the implementer follows these tests.
Page rendering (DOM, notices, polling, plain-text rendering) is out of scope here; it is
checked in headless Chrome by test_page_journeys.py.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest
from review_world import (
    CRED_DOC_TEXT,
    CRED_DOC_TITLE,
    IBAN_CODED,
    IBAN_RAW,
    IBAN_TEXT,
    LABEL_TEXT,
    PASSWORD_SECRET,
    PASSWORD_TEXT,
    PRIVATE_KEY_TEXT,
    READER,
    RENAME_SECRET,
    RENAME_SECRET_TITLE,
    SECRETS,
    TITLE_SECRET_TEXT,
    TOKEN,
    XSS_TEXT,
    Running,
    batch,
    hid,
    now_utc,
    seed_world,
    wire_event,
)
from taskmine import review  # noqa: F401  (fails with ImportError until review exists)
from taskmine.contracts import Policy, validate_ack
from taskmine.store import BadOperation

HEX32 = re.compile(r"^[0-9a-f]{32}$")
WORKFLOW_FIELDS = {"outcome", "steps", "inputs", "exceptions", "completion", "owner"}
MISSING = hid("task", "missing")


# ---------------------------------------------------------------- fixtures and helpers


@pytest.fixture
def srv(tmp_path):
    running = Running(tmp_path)
    yield running
    running.close()


@pytest.fixture
def client(srv):
    return srv.client


@pytest.fixture
def world(srv):
    return seed_world(srv.client, srv.store)


def assert_error(resp, status: int, code: str | None = None) -> dict:
    assert resp.status == status, (resp.status, resp.text)
    assert resp.headers.get("content-type", "").startswith("application/json")
    body = resp.json
    assert set(body) == {"error"}
    err = body["error"]
    assert isinstance(err.get("code"), str) and err["code"]
    assert isinstance(err.get("message"), str) and err["message"]
    if code is not None:
        assert err["code"] == code
    return err


def stored(srv, sql: str, *args) -> list[dict]:
    with closing(sqlite3.connect(srv.home / "taskmine.db")) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def event_text(srv, event_id: str) -> str | None:
    found = stored(srv, "SELECT text FROM events WHERE event_id = ?", event_id)
    return found[0]["text"] if found else None


def event_ids(srv) -> set[str]:
    return {r["event_id"] for r in stored(srv, "SELECT event_id FROM events")}


def task_of(client, task_id: str) -> dict:
    resp = client.get(f"/v1/tasks/{task_id}/actions")
    assert resp.status == 200, resp.text
    return resp.json["task"]


def correct(client, task_id: str, revision, operation, **kw):
    return client.post(f"/v1/tasks/{task_id}/correct",
                       {"expected_revision": revision, "operation": operation}, **kw)


# ---------------------------------------------------------------- receive: auth, size, schema


def test_batch_stores_events_and_replies_202_with_a_valid_ack(srv, client):
    now = now_utc()
    events = [wire_event("desktop-1", now - timedelta(minutes=2)),
              wire_event("desktop-2", now - timedelta(minutes=1))]
    resp = client.post_batch(batch(events, batch_id="batch-ok"))
    assert resp.status == 202, resp.text
    ack = resp.json
    assert validate_ack(ack) == []
    assert ack == {
        "schema": "taskmine.ack/1", "batch_id": "batch-ok",
        "accepted": [{"event_id": "desktop-1", "revision": 1, "status": "stored"},
                     {"event_id": "desktop-2", "revision": 1, "status": "stored"}],
        "rejected": [],
    }
    assert event_ids(srv) == {"desktop-1", "desktop-2"}


def test_batch_repeat_event_is_duplicate_and_lower_revision_is_stale(client):
    now = now_utc()
    first = wire_event("desktop-1", now, revision=3, text="third")
    assert client.post_batch(batch([first], batch_id="b-1")).status == 202
    dup = client.post_batch(batch([first], batch_id="b-2")).json
    assert dup["accepted"] == [{"event_id": "desktop-1", "revision": 3, "status": "duplicate"}]
    old = wire_event("desktop-1", now, revision=2, text="second")
    stale = client.post_batch(batch([old], batch_id="b-3")).json
    assert stale["accepted"] == [{"event_id": "desktop-1", "revision": 2, "status": "stale"}]


def test_batch_replay_returns_the_stored_ack(srv, client):
    now = now_utc()
    first = client.post_batch(batch([wire_event("desktop-1", now)], batch_id="replay-1"))
    assert first.status == 202
    other = batch([wire_event("desktop-2", now), wire_event("desktop-3", now)],
                  batch_id="replay-1")
    again = client.post_batch(other)
    assert again.status == 202
    assert again.json == first.json
    assert event_ids(srv) == {"desktop-1"}
    row = stored(srv, "SELECT ack FROM batches WHERE batch_id = ?", "replay-1")
    assert len(row) == 1 and json.loads(row[0]["ack"]) == first.json


@pytest.mark.parametrize("auth", [None, "Bearer wrong-token", f"Bearer {TOKEN}x",
                                  f"Basic {TOKEN}", TOKEN, "Bearer "])
def test_batch_without_the_right_bearer_token_is_401(srv, client, auth):
    doc = batch([wire_event("desktop-1", now_utc())])
    resp = client.request("POST", "/v1/batches", doc,
                          headers={"Authorization": auth, "X-Taskmine-Reader": READER})
    assert_error(resp, 401)
    assert event_ids(srv) == set()


def test_batch_over_2_000_000_bytes_is_413(srv, client):
    body = b'{"pad":"' + b"x" * 2_000_000 + b'"}'
    resp = client.raw("POST", "/v1/batches",
                      {"Authorization": f"Bearer {TOKEN}", "X-Taskmine-Reader": READER}, body)
    assert_error(resp, 413)


def test_batch_of_exactly_the_cap_is_not_413(client):
    doc = batch([wire_event("desktop-1", now_utc())])
    text = json.dumps(doc)
    body = (text[:-1] + " " * (2_000_000 - len(text)) + "}").encode()
    assert len(body) == 2_000_000
    resp = client.raw("POST", "/v1/batches",
                      {"Authorization": f"Bearer {TOKEN}", "X-Taskmine-Reader": READER}, body)
    assert resp.status == 202, resp.text


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("batch_id"),
    lambda d: d.update(schema="taskmine.batch/2"),
    lambda d: d.update(events=[]),
    lambda d: d["events"][0].update(source_id="mac-other"),
    lambda d: d["events"][0]["content"].update(text_sha256="0" * 64),
    lambda d: d.update(extra=1),
], ids=["no_batch_id", "wrong_schema", "no_events", "source_mismatch", "bad_hash", "extra_key"])
def test_batch_failing_validation_is_400(srv, client, mutate):
    doc = batch([wire_event("desktop-1", now_utc())])
    mutate(doc)
    assert_error(client.post_batch(doc), 400)
    assert event_ids(srv) == set()


def test_batch_that_is_not_json_is_400(client):
    resp = client.request("POST", "/v1/batches", b"{not json",
                          headers={"Authorization": f"Bearer {TOKEN}",
                                   "X-Taskmine-Reader": READER})
    assert_error(resp, 400)


@pytest.mark.parametrize("reader", ["1.0.0/1", "1.0.0/3", "2.4.1/0"])
def test_batch_from_an_unsupported_sanitizer_is_426(srv, client, reader):
    resp = client.post_batch(batch([wire_event("desktop-1", now_utc())]), reader=reader)
    assert_error(resp, 426)
    assert event_ids(srv) == set()


def test_batch_does_not_need_the_page_header(client):
    resp = client.post_batch(batch([wire_event("desktop-1", now_utc())]),
                             headers={"X-Taskmine-Request": None})
    assert resp.status == 202


# ---------------------------------------------------------------- receive: check steps


def test_password_in_text_is_redacted_in_the_store(srv, client):
    ev = wire_event("desktop-pw", now_utc(), text=PASSWORD_TEXT)
    ack = client.post_batch(batch([ev])).json
    assert ack["accepted"] == [{"event_id": "desktop-pw", "revision": 1, "status": "stored"}]
    text = event_text(srv, "desktop-pw")
    assert "[redacted]" in text
    assert PASSWORD_SECRET not in text
    row = stored(srv, "SELECT metadata FROM events WHERE event_id = 'desktop-pw'")[0]
    assert PASSWORD_SECRET not in row["metadata"]


def test_iban_is_coded_with_the_key_from_coding_key(srv, client):
    ev = wire_event("desktop-iban", now_utc(), text=IBAN_TEXT)
    client.post_batch(batch([ev]))
    text = event_text(srv, "desktop-iban")
    assert text == IBAN_CODED
    assert IBAN_RAW not in text


def test_without_coding_key_the_iban_is_still_never_stored(tmp_path):
    running = Running(tmp_path, coding_key=False)
    try:
        ev = wire_event("desktop-iban", now_utc(), text=IBAN_TEXT)
        running.client.post_batch(batch([ev]))
        text = event_text(running, "desktop-iban")
        assert IBAN_RAW not in text
        assert "[IBAN·IT·????]" in text
    finally:
        running.close()


def test_title_is_hidden_and_coded(srv, client):
    title = "Invoice " + IBAN_RAW
    ev = wire_event("desktop-t", now_utc(), title=title)
    client.post_batch(batch([ev]))
    row = stored(srv, "SELECT title, metadata FROM events WHERE event_id = 'desktop-t'")[0]
    assert IBAN_RAW not in (row["title"] or "")
    assert IBAN_RAW not in row["metadata"]


def test_text_is_tidied_before_storing(srv, client):
    ev = wire_event("desktop-tidy", now_utc(), text="Order shipped\n10:42\nOrder shipped")
    client.post_batch(batch([ev]))
    from taskmine import check
    assert event_text(srv, "desktop-tidy") == check.tidy("Order shipped\n10:42\nOrder shipped")


REJECTIONS = [
    ("pm-app", {"app": "com.1password.1password", "app_name": "1Password"}, "policy_denied"),
    ("keychain", {"app": "com.apple.keychainaccess", "app_name": "Keychain Access"},
     "policy_denied"),
    ("sign-in-url", {"app": "com.google.Chrome", "app_name": "Chrome",
                     "document_url": "https://accounts.google.com/v3/signin/identifier"},
     "policy_denied"),
    ("bank-url", {"app": "com.google.Chrome", "app_name": "Chrome",
                  "document_url": "https://www.paypal.com/myaccount/summary"}, "policy_denied"),
    ("private-flag", {"app": "com.google.Chrome", "app_name": "Chrome",
                      "private_browsing": True}, "policy_denied"),
    ("incognito-title", {"app": "com.google.Chrome", "app_name": "Chrome",
                         "title": "New Tab - Google Chrome (Incognito)"}, "policy_denied"),
    ("cred-doc-title", {"title": CRED_DOC_TITLE}, "credential_detected"),
    ("cred-doc-text", {"text": CRED_DOC_TEXT}, "credential_detected"),
    ("label-title", {"title": TITLE_SECRET_TEXT}, "credential_detected"),
    ("label-text", {"text": LABEL_TEXT}, "credential_detected"),
    ("private-key", {"text": PRIVATE_KEY_TEXT}, "credential_detected"),
]


@pytest.mark.parametrize("name,fields,code", REJECTIONS, ids=[r[0] for r in REJECTIONS])
def test_check_rejections(srv, client, name, fields, code):
    ok = wire_event("desktop-ok", now_utc())
    bad = wire_event(f"desktop-{name}", now_utc(), **fields)
    resp = client.post_batch(batch([ok, bad]))
    assert resp.status == 202, resp.text
    ack = resp.json
    assert validate_ack(ack) == []
    assert ack["accepted"] == [{"event_id": "desktop-ok", "revision": 1, "status": "stored"}]
    assert len(ack["rejected"]) == 1
    rej = ack["rejected"][0]
    assert (rej["event_id"], rej["revision"], rej["code"]) == (f"desktop-{name}", 1, code)
    assert event_ids(srv) == {"desktop-ok"}
    for secret in SECRETS:
        assert secret not in resp.text


def test_event_older_than_retention_is_expired(srv, client):
    old = wire_event("desktop-old", now_utc() - timedelta(days=40))
    fresh = wire_event("desktop-new", now_utc() - timedelta(days=29))
    ack = client.post_batch(batch([old, fresh])).json
    assert [(r["event_id"], r["code"]) for r in ack["rejected"]] == [("desktop-old",
                                                                      "event_expired")]
    assert ack["accepted"] == [{"event_id": "desktop-new", "revision": 1, "status": "stored"}]
    assert event_ids(srv) == {"desktop-new"}


def test_ack_names_every_event_once_in_a_mixed_batch(client):
    now = now_utc()
    events = [wire_event("desktop-1", now), wire_event("desktop-2", now, title=CRED_DOC_TITLE),
              wire_event("desktop-3", now - timedelta(days=60)),
              wire_event("desktop-4", now, app="com.bitwarden.desktop", app_name="Bitwarden")]
    ack = client.post_batch(batch(events)).json
    assert validate_ack(ack) == []
    named = [a["event_id"] for a in ack["accepted"]] + [r["event_id"] for r in ack["rejected"]]
    assert sorted(named) == ["desktop-1", "desktop-2", "desktop-3", "desktop-4"]


def test_policy_allowed_apps_and_excluded_domains(tmp_path):
    policy = Policy(allowed_apps=["com.apple.TextEdit", "com.google.Chrome"],
                    excluded_domains=["example-bank.test"])
    running = Running(tmp_path, policy)
    try:
        now = now_utc()
        events = [
            wire_event("desktop-edit", now),
            wire_event("desktop-safari", now, app="com.apple.Safari", app_name="Safari"),
            wire_event("desktop-bank", now, app="com.google.Chrome", app_name="Chrome",
                       browser_domain="example-bank.test"),
            wire_event("desktop-shop", now, app="com.google.Chrome", app_name="Chrome",
                       browser_domain="shop.example.test"),
        ]
        ack = running.client.post_batch(batch(events)).json
        assert {a["event_id"] for a in ack["accepted"]} == {"desktop-edit", "desktop-shop"}
        assert {(r["event_id"], r["code"]) for r in ack["rejected"]} == {
            ("desktop-safari", "policy_denied"), ("desktop-bank", "policy_denied")}
        assert event_ids(running) == {"desktop-edit", "desktop-shop"}
    finally:
        running.close()


# ---------------------------------------------------------------- app routes: policy, health


def test_get_policy_is_the_policy_document(client):
    resp = client.get("/v1/policy")
    assert resp.status == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json == Policy.default().to_dict()


def test_health(client):
    resp = client.get("/v1/health")
    assert resp.status == 200
    body = resp.json
    assert body["ok"] is True
    assert isinstance(body["version"], str) and body["version"]


# ---------------------------------------------------------------- static page


@pytest.mark.parametrize("path,kind", [("/", "text/html"), ("/app.js", "javascript"),
                                       ("/app.css", "text/css")])
def test_page_and_static_files(client, path, kind):
    resp = client.get(path)
    assert resp.status == 200
    assert kind in resp.headers["content-type"]
    assert resp.body


def test_index_is_the_page_file(client):
    from pathlib import Path

    import taskmine
    page = Path(taskmine.__file__).parent / "page" / "index.html"
    assert client.get("/").body == page.read_bytes()


@pytest.mark.parametrize("path", ["/nope.html", "/page/index.html", "/../contracts.py",
                                  "/%2e%2e/contracts.py", "/app.js/../../store.py",
                                  "/taskmine.db", "/token", "/v1/nope"])
def test_other_paths_are_404(client, path):
    resp = client.get(path)
    assert resp.status == 404
    assert b"def " not in resp.body and TOKEN.encode() not in resp.body
    if path.startswith("/v1/"):
        assert_error(resp, 404)


# ---------------------------------------------------------------- tasks list and coverage


def test_tasks_newest_first_with_coverage(srv, client, world):
    resp = client.get("/v1/tasks")
    assert resp.status == 200
    body = resp.json
    assert set(body) >= {"tasks", "next_cursor", "coverage"}
    assert [t["id"] for t in body["tasks"]] == [world.D, world.C, world.A, world.B]
    assert body["next_cursor"] is None
    a = body["tasks"][2]
    assert a["title"] == "Invoice review A"
    assert a["revision"] == 1
    assert a["action_count"] == 3
    assert a["lifecycle"] == "active"
    assert not a["locked"]
    assert body["tasks"][0]["possibly_personal"]
    for key in ("started_at", "last_seen_at"):
        assert isinstance(a[key], str)
    cov = body["coverage"]
    assert cov["enabled"] is True
    assert cov["online"] is True
    assert cov["capture_state"] == "recording"
    assert cov["action_count"] == 7
    assert cov["task_count"] == 4
    assert cov["retention_days"] == 30
    assert isinstance(cov["personal_time"]["today_tasks"], int)
    assert isinstance(cov["personal_time"]["today_minutes"], int)


def test_tasks_paging_with_opaque_cursor(client, world):
    first = client.get("/v1/tasks?limit=3").json
    assert [t["id"] for t in first["tasks"]] == [world.D, world.C, world.A]
    cursor = first["next_cursor"]
    assert isinstance(cursor, str) and cursor
    from urllib.parse import quote
    second = client.get(f"/v1/tasks?limit=3&before={quote(cursor, safe='')}").json
    assert [t["id"] for t in second["tasks"]] == [world.B]
    assert second["next_cursor"] is None


def test_tasks_empty(client):
    body = client.get("/v1/tasks").json
    assert body["tasks"] == [] and body["next_cursor"] is None
    assert body["coverage"]["task_count"] == 0


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=1000", "limit=-1",
                                   "limit=abc", "limit=1.5"])
def test_tasks_bad_limit_is_400(client, query):
    err = assert_error(client.get(f"/v1/tasks?{query}"), 400)
    assert err["message"] == "Choose a page size from 1 to 100"


@pytest.mark.parametrize("before", ["", "not-a-cursor", "x" * 600])
def test_tasks_bad_cursor_is_400(client, world, before):
    assert_error(client.get(f"/v1/tasks?limit=2&before={before}"), 400)


# ---------------------------------------------------------------- task actions


def test_task_actions_shape_and_text(client, world):
    resp = client.get(f"/v1/tasks/{world.A}/actions")
    assert resp.status == 200
    body = resp.json
    assert set(body) >= {"task", "episodes", "actions", "next_cursor"}
    assert body["task"]["id"] == world.A
    assert {e["id"] for e in body["episodes"]} == {world.a1["id"], world.a2["id"]}
    actions = {a["event_id"]: a for a in body["actions"]}
    assert set(actions) == {"desktop-a1", "desktop-a2", "desktop-a3"}
    assert [a["event_id"] for a in body["actions"]] == ["desktop-a3", "desktop-a2", "desktop-a1"]
    assert actions["desktop-a1"]["text"] == IBAN_CODED
    assert actions["desktop-a2"]["text"] == XSS_TEXT
    assert "[redacted]" in actions["desktop-a3"]["text"]
    assert all(a["episode_id"] for a in body["actions"])
    assert body["next_cursor"] is None


def test_task_actions_paging(client, world):
    from urllib.parse import quote
    first = client.get(f"/v1/tasks/{world.A}/actions?limit=2").json
    assert [a["event_id"] for a in first["actions"]] == ["desktop-a3", "desktop-a2"]
    cursor = first["next_cursor"]
    assert isinstance(cursor, str) and cursor
    second = client.get(
        f"/v1/tasks/{world.A}/actions?limit=2&before={quote(cursor, safe='')}").json
    assert [a["event_id"] for a in second["actions"]] == ["desktop-a1"]
    assert second["next_cursor"] is None


@pytest.mark.parametrize("task_id", [MISSING, "nothex", MISSING.upper(), MISSING + "0",
                                     "..%2F..%2Fetc"])
def test_task_actions_unknown_or_malformed_id_is_404(client, world, task_id):
    assert_error(client.get(f"/v1/tasks/{task_id}/actions"), 404)


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "before="])
def test_task_actions_bad_paging_is_400(client, world, query):
    assert_error(client.get(f"/v1/tasks/{world.A}/actions?{query}"), 400)


# ---------------------------------------------------------------- corrections


def test_rename(client, world):
    resp = correct(client, world.A, 1, {"op": "rename", "title": "Approve invoice"})
    assert resp.status == 200, resp.text
    task = resp.json["task"]
    assert task["id"] == world.A
    assert task["title"] == "Approve invoice"
    assert task["revision"] == 2
    assert task["locked"]
    assert task["name_basis"] == "person"
    assert task_of(client, world.A)["title"] == "Approve invoice"


def test_stale_revision_is_409_instance_changed(client, world):
    assert correct(client, world.A, 1, {"op": "rename", "title": "One"}).status == 200
    err = assert_error(correct(client, world.A, 1, {"op": "rename", "title": "Two"}), 409,
                       "instance_changed")
    assert "correct it again" in err["message"]
    assert "Refresh" not in err["message"]
    assert task_of(client, world.A)["title"] == "One"


def test_correct_unknown_task_is_404(client, world):
    assert_error(correct(client, MISSING, 1, {"op": "rename", "title": "x"}), 404)
    assert_error(correct(client, "nothex", 1, {"op": "rename", "title": "x"}), 404)


def test_merge_with_unknown_other_task_is_404(client, world):
    assert_error(correct(client, world.A, 1, {"op": "merge", "other_task_id": MISSING}), 404)
    assert task_of(client, world.A)["revision"] == 1


@pytest.mark.parametrize("op", [
    {"op": "rename", "title": "   "},
    {"op": "rename", "title": "x" * 241},
    {"op": "abandon_ship"},
    {"op": "split", "episode_ids": []},
], ids=["blank", "long", "unknown_op", "split_nothing"])
def test_bad_operation_is_400_with_the_store_message(srv, client, world, op):
    with pytest.raises(BadOperation) as raised:
        srv.store.correct(world.A, 1, dict(op))
    err = assert_error(correct(client, world.A, 1, op), 400)
    assert err["message"] == str(raised.value)
    assert task_of(client, world.A)["revision"] == 1


@pytest.mark.parametrize("body", [
    {"operation": {"op": "rename", "title": "x"}},
    {"expected_revision": "1", "operation": {"op": "rename", "title": "x"}},
    {"expected_revision": True, "operation": {"op": "rename", "title": "x"}},
    {"expected_revision": 1},
    {"expected_revision": 1, "operation": "rename"},
    [],
], ids=["no_revision", "string_revision", "bool_revision", "no_operation", "string_op", "list"])
def test_malformed_correction_body_is_400(client, world, body):
    assert_error(client.post(f"/v1/tasks/{world.A}/correct", body), 400)
    assert task_of(client, world.A)["revision"] == 1


def test_correction_body_that_is_not_json_is_400(client, world):
    assert_error(client.post(f"/v1/tasks/{world.A}/correct", b"{oops"), 400)


def test_correction_holding_a_credential_is_422(client, world):
    resp = correct(client, world.A, 1, {"op": "rename", "title": RENAME_SECRET_TITLE})
    err = assert_error(resp, 422, "credential_detected")
    assert RENAME_SECRET not in resp.text
    assert RENAME_SECRET not in err["message"]
    task = task_of(client, world.A)
    assert task["revision"] == 1 and task["title"] == "Invoice review A"


def test_merge_split_and_complete_through_the_route(client, world):
    merged = correct(client, world.A, 1, {"op": "merge", "other_task_id": world.B})
    assert merged.status == 200, merged.text
    assert merged.json["task"]["id"] == world.A
    assert_error(client.get(f"/v1/tasks/{world.B}/actions"), 404)
    split = correct(client, world.A, 2, {"op": "split", "episode_ids": [world.a2["id"]]})
    assert split.status == 200, split.text
    new = split.json["task"]
    assert HEX32.match(new["id"]) and new["id"] != world.A
    done = correct(client, world.C, 1, {"op": "complete"})
    assert done.json["task"]["lifecycle"] == "completed"


# ---------------------------------------------------------------- personal


def test_personal_deletes_a_possibly_personal_task(client, world):
    resp = client.post(f"/v1/tasks/{world.D}/personal")
    assert resp.status == 200, resp.text
    pt = resp.json["personal_time"]
    assert isinstance(pt["today_tasks"], int) and isinstance(pt["today_minutes"], int)
    assert_error(client.get(f"/v1/tasks/{world.D}/actions"), 404)
    assert world.D not in [t["id"] for t in client.get("/v1/tasks").json["tasks"]]


def test_personal_on_an_ordinary_task_is_409(client, world):
    assert_error(client.post(f"/v1/tasks/{world.A}/personal"), 409)
    assert task_of(client, world.A)["id"] == world.A


def test_personal_on_an_unknown_task_is_404(client, world):
    assert_error(client.post(f"/v1/tasks/{MISSING}/personal"), 404)


# ---------------------------------------------------------------- drafts


def test_draft_is_idempotent_per_revision(client, world):
    first = client.post(f"/v1/tasks/{world.A}/draft")
    assert first.status in (200, 201), first.text
    assert first.json["status"] == "created"
    draft_id = first.json["draft_id"]
    assert isinstance(draft_id, str) and draft_id
    again = client.post(f"/v1/tasks/{world.A}/draft")
    assert again.status in (200, 201)
    assert again.json == {"draft_id": draft_id, "status": "existing"}

    assert correct(client, world.A, 1, {"op": "rename", "title": "Approve invoice"}).status == 200
    later = client.post(f"/v1/tasks/{world.A}/draft").json
    assert later["status"] == "created" and later["draft_id"] != draft_id
    ids = {d["id"] for d in client.get("/v1/drafts").json["drafts"]}
    assert ids == {draft_id, later["draft_id"]}
    assert client.get(f"/v1/drafts/{draft_id}").json["draft"]["workflow"]["outcome"] == \
        "Invoice review A"
    assert client.get(f"/v1/drafts/{later['draft_id']}").json["draft"]["workflow"]["outcome"] \
        == "Approve invoice"


def test_draft_shape(client, world):
    draft_id = client.post(f"/v1/tasks/{world.A}/draft").json["draft_id"]
    resp = client.get(f"/v1/drafts/{draft_id}")
    assert resp.status == 200
    draft = resp.json["draft"]
    assert draft["id"] == draft_id
    assert draft["status"] == "pending_review"
    assert isinstance(draft["digest"], str) and draft["digest"]
    assert isinstance(draft["created_at"], str)
    assert draft["source"] == {"task_id": world.A, "revision": 1}
    wf = draft["workflow"]
    assert set(wf) == WORKFLOW_FIELDS
    assert wf["outcome"] == "Invoice review A"
    steps = wf["steps"]
    assert steps.startswith("These observed steps need review. Confirm the task boundaries "
                            "and add any missing steps.")
    assert re.search(r"^1\. ", steps, re.MULTILINE) and re.search(r"^2\. ", steps, re.MULTILINE)
    assert not re.search(r"^3\. ", steps, re.MULTILINE)
    assert "This draft covers all 3 saved actions." in steps
    assert re.search(r"The step notes use \d+ sampled actions?\.", steps)
    assert len(steps) <= 11500
    for secret in SECRETS:
        assert secret not in resp.text


def test_draft_for_unknown_task_is_404(client, world):
    assert_error(client.post(f"/v1/tasks/{MISSING}/draft"), 404)
    assert_error(client.post("/v1/tasks/nothex/draft"), 404)


def test_patch_and_delete_draft(client, world):
    draft_id = client.post(f"/v1/tasks/{world.A}/draft").json["draft_id"]
    resp = client.request("PATCH", f"/v1/drafts/{draft_id}",
                          {"owner": "Finance lead", "inputs": "Invoice PDF"})
    assert resp.status == 200, resp.text
    wf = resp.json["draft"]["workflow"]
    assert wf["owner"] == "Finance lead" and wf["inputs"] == "Invoice PDF"
    assert wf["outcome"] == "Invoice review A"
    assert client.get(f"/v1/drafts/{draft_id}").json["draft"]["workflow"]["owner"] == \
        "Finance lead"

    assert_error(client.request("PATCH", f"/v1/drafts/{draft_id}", {"colour": "red"}), 400)
    assert_error(client.request("PATCH", f"/v1/drafts/{draft_id}", {"owner": 5}), 400)

    gone = client.request("DELETE", f"/v1/drafts/{draft_id}")
    assert gone.status == 200 and gone.json == {"deleted": True}
    assert_error(client.get(f"/v1/drafts/{draft_id}"), 404)
    assert client.get("/v1/drafts").json["drafts"] == []


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_unknown_draft_is_404(client, method):
    body = {"owner": "x"} if method == "PATCH" else None
    assert_error(client.request(method, f"/v1/drafts/{'0' * 32}", body), 404)


# ---------------------------------------------------------------- questions


def test_questions_and_answer_one(srv, client, world):
    resp = client.get("/v1/questions")
    assert resp.status == 200
    qs = resp.json["questions"]
    assert len(qs) == 1
    q = qs[0]
    key = world.decisions[0]["key"]
    assert q["key"] == key
    assert q["left_task_id"] == world.A and q["left_title"] == "Invoice review A"
    assert q["right_task_id"] == world.B and q["right_title"] == "Order B"

    ans = client.post(f"/v1/questions/{key}/answer", {"answer": "one"})
    assert ans.status == 200, ans.text
    assert ans.json["question"]["question_status"] == "answered"
    assert ans.json["question"]["answer"] == "one"
    assert ans.json["questions"] == []
    assert client.get("/v1/questions").json["questions"] == []
    row = stored(srv, "SELECT question_status, answer FROM decisions WHERE key = ?", key)[0]
    assert row == {"question_status": "answered", "answer": "one"}


def test_questions_at_most_three(tmp_path):
    running = Running(tmp_path)
    try:
        seed_world(running.client, running.store, questions=4)
        assert len(running.client.get("/v1/questions").json["questions"]) == 3
    finally:
        running.close()


@pytest.mark.parametrize("answer,status", [("later", "snoozed"), ("never", "dismissed"),
                                           ("two", "answered")])
def test_other_answers(client, world, answer, status):
    key = world.decisions[0]["key"]
    ans = client.post(f"/v1/questions/{key}/answer", {"answer": answer})
    assert ans.status == 200, ans.text
    assert ans.json["question"]["question_status"] == status
    assert key not in [q["key"] for q in ans.json["questions"]]


@pytest.mark.parametrize("body", [{"answer": "maybe"}, {"answer": 1}, {}, {"answer": "ONE"}])
def test_answer_with_another_value_is_400(client, world, body):
    key = world.decisions[0]["key"]
    assert_error(client.post(f"/v1/questions/{key}/answer", body), 400)
    assert len(client.get("/v1/questions").json["questions"]) == 1


def test_answering_twice_is_409(client, world):
    key = world.decisions[0]["key"]
    assert client.post(f"/v1/questions/{key}/answer", {"answer": "one"}).status == 200
    assert_error(client.post(f"/v1/questions/{key}/answer", {"answer": "two"}), 409)


def test_answer_unknown_question_is_404(client, world):
    assert_error(client.post(f"/v1/questions/{hid('decision', 'x', 'y')}/answer",
                             {"answer": "one"}), 404)


# ---------------------------------------------------------------- processes, status, policy


def test_processes(client, world):
    resp = client.get("/v1/processes")
    assert resp.status == 200
    procs = resp.json["processes"]
    assert [p["id"] for p in procs] == [world.P]
    assert set(procs[0]["episode_ids"]) == {world.a2["id"], world.b1["id"]}


def test_status(srv, client, world):
    resp = client.get("/v1/status")
    assert resp.status == 200
    s = resp.json
    assert s["events"] == 7 and s["tasks"] == 4 and s["open_questions"] == 1
    assert "last_pass_at" in s and "last_refit_at" in s
    assert s["policy_state"] == "active"
    assert s["model_name"] == srv.settings.model_name
    assert "last_model_call" in s


def test_pause_and_resume(srv, client):
    resp = client.post("/v1/policy", {"paused": True})
    assert resp.status == 200, resp.text
    assert resp.json["policy"]["state"] == "paused"
    assert Policy.load(srv.home / "policy.json").state == "paused"
    assert client.get("/v1/policy").json["state"] == "paused"
    cov = client.get("/v1/tasks").json["coverage"]
    assert cov["capture_state"] == "paused" and cov["enabled"] is False
    assert client.get("/v1/status").json["policy_state"] == "paused"

    resp = client.post("/v1/policy", {"paused": False})
    assert resp.json["policy"]["state"] == "active"
    assert Policy.load(srv.home / "policy.json").state == "active"
    assert client.get("/v1/tasks").json["coverage"]["capture_state"] == "recording"


@pytest.mark.parametrize("body", [{"paused": "yes"}, {"paused": 1}, {}, {"state": "paused"}])
def test_policy_with_a_bad_body_is_400(srv, client, body):
    assert_error(client.post("/v1/policy", body), 400)
    assert client.get("/v1/policy").json["state"] == "active"


# ---------------------------------------------------------------- request boundary


def _mutations(world) -> list[tuple[str, str, object]]:
    key = world.decisions[0]["key"]
    return [
        ("POST", f"/v1/tasks/{world.A}/correct",
         {"expected_revision": 1, "operation": {"op": "rename", "title": "x"}}),
        ("POST", f"/v1/tasks/{world.D}/personal", {}),
        ("POST", f"/v1/tasks/{world.A}/draft", {}),
        ("PATCH", f"/v1/drafts/{'0' * 32}", {"owner": "x"}),
        ("DELETE", f"/v1/drafts/{'0' * 32}", None),
        ("POST", f"/v1/questions/{key}/answer", {"answer": "one"}),
        ("POST", "/v1/policy", {"paused": True}),
    ]


@pytest.mark.parametrize("header", [None, "review", "workspace"])
def test_page_mutations_need_the_request_header(srv, client, world, header):
    for method, path, body in _mutations(world):
        resp = client.request(method, path, body, page=False,
                              headers={"X-Taskmine-Request": header})
        assert_error(resp, 403)
    assert task_of(client, world.A)["revision"] == 1
    assert task_of(client, world.D)["id"] == world.D
    assert client.get("/v1/drafts").json["drafts"] == []
    assert len(client.get("/v1/questions").json["questions"]) == 1
    assert client.get("/v1/policy").json["state"] == "active"


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example",
                                  "localhost.evil.example:80", "192.168.1.5", "[::2]:8797"])
def test_non_loopback_host_is_403(srv, client, world, host):
    assert_error(client.get("/v1/tasks", headers={"Host": host}), 403)
    assert_error(client.get("/", headers={"Host": host}), 403)
    resp = client.post_batch(batch([wire_event("desktop-x", now_utc())]),
                             headers={"Host": host})
    assert_error(resp, 403)
    assert "desktop-x" not in event_ids(srv)
    assert_error(correct(client, world.A, 1, {"op": "rename", "title": "x"},
                         headers={"Host": host}), 403)
    assert task_of(client, world.A)["revision"] == 1


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "localhost:1234", "127.0.0.1:{port}"])
def test_loopback_hosts_are_served(srv, client, world, host):
    resp = client.get("/v1/tasks", headers={"Host": host.format(port=srv.port)})
    assert resp.status == 200


@pytest.mark.parametrize("origin", ["http://evil.example", "null", "http://127.0.0.1:1",
                                    "https://127.0.0.1:{port}"])
def test_foreign_origin_is_403(srv, client, world, origin):
    origin = origin.format(port=srv.port)
    assert_error(client.get("/v1/tasks", headers={"Origin": origin}), 403)
    resp = correct(client, world.A, 1, {"op": "rename", "title": "x"},
                   headers={"Origin": origin})
    assert_error(resp, 403)
    assert task_of(client, world.A)["revision"] == 1


def test_own_origin_is_allowed(srv, client, world):
    resp = correct(client, world.A, 1, {"op": "rename", "title": "Approve invoice"},
                   headers={"Origin": srv.origin})
    assert resp.status == 200, resp.text
    assert client.get("/v1/tasks", headers={"Origin": srv.origin}).status == 200


def test_page_route_body_over_100_000_bytes_is_413(srv, client, world):
    body = json.dumps({"expected_revision": 1,
                       "operation": {"op": "rename", "title": "x" * 100_001}}).encode()
    resp = client.raw("POST", f"/v1/tasks/{world.A}/correct",
                      {"X-Taskmine-Request": "page"}, body)
    assert_error(resp, 413)
    assert task_of(client, world.A)["revision"] == 1


def test_unknown_v1_route_and_method(client, world):
    assert_error(client.get("/v1/unknown"), 404)
    resp = client.request("DELETE", f"/v1/tasks/{world.A}/actions")
    assert resp.status in (404, 405)
    assert_error(resp, resp.status)


# ---------------------------------------------------------------- no credential leaves the core


def test_no_response_ever_contains_a_seeded_credential(srv, client, world):
    now = now_utc()
    client.post_batch(batch([
        wire_event("desktop-s1", now, title=TITLE_SECRET_TEXT),
        wire_event("desktop-s2", now, text=PRIVATE_KEY_TEXT),
        wire_event("desktop-s3", now, title=CRED_DOC_TITLE, text="DATABASE=fixture"),
        wire_event("desktop-s4", now, text=LABEL_TEXT),
    ]))
    correct(client, world.A, 1, {"op": "rename", "title": RENAME_SECRET_TITLE})
    draft_id = client.post(f"/v1/tasks/{world.A}/draft").json["draft_id"]
    for path in ["/v1/tasks", "/v1/tasks?limit=1", f"/v1/tasks/{world.A}/actions",
                 f"/v1/tasks/{world.B}/actions", "/v1/questions", "/v1/processes",
                 "/v1/status", "/v1/policy", "/v1/health", "/v1/drafts",
                 f"/v1/drafts/{draft_id}"]:
        assert client.get(path).status == 200, path
    assert len(client.seen) >= 15
    for body in client.seen:
        text = body.decode("utf-8", errors="replace")
        for secret in SECRETS:
            assert secret not in text

    stored_text = " ".join(r["text"] + (r["title"] or "") + r["metadata"]
                           for r in stored(srv, "SELECT text, title, metadata FROM events"))
    for secret in SECRETS:
        assert secret not in stored_text
    raw = b"".join(p.read_bytes() for p in srv.home.glob("taskmine.db*"))
    for secret in SECRETS:
        assert secret.encode() not in raw
