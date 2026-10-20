"""The command line: serve, replay, refit and doctor, plus the pass timing behind serve."""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import secrets
import sqlite3
import stat
import sys
import threading
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from taskmine.contracts import MissingModelKey, Policy, Settings
from taskmine.learn import Asker, Scorer, refit
from taskmine.model import FakeModel, from_settings
from taskmine.store import Store

TICK_S = 15
QUIET_S, BACKSTOP_S = 300, 1200  # newest action quiet; oldest action old
PASS_SPACING_S, BACKSTOP_SPACING_S, PRUNE_SPACING_S = 60, 600, 60
REPLAY_SETTLE_S = 600
REPLAY_SALT = b"taskmine-replay"
STALE_PASS = timedelta(minutes=30)
STALE_REFIT = timedelta(hours=48)
WARN_ONLY = ("coding key", "last pass", "last refit")
ACTION_KINDS = {"click", "edit", "shortcut", "scroll", "focus", "window_focus", "element_focus",
                "navigation", "content_change"}


def run_pass(store, scorer, asker, model, now, *, force=False):
    """One grouping pass over the store; see taskmine.group.run."""
    from taskmine.group import run

    return run(store, scorer, asker, model, now, force=force)


def run_batch(*args, **kwargs):
    """One in-memory grouping pass; see taskmine.group.run_batch."""
    from taskmine.group import run_batch as batch

    return batch(*args, **kwargs)


def make_server(store, policy, settings):
    """The review server; see taskmine.review.make_server."""
    from taskmine.review import make_server as build

    return build(store, policy, settings)


