"""Wire contracts, policy and settings for the taskmine core."""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

__all__ = [
    "SCHEMAS", "MissingModelKey", "Policy", "Settings", "ack", "parse_time", "read_dotenv",
    "validate_ack", "validate_batch", "validate_event", "validate_policy",
]

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "schemas"
PROVENANCES = ("native_accessibility", "native_interaction", "screen_ocr", "app_reader", "synthetic")
_TIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$"
)
_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
}


def _load_schemas(directory: Path) -> dict[str, dict]:
    """Read every *.schema.json file in `directory`, keyed by file name."""
    return {p.name: json.loads(p.read_text()) for p in sorted(directory.glob("*.schema.json"))}


SCHEMAS = _load_schemas(SCHEMA_DIR)


def _child(path: str, key: str) -> str:
    """Join an object key onto a problem path."""
    return f"{path}.{key}" if path else key


def _where(path: str) -> str:
    """Name a path for a problem string."""
    return path or "(document)"


def _compile(pattern: str) -> re.Pattern:
    """Compile a schema pattern so a trailing $ does not match before a newline."""
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        pattern = pattern[:-1] + r"\Z"
    return re.compile(pattern)


def _same(a: object, b: object) -> bool:
    """Compare two JSON values without treating True as 1."""
    return type(a) is type(b) and a == b


def _check(schema: dict, value: object, path: str, out: list[str]) -> None:
    """Append every problem with `value` against `schema` to `out`."""
    if "$ref" in schema:
        _check(SCHEMAS[schema["$ref"]], value, path, out)
        return
    kind = schema.get("type")
    if kind and not _TYPES[kind](value):
        out.append(f"{_where(path)}: expected {kind}")
        return
    if "const" in schema and not _same(value, schema["const"]):
        out.append(f"{_where(path)}: must be {schema['const']!r}")
    if "enum" in schema and not any(_same(value, e) for e in schema["enum"]):
        out.append(f"{_where(path)}: must be one of {', '.join(map(str, schema['enum']))}")
    if isinstance(value, str):
        _check_string(schema, value, path, out)
    elif _TYPES["number"](value):
        _check_number(schema, value, path, out)
    elif isinstance(value, list):
        _check_array(schema, value, path, out)
    elif isinstance(value, dict):
        _check_object(schema, value, path, out)


def _check_string(schema: dict, value: str, path: str, out: list[str]) -> None:
    """Check length, pattern and format of a string."""
    where = _where(path)
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        out.append(f"{where}: longer than {schema['maxLength']} characters")
    if "minLength" in schema and len(value) < schema["minLength"]:
        out.append(f"{where}: shorter than {schema['minLength']} characters")
    if "pattern" in schema and not _compile(schema["pattern"]).search(value):
        out.append(f"{where}: does not match {schema['pattern']}")
    if schema.get("format") == "date-time" and parse_time(value) is None:
        out.append(f"{where}: not a valid date-time")


def _check_number(schema: dict, value: float, path: str, out: list[str]) -> None:
    """Check the bounds of a number."""
    if "minimum" in schema and value < schema["minimum"]:
        out.append(f"{_where(path)}: below minimum {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        out.append(f"{_where(path)}: above maximum {schema['maximum']}")


def _check_array(schema: dict, value: list, path: str, out: list[str]) -> None:
    """Check the size, uniqueness and items of an array."""
    where = _where(path)
    if "minItems" in schema and len(value) < schema["minItems"]:
        out.append(f"{where}: fewer than {schema['minItems']} items")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        out.append(f"{where}: more than {schema['maxItems']} items")
    if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) < len(value):
        out.append(f"{where}: items are not unique")
    if "items" in schema:
        for i, item in enumerate(value):
            _check(schema["items"], item, f"{path}[{i}]", out)


def _check_object(schema: dict, value: dict, path: str, out: list[str]) -> None:
    """Check required, known and nested keys of an object."""
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in value:
            out.append(f"{_child(path, key)}: missing")
    for key, item in value.items():
        if key in props:
            _check(props[key], item, _child(path, key), out)
        elif schema.get("additionalProperties") is False:
            out.append(f"{_child(path, str(key))}: unknown key")


