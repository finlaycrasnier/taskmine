"""Tests for taskmine.contracts, written from docs/CONTRACTS.md and data/fixtures/schemas.

Each event test starts from the valid fixture, changes one thing, and checks the result.
"""
import copy
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from taskmine.contracts import (
    MissingModelKey,
    Policy,
    Settings,
    ack,
    validate_ack,
    validate_batch,
    validate_event,
    validate_policy,
)

PROVENANCES = [
    "native_accessibility",
    "native_interaction",
    "screen_ocr",
    "app_reader",
    "synthetic",
]
WINDOW_REF = "window:0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
DOCUMENT_ID = "document:" + "a1" * 32


# Helpers


def load_fixture(fixtures_dir: Path, name: str) -> dict:
    return json.loads((fixtures_dir / "contract_fixtures" / f"{name}.json").read_text())


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def set_text(event: dict, text: str) -> dict:
    event["content"]["text"] = text
    event["content"]["text_sha256"] = sha(text)
    return event


def reports(problems: list[str], word: str) -> bool:
    """True when at least one problem names `word`."""
    return any(word in p for p in problems)


@pytest.fixture
def event(fixtures_dir) -> dict:
    return load_fixture(fixtures_dir, "event")


@pytest.fixture
def batch(fixtures_dir) -> dict:
    return load_fixture(fixtures_dir, "batch")


@pytest.fixture
def policy_doc(fixtures_dir) -> dict:
    return load_fixture(fixtures_dir, "policy")


def numbered_events(event: dict, count: int) -> list[dict]:
    events = []
    for i in range(count):
        e = copy.deepcopy(event)
        e["event_id"] = f"evt-{i}"
        events.append(e)
    return events


# Contract fixtures


def test_event_fixture_is_valid(event):
    assert validate_event(event) == []


def test_batch_fixture_is_valid(batch):
    assert validate_batch(batch) == []


def test_ack_fixture_is_valid(fixtures_dir):
    assert validate_ack(load_fixture(fixtures_dir, "ack")) == []


def test_policy_fixture_is_valid(policy_doc):
    assert validate_policy(policy_doc) == []


def test_validate_event_does_not_mutate_input(event):
    before = copy.deepcopy(event)
    validate_event(event)
    assert event == before


# Event: required keys

EVENT_KEYS = [
    "schema",
    "event_id",
    "revision",
    "source_id",
    "observed_at",
    "first_seen_at",
    "last_seen_at",
    "provenance",
    "content",
]
CONTENT_KEYS = ["type", "text", "text_sha256", "metadata"]


@pytest.mark.parametrize("key", EVENT_KEYS)
def test_event_missing_top_level_key_is_reported(event, key):
    del event[key]
    assert reports(validate_event(event), key)


@pytest.mark.parametrize("key", CONTENT_KEYS)
def test_event_missing_content_key_is_reported(event, key):
    del event["content"][key]
    assert reports(validate_event(event), key)


def test_event_missing_app_bundle_id_is_reported(event):
    del event["content"]["metadata"]["app_bundle_id"]
    assert reports(validate_event(event), "app_bundle_id")


def test_event_unknown_top_level_key_is_rejected(event):
    event["extra"] = 1
    assert reports(validate_event(event), "extra")


def test_event_unknown_content_key_is_rejected(event):
    event["content"]["extra"] = 1
    assert reports(validate_event(event), "extra")


def test_event_non_dict_parts_give_problems_not_errors(event):
    event["content"] = "not an object"
    assert validate_event(event) != []


def test_event_non_dict_metadata_gives_problems_not_errors(event):
    event["content"]["metadata"] = []
    assert validate_event(event) != []


# Event: constants, enums, ids, revision


def test_event_wrong_schema_is_rejected(event):
    event["schema"] = "taskmine.event/2"
    assert reports(validate_event(event), "schema")


@pytest.mark.parametrize("value", PROVENANCES)
def test_event_each_provenance_is_accepted(event, value):
    event["provenance"] = value
    assert validate_event(event) == []


