"""Shared helpers for test_review.py and test_page_journeys.py.

Builds wire events from the contract fixture, posts them through /v1/batches, and places them
with a hand-built PassResult so the tests do not depend on taskmine.group.
This module never imports taskmine.review, so the page journeys can skip cleanly without it.
"""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import select
import socket
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "data" / "fixtures"
EVENT_TEMPLATE = json.loads((FIXTURES / "contract_fixtures" / "event.json").read_text())
CRED = json.loads((FIXTURES / "credential_cases.json").read_text(encoding="utf-8"))
PRIVATE = CRED["private_data"]
KEY_HEX = PRIVATE["key_hex"]
TOKEN = "taskmine-test-token-6f1c2e9a0b7d4c3e"
READER = "1.0.0/2"
SOURCE = "mac-test"


# ---------------------------------------------------------------- fixture values


def value(name: str) -> str:
    """A synthetic credential value from credential_cases.json, joined from its parts."""
    return "".join(CRED["values"][name])


def case_text(case_id: str) -> str:
    """The input text of a credential case, with $value references filled in."""
    case = next(c for c in CRED["cases"] if c["id"] == case_id)
    out = []
    for part in case["parts"]:
        out.append(value(part[1:]) if part.startswith("$") and part[1:] in CRED["values"] else part)
    return "".join(out)


def private_case(case_id: str) -> dict:
    return next(c for c in PRIVATE["cases"] if c["id"] == case_id)


IBAN_CASE = private_case("iban_spaced")
IBAN_TEXT = "".join(IBAN_CASE["parts"])           # "Pay to IT60 ... please"
IBAN_CODED = IBAN_CASE["coded"]                   # "Pay to [IBAN·IT·4154] please"
IBAN_RAW = IBAN_TEXT[len("Pay to "):-len(" please")]
PASSWORD_TEXT = case_text("composition_standalone_quoted")   # redacted, not excluded
PASSWORD_SECRET = value("assignment_two")
TITLE_SECRET_TEXT = case_text("composition_fragment_0_0_title")  # excluded in title mode
TITLE_SECRET = value("span_tail")
LABEL_TEXT = case_text("composition_fragment_0_0_text")      # excluded in text mode
PRIVATE_KEY_TEXT = case_text("private_key_block")
PRIVATE_KEY_SECRET = value("keybody")
CRED_DOC_TITLE = case_text("title_credential_document")      # ".env.local - Acmeflo - Cursor"
CRED_DOC_TEXT = case_text("credential_document_terminal")
RENAME_SECRET_TITLE = case_text("keyword_suffix_0")           # contains_credential is True
RENAME_SECRET = value("keyword_suffix")
XSS_TEXT = '<img src=x onerror="window.__taskmineXss=1">'

SECRETS = [PASSWORD_SECRET, TITLE_SECRET, PRIVATE_KEY_SECRET, IBAN_RAW, RENAME_SECRET,
           value("span_head"), CRED_DOC_TITLE]


