"""Review: the core's HTTP server on loopback.

Receive takes batches from the Mac app and runs Check (intake.py) on every event. The page and
its JSON API read and correct tasks. Every request passes one boundary first: a loopback Host,
no foreign Origin, the page header on page mutations and a body limit. Each route returns
(status, body); one place writes the reply. Errors are {"error": {"code", "message"}}.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import taskmine
from taskmine import check, drafts, intake
from taskmine.contracts import Policy, Settings, ack, validate_batch
from taskmine.store import BadOperation, Conflict, NotFound, StaleRevision, Store

__all__ = ["make_server"]

BATCH_MAX = 2_000_000
PAGE_MAX = 100_000
ONLINE_S = 120
CURSOR_MAX = 512
HEX32 = re.compile(r"[0-9a-f]{32}")
CURSOR = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ_[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
HOST = re.compile(r"(\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]+)(?::[0-9]{1,5})?")
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
JSON_TYPE = "application/json; charset=utf-8"
PAGE_DIR = Path(__file__).resolve().parent / "page"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
ANSWERS = {"one": "answered", "two": "answered", "later": "later", "never": "dismissed"}
LIMITATIONS = [
    "Only visible activity from supported apps and the browser is recorded.",
    "Passwords and private browsing are excluded. Protected or unreadable screens are skipped.",
    "Keystrokes, clipboard contents and background file changes are not recorded.",
]
STALE = ("Task changed while you were correcting it. Taskmine reloaded it; check it and correct "
         "it again.")
TASK_KEYS = ("id", "title", "name_basis", "summary", "lifecycle", "revision", "started_at",
             "last_seen_at", "draft_id")
ACTION_KEYS = ("event_id", "episode_id", "observed_at", "first_seen_at", "last_seen_at", "kind",
               "provenance", "interaction", "app", "app_name", "text", "url", "sightings",
               "duration_s")
EPISODE_KEYS = ("id", "task_id", "started_at", "ended_at", "first_action_at", "last_action_at",
                "close_reason", "process_id", "revision")
QUESTION_KEYS = ("key", "at", "asked_at", "left_task_id", "left_title", "right_task_id",
                 "right_title", "question_status", "answer", "answered_at")
PROCESS_KEYS = ("id", "name", "steps", "templates", "score", "basis", "updated_at", "episode_ids")


ApiError = drafts.Refused  # one error type for every route: status, code, message


def _iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick(row: dict, keys: tuple) -> dict:
    return {k: row.get(k) for k in keys}


# ---------------------------------------------------------------- shared state


class Review:
    """What the routes share: the store, the policy, the settings and the home files."""

    def __init__(self, store: Store, policy: Policy, settings: Settings, model=None, scorer=None,
                 asker=None):
        self.store, self.policy, self.settings = store, policy, settings
        self.model, self.scorer, self.asker = model, scorer, asker
        self.home = Path(settings.home)
        self.lock = threading.Lock()
        self.contact_at, self.excluded = 0.0, 0
        self.token = self._token()
        salt = store.setting("anchor_salt")
        if salt is None:
            salt = secrets.token_hex(16)
            store.set_setting("anchor_salt", salt)
        self.salt = bytes.fromhex(salt)

    def _token(self) -> str:
        """The upload token from home/token; made once, mode 0600, when missing."""
        path = self.home / "token"
        if not path.is_file():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(secrets.token_hex(24))
        return path.read_text().strip()

    def coding_key(self) -> bytes | None:
        """The private-data key the app leaves in home/coding.key, or None."""
        try:
            return bytes.fromhex((self.home / "coding.key").read_text().strip()) or None
        except (OSError, ValueError):
            return None

    def contact(self) -> None:
        """The recorder reported: a batch or a policy poll."""
        self.contact_at = time.monotonic()

    def online(self) -> bool:
        return self.contact_at > 0 and time.monotonic() - self.contact_at <= ONLINE_S

    def retention_days(self) -> int:
        return min(self.policy.retention_days, self.settings.retention_days)


# ---------------------------------------------------------------- request


class Request:
    """One request as a route function sees it."""

    def __init__(self, handler, method: str, query: dict, groups: tuple, limit: int):
        self.handler, self.method, self.query, self.groups = handler, method, query, groups
        self.limit, self._body = limit, None

    def header(self, name: str) -> str | None:
        return self.handler.headers.get(name)

    def body(self) -> bytes:
        """The body, read once; 413 over the route's limit."""
        if self._body is None:
            if self.header("Transfer-Encoding"):
                raise ApiError(411, "length_required", "Send the body with a Content-Length")
            size = _length(self.handler)
            if size > self.limit:
                raise ApiError(413, "too_large", f"The body is over {self.limit} bytes")
            self._body = self.handler.rfile.read(size) if size else b""
            self.handler.body_read = True
        return self._body

    def json(self):
        try:
            return json.loads(self.body().decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "bad_json", "The body is not valid JSON") from None

    def hex_id(self, what: str) -> str:
        """The 32-hex id in the path; 404 for anything else."""
        if not HEX32.fullmatch(self.groups[0]):
            raise ApiError(404, "not_found", f"{what} was not found")
        return self.groups[0]

    def limit_param(self) -> int:
        raw = self.query.get("limit")
        if raw is None:
            return 100
        if not re.fullmatch(r"[0-9]{1,3}", raw) or not 1 <= int(raw) <= 100:
            raise ApiError(400, "bad_limit", "Choose a page size from 1 to 100")
        return int(raw)

    def cursor_param(self) -> str | None:
        """The store cursor behind the opaque `before` value."""
        raw = self.query.get("before")
        if raw is None:
            return None
        if not raw or len(raw) > CURSOR_MAX:
            raise ApiError(400, "bad_cursor", "Invalid activity page")
        try:
            text = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("ascii")
        except (binascii.Error, ValueError):
            text = ""
        if not CURSOR.fullmatch(text):
            raise ApiError(400, "bad_cursor", "Invalid activity cursor")
        return text