def test_event_unknown_provenance_is_rejected(event):
    event["provenance"] = "keylogger"
    assert reports(validate_event(event), "provenance")


def test_event_unknown_content_type_is_rejected(event):
    event["content"]["type"] = "screenshot"
    assert reports(validate_event(event), "type")


def test_event_unknown_text_revision_is_rejected(event):
    event["content"]["metadata"]["text_revision"] = "rewrite"
    assert reports(validate_event(event), "text_revision")


@pytest.mark.parametrize("value", ["append", "similar", "final"])
def test_event_each_text_revision_is_accepted(event, value):
    event["content"]["metadata"]["text_revision"] = value
    assert validate_event(event) == []


@pytest.mark.parametrize("value", ["", "-starts-with-dash", "has space", "a" * 129, "é"])
def test_event_bad_event_id_is_rejected(event, value):
    event["event_id"] = value
    assert reports(validate_event(event), "event_id")


def test_event_longest_event_id_is_accepted(event):
    event["event_id"] = "a" * 128
    assert validate_event(event) == []


def test_event_bad_source_id_is_rejected(event):
    event["source_id"] = "mac/fixture"
    assert reports(validate_event(event), "source_id")


@pytest.mark.parametrize("value", [0, -1, "1", 1.5, True])
def test_event_bad_revision_is_rejected(event, value):
    event["revision"] = value
    assert reports(validate_event(event), "revision")


# Event: text and hash


def test_event_wrong_sha_is_rejected(event):
    event["content"]["text_sha256"] = sha("something else")
    assert reports(validate_event(event), "text_sha256")


def test_event_uppercase_sha_is_rejected(event):
    event["content"]["text_sha256"] = event["content"]["text_sha256"].upper()
    assert reports(validate_event(event), "text_sha256")


def test_event_sha_uses_utf8_bytes(event):
    set_text(event, "Café £ ☃")
    assert validate_event(event) == []


def test_event_text_at_limit_is_accepted(event):
    set_text(event, "x" * 60000)
    assert validate_event(event) == []


def test_event_text_over_limit_is_rejected(event):
    set_text(event, "x" * 60001)
    assert reports(validate_event(event), "text")


def test_event_non_string_text_is_rejected(event):
    event["content"]["text"] = 42
    assert reports(validate_event(event), "text")


# Event: timestamps


def set_times(event: dict, first: str, observed: str, last: str) -> dict:
    event["first_seen_at"] = first
    event["observed_at"] = observed
    event["last_seen_at"] = last
    return event


def test_event_ordered_timestamps_are_accepted(event):
    set_times(event, "2026-09-22T09:59:00Z", "2026-09-22T10:00:00Z", "2026-09-22T10:00:30Z")
    assert validate_event(event) == []


def test_event_fractional_seconds_compare_by_time_not_text(event):
    # As text, "10:00:00.5Z" sorts before "10:00:00Z"; as time it is later.
    set_times(event, "2026-09-22T10:00:00Z", "2026-09-22T10:00:00.5Z", "2026-09-22T10:00:01Z")
    assert validate_event(event) == []


def test_event_observed_before_first_seen_is_rejected(event):
    set_times(event, "2026-09-22T10:00:01Z", "2026-09-22T10:00:00Z", "2026-09-22T10:00:02Z")
    assert validate_event(event) != []


def test_event_last_seen_before_observed_is_rejected(event):
    set_times(event, "2026-09-22T10:00:00Z", "2026-09-22T10:00:02Z", "2026-09-22T10:00:01Z")
    assert validate_event(event) != []


@pytest.mark.parametrize("key", ["observed_at", "first_seen_at", "last_seen_at"])
def test_event_timestamp_with_offset_instead_of_z_is_rejected(event, key):
    event[key] = "2026-09-22T10:00:00+00:00"
    assert reports(validate_event(event), key)