# ---------------------------------------------------------------- time and ids


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hid(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


# ---------------------------------------------------------------- wire events


def wire_event(event_id: str, at: datetime, *, text: str = "Invoice INV-1042 needs approval.",
               app: str = "com.apple.TextEdit", app_name: str = "TextEdit",
               title: str | None = "Fictional invoice review", kind: str = "screen_text",
               revision: int = 1, **metadata) -> dict:
    """A valid taskmine.event/1 built from the contract fixture, with a correct text hash."""
    event = copy.deepcopy(EVENT_TEMPLATE)
    event.update({
        "event_id": event_id, "revision": revision, "source_id": SOURCE,
        "observed_at": iso(at), "first_seen_at": iso(at), "last_seen_at": iso(at),
    })
    meta = {"app_bundle_id": app, "app_name": app_name}
    if title is not None:
        meta["window_title"] = title
    if kind == "interaction":
        meta["interaction_type"] = "click"
    meta.update(metadata)
    event["content"] = {"type": kind, "text": text, "text_sha256": sha(text), "metadata": meta}
    return event


def batch(events: list[dict], batch_id: str | None = None) -> dict:
    if batch_id is None:
        batch_id = "batch-" + hid(*[e["event_id"] for e in events])[:16]
    return {"schema": "taskmine.batch/1", "batch_id": batch_id, "source_id": SOURCE,
            "events": events}


# ---------------------------------------------------------------- HTTP client


@dataclass
class Resp:
    status: int
    headers: dict
    body: bytes

    @property
    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass
class Client:
    """Small http.client wrapper. Every response body is kept in `seen` for leak checks."""

    port: int
    seen: list = field(default_factory=list)

    def request(self, method: str, path: str, body=None, *, headers: dict | None = None,
                page: bool = True) -> Resp:
        hdrs = {}
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if page and method in ("POST", "PATCH", "DELETE") and not path.startswith("/v1/batches"):
            hdrs["X-Taskmine-Request"] = "page"
        hdrs.update(headers or {})
        hdrs = {k: v for k, v in hdrs.items() if v is not None}
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.request(method, path, body=data, headers=hdrs)
            r = conn.getresponse()
            resp = Resp(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        finally:
            conn.close()
        self.seen.append(resp.body)
        return resp

    def get(self, path: str, **kw) -> Resp:
        return self.request("GET", path, **kw)

    def post(self, path: str, body=None, **kw) -> Resp:
        return self.request("POST", path, {} if body is None else body, **kw)

    def post_batch(self, doc, *, token: str | None = TOKEN, reader: str | None = READER,
                   headers: dict | None = None) -> Resp:
        hdrs = {"Authorization": f"Bearer {token}" if token is not None else None,
                "X-Taskmine-Reader": reader}
        hdrs.update(headers or {})
        return self.request("POST", "/v1/batches", doc, headers=hdrs)

    def raw(self, method: str, path: str, headers: dict, body: bytes) -> Resp:
        """Send a request whose body the server may refuse unread (413).

        Headers go first. If the server answers within 2 s, the body is never sent. Otherwise
        the body is sent from a thread while the response is read.
        """
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=20)
        try:
            lines = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{self.port}",
                     f"Content-Length: {len(body)}", "Content-Type: application/json",
                     "Connection: close"]
            lines += [f"{k}: {v}" for k, v in headers.items()]
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
            ready, _, _ = select.select([sock], [], [], 2.0)
            if not ready:
                def pump():
                    try:
                        sock.sendall(body)
                    except OSError:
                        pass
                threading.Thread(target=pump, daemon=True).start()
            r = http.client.HTTPResponse(sock)
            r.begin()
            resp = Resp(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        finally:
            sock.close()
        self.seen.append(resp.body)
        return resp


# ---------------------------------------------------------------- pass results


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
        "task_id": task_id, "started_at": first, "ended_at": last,
        "first_action_at": first, "last_action_at": last, "close_reason": close_reason,
        "trivial": 0, "diversion": 0,
        "signature": {"steps": steps, "apps": sorted({s["app"] for s in steps}), "artifacts": []},
        "process_id": process_id, "revision": 1,
    }


def task_row(task_id: str, title: str | None, episodes: list[dict], *,
             possibly_personal: int = 0) -> dict:
    return {
        "id": task_id, "title": title, "name_basis": "rules", "summary": None,
        "lifecycle": "active", "locked": 0, "possibly_personal": possibly_personal,
        "revision": 1, "started_at": min(e["started_at"] for e in episodes),
        "last_seen_at": max(e["last_action_at"] for e in episodes), "case_keys": [],
        "draft_id": None,
    }


def decision(left: str, right: str, at: str, *, status: str | None = None,
             asked_at: str | None = None, reason: str = "gap", choice: str = "break",
             hard: int = 0) -> dict:
    return {
        "key": hid("decision", left, right), "left_event_id": left, "right_event_id": right,
        "at": at, "features": {"gap_s": 42.0, "recent": 1.0, "same_app": 0.0},
        "model_confidence": None if hard else 0.75, "choice": choice, "basis": "rule",
        "reason": reason, "hard": hard,
        "asked_at": asked_at, "ask_context": "near:work:seen" if status else None,
        "question_status": status, "answer": None, "answered_at": None,
    }


