"""Shared helpers of the group stages: times, hashes, words and titles."""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import UTC, datetime
from functools import lru_cache
from urllib.parse import urlsplit

from taskmine.check import clean_title
from taskmine.group.words import STOP_WORDS

ACTIONS = frozenset({"click", "edit", "shortcut", "scroll", "focus", "window_focus",
                     "element_focus", "navigation", "content_change"})
WORD = re.compile(r"[^\W\d_]{3,}")
EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{1,4}$")
ENGINE_SPLIT = re.compile(r"\s[–—|-]\s")
STOP = frozenset(STOP_WORDS)
JOINS = frozenset({"same", "supporting"})


@lru_cache(maxsize=1 << 16)
def seconds(value: str) -> float:
    """Epoch seconds of an ISO 8601 time with a timezone."""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"time without a timezone: {value}")
    return moment.timestamp()


def iso(value: float) -> str:
    """ISO 8601 UTC with a Z for epoch seconds."""
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(*parts) -> str:
    """32 hex of sha256 over the parts joined by the unit separator."""
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:32]


def keyed(salt: bytes, *parts) -> str:
    """32 hex of a salted HMAC-SHA256 over the parts joined by the unit separator."""
    data = "\x1f".join(str(p) for p in parts).encode()
    return hmac.new(salt, data, hashlib.sha256).hexdigest()[:32]


def words(text: str | None, limit: int | None = None) -> list[str]:
    """Casefolded runs of three or more letters, stop words removed, in order."""
    if not text:
        return []
    found = WORD.findall(text[:limit] if limit else text)
    return [w for w in (f.casefold() for f in found) if w not in STOP]


@lru_cache(maxsize=1 << 16)
def _clean(raw: str, app: str | None) -> str:
    """The cleaned title, cached."""
    return clean_title(raw, app)


def title_of(item: dict) -> str:
    """The cleaned title of an item, or an empty string."""
    raw = item.get("title")
    if not raw:
        return ""
    return _clean(raw, item.get("app_label") or item.get("app_name"))


def untitled(title: str) -> bool:
    """True when a cleaned title names an unsaved document."""
    return title.casefold().startswith("untitled")


def is_action(item: dict) -> bool:
    """True for an interaction, False for a snapshot."""
    return item.get("interaction") in ACTIONS


def order_key(item: dict) -> tuple:
    """Time order of 1.2: at, snapshot before action, id."""
    return seconds(item["at"]), is_action(item), item["id"]


def web_page(item: dict) -> bool:
    """True for a browser item, an item with a URL or a navigation."""
    return bool(item.get("browser") or item.get("url") or item.get("interaction") == "navigation")


def host_path(url: str | None) -> tuple[str, str]:
    """Lower-cased host and the path with repeated and trailing slashes removed."""
    if not url:
        return "", ""
    parts = urlsplit(url)
    path = re.sub(r"/{2,}", "/", parts.path or "").rstrip("/")
    return (parts.hostname or "").lower(), path


def subject(title: str) -> list[str]:
    """Words of a search title without its engine part."""
    parts = ENGINE_SPLIT.split(title)
    if len(parts) >= 2:
        parts = parts[:-1]
    return words(" ".join(parts))


def related(left, right) -> bool:
    """Two word sets share a word, or a 5-letter word prefixes another of 5 or more."""
    left, right = set(left), set(right)
    if left & right:
        return True
    long_left = [w for w in left if len(w) >= 5]
    long_right = [w for w in right if len(w) >= 5]
    return any(a.startswith(b) or b.startswith(a) for a in long_left for b in long_right)