def parse_time(text: str) -> datetime | None:
    """Parse an RFC 3339 date-time, or return None when it is not one."""
    match = _TIME.match(text)
    if not match:
        return None
    *parts, fraction, zone = match.groups()
    micros = int((fraction or "0")[:6].ljust(6, "0"))
    try:
        tz = UTC if zone == "Z" else _offset(zone)
        return datetime(*map(int, parts), micros, tzinfo=tz)
    except ValueError:
        return None


def _offset(zone: str) -> timezone:
    """Turn a +HH:MM or -HH:MM suffix into a timezone."""
    sign = -1 if zone[0] == "-" else 1
    return timezone(sign * timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])))


def _time_order(event: dict, path: str) -> list[str]:
    """Require first_seen_at <= observed_at <= last_seen_at."""
    keys = ("first_seen_at", "observed_at", "last_seen_at")
    raw = [event.get(k) for k in keys]
    times = [parse_time(t) if isinstance(t, str) else None for t in raw]
    if None in times:
        return []
    out = []
    if times[1] < times[0]:
        out.append(f"{_child(path, 'observed_at')}: before first_seen_at")
    if times[2] < times[1]:
        out.append(f"{_child(path, 'last_seen_at')}: before observed_at")
    return out


def _text_hash(content: dict, path: str) -> list[str]:
    """Require text_sha256 to be the SHA-256 of the UTF-8 text."""
    text, digest = content.get("text"), content.get("text_sha256")
    if not isinstance(text, str) or not isinstance(digest, str):
        return []
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
        return [f"{_child(path, 'text_sha256')}: does not match text"]
    return []


def _type_metadata(content: dict, path: str) -> list[str]:
    """Require the metadata key that each content type depends on."""
    needs = {"capture_gap": "gap_reason", "interaction": "interaction_type"}
    key, metadata = needs.get(content.get("type")), content.get("metadata")
    if key and isinstance(metadata, dict) and key not in metadata:
        where = _child(_child(path, "metadata"), key)
        return [f"{where}: required for {content['type']}"]
    return []


def _event_rules(event: object, path: str) -> list[str]:
    """Check the rules that span more than one event field."""
    if not isinstance(event, dict):
        return []
    out = _time_order(event, path)
    content = event.get("content")
    if isinstance(content, dict):
        where = _child(path, "content")
        out += _text_hash(content, where) + _type_metadata(content, where)
    return out


def _batch_rules(batch: dict) -> list[str]:
    """Check each event's own rules and that it came from the batch source."""
    events = batch.get("events")
    if not isinstance(events, list):
        return []
    out = []
    for i, event in enumerate(events):
        out += _event_rules(event, f"events[{i}]")
        source = event.get("source_id") if isinstance(event, dict) else None
        if "source_id" in batch and source is not None and source != batch["source_id"]:
            out.append(f"events[{i}].source_id: does not match batch source_id")
    return out


def _ack_rules(doc: dict) -> list[str]:
    """Require each event revision to appear once across accepted and rejected."""
    seen: set[tuple] = set()
    out = []
    for part in ("accepted", "rejected"):
        entries = doc.get(part)
        for i, entry in enumerate(entries if isinstance(entries, list) else []):
            if not isinstance(entry, dict) or not isinstance(entry.get("event_id"), str):
                continue
            key = (entry["event_id"], entry.get("revision"))
            if key in seen:
                out.append(f"{part}[{i}].event_id: {key[0]} named more than once")
            seen.add(key)
    return out


def _validate(name: str, doc: object) -> list[str]:
    """Validate `doc` against the named schema file."""
    out: list[str] = []
    _check(SCHEMAS[name], doc, "", out)
    return out


def validate_event(event: object) -> list[str]:
    """Return the problems with a taskmine.event/1 document."""
    return _validate("event.schema.json", event) + _event_rules(event, "")


def validate_batch(batch: object) -> list[str]:
    """Return the problems with a taskmine.batch/1 document."""
    out = _validate("batch.schema.json", batch)
    return out + (_batch_rules(batch) if isinstance(batch, dict) else [])