def _dt(value) -> datetime:
    """An aware datetime from a datetime or ISO text; naive values are UTC."""
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _text(value) -> str:
    """UTC ISO 8601 text with a Z."""
    return _dt(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _since(then, now: datetime) -> float:
    """Seconds from `then` to `now`; infinite when `then` is None."""
    return math.inf if then is None else (now - _dt(then)).total_seconds()


def pass_due(newest, oldest, last_pass, last_settled, now, force=False) -> str | None:
    """'settled', 'provisional' or None for the newest and oldest unplaced action times."""
    if newest is None:
        return None
    now = _dt(now)
    if force:
        return "settled"
    settled_ago = _since(last_settled, now)
    if _since(newest, now) >= QUIET_S and settled_ago >= PASS_SPACING_S:
        return "settled"
    if _since(oldest, now) >= BACKSTOP_S and settled_ago >= BACKSTOP_SPACING_S:
        return "settled"
    return "provisional" if _since(last_pass, now) >= PASS_SPACING_S else None


def prune_due(last_prune, now) -> bool:
    """True when no prune has run in the last 60 s."""
    return _since(last_prune, _dt(now)) >= PRUNE_SPACING_S


def _span(actions: list[dict]) -> tuple[str | None, str | None]:
    """The newest last-seen time and the oldest first-seen time of `actions`."""
    if not actions:
        return None, None
    newest = max(_dt(a.get("last_seen_at") or a["observed_at"]) for a in actions)
    oldest = min(_dt(a.get("first_seen_at") or a["observed_at"]) for a in actions)
    return _text(newest), _text(oldest)


def _pause_started(settings: Settings, state: dict) -> bool:
    """True on the first tick after the policy turns paused."""
    try:
        paused = Policy.load(settings.home / "policy.json").state == "paused"
    except (OSError, ValueError):
        paused = False
    started = paused and not state.pop("paused", False)
    if paused:
        state["paused"] = True
    return started


def tick(store, scorer, asker, model, settings: Settings, now, state: dict) -> dict:
    """Prune when due, run a pass when due, refit after a settled pass; `state` keeps the times."""
    out = {"pruned": False, "pass": None, "refit": None}
    stamp = _text(now)
    if prune_due(state.get("last_prune_at"), now):
        store.prune(now, retention_days=settings.retention_days)
        state["last_prune_at"], out["pruned"] = stamp, True
    newest, oldest = _span(store.unplaced_actions())
    force = _pause_started(settings, state)
    kind = pass_due(newest, oldest, state.get("last_pass_at"), state.get("last_settled_at"), now,
                    force=force)
    if kind is None:
        return out
    run_pass(store, scorer, asker, model, now, force=kind == "settled")
    store.set_setting("last_pass_at", stamp)
    state["last_pass_at"], out["pass"] = stamp, kind
    if kind == "settled":
        state["last_settled_at"] = stamp
        out["refit"] = refit(store, now)
    return out


def _make_home(home: Path) -> None:
    """Create the home folder with mode 0700."""
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(home, 0o700)


def first_run(home: Path) -> Policy:
    """Make the home, token and policy when missing; return the policy."""
    _make_home(home)
    if not (home / "token").exists():
        fd = os.open(home / "token", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_urlsafe(32) + "\n")
    if not (home / "policy.json").exists():
        Policy.default().save(home / "policy.json")
    return Policy.load(home / "policy.json")


def _ticker(store, scorer, asker, model, settings: Settings, stop: threading.Event) -> None:
    """Call tick every 15 s until `stop` is set; a failing tick logs its type only."""
    state: dict = {}
    while not stop.wait(TICK_S):
        try:
            tick(store, scorer, asker, model, settings, datetime.now(UTC), state)
        except Exception as exc:  # noqa: BLE001 - the loop must outlive one bad pass
            print(f"taskmine: tick failed: {type(exc).__name__}", file=sys.stderr)


def serve(settings: Settings) -> int:
    """Run the core: review server, 15 s tick and the page in the browser."""
    try:
        settings.require_model_key()
    except MissingModelKey as exc:
        print(f"taskmine: {exc}", file=sys.stderr)
        return 2
    policy = first_run(settings.home)
    store = Store(settings.home / "taskmine.db")
    scorer, asker, model = Scorer.load(store), Asker.load(store), from_settings(settings)
    server = make_server(store, policy, settings)
    stop = threading.Event()
    threading.Thread(target=_ticker, args=(store, scorer, asker, model, settings, stop),
                     daemon=True).start()
    if getattr(sys.stdout, "isatty", lambda: False)():
        webbrowser.open(f"http://127.0.0.1:{settings.port}/")
    try:
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
    finally:
        stop.set()
        server.server_close()
    return 0


def _action(item: dict) -> dict:
    """An old evidence item or a taskmine.event/1 event as a grouper action."""
    f = {**item, **item.get("content", {}), **item.get("content", {}).get("metadata", {})}
    at = f["observed_at"]
    url = f.get("document_url") or f.get("url") or None
    act = f.get("interaction_type") or f.get("trigger")
    return {"id": f.get("event_id") or f["id"], "at": at, "first_seen_at": f.get("first_seen_at") or at,
            "last_seen_at": f.get("last_seen_at") or at, "app": f.get("app_bundle_id") or f.get("app"),
            "app_name": f.get("app_name"), "title": f.get("window_title", f.get("title")),
            "text": f.get("text") or "", "document_id": f.get("document_id"),
            "url": url and urlsplit(url)._replace(query="", fragment="").geturl(),
            "window_ref": f.get("window_ref"), "browser": bool(f.get("browser_domain")),
            "interaction": act if act in ACTION_KINDS else None, "case_ref": f.get("case_ref"),
            "withheld": f.get("type") == "capture_gap" or f.get("capture_method") == "withheld",
            "sightings": int(f.get("sighting_count") or f.get("sightings") or 1),
            "duration_s": int(f.get("duration_s") or 0), "focus_name": f.get("focus_name"),
            "shortcut": f.get("shortcut"), "external_ok": True}


def _field(item, name: str):
    """Read `name` from a dict or an object."""
    return item[name] if isinstance(item, dict) else getattr(item, name)


def replay(path) -> dict:
    """One settled in-memory pass over a recorded day; counts and episode boundaries."""
    doc = json.loads(Path(path).read_text())
    rows = doc if isinstance(doc, list) else doc.get("events") or doc.get("evidence") or []
    if not rows or not all(isinstance(r, dict) and ("observed_at" in r) for r in rows):
        raise SystemExit(f"{path}: not a recorded day (expected a list of events with observed_at)")
    actions = [_action(row) for row in rows]
    now = max(_dt(a["last_seen_at"]) for a in actions) + timedelta(seconds=REPLAY_SETTLE_S)
    result = run_batch(actions, Scorer.from_rules(), Asker.load({}, seed=0), FakeModel({}),
                       _text(now), force=True, salt=REPLAY_SALT, existing=None)
    bounds = sorted((_field(e, "started_at"), _field(e, "ended_at")) for e in result.episodes)
    return {"episodes": len(result.episodes), "tasks": len(result.tasks),
            "processes": len(result.processes),
            "boundaries": [{"from": a, "to": b} for a, b in bounds]}


def _private(path: Path, want: int) -> tuple[bool, str]:
    """Whether `path` exists with exactly mode `want`."""
    if not path.exists():
        return False, f"{path} is missing"
    got = stat.S_IMODE(path.stat().st_mode)
    return got == want, f"{path} mode {got:o}" + ("" if got == want else f", want {want:o}")


def _coding_key(home: Path) -> tuple[bool, str]:
    """The coding key is 64 hex characters with mode 0600."""
    ok, detail = _private(home / "coding.key", 0o600)
    if not ok:
        return ok, detail
    text = (home / "coding.key").read_text().strip()
    good = len(text) == 64 and all(c in "0123456789abcdefABCDEF" for c in text)
    return good, "64 hex characters" if good else "not 64 hex characters"


def _database(path: Path) -> tuple[bool, str]:
    """The database opens read-only and holds the events table."""
    if not path.exists():
        return False, f"{path} is missing"
    try:
        with contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            found = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'events'").fetchone()
    except sqlite3.Error as exc:
        return False, f"unreadable: {type(exc).__name__}"
    return found is not None, "readable" if found else "no events table"


def _policy(path: Path) -> tuple[bool, str]:
    """The policy file loads and validates."""
    try:
        return True, f"state {Policy.load(path).state}"
    except (OSError, ValueError) as exc:
        return False, f"invalid or missing: {type(exc).__name__}"


def _fresh(what: str, last: str | None, waiting: bool, now: datetime, limit) -> tuple[bool, str]:
    """Fail when work waits and the last `what` is missing or older than `limit`."""
    ok = not waiting or (last is not None and now - _dt(last) < limit)
    return ok, f"last {what} {last or 'never'}; " + ("work waits" if waiting else "nothing waits")


def _last_pass(store: Store, now: datetime) -> tuple[bool, str]:
    """Unplaced work older than 30 min needs a pass in the last 30 min."""
    oldest = store.unplaced_actions(limit=1)
    waiting = bool(oldest) and now - _dt(oldest[0]["observed_at"]) >= STALE_PASS
    return _fresh("pass", store.setting("last_pass_at"), waiting, now, STALE_PASS)


def _last_refit(store: Store, now: datetime) -> tuple[bool, str]:
    """New relation labels need a refit in the last 48 h."""
    last = store.setting("last_refit_at")
    return _fresh("refit", last, bool(store.labels("relation", since=last)), now, STALE_REFIT)


def doctor(settings: Settings) -> list[tuple[str, bool, str]]:
    """The eight install checks as (name, ok, detail); details never hold the model key."""
    home, now = settings.home, datetime.now(UTC)
    home_ok = home.is_dir() and stat.S_IMODE(home.stat().st_mode) == 0o700
    db_ok, db_detail = _database(home / "taskmine.db")
    store = Store(home / "taskmine.db") if db_ok else None
    no_db = (False, "no readable database")
    return [
        ("home", home_ok, f"{home} " + ("mode 700" if home_ok else "missing or not mode 700")),
        ("token", *_private(home / "token", 0o600)),
        ("coding key", *_coding_key(home)),
        ("model key", bool(settings.model_key),
         "set" if settings.model_key else "TASKMINE_MODEL_KEY is not set"),
        ("database", db_ok, db_detail),
        ("policy", *_policy(home / "policy.json")),
        ("last pass", *(_last_pass(store, now) if store else no_db)),
        ("last refit", *(_last_refit(store, now) if store else no_db)),
    ]


def _print_doctor(settings: Settings) -> int:
    """Print one `level  name  detail` line per check; 1 on any failure."""
    failed = False
    for name, ok, detail in doctor(settings):
        level = "ok" if ok else "warn" if name in WARN_ONLY else "fail"
        failed = failed or level == "fail"
        print(f"{level}  {name}  {detail}")
    return 1 if failed else 0


def _parser() -> argparse.ArgumentParser:
    """The argument parser with its four commands."""
    parser = argparse.ArgumentParser(prog="taskmine", description="Turn your Mac activity into tasks.")
    sub = parser.add_subparsers(dest="command", metavar="{serve,replay,refit,doctor}")
    sub.add_parser("serve", help="run the core and open My activity")
    sub.add_parser("replay", help="group a recorded day and print counts as JSON").add_argument(
        "file", type=Path)
    sub.add_parser("refit", help="refit the scorer on the answers so far").add_argument(
        "--force", action="store_true", help="ignore the nightly window")
    sub.add_parser("doctor", help="check paths, permissions, key, database, last pass and refit")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one command; 0 on success, 1 on a failed check, 2 on bad usage or a missing key."""
    parser = _parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "replay":
        print(json.dumps(replay(args.file), indent=2))
        return 0
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"taskmine: {exc}", file=sys.stderr)
        return 2
    if args.command == "serve":
        return serve(settings)
    if args.command == "refit":
        _make_home(settings.home)
        store = Store(settings.home / "taskmine.db")
        print(json.dumps(refit(store, datetime.now(UTC), force=args.force), indent=2))
        return 0
    return _print_doctor(settings)