def _length(handler) -> int:
    raw = handler.headers.get("Content-Length") or "0"
    if not raw.isdigit():
        raise ApiError(400, "bad_length", "Content-Length is not a number")
    return int(raw)


def _opaque(cursor: str | None) -> str | None:
    return None if cursor is None else base64.urlsafe_b64encode(cursor.encode()).decode().strip("=")


# ---------------------------------------------------------------- receive


def receive(app: Review, req: Request):
    auth = req.header("Authorization") or ""
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not token or not hmac.compare_digest(token.encode(), app.token.encode()):
        raise ApiError(401, "unauthorized", "The upload token is missing or wrong")
    if (req.header("X-Taskmine-Reader") or "").rpartition("/")[2] != str(check.SANITIZER_VERSION):
        raise ApiError(426, "unsupported_sanitizer",
                       f"This core needs sanitizer version {check.SANITIZER_VERSION}")
    doc = req.json()
    problems = validate_batch(doc)
    if problems:
        raise ApiError(400, "invalid_batch", "Not a taskmine.batch/1: " + "; ".join(problems[:3]))
    app.contact()
    stored = app.store.batch_ack(doc["batch_id"])
    if stored is not None:
        return 202, stored
    now = datetime.now(UTC).replace(microsecond=0)
    keep, rejected = [], []
    for event in doc["events"]:
        checked, rejection = intake.check_event(
            event, policy=app.policy, key=app.coding_key(), salt=app.salt, now=now,
            retention_days=app.retention_days())
        keep += [checked] if checked else []
        rejected += [rejection] if rejection else []
    accepted = app.store.put_events(keep, now)["accepted"] if keep else []
    out = ack(doc["batch_id"], accepted, rejected)
    with app.lock:
        app.excluded += len(rejected)
    app.store.record_batch(doc["batch_id"], out, now)
    return 202, out


# ---------------------------------------------------------------- shapes


def _personal_time(app: Review) -> dict:
    today = app.store.personal_time(date.today().isoformat())
    return {"today_tasks": today["tasks"], "today_minutes": today["minutes"]}