@pytest.mark.parametrize("key", ["observed_at", "first_seen_at", "last_seen_at"])
def test_event_timestamp_without_zone_is_rejected(event, key):
    event[key] = "2026-09-22T10:00:00"
    assert reports(validate_event(event), key)


@pytest.mark.parametrize("value", ["not a dateZ", "2026-13-01T10:00:00Z", "2026-09-22Z"])
def test_event_unparseable_timestamp_is_rejected(event, value):
    event["observed_at"] = value
    assert reports(validate_event(event), "observed_at")


def test_event_timestamp_over_40_chars_is_rejected(event):
    event["last_seen_at"] = "2026-09-22T10:00:00." + "0" * 20 + "Z"
    assert reports(validate_event(event), "last_seen_at")


# Event: metadata


def test_event_unknown_metadata_key_is_rejected(event):
    event["content"]["metadata"]["screen_name"] = "x"
    assert reports(validate_event(event), "screen_name")


def test_event_valid_window_ref_is_accepted(event):
    event["content"]["metadata"]["window_ref"] = WINDOW_REF
    assert validate_event(event) == []


@pytest.mark.parametrize(
    "value",
    [
        "window:123",
        "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
        "window:0F1E2D3C-4B5A-6978-8796-A5B4C3D2E1F0",
        WINDOW_REF + "0",
    ],
)
def test_event_bad_window_ref_is_rejected(event, value):
    event["content"]["metadata"]["window_ref"] = value
    assert reports(validate_event(event), "window_ref")


def test_event_valid_document_id_is_accepted(event):
    event["content"]["metadata"]["document_id"] = DOCUMENT_ID
    assert validate_event(event) == []


@pytest.mark.parametrize(
    "value",
    ["document:abc", "a1" * 32, "document:" + "A1" * 32, DOCUMENT_ID + "a"],
)
def test_event_bad_document_id_is_rejected(event, value):
    event["content"]["metadata"]["document_id"] = value
    assert reports(validate_event(event), "document_id")


@pytest.mark.parametrize("value", ["Example.com", "example.com/path", "-example.com"])
def test_event_bad_browser_domain_is_rejected(event, value):
    event["content"]["metadata"]["browser_domain"] = value
    assert reports(validate_event(event), "browser_domain")


def test_event_valid_optional_metadata_is_accepted(event):
    event["content"]["metadata"].update(
        {
            "browser_domain": "mail.example.com",
            "private_browsing": False,
            "shortcut": "cmd+shift+s",
            "sighting_count": "3",
            "duration_s": "0",
            "codes": "EMAIL=2,PHONE=1",
            "furniture": "0-2,7",
        }
    )
    assert validate_event(event) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("shortcut", "s"),
        ("sighting_count", "0"),
        ("duration_s", "01"),
        ("codes", "email=2"),
        ("furniture", "1-"),
        ("value_omitted", "Has-Caps"),
        ("private_browsing", "yes"),
        ("app_name", 5),
        ("app_bundle_id", "a" * 257),
    ],
)
def test_event_bad_metadata_value_is_rejected(event, key, value):
    event["content"]["metadata"][key] = value
    assert reports(validate_event(event), key)


# Event: capture_gap and interaction


def capture_gap(event: dict, **metadata) -> dict:
    event["content"]["type"] = "capture_gap"
    event["content"]["metadata"] = {"app_bundle_id": "com.example.Bank", **metadata}
    return set_text(event, "")


def interaction(event: dict, **metadata) -> dict:
    event["content"]["type"] = "interaction"
    event["provenance"] = "native_interaction"
    event["content"]["metadata"] = {"app_bundle_id": "com.apple.TextEdit", **metadata}
    return set_text(event, "Save")


def test_capture_gap_with_gap_reason_is_accepted(event):
    capture_gap(event, gap_reason="withheld:banking")
    assert validate_event(event) == []


def test_capture_gap_without_gap_reason_is_rejected(event):
    capture_gap(event)
    assert reports(validate_event(event), "gap_reason")


