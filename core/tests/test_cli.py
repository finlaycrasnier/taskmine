"""Tests for taskmine.cli: pass timing, the tick, serve's first run, replay, tidy and doctor.

No server binds and no network is used. `make_server`, `run_pass`, `tidy` and `run_batch` are
patched on the cli module, so these tests do not need taskmine.group or taskmine.review.
Doctor and serve open a real Store under a tmp_path home.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from taskmine import cli
from taskmine.cli import doctor, main, pass_due, prune_due, replay, tick
from taskmine.contracts import Policy, Settings
from taskmine.learn import Asker, Notes
from taskmine.model import FakeModel

ROOT = Path(__file__).resolve().parents[2]
BATCH_FIXTURE = ROOT / "data" / "fixtures" / "contract_fixtures" / "batch.json"

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
CHECK_NAMES = {
    "home", "token", "coding key", "anchor key", "model key", "database", "policy", "last pass",
    "last tidy", "notes",
}
WARN_ONLY = {"coding key", "last pass", "last tidy", "notes"}
SECRET = "sk-test-secret-model-key-0123456789"
CODING_KEY = "ab" * 32
ANCHOR_KEY = "cd" * 32


# ---------------------------------------------------------------- helpers


def at(seconds_before: float, now: datetime = NOW) -> str:
    """UTC text `seconds_before` seconds before `now`."""
    return (now - timedelta(seconds=seconds_before)).strftime("%Y-%m-%dT%H:%M:%SZ")


def as_dt(value) -> datetime:
    """A datetime from a datetime or ISO text with Z."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def settings_for(home: Path, tmp_path: Path, **extra: str) -> Settings:
    env = {"TASKMINE_HOME": str(home), "TASKMINE_MODEL_KEY": SECRET, **extra}
    return Settings.from_env(env, dotenv=tmp_path / "absent.env")