def validate_ack(doc: object) -> list[str]:
    """Return the problems with a taskmine.ack/1 document."""
    out = _validate("ack.schema.json", doc)
    return out + (_ack_rules(doc) if isinstance(doc, dict) else [])


def validate_policy(doc: object) -> list[str]:
    """Return the problems with a taskmine.policy/1 document."""
    return _validate("policy.schema.json", doc)


def ack(batch_id: str, accepted: list[dict], rejected: list[dict]) -> dict:
    """Build a taskmine.ack/1 document."""
    return {
        "schema": "taskmine.ack/1",
        "batch_id": batch_id,
        "accepted": [dict(a) for a in accepted],
        "rejected": [dict(r) for r in rejected],
    }


def _write_private(path: Path, text: str) -> None:
    """Write `text` to `path` atomically with mode 0600."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


@dataclass
class Policy:
    """The capture policy the Mac app polls from the core."""

    version: int = 1
    state: str = "active"
    allowed_apps: list[str] = field(default_factory=lambda: ["*"])
    excluded_domains: list[str] = field(default_factory=list)
    allowed_provenance: list[str] = field(default_factory=lambda: list(PROVENANCES))
    retention_days: int = 30

    @classmethod
    def default(cls) -> Policy:
        """Return the policy a new install starts with."""
        return cls()

    @classmethod
    def from_dict(cls, doc: object) -> Policy:
        """Build a policy from a validated document, or raise ValueError."""
        problems = validate_policy(doc)
        if problems:
            raise ValueError("invalid policy: " + "; ".join(problems))
        return cls(**{k: v for k, v in doc.items() if k != "schema"})

    @classmethod
    def load(cls, path: Path) -> Policy:
        """Read and validate a policy file."""
        return cls.from_dict(json.loads(Path(path).read_text()))

    def to_dict(self) -> dict:
        """Return the policy as a taskmine.policy/1 document."""
        return {"schema": "taskmine.policy/1", **asdict(self)}

    def save(self, path: Path) -> None:
        """Write the policy to `path`, readable only by the owner."""
        _write_private(Path(path), json.dumps(self.to_dict(), indent=2) + "\n")


_DEFAULTS = {
    "home": "~/.taskmine",
    "port": "8797",
    "retention_days": "30",
    "log": "info",
    "model_url": "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
    "model_name": "typesafe-ai/jev",
    "model_key": "",
    "model_calls_per_day": "120",
}
_INTS = ("port", "retention_days", "model_calls_per_day")


class MissingModelKey(RuntimeError):
    """TASKMINE_MODEL_KEY is not set."""


def read_dotenv(path: Path) -> dict[str, str]:
    """Read KEY=value lines from a .env file, dropping comments and blanks."""
    path = Path(path)
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = re.sub(r"(?:^|\s)#.*$", "", value).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _int(name: str, value: str) -> int:
    """Parse an integer setting, naming the variable on failure."""
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


@dataclass(frozen=True)
class Settings:
    """Core settings from the environment and .env."""

    home: Path
    port: int
    retention_days: int
    log: str
    model_url: str
    model_name: str
    model_key: str = field(repr=False)
    model_calls_per_day: int

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, dotenv: Path | None = None
    ) -> Settings:
        """Build settings; the environment wins over .env, which wins over defaults."""
        environ = os.environ if environ is None else environ
        file_values = read_dotenv(Path(".env") if dotenv is None else dotenv)
        values = {}
        for name, default in _DEFAULTS.items():
            var = "TASKMINE_" + name.upper()
            found = (src.get(var, "").strip() for src in (environ, file_values))
            raw = next((v for v in found if v), default)
            values[name] = _int(var, raw) if name in _INTS else raw
        return cls(**{**values, "home": Path(values["home"]).expanduser()})

    def require_model_key(self) -> str:
        """Return the model key, or raise MissingModelKey when it is blank."""
        if not self.model_key:
            raise MissingModelKey("TASKMINE_MODEL_KEY is required; set it in .env")
        return self.model_key