def test_interaction_with_interaction_type_is_accepted(event):
    interaction(event, interaction_type="click")
    assert validate_event(event) == []


def test_interaction_without_interaction_type_is_rejected(event):
    interaction(event)
    assert reports(validate_event(event), "interaction_type")


# Batch


def test_batch_with_no_events_is_rejected(batch):
    batch["events"] = []
    assert reports(validate_batch(batch), "events")


def test_batch_with_100_events_is_accepted(batch):
    batch["events"] = numbered_events(batch["events"][0], 100)
    assert validate_batch(batch) == []


def test_batch_with_101_events_is_rejected(batch):
    batch["events"] = numbered_events(batch["events"][0], 101)
    assert reports(validate_batch(batch), "events")


def test_batch_event_source_mismatch_is_rejected(batch):
    batch["events"][0]["source_id"] = "mac-other"
    assert reports(validate_batch(batch), "source_id")


def test_batch_reports_invalid_event(batch):
    batch["events"][0]["content"]["text_sha256"] = sha("other")
    assert reports(validate_batch(batch), "text_sha256")


def test_batch_wrong_schema_is_rejected(batch):
    batch["schema"] = "taskmine.event/1"
    assert reports(validate_batch(batch), "schema")


@pytest.mark.parametrize("key", ["schema", "batch_id", "source_id", "events"])
def test_batch_missing_key_is_reported(batch, key):
    del batch[key]
    assert reports(validate_batch(batch), key)


def test_batch_bad_batch_id_is_rejected(batch):
    batch["batch_id"] = "bad id"
    assert reports(validate_batch(batch), "batch_id")


def test_batch_unknown_key_is_rejected(batch):
    batch["extra"] = 1
    assert reports(validate_batch(batch), "extra")


def test_batch_events_not_a_list_gives_problems_not_errors(batch):
    batch["events"] = {"0": batch["events"][0]}
    assert reports(validate_batch(batch), "events")


# Ack

ACCEPTED = [
    {"event_id": "evt-1", "revision": 1, "status": "stored"},
    {"event_id": "evt-2", "revision": 3, "status": "duplicate"},
]
REJECTED = [
    {"event_id": "evt-3", "revision": 1, "code": "credential_detected", "reason": "token"},
]


def test_ack_builds_a_valid_document():
    doc = ack("batch-1", ACCEPTED, REJECTED)
    assert validate_ack(doc) == []


def test_ack_round_trip_keeps_fields():
    doc = ack("batch-1", ACCEPTED, REJECTED)
    assert doc["schema"] == "taskmine.ack/1"
    assert doc["batch_id"] == "batch-1"
    assert doc["accepted"] == ACCEPTED
    assert doc["rejected"] == REJECTED


def test_ack_survives_json_round_trip():
    doc = ack("batch-1", ACCEPTED, REJECTED)
    assert validate_ack(json.loads(json.dumps(doc))) == []


def test_ack_with_empty_lists_is_valid():
    assert validate_ack(ack("batch-1", [], [])) == []


def test_validate_ack_rejects_event_in_accepted_and_rejected():
    rejected = [{"event_id": "evt-1", "revision": 1, "code": "invalid_event"}]
    doc = {"schema": "taskmine.ack/1", "batch_id": "b", "accepted": ACCEPTED[:1], "rejected": rejected}
    assert reports(validate_ack(doc), "evt-1")


def test_validate_ack_rejects_event_twice_in_accepted():
    doc = {
        "schema": "taskmine.ack/1",
        "batch_id": "b",
        "accepted": [ACCEPTED[0], dict(ACCEPTED[0])],
        "rejected": [],
    }
    assert reports(validate_ack(doc), "evt-1")


def test_validate_ack_rejects_unknown_status():
    doc = ack("b", [{"event_id": "e", "revision": 1, "status": "lost"}], [])
    assert reports(validate_ack(doc), "status")