def call(argv: list[str]) -> int:
    """Run main and return its exit code, whether it returns or raises SystemExit."""
    try:
        return main(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else code


@pytest.fixture
def env_home(tmp_path, monkeypatch):
    """main() reads Settings from the environment and ./.env; point both at tmp_path."""
    for key in list(os.environ):
        if key.startswith("TASKMINE_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("TASKMINE_HOME", str(home))
    monkeypatch.setenv("TASKMINE_MODEL_KEY", SECRET)
    return home


# ---------------------------------------------------------------- pass_due


def test_pass_due_nothing_unplaced_is_none():
    assert pass_due(None, None, None, None, at(0)) is None
    assert pass_due(None, None, at(3600), at(3600), at(0)) is None


def test_pass_due_force_is_settled_even_when_newest_is_fresh():
    assert pass_due(at(1), at(1), None, None, at(0), force=True) == "settled"
    assert pass_due(at(1), at(1), at(3600), at(3600), at(0), force=True) == "settled"


def test_pass_due_quiet_at_exactly_300s_is_settled():
    assert pass_due(at(300), at(300), None, None, at(0)) == "settled"


def test_pass_due_quiet_at_299s_is_provisional():
    assert pass_due(at(299), at(299), None, None, at(0)) == "provisional"


def test_pass_due_backstop_at_exactly_1200s_is_settled():
    assert pass_due(at(10), at(1200), None, None, at(0)) == "settled"


def test_pass_due_backstop_at_1199s_is_provisional():
    assert pass_due(at(10), at(1199), None, None, at(0)) == "provisional"


def test_pass_due_quiet_settled_spacing_exactly_60s():
    assert pass_due(at(400), at(400), at(60), at(60), at(0)) == "settled"


def test_pass_due_quiet_settled_within_60s_is_none():
    assert pass_due(at(400), at(400), at(59), at(59), at(0)) is None


def test_pass_due_backstop_spacing_exactly_600s():
    assert pass_due(at(10), at(1500), at(600), at(600), at(0)) == "settled"


def test_pass_due_backstop_within_600s_falls_back_to_provisional():
    assert pass_due(at(10), at(1500), at(60), at(599), at(0)) == "provisional"


def test_pass_due_backstop_within_600s_and_recent_pass_is_none():
    assert pass_due(at(10), at(1500), at(59), at(599), at(0)) is None


def test_pass_due_quiet_uses_quiet_spacing_not_backstop_spacing():
    # Both conditions hold; the quiet rule allows a settled pass 100 s after the last one.
    assert pass_due(at(400), at(1500), at(100), at(100), at(0)) == "settled"


def test_pass_due_provisional_exactly_60s_since_last_pass():
    assert pass_due(at(10), at(100), at(60), at(900), at(0)) == "provisional"


def test_pass_due_provisional_within_60s_is_none():
    assert pass_due(at(10), at(100), at(59), at(900), at(0)) is None


def test_pass_due_first_provisional_without_any_pass():
    assert pass_due(at(5), at(5), None, None, at(0)) == "provisional"


# ---------------------------------------------------------------- prune_due


def test_prune_due_first_time():
    assert prune_due(None, at(0)) is True


def test_prune_due_exactly_60s():
    assert prune_due(at(60), at(0)) is True


def test_prune_due_within_60s():
    assert prune_due(at(59), at(0)) is False
    assert prune_due(at(0), at(0)) is False


# ---------------------------------------------------------------- tick


def action(event_id: str, when: str) -> dict:
    """An unplaced action row; every time column holds the same instant."""
    return {"event_id": event_id, "observed_at": when, "first_seen_at": when,
            "last_seen_at": when, "received_at": when}


class FakeStore:
    def __init__(self, unplaced: list[dict], log: list[str]):
        self.unplaced = unplaced
        self.log = log
        self.settings: dict[str, str] = {}
        self.prune_calls: list[tuple] = []

    def status(self) -> dict:
        return {"events": len(self.unplaced), "tasks": 0, "open_questions": 0,
                "last_pass_at": self.settings.get("last_pass_at"),
                "last_tidy_at": self.settings.get("last_tidy_at")}

    def unplaced_actions(self, limit: int = 3000) -> list[dict]:
        return list(self.unplaced[:limit])

    def prune(self, *args, **kwargs) -> dict:
        self.log.append("prune")
        self.prune_calls.append((args, kwargs))
        return {"events": 0, "episodes": 0, "tasks": 0}

    def setting(self, key: str) -> str | None:
        return self.settings.get(key)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A fake store plus patched run_pass and tidy that log their calls in order."""
    log: list[str] = []
    passes: list[dict] = []
    tidies: list[dict] = []
    tidy_result = {"before": 12, "after": 9, "rewritten": True}
    notes = Notes(tmp_path / "notes.md")
    notes.add("Preview to Excel is usually one task")

    def fake_run(store, asker, model, now, *, force=False, notes=""):
        log.append("run_pass")
        passes.append({"store": store, "asker": asker, "model": model, "now": now,
                       "force": force, "notes": notes})
        return SimpleNamespace(episodes=[], tasks=[], processes=[], decisions=[],
                               placements={}, settled=True)

    def fake_tidy(store, notes, model, now, force=False):
        log.append("tidy")
        tidies.append({"store": store, "notes": notes, "model": model, "now": now,
                       "force": force})
        return tidy_result

    monkeypatch.setattr(cli, "run_pass", fake_run)
    monkeypatch.setattr(cli, "tidy", fake_tidy)
    return SimpleNamespace(
        log=log, passes=passes, tidies=tidies, tidy_result=tidy_result,
        asker=object(), model=object(), notes=notes,
        settings=settings_for(tmp_path / "home", tmp_path, TASKMINE_RETENTION_DAYS="12"),
    )


def run_tick(world, store, now, state):
    return tick(store, world.asker, world.model, world.notes, world.settings, now, state)


def test_tick_prunes_then_settles_then_tidies(world):
    store = FakeStore([action("desktop-1", at(500)), action("desktop-2", at(400))], world.log)
    state: dict = {}
    out = run_tick(world, store, NOW, state)
    assert out == {"pruned": True, "pass": "settled", "tidy": world.tidy_result}
    assert world.log == ["prune", "run_pass", "tidy"]
    p = world.passes[0]
    assert (p["store"], p["asker"], p["model"]) == (store, world.asker, world.model)
    assert p["notes"] == "- Preview to Excel is usually one task\n"
    assert as_dt(p["now"]) == NOW
    t = world.tidies[0]
    assert t["store"] is store and t["notes"] is world.notes and t["model"] is world.model
    assert as_dt(t["now"]) == NOW
    assert t["force"] is False
    for key in ("last_prune_at", "last_pass_at", "last_settled_at"):
        assert as_dt(state[key]) == NOW, key


def test_tick_prune_gets_now_and_retention(world):
    store = FakeStore([], world.log)
    run_tick(world, store, NOW, {})
    (args, kwargs), = store.prune_calls
    values = list(args) + list(kwargs.values())
    assert 12 in values
    assert any(isinstance(v, str | datetime) and as_dt(v) == NOW for v in values)


def test_tick_provisional_pass_skips_tidy(world):
    store = FakeStore([action("desktop-1", at(100)), action("desktop-2", at(10))], world.log)
    state: dict = {}
    out = run_tick(world, store, NOW, state)
    assert out == {"pruned": True, "pass": "provisional", "tidy": None}
    assert world.log == ["prune", "run_pass"]
    assert world.tidies == []
    assert as_dt(state["last_pass_at"]) == NOW
    assert state.get("last_settled_at") is None


def test_tick_nothing_unplaced_runs_no_pass(world):
    store = FakeStore([], world.log)
    state: dict = {}
    out = run_tick(world, store, NOW, state)
    assert out == {"pruned": True, "pass": None, "tidy": None}
    assert world.log == ["prune"]
    assert state.get("last_pass_at") is None and state.get("last_settled_at") is None


def test_tick_spacing_across_ticks(world):
    store = FakeStore([action("desktop-1", at(100)), action("desktop-2", at(10))], world.log)
    state: dict = {}
    assert run_tick(world, store, NOW, state)["pass"] == "provisional"

    later = NOW + timedelta(seconds=15)
    out = run_tick(world, store, later, state)
    assert out == {"pruned": False, "pass": None, "tidy": None}
    assert as_dt(state["last_prune_at"]) == NOW and as_dt(state["last_pass_at"]) == NOW

    minute = NOW + timedelta(seconds=60)
    out = run_tick(world, store, minute, state)
    assert out["pruned"] is True and out["pass"] == "provisional"
    assert as_dt(state["last_prune_at"]) == minute and as_dt(state["last_pass_at"]) == minute
    assert world.log == ["prune", "run_pass", "prune", "run_pass"]


def test_tick_backstop_settles_and_tidies(world):
    store = FakeStore([action("desktop-1", at(1200)), action("desktop-2", at(5))], world.log)
    state: dict = {}
    out = run_tick(world, store, NOW, state)
    assert out["pass"] == "settled" and out["tidy"] == world.tidy_result
    assert as_dt(state["last_settled_at"]) == NOW


def test_tick_respects_recent_settled_pass_in_state(world):
    store = FakeStore([action("desktop-1", at(400))], world.log)
    state = {"last_prune_at": at(30), "last_pass_at": at(30), "last_settled_at": at(30)}
    out = run_tick(world, store, NOW, state)
    assert out == {"pruned": False, "pass": None, "tidy": None}
    assert world.log == []
    assert state == {"last_prune_at": at(30), "last_pass_at": at(30), "last_settled_at": at(30)}


def test_tick_provisional_keeps_earlier_settled_time(world):
    store = FakeStore([action("desktop-1", at(100)), action("desktop-2", at(10))], world.log)
    state = {"last_prune_at": at(120), "last_pass_at": at(120), "last_settled_at": at(120)}
    out = run_tick(world, store, NOW, state)
    assert out["pass"] == "provisional"
    assert as_dt(state["last_pass_at"]) == NOW
    assert as_dt(state["last_settled_at"]) == as_dt(at(120))


# ---------------------------------------------------------------- serve


class FakeServer:
    def __init__(self):
        self.served = 0

    def serve_forever(self, *args, **kwargs):
        self.served += 1

    def shutdown(self):
        pass

    def server_close(self):
        pass


@pytest.fixture
def no_server(monkeypatch):
    calls: list[tuple] = []
    server = FakeServer()

    def fake_make_server(*args, **kwargs):
        calls.append((args, kwargs))
        return server

    monkeypatch.setattr(cli, "make_server", fake_make_server)
    monkeypatch.setattr(cli, "tick", lambda *a, **k: {"pruned": False, "pass": None,
                                                     "tidy": None})
    monkeypatch.setattr(cli, "run_pass", lambda *a, **k: None)
    monkeypatch.setattr(cli, "tidy", lambda *a, **k: None)
    return SimpleNamespace(calls=calls, server=server)


def test_serve_without_model_key_exits_2(env_home, no_server, monkeypatch, capsys):
    monkeypatch.setenv("TASKMINE_MODEL_KEY", "")
    assert call(["serve"]) == 2
    captured = capsys.readouterr()
    assert "TASKMINE_MODEL_KEY" in captured.err
    assert no_server.calls == []


def test_serve_first_run_makes_home_token_and_policy(env_home, no_server):
    assert not env_home.exists()
    assert call(["serve"]) == 0
    assert env_home.is_dir() and mode(env_home) == 0o700

    token = env_home / "token"
    assert mode(token) == 0o600
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token.read_text().strip())

    policy_path = env_home / "policy.json"
    assert mode(policy_path) == 0o600
    assert Policy.load(policy_path) == Policy.default()

    assert no_server.server.served == 1
    (args, kwargs), = no_server.calls
    values = list(args) + list(kwargs.values())
    assert any(isinstance(v, Policy) for v in values)
    assert any(isinstance(v, Settings) and v.home == env_home for v in values)


def test_serve_first_run_makes_anchor_key_and_notes(env_home, no_server):
    assert call(["serve"]) == 0
    key = env_home / "anchor.key"
    assert mode(key) == 0o600
    assert re.fullmatch(r"[0-9a-f]{64}", key.read_text().strip())
    notes = env_home / "notes.md"
    assert mode(notes) == 0o600
    assert notes.read_text() == ""


def test_first_run_makes_anchor_key_and_notes(tmp_path):
    home = tmp_path / "home"
    cli.first_run(home)
    assert mode(home / "anchor.key") == 0o600
    assert re.fullmatch(r"[0-9a-f]{64}", (home / "anchor.key").read_text().strip())
    assert mode(home / "notes.md") == 0o600
    assert (home / "notes.md").read_text() == ""


def test_first_run_keeps_an_existing_anchor_key_and_notes(tmp_path):
    home = tmp_path / "home"
    cli.first_run(home)
    key = (home / "anchor.key").read_text()
    Notes(home / "notes.md").add("Preview to Excel is usually one task")
    cli.first_run(home)
    assert (home / "anchor.key").read_text() == key
    assert Notes(home / "notes.md").lines() == ["Preview to Excel is usually one task"]


def test_anchor_keys_differ_between_homes(tmp_path):
    for name in ("one", "two"):
        cli.first_run(tmp_path / name)
    assert ((tmp_path / "one" / "anchor.key").read_text()
            != (tmp_path / "two" / "anchor.key").read_text())


def test_serve_passes_the_model_to_make_server(env_home, no_server):
    from taskmine.model import DecisionModel

    assert call(["serve"]) == 0
    (args, kwargs), = no_server.calls
    values = list(args) + list(kwargs.values())
    assert any(isinstance(v, DecisionModel) for v in values)


def test_serve_second_run_keeps_token_and_policy(env_home, no_server):
    assert call(["serve"]) == 0
    token = (env_home / "token").read_text()
    paused = Policy(state="paused")
    paused.save(env_home / "policy.json")
    assert call(["serve"]) == 0
    assert (env_home / "token").read_text() == token
    assert Policy.load(env_home / "policy.json").state == "paused"


def test_serve_tokens_differ_between_homes(tmp_path, monkeypatch, no_server):
    tokens = []
    for name in ("one", "two"):
        home = tmp_path / name
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TASKMINE_HOME", str(home))
        monkeypatch.setenv("TASKMINE_MODEL_KEY", SECRET)
        assert call(["serve"]) == 0
        tokens.append((home / "token").read_text().strip())
    assert tokens[0] != tokens[1]


# ---------------------------------------------------------------- replay


def stub_episode(n: int, start: str, end: str) -> dict:
    return {"id": f"{n:032x}", "task_id": f"{n + 100:032x}", "started_at": start,
            "ended_at": end, "first_action_at": start, "last_action_at": end,
            "close_reason": "quiet", "settled": 1}


@pytest.fixture
def batch_stub(monkeypatch):
    calls: list[tuple] = []
    episodes = [
        stub_episode(1, "2026-09-22T10:00:00Z", "2026-09-22T10:05:00Z"),
        stub_episode(2, "2026-09-22T10:20:00Z", "2026-09-22T10:31:00Z"),
    ]
    result = SimpleNamespace(
        episodes=episodes,
        tasks=[{"id": f"{101:032x}"}, {"id": f"{102:032x}"}],
        processes=[{"id": f"{7:032x}"}],
        decisions=[], placements={}, settled=True,
    )

    def fake_run_batch(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(cli, "run_batch", fake_run_batch)
    return SimpleNamespace(calls=calls, result=result)


@pytest.fixture
def replay_file(tmp_path) -> Path:
    path = tmp_path / "day.json"
    path.write_text(BATCH_FIXTURE.read_text())
    return path


EXPECTED_REPLAY = {
    "episodes": 2,
    "tasks": 2,
    "processes": 1,
    "boundaries": [
        {"from": "2026-09-22T10:00:00Z", "to": "2026-09-22T10:05:00Z"},
        {"from": "2026-09-22T10:20:00Z", "to": "2026-09-22T10:31:00Z"},
    ],
}


def test_replay_returns_counts_and_boundaries(replay_file, batch_stub):
    out = replay(replay_file)
    assert {k: out[k] for k in EXPECTED_REPLAY} == EXPECTED_REPLAY


def test_replay_uses_an_asker_and_a_silent_fake_model(replay_file, batch_stub):
    replay(replay_file)
    (args, kwargs), = batch_stub.calls
    values = list(args) + list(kwargs.values())
    models = [v for v in values if isinstance(v, FakeModel)]
    assert len(models) == 1 and models[0].script == {} and models[0].calls == []
    assert any(isinstance(v, Asker) for v in values)


def test_main_replay_prints_json(replay_file, batch_stub, env_home, capsys):
    assert call(["replay", str(replay_file)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert {k: printed[k] for k in EXPECTED_REPLAY} == EXPECTED_REPLAY


# ---------------------------------------------------------------- tidy --force


def test_main_tidy_force_passes_force_and_prints_result(env_home, monkeypatch, capsys):
    env_home.mkdir(mode=0o700)
    calls: list[tuple] = []
    outcome = {"before": 40, "after": 31, "rewritten": True}

    def fake_tidy(*args, **kwargs):
        calls.append((args, kwargs))
        return outcome

    monkeypatch.setattr(cli, "tidy", fake_tidy)
    assert call(["tidy", "--force"]) == 0
    (args, kwargs), = calls
    assert kwargs.get("force") is True
    store, notes = args[0], args[1]
    now = args[3] if len(args) > 3 else kwargs["now"]
    assert hasattr(store, "setting") and hasattr(store, "set_setting")
    assert isinstance(notes, Notes)
    assert isinstance(now, datetime) and now.tzinfo is not None
    assert abs(now - datetime.now(UTC)) < timedelta(minutes=5)
    assert json.loads(capsys.readouterr().out) == outcome


# ---------------------------------------------------------------- doctor


def wire_event(event_id: str, when: str) -> dict:
    """One checked event in the shape Store.put_events takes."""
    text = "Invoice INV-1042 needs approval before payment."
    meta = {"app_bundle_id": "com.apple.TextEdit", "app_name": "TextEdit",
            "window_title": "Invoice review",
            "window_ref": "window:6f1c2e9a-0000-4000-8000-000000000001",
            "sighting_count": 1, "duration_s": 0}
    return {
        "schema": "taskmine.event/1", "event_id": event_id, "revision": 1,
        "source_id": "mac-test", "observed_at": when, "first_seen_at": when,
        "last_seen_at": when, "provenance": "synthetic",
        "content": {"type": "screen_text", "text": text,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "metadata": meta},
        "app": "com.apple.TextEdit", "app_name": "TextEdit", "title": "Invoice review",
        "url": None, "document_id": None, "window_ref": meta["window_ref"], "interaction": None,
        "sightings": 1, "duration_s": 0, "case_keys": [], "artifact_key": None,
        "surface_key": None, "window_key": None,
    }


def ago(seconds: float) -> str:
    """UTC text `seconds` before the real clock; doctor reads the real clock."""
    return at(seconds, datetime.now(UTC))


def healthy_home(tmp_path: Path):
    """A home that passes every check; returns (settings, store)."""
    from taskmine.store import Store

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    os.chmod(home, 0o700)
    for name, text in (("token", "t" * 43 + "\n"), ("coding.key", CODING_KEY + "\n"),
                       ("anchor.key", ANCHOR_KEY + "\n"), ("notes.md", "")):
        (home / name).write_text(text)
        os.chmod(home / name, 0o600)
    Policy.default().save(home / "policy.json")
    store = Store(home / "taskmine.db")
    return settings_for(home, tmp_path), store


def checks_by_name(settings: Settings) -> dict[str, tuple[bool, str]]:
    result = doctor(settings)
    assert len(result) == len(CHECK_NAMES)
    assert {c[0] for c in result} == CHECK_NAMES
    for name, ok, detail in result:
        assert isinstance(ok, bool) and isinstance(detail, str), name
    return {name: (ok, detail) for name, ok, detail in result}


def parse_lines(text: str) -> dict[str, str]:
    """`level  name  detail` lines into {name: level}."""
    out = {}
    for line in text.strip().splitlines():
        parts = line.split("  ", 2)
        assert parts[0] in ("ok", "warn", "fail"), line
        out[parts[1].strip()] = parts[0]
    return out


def test_doctor_all_ok(tmp_path):
    settings, _ = healthy_home(tmp_path)
    got = checks_by_name(settings)
    assert all(ok for ok, _ in got.values()), got


def test_doctor_home_missing_fails(tmp_path):
    settings = settings_for(tmp_path / "nowhere", tmp_path)
    got = checks_by_name(settings)
    assert got["home"][0] is False
    assert got["token"][0] is False


def test_doctor_home_wrong_mode_fails(tmp_path):
    settings, _ = healthy_home(tmp_path)
    os.chmod(settings.home, 0o755)
    try:
        assert checks_by_name(settings)["home"][0] is False
    finally:
        os.chmod(settings.home, 0o700)


def test_doctor_token_missing_fails(tmp_path):
    settings, _ = healthy_home(tmp_path)
    (settings.home / "token").unlink()
    assert checks_by_name(settings)["token"][0] is False


def test_doctor_token_wrong_mode_fails(tmp_path):
    settings, _ = healthy_home(tmp_path)
    os.chmod(settings.home / "token", 0o644)
    assert checks_by_name(settings)["token"][0] is False


@pytest.mark.parametrize("content", [None, "not-hex", "ab" * 31, "zz" * 32])
def test_doctor_coding_key_bad_warns(tmp_path, content):
    settings, _ = healthy_home(tmp_path)
    key = settings.home / "coding.key"
    if content is None:
        key.unlink()
    else:
        key.write_text(content)
    got = checks_by_name(settings)
    assert got["coding key"][0] is False
    others = {n: ok for n, (ok, _) in got.items() if n != "coding key"}
    assert all(others.values()), others


def test_doctor_model_key_missing_fails(tmp_path):
    settings, _ = healthy_home(tmp_path)
    blank = settings_for(settings.home, tmp_path, TASKMINE_MODEL_KEY="")
    assert checks_by_name(blank)["model key"][0] is False


def test_doctor_never_prints_the_model_key(tmp_path):
    settings, _ = healthy_home(tmp_path)
    for _, _, detail in doctor(settings):
        assert SECRET not in detail


def test_doctor_database_not_sqlite_fails(tmp_path):
    settings, _ = healthy_home(tmp_path)
    db = settings.home / "taskmine.db"
    for extra in ("-wal", "-shm"):
        Path(str(db) + extra).unlink(missing_ok=True)
    db.write_bytes(b"this is not a database file at all, not even close" * 100)
    assert checks_by_name(settings)["database"][0] is False


def test_doctor_policy_missing_fails(tmp_path):
    settings, _ = healthy_home(tmp_path)
    (settings.home / "policy.json").unlink()
    assert checks_by_name(settings)["policy"][0] is False


@pytest.mark.parametrize("text", ["{not json", '{"schema": "taskmine.policy/1"}'])
def test_doctor_policy_invalid_fails(tmp_path, text):
    settings, _ = healthy_home(tmp_path)
    (settings.home / "policy.json").write_text(text)
    assert checks_by_name(settings)["policy"][0] is False


def test_doctor_last_pass_ok_without_old_unplaced_work(tmp_path):
    settings, store = healthy_home(tmp_path)
    store.put_events([wire_event("desktop-new", ago(60))], now=ago(60))
    assert checks_by_name(settings)["last pass"][0] is True


def test_doctor_last_pass_ok_when_recent(tmp_path):
    settings, store = healthy_home(tmp_path)
    store.put_events([wire_event("desktop-old", ago(7200))], now=ago(7200))
    store.set_setting("last_pass_at", ago(300))
    assert checks_by_name(settings)["last pass"][0] is True


def test_doctor_last_pass_never_warns(tmp_path):
    settings, store = healthy_home(tmp_path)
    store.put_events([wire_event("desktop-old", ago(7200))], now=ago(7200))
    assert checks_by_name(settings)["last pass"][0] is False


def test_doctor_last_pass_stale_warns(tmp_path):
    settings, store = healthy_home(tmp_path)
    store.put_events([wire_event("desktop-old", ago(7200))], now=ago(7200))
    store.set_setting("last_pass_at", ago(31 * 60))
    assert checks_by_name(settings)["last pass"][0] is False


def set_tidy(store, when: str) -> None:
    store.set_setting("tidy_at", when)
    store.set_setting("last_tidy_at", when)


def test_doctor_last_tidy_ok_without_notes_lines(tmp_path):
    settings, _ = healthy_home(tmp_path)
    assert checks_by_name(settings)["last tidy"][0] is True


def test_doctor_last_tidy_ok_when_recent(tmp_path):
    settings, store = healthy_home(tmp_path)
    store.set_setting("notes_added_at", ago(7200))
    set_tidy(store, ago(3600))
    assert checks_by_name(settings)["last tidy"][0] is True


def test_doctor_last_tidy_ok_when_no_lines_since(tmp_path):
    settings, store = healthy_home(tmp_path)
    store.set_setting("notes_added_at", ago(60 * 3600))
    set_tidy(store, ago(50 * 3600))
    assert checks_by_name(settings)["last tidy"][0] is True


def test_doctor_last_tidy_never_warns(tmp_path):
    settings, store = healthy_home(tmp_path)
    store.set_setting("notes_added_at", ago(7200))
    assert checks_by_name(settings)["last tidy"][0] is False


def test_doctor_last_tidy_stale_warns(tmp_path):
    settings, store = healthy_home(tmp_path)
    store.set_setting("notes_added_at", ago(3600))
    set_tidy(store, ago(49 * 3600))
    assert checks_by_name(settings)["last tidy"][0] is False


def test_doctor_notes_missing_warns(tmp_path):
    settings, _ = healthy_home(tmp_path)
    (settings.home / "notes.md").unlink()
    got = checks_by_name(settings)
    assert got["notes"][0] is False
    others = {n: ok for n, (ok, _) in got.items() if n != "notes"}
    assert all(others.values()), others


def test_doctor_notes_wrong_mode(tmp_path):
    settings, _ = healthy_home(tmp_path)
    os.chmod(settings.home / "notes.md", 0o644)
    assert checks_by_name(settings)["notes"][0] is False


def test_doctor_anchor_key_missing(tmp_path):
    settings, _ = healthy_home(tmp_path)
    (settings.home / "anchor.key").unlink()
    got = checks_by_name(settings)
    assert got["anchor key"][0] is False
    others = {n: ok for n, (ok, _) in got.items() if n != "anchor key"}
    assert all(others.values()), others


def test_doctor_anchor_key_wrong_mode(tmp_path):
    settings, _ = healthy_home(tmp_path)
    os.chmod(settings.home / "anchor.key", 0o644)
    assert checks_by_name(settings)["anchor key"][0] is False


# ---------------------------------------------------------------- main doctor


def test_main_doctor_all_ok_exits_0(env_home, capsys):
    from taskmine.store import Store

    env_home.mkdir(mode=0o700)
    os.chmod(env_home, 0o700)
    for name, text in (("token", "t" * 43), ("coding.key", CODING_KEY),
                       ("anchor.key", ANCHOR_KEY), ("notes.md", "")):
        (env_home / name).write_text(text)
        os.chmod(env_home / name, 0o600)
    Policy.default().save(env_home / "policy.json")
    Store(env_home / "taskmine.db")
    assert call(["doctor"]) == 0
    out = capsys.readouterr().out
    levels = parse_lines(out)
    assert set(levels) == CHECK_NAMES
    assert set(levels.values()) == {"ok"}
    assert SECRET not in out


def test_main_doctor_warnings_only_exit_0(env_home, capsys):
    from taskmine.store import Store

    env_home.mkdir(mode=0o700)
    os.chmod(env_home, 0o700)
    for name, text in (("token", "t" * 43), ("anchor.key", ANCHOR_KEY)):
        (env_home / name).write_text(text)
        os.chmod(env_home / name, 0o600)
    Policy.default().save(env_home / "policy.json")
    store = Store(env_home / "taskmine.db")
    store.set_setting("notes_added_at", ago(7200))
    assert call(["doctor"]) == 0
    levels = parse_lines(capsys.readouterr().out)
    assert levels["coding key"] == "warn"
    assert levels["last tidy"] == "warn"
    assert levels["notes"] == "warn"
    assert {levels[n] for n in CHECK_NAMES if n not in WARN_ONLY} == {"ok"}


def _doctor_home(env_home) -> None:
    from taskmine.store import Store

    env_home.mkdir(mode=0o700)
    os.chmod(env_home, 0o700)
    for name, text in (("token", "t" * 43), ("coding.key", CODING_KEY),
                       ("anchor.key", ANCHOR_KEY), ("notes.md", "")):
        (env_home / name).write_text(text)
        os.chmod(env_home / name, 0o600)
    Policy.default().save(env_home / "policy.json")
    Store(env_home / "taskmine.db")


def test_main_doctor_missing_anchor_key_warns(env_home, capsys):
    _doctor_home(env_home)
    (env_home / "anchor.key").unlink()
    assert call(["doctor"]) == 0
    levels = parse_lines(capsys.readouterr().out)
    assert levels["anchor key"] == "warn"


def test_main_doctor_anchor_key_wrong_mode_fails(env_home, capsys):
    _doctor_home(env_home)
    os.chmod(env_home / "anchor.key", 0o644)
    assert call(["doctor"]) == 1
    levels = parse_lines(capsys.readouterr().out)
    assert levels["anchor key"] == "fail"


def test_main_doctor_failure_exits_1(env_home, monkeypatch, capsys):
    monkeypatch.setenv("TASKMINE_MODEL_KEY", "")
    assert call(["doctor"]) == 1
    levels = parse_lines(capsys.readouterr().out)
    assert set(levels) == CHECK_NAMES
    assert levels["home"] == "fail"
    assert levels["model key"] == "fail"
    assert all(levels[n] in ("ok", "warn") for n in WARN_ONLY)


# ---------------------------------------------------------------- usage


@pytest.mark.parametrize("argv", [[], ["--help"]])
def test_usage(argv, env_home, capsys):
    assert call(argv) == 0
    out = capsys.readouterr().out
    for command in ("serve", "replay", "tidy", "doctor"):
        assert command in out


def test_unknown_command_exits_2(env_home, capsys):
    assert call(["frobnicate"]) == 2


def test_main_default_argv_reads_sys_argv(env_home, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["taskmine", "--help"])
    assert call(None) == 0
    assert "doctor" in capsys.readouterr().out