def coverage(app: Review) -> dict:
    counts, active = app.store.status(), app.policy.state == "active"
    return {"enabled": active, "online": app.online(),
            "capture_state": "recording" if active else "paused",
            "action_count": counts["events"], "task_count": counts["tasks"],
            "excluded_count": app.excluded, "retention_days": app.retention_days(),
            "limitations": LIMITATIONS, "personal_time": _personal_time(app),
            "last_pass_at": counts["last_pass_at"]}


def _tasks_json(app: Review, rows: list[dict]) -> list[dict]:
    """Task rows with their app display names and repeated process."""
    ids = [r["id"] for r in rows]
    apps, process = app.store.task_apps(ids), app.store.task_processes(ids)
    return [{**_pick(r, TASK_KEYS), "locked": bool(r.get("locked")),
             "possibly_personal": bool(r.get("possibly_personal")),
             "action_count": int(r.get("action_count") or 0),
             "episode_count": int(r.get("episode_count") or 0),
             "apps": apps.get(r["id"], []), "process": process.get(r["id"])} for r in rows]


def _action_json(row: dict) -> dict:
    return {**_pick(row, ACTION_KEYS),
            "title": check.tidy_title(row["title"]) if row.get("title") else None}


def _episode_json(row: dict) -> dict:
    sig = row.get("signature") or {}
    return {**_pick(row, EPISODE_KEYS), "settled": bool(row.get("settled")),
            "apps": sig.get("apps", []),
            "steps": [{"kind": s.get("kind"), "app": s.get("app")} for s in sig.get("steps", [])]}


# ---------------------------------------------------------------- app and status routes


def get_policy(app: Review, req: Request):
    app.contact()
    return 200, app.policy.to_dict()


def set_policy(app: Review, req: Request):
    body = req.json()
    if not isinstance(body, dict) or set(body) != {"paused"} or type(body["paused"]) is not bool:
        raise ApiError(400, "bad_policy", 'Send {"paused": true} or {"paused": false}')
    state = "paused" if body["paused"] else "active"
    with app.lock:
        if app.policy.state != state:
            app.policy = replace(app.policy, state=state, version=app.policy.version + 1)
        app.policy.save(app.home / "policy.json")
    return 200, {"policy": app.policy.to_dict()}


def health(app: Review, req: Request):
    return 200, {"ok": True, "version": taskmine.__version__}


def status(app: Review, req: Request):
    try:
        last_call = json.loads(app.store.setting("last_model_call") or "null")
    except ValueError:
        last_call = None
    learning = app.store.label_counts()
    return 200, {**app.store.status(), "policy_state": app.policy.state,
                 "model_name": app.settings.model_name, "last_model_call": last_call,
                 "coverage": coverage(app), "learning": {"examples": learning}}


# ---------------------------------------------------------------- tasks


def list_tasks(app: Review, req: Request):
    limit, before = req.limit_param(), req.cursor_param()
    try:
        rows, cursor = app.store.tasks(limit, before)
    except ValueError:
        raise ApiError(400, "bad_cursor", "Invalid activity cursor") from None
    return 200, {"tasks": _tasks_json(app, rows), "next_cursor": _opaque(cursor),
                 "coverage": coverage(app)}


def task_actions(app: Review, req: Request):
    task_id, limit, before = req.hex_id("Task"), req.limit_param(), req.cursor_param()
    try:
        episodes, actions, cursor = app.store.task_actions(task_id, limit, before)
    except NotFound:
        raise ApiError(404, "not_found", "Task was not found or its evidence expired") from None
    task = app.store.task(task_id)
    if task is None:
        raise ApiError(404, "not_found", "Task was not found or its evidence expired")
    return 200, {"task": _tasks_json(app, [task])[0],
                 "episodes": [_episode_json(e) for e in episodes],
                 "actions": [_action_json(a) for a in actions], "next_cursor": _opaque(cursor)}


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(k) + _strings(v)]
    return [s for v in value for s in _strings(v)] if isinstance(value, list) else []