def test_validate_ack_rejects_unknown_code():
    doc = ack("b", [], [{"event_id": "e", "revision": 1, "code": "too_long"}])
    assert reports(validate_ack(doc), "code")


def test_validate_ack_rejects_long_reason():
    doc = ack("b", [], [{"event_id": "e", "revision": 1, "code": "invalid_event", "reason": "x" * 201}])
    assert reports(validate_ack(doc), "reason")


def test_validate_ack_rejects_wrong_schema(fixtures_dir):
    doc = load_fixture(fixtures_dir, "ack")
    doc["schema"] = "taskmine.batch/1"
    assert reports(validate_ack(doc), "schema")


@pytest.mark.parametrize("key", ["schema", "batch_id", "accepted", "rejected"])
def test_validate_ack_missing_key_is_reported(fixtures_dir, key):
    doc = load_fixture(fixtures_dir, "ack")
    del doc[key]
    assert reports(validate_ack(doc), key)


# Policy


def test_policy_default_values():
    policy = Policy.default()
    doc = policy.to_dict()
    assert doc["schema"] == "taskmine.policy/1"
    assert doc["state"] == "active"
    assert doc["allowed_apps"] == ["*"]
    assert doc["retention_days"] == 30
    assert sorted(doc["allowed_provenance"]) == sorted(PROVENANCES)


def test_policy_default_is_valid():
    assert validate_policy(Policy.default().to_dict()) == []


def test_policy_load_matches_fixture(fixtures_dir, policy_doc):
    policy = Policy.load(fixtures_dir / "contract_fixtures" / "policy.json")
    assert policy.to_dict() == policy_doc


def test_policy_save_sets_mode_0600(tmp_path):
    path = tmp_path / "policy.json"
    Policy.default().save(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_policy_save_then_load_round_trip(tmp_path, fixtures_dir):
    original = Policy.load(fixtures_dir / "contract_fixtures" / "policy.json")
    path = tmp_path / "policy.json"
    original.save(path)
    loaded = Policy.load(path)
    assert loaded == original
    assert loaded.to_dict() == original.to_dict()


def test_policy_saved_file_is_valid_json(tmp_path):
    path = tmp_path / "policy.json"
    Policy.default().save(path)
    assert validate_policy(json.loads(path.read_text())) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema", "taskmine.policy/2"),
        ("state", "stopped"),
        ("retention_days", 0),
        ("retention_days", 366),
        ("version", 0),
        ("allowed_provenance", ["webcam"]),
        ("allowed_apps", ["com.a", "com.a"]),
        ("excluded_domains", ["Bank.Example"]),
    ],
)
def test_policy_bad_value_is_rejected(policy_doc, key, value):
    policy_doc[key] = value
    assert reports(validate_policy(policy_doc), key)


def test_policy_missing_key_is_reported(policy_doc):
    del policy_doc["retention_days"]
    assert reports(validate_policy(policy_doc), "retention_days")


def test_policy_unknown_key_is_rejected(policy_doc):
    policy_doc["extra"] = True
    assert reports(validate_policy(policy_doc), "extra")


# Settings


def write_env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_settings_defaults(tmp_path):
    settings = Settings.from_env({}, write_env(tmp_path, ""))
    assert settings.home == Path("~/.taskmine").expanduser()
    assert settings.port == 8797
    assert settings.retention_days == 30
    assert settings.model_calls_per_day == 120


def test_settings_reads_dotenv(tmp_path):
    dotenv = write_env(tmp_path, "TASKMINE_PORT=9001\nTASKMINE_MODEL_NAME=local/test\n")
    settings = Settings.from_env({}, dotenv)
    assert settings.port == 9001
    assert settings.model_name == "local/test"


def test_settings_environment_wins_over_dotenv(tmp_path):
    dotenv = write_env(tmp_path, "TASKMINE_PORT=9001\nTASKMINE_LOG=debug\n")
    settings = Settings.from_env({"TASKMINE_PORT": "9002"}, dotenv)
    assert settings.port == 9002
    assert settings.log == "debug"