def seed_world(client: Client, store, *, questions: int = 1) -> SimpleNamespace:
    """Post seven events through /v1/batches and place them in four tasks.

    A "Invoice review A": episodes A1 (a1 IBAN text, a2 HTML-like text) and A2 (a3 password text).
    B "Order B": episode B1 (b1, b2) in Safari. C "Notes C": c1. D "Personal D": d1, possibly
    personal. Newest first: D, C, A, B. Process P joins A2 and B1. `questions` open questions.
    """
    from taskmine.store import PassResult

    t0 = now_utc() - timedelta(minutes=30)
    at = lambda s: t0 + timedelta(seconds=s)
    wire = {
        "a1": wire_event("desktop-a1", at(0), text=IBAN_TEXT, title="Invoice review A"),
        "a2": wire_event("desktop-a2", at(60), text=XSS_TEXT, title="Invoice review A"),
        "a3": wire_event("desktop-a3", at(600), text=PASSWORD_TEXT, title="Invoice review A"),
        "b1": wire_event("desktop-b1", at(300), text="Order PO-7 shipped.", title="Order B",
                         app="com.apple.Safari", app_name="Safari",
                         browser_domain="orders.example.test"),
        "b2": wire_event("desktop-b2", at(360), text="Order PO-7 invoice sent.",
                         title="Order B", app="com.apple.Safari", app_name="Safari",
                         browser_domain="orders.example.test"),
        "c1": wire_event("desktop-c1", at(900), text="Meeting notes for Friday.",
                         title="Notes C"),
        "d1": wire_event("desktop-d1", at(1000), text="Holiday flights compared.",
                         title="Personal D"),
    }
    resp = client.post_batch(batch(list(wire.values()), batch_id="seed-world"))
    assert resp.status == 202, resp.text
    assert resp.json["rejected"] == [], resp.json
    assert {a["status"] for a in resp.json["accepted"]} == {"stored"}

    ev = {k: {"event_id": e["event_id"], "observed_at": e["observed_at"],
              "last_seen_at": e["last_seen_at"]} for k, e in wire.items()}
    A, B = hid("task", "desktop-a1"), hid("task", "desktop-b1")
    C, D = hid("task", "desktop-c1"), hid("task", "desktop-d1")
    P = hid("process", "invoice")
    a1 = episode(A, [ev["a1"], ev["a2"]], close_reason="diversion")
    a2 = episode(A, [ev["a3"]], process_id=P)
    b1 = episode(B, [ev["b1"], ev["b2"]], process_id=P)
    c1 = episode(C, [ev["c1"]], steps=STEPS_BROWSE)
    d1 = episode(D, [ev["d1"]])
    pairs = [("desktop-a3", "desktop-b1"), ("desktop-b2", "desktop-a2"),
             ("desktop-b2", "desktop-c1"), ("desktop-c1", "desktop-a3")]
    decisions = [decision(left, right, iso(at(600 + i)), status="open" if i < questions else None,
                          asked_at=iso(at(700 + i)) if i < questions else None)
                 for i, (left, right) in enumerate(pairs)]
    proc = {"id": P, "name": "Review invoice", "signature": {"steps": STEPS_EDIT},
            "steps": STEPS_EDIT, "templates": ["Invoice {id}"], "score": 0.9, "basis": "rules",
            "created_at": iso(at(1000)), "updated_at": iso(at(1000))}
    places = {}
    for ep, keys in ((a1, ["a1", "a2"]), (a2, ["a3"]), (b1, ["b1", "b2"]), (c1, ["c1"]),
                     (d1, ["d1"])):
        for k in keys:
            places[ev[k]["event_id"]] = ep["id"]
    store.write_pass(PassResult(
        episodes=[a1, a2, b1, c1, d1],
        tasks=[task_row(A, "Invoice review A", [a1, a2]), task_row(B, "Order B", [b1]),
               task_row(C, "Notes C", [c1]),
               task_row(D, "Personal D", [d1], possibly_personal=1)],
        processes=[proc], decisions=decisions, placements=places, settled=True,
    ))
    return SimpleNamespace(wire=wire, A=A, B=B, C=C, D=D, P=P, a1=a1, a2=a2, b1=b1, c1=c1,
                           d1=d1, decisions=decisions, t0=t0)


# ---------------------------------------------------------------- server


def make_settings(home: Path):
    from taskmine.contracts import Settings

    return Settings(home=Path(home), port=0, retention_days=30, log="info",
                    model_url="http://127.0.0.1:9/unused", model_name="fake-model",
                    model_key="test-key", model_calls_alert=120)


def write_home(home: Path, *, coding_key: bool = True) -> None:
    """Write the upload token and the coding key the way the app and core leave them."""
    (Path(home) / "token").write_text(TOKEN)
    if coding_key:
        (Path(home) / "coding.key").write_text(KEY_HEX)


class Running:
    """A review server on 127.0.0.1, port 0, served from a thread."""

    def __init__(self, home: Path, policy=None, *, coding_key: bool = True):
        from taskmine.contracts import Policy
        from taskmine.model import FakeModel
        from taskmine.review import make_server
        from taskmine.store import Store

        write_home(home, coding_key=coding_key)
        self.home = Path(home)
        self.settings = make_settings(home)
        self.policy = policy or Policy.default()
        self.store = Store(self.home / "taskmine.db")
        self.model = FakeModel({})
        self.server = make_server(self.store, self.policy, self.settings, model=self.model)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = Client(self.port)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