def correct(app: Review, req: Request):
    task_id, body = req.hex_id("Task"), req.json()
    revision = body.get("expected_revision") if isinstance(body, dict) else None
    op = body.get("operation") if isinstance(body, dict) else None
    if type(revision) is not int or not isinstance(op, dict):
        raise ApiError(400, "bad_correction", "Reload this task before correcting it")
    if any(check.contains_credential(s) for s in _strings(op)):
        where = "this name" if op.get("op") == "rename" else "this correction"
        raise ApiError(422, "credential_detected", f"Remove the password or key from {where}")
    try:
        task = app.store.correct(task_id, revision, op)
    except NotFound as missing:
        other = bool(missing.args) and missing.args[0] != task_id
        raise ApiError(404, "not_found", "The other task was not found" if other
                       else "Task was not found") from None
    except StaleRevision:
        raise ApiError(409, "instance_changed", STALE) from None
    except BadOperation as bad:
        raise ApiError(400, "bad_operation", str(bad)) from None
    return 200, {"task": _tasks_json(app, [task])[0]}


def personal(app: Review, req: Request):
    """Delete a possibly personal task and its actions; keep only today's minutes total."""
    try:
        app.store.delete_personal_task(req.hex_id("Task"), date.today().isoformat())
    except NotFound:
        raise ApiError(404, "not_found", "Task was not found") from None
    except Conflict:
        raise ApiError(409, "not_personal", "This task is not marked as possibly personal") from None
    return 200, {"personal_time": _personal_time(app)}


# ---------------------------------------------------------------- drafts


def make_draft(app: Review, req: Request):
    return drafts.create(app.store, req.hex_id("Task"))


def list_drafts(app: Review, req: Request):
    return 200, {"drafts": drafts.all_drafts(app.store)}


def one_draft(app: Review, req: Request):
    """GET, PATCH or DELETE one draft."""
    did = req.hex_id("Draft")
    changes = drafts.changes(req.json()) if req.method == "PATCH" else None
    if req.method == "DELETE":
        found = {"deleted": True} if app.store.delete_draft(did) else None
    else:
        found = drafts.update(app.store, did, changes) if changes else drafts.get(app.store, did)
        found = found and {"draft": found}
    if found is None:
        raise ApiError(404, "not_found", "Draft was not found")
    return 200, found


# ---------------------------------------------------------------- questions and processes


def _open_questions(app: Review) -> list[dict]:
    return [_pick(q, QUESTION_KEYS) for q in app.store.questions(3, _iso())]


def questions(app: Review, req: Request):
    return 200, {"questions": _open_questions(app)}


def answer(app: Review, req: Request):
    key, body = req.hex_id("Question"), req.json()
    choice = body.get("answer") if isinstance(body, dict) else None
    if not isinstance(choice, str) or choice not in ANSWERS:
        raise ApiError(400, "bad_answer", "Choose one task or two")
    try:
        row = app.store.answer(key, choice)
    except NotFound:
        raise ApiError(404, "not_found", "Question was not found") from None
    except Conflict:
        raise ApiError(409, "question_closed",
                       "This question was already answered. Reload the page.") from None
    try:  # The scorer and the asker learn at once; a failure here never fails the answer.
        if app.scorer is not None and choice in ("one", "two"):
            app.scorer.update(row.get("features") or {}, choice == "one")
        if app.asker is not None and row.get("ask_context"):
            app.asker.reward(row["ask_context"], ANSWERS[choice])
    except (KeyError, ValueError, TypeError):
        pass
    return 200, {"question": _pick(row, QUESTION_KEYS), "questions": _open_questions(app)}


def processes(app: Review, req: Request):
    procs = app.store.processes()[:50]
    ids = sorted({e for p in procs for e in p["episode_ids"]})
    cases = app.store.episode_cases(ids)
    return 200, {"processes": [{**_pick(p, PROCESS_KEYS), "cases": [
        cases[e] for e in p["episode_ids"] if e in cases]} for p in procs]}


# ---------------------------------------------------------------- routing and the boundary