def test_settings_reads_every_variable(tmp_path):
    env = {
        "TASKMINE_HOME": str(tmp_path / "home"),
        "TASKMINE_PORT": "9100",
        "TASKMINE_RETENTION_DAYS": "7",
        "TASKMINE_LOG": "warning",
        "TASKMINE_MODEL_URL": "http://127.0.0.1:9/v1",
        "TASKMINE_MODEL_NAME": "fake",
        "TASKMINE_MODEL_KEY": "sk-test",
        "TASKMINE_MODEL_CALLS_PER_DAY": "5",
    }
    settings = Settings.from_env(env, write_env(tmp_path, ""))
    assert settings.home == tmp_path / "home"
    assert settings.port == 9100
    assert settings.retention_days == 7
    assert settings.log == "warning"
    assert settings.model_url == "http://127.0.0.1:9/v1"
    assert settings.model_name == "fake"
    assert settings.model_key == "sk-test"
    assert settings.model_calls_per_day == 5


def test_settings_home_expands_tilde(tmp_path):
    settings = Settings.from_env({"TASKMINE_HOME": "~/tm-test"}, write_env(tmp_path, ""))
    assert settings.home == Path("~/tm-test").expanduser()


def test_settings_dotenv_strips_comment_and_whitespace(tmp_path):
    dotenv = write_env(tmp_path, "  TASKMINE_LOG =  debug   # verbose logs\n")
    assert Settings.from_env({}, dotenv).log == "debug"


def test_settings_dotenv_skips_comment_lines_and_blanks(tmp_path):
    dotenv = write_env(tmp_path, "# a comment\n\n   \nTASKMINE_PORT=9003\n# TASKMINE_PORT=1\n")
    assert Settings.from_env({}, dotenv).port == 9003


def test_settings_reads_env_example_layout(tmp_path):
    dotenv = write_env(
        tmp_path,
        "TASKMINE_PORT=8797               # loopback only\n"
        "TASKMINE_MODEL_KEY=              # required; the core does not start without it\n"
        "# Any OpenAI-compatible decision model works. Jev is the default.\n"
        "TASKMINE_MODEL_CALLS_PER_DAY=120\n",
    )
    settings = Settings.from_env({}, dotenv)
    assert settings.port == 8797
    assert settings.model_calls_per_day == 120


def test_settings_ints_are_ints(tmp_path):
    dotenv = write_env(tmp_path, "TASKMINE_PORT=9004\nTASKMINE_RETENTION_DAYS=14\n")
    settings = Settings.from_env({"TASKMINE_MODEL_CALLS_PER_DAY": "60"}, dotenv)
    assert isinstance(settings.port, int)
    assert isinstance(settings.retention_days, int)
    assert isinstance(settings.model_calls_per_day, int)


def test_settings_from_env_does_not_require_model_key(tmp_path):
    Settings.from_env({}, write_env(tmp_path, ""))


def test_require_model_key_raises_when_missing(tmp_path):
    settings = Settings.from_env({}, write_env(tmp_path, ""))
    with pytest.raises(MissingModelKey):
        settings.require_model_key()


def test_require_model_key_raises_when_blank_with_comment(tmp_path):
    dotenv = write_env(tmp_path, "TASKMINE_MODEL_KEY=     # required\n")
    with pytest.raises(MissingModelKey):
        Settings.from_env({}, dotenv).require_model_key()


def test_require_model_key_returns_key(tmp_path):
    dotenv = write_env(tmp_path, "TASKMINE_MODEL_KEY=sk-from-file\n")
    assert Settings.from_env({}, dotenv).require_model_key() == "sk-from-file"


def test_require_model_key_prefers_environment(tmp_path):
    dotenv = write_env(tmp_path, "TASKMINE_MODEL_KEY=sk-from-file\n")
    settings = Settings.from_env({"TASKMINE_MODEL_KEY": "sk-from-env"}, dotenv)
    assert settings.require_model_key() == "sk-from-env"