ROUTES = [(m, re.compile(p), f) for m, p, f in [
    ("POST", r"/v1/batches", receive),
    ("GET", r"/v1/policy", get_policy), ("POST", r"/v1/policy", set_policy),
    ("GET", r"/v1/health", health), ("GET", r"/v1/status", status),
    ("GET", r"/v1/tasks", list_tasks),
    ("GET", r"/v1/tasks/([^/]+)/actions", task_actions),
    ("POST", r"/v1/tasks/([^/]+)/correct", correct),
    ("POST", r"/v1/tasks/([^/]+)/personal", personal),
    ("POST", r"/v1/tasks/([^/]+)/draft", make_draft),
    ("GET", r"/v1/drafts", list_drafts),
    ("GET", r"/v1/drafts/([^/]+)", one_draft), ("PATCH", r"/v1/drafts/([^/]+)", one_draft),
    ("DELETE", r"/v1/drafts/([^/]+)", one_draft),
    ("GET", r"/v1/questions", questions), ("POST", r"/v1/questions/([^/]+)/answer", answer),
    ("GET", r"/v1/processes", processes),
]]


def _loopback_host(value: str | None) -> bool:
    """True for 127.0.0.1, localhost or [::1], with or without a port."""
    match = HOST.fullmatch(value or "")
    return match is not None and match.group(1).lower() in LOOPBACK


class Handler(BaseHTTPRequestHandler):
    server_version = "taskmine"
    sys_version = ""

    def log_message(self, format, *args):
        """No paths or bodies in logs."""

    def _handle(self, method: str) -> None:
        self.body_read = False
        try:
            status, body, ctype = self._dispatch(method)
        except ApiError as err:
            status, ctype = err.status, JSON_TYPE
            body = {"error": {"code": err.code, "message": err.message}}
        except Exception:  # noqa: BLE001  (one reply shape for every failure)
            status, ctype = 500, JSON_TYPE
            body = {"error": {"code": "internal_error", "message": "Taskmine hit an error"}}
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self._drain(status)
        self.send_response(status)
        headers = {"Content-Type": ctype, "Content-Length": str(len(data)),
                   "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                   "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                   "Connection": "close"}
        if ctype.startswith("text/html"):
            headers["Content-Security-Policy"] = CSP
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _drain(self, status: int) -> None:
        """Read a body the route left unread, so closing the socket does not reset the reply."""
        if self.body_read or status == 413:
            return
        try:
            size = _length(self)
        except ApiError:
            return
        if 0 < size <= BATCH_MAX:
            self.rfile.read(size)

    def _dispatch(self, method: str):
        port = self.server.server_address[1]
        if not _loopback_host(self.headers.get("Host")):
            raise ApiError(403, "forbidden_host", "Taskmine answers loopback requests only")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{h}:{port}" for h in LOOPBACK}:
            raise ApiError(403, "forbidden_origin", "Requests from other sites are refused")
        parts = urlsplit(self.path)
        if method == "GET" and parts.path in STATIC:
            name, ctype = STATIC[parts.path]
            return 200, (PAGE_DIR / name).read_bytes(), ctype
        found = [(m, f, mo) for m, rx, f in ROUTES if (mo := rx.fullmatch(parts.path))]
        if not found:
            raise ApiError(404, "not_found", "Nothing is here")
        func, match = next(((f, mo) for m, f, mo in found if m == method), (None, None))
        if func is None:
            raise ApiError(405, "method_not_allowed", "This route does not take that method")
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        req = Request(self, method, query, match.groups(),
                      BATCH_MAX if func is receive else PAGE_MAX)
        if method != "GET" and func is not receive:
            if self.headers.get("X-Taskmine-Request") != "page":
                raise ApiError(403, "page_header_required", "Send X-Taskmine-Request: page")
            req.body()
        status, body = func(self.server.app, req)
        return status, body, JSON_TYPE


for _method in ("GET", "POST", "PATCH", "DELETE", "PUT"):
    setattr(Handler, f"do_{_method}", lambda self, m=_method: self._handle(m))


def make_server(store: Store, policy: Policy, settings: Settings, model=None, scorer=None,
                asker=None) -> ThreadingHTTPServer:
    """The review server on 127.0.0.1 and settings.port; call serve_forever to run it."""
    server = ThreadingHTTPServer(("127.0.0.1", settings.port), Handler)
    server.daemon_threads = True
    server.app = Review(store, policy, settings, model, scorer, asker)
    return server
