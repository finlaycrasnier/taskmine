"""Anchors of an action (grouper spec 2.2): hashed cases, artifact, surface and window."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from taskmine.group.common import keyed, title_of, untitled

ISSUE = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,9})-(\d{1,7})(?![A-Za-z0-9])")
IGNORED = frozenset((
    "UTF", "ISO", "SHA", "GPT", "RFC", "MD", "COVID", "IPV", "TLS", "SSL", "HTTP", "WCAG", "CSS",
    "ES", "PEP", "AES", "RSA", "ECMA", "PDF", "USB", "HDMI", "RGB", "UTC", "GMT", "CET", "BST",
    "PST", "EST", "UX", "UI", "API", "SDK", "OS", "IOS", "MACOS", "WIN", "AM", "PM", "KPI", "OKR",
    "Q", "FY",
))
CASE_PATH = re.compile(
    r"/(issues|pull|pulls|merge_requests|tickets?|cases?|orders?|invoices?|requests?|incidents?"
    r"|deals?|opportunit(?:y|ies)|browse|claims?|applications?|quotes?)/([A-Za-z0-9_.-]{1,64})(?=/|$)")
SEGMENT = (re.compile(r"^(?=.*\d)[A-Za-z0-9_.-]{3,}$"), re.compile(r"^[A-Za-z0-9_-]{16,}$"))
WINDOW_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


@dataclass(frozen=True)
class Anchors:
    """Opaque anchors of one action."""

    case_keys: tuple[tuple[str, str], ...]
    artifact_key: str | None
    surface_key: str | None
    window_key: str | None


def issue_keys(text: str | None, salt: bytes) -> list[tuple[str, str]]:
    """Hashed issue-style keys in the first 500 characters of text."""
    found = []
    for prefix, number in ISSUE.findall((text or "")[:500]):
        if prefix not in IGNORED:
            found.append((keyed(salt, "key", prefix), keyed(salt, "key", prefix, str(int(number)))))
    return found


def _path_keys(url: str | None, salt: bytes) -> list[tuple[str, str]]:
    """Case anchors from a case path in a URL."""
    if not url:
        return []
    parts = urlsplit(url)
    host, path = (parts.hostname or "").lower(), parts.path or ""
    found = []
    for match in CASE_PATH.finditer(path):
        ident = match.group(2)
        whole = ISSUE.fullmatch(ident)
        if whole:
            found += issue_keys(ident, salt)
            continue
        namespace = host + path[:match.start(2)]
        found.append((keyed(salt, "path", namespace), keyed(salt, "path", namespace, ident)))
    return found


def _cases(action: dict, salt: bytes) -> list[tuple[str, str]]:
    """Explicit case anchors in the order of 2.2."""
    ref = action.get("case_ref")
    if isinstance(ref, str) and ref.strip() and len(ref) <= 160:
        return [(keyed(salt, "explicit"), keyed(salt, "explicit", ref.strip()))]
    found = issue_keys(action.get("title"), salt) + _path_keys(action.get("url"), salt)
    found += issue_keys(action.get("focus_name"), salt)
    return list(dict.fromkeys(found))


def _artifact(action: dict, salt: bytes) -> str | None:
    """A document id, else a URL path that names one thing."""
    doc = action.get("document_id")
    if doc and len(doc) <= 512 and not untitled(title_of(action)):
        return keyed(salt, "document", doc)
    if not action.get("url") or doc:
        return None
    parts = urlsplit(action["url"])
    segments = [s for s in (parts.path or "").split("/") if s]
    hits = [i for i, s in enumerate(segments) if any(p.match(s) for p in SEGMENT)]
    if not hits:
        return None
    return keyed(salt, "url", (parts.hostname or "").lower(), "/".join(segments[:hits[-1] + 1]))


def extract(action: dict, salt: bytes) -> Anchors:
    """Anchors of one action; every value is a salted 32-hex hash."""
    title, url = action.get("title") or "", action.get("url") or ""
    surface = (keyed(salt, "surface", action.get("source_id") or "", action.get("app") or "",
                     title, url) if title or url else None)
    ref = action.get("window_ref")
    window = (keyed(salt, "window", action.get("source_id") or "", action.get("app") or "", ref)
              if isinstance(ref, str) and WINDOW_REF.match(ref) else None)
    return Anchors(tuple(_cases(action, salt)), _artifact(action, salt), surface, window)


def _by_namespace(pairs) -> dict[str, set[str]]:
    """Case values grouped by namespace."""
    out: dict[str, set[str]] = {}
    for namespace, value in pairs or ():
        out.setdefault(namespace, set()).add(value)
    return out


def case_conflict(left, right) -> bool:
    """Some namespace appears on both sides with different value sets."""
    a, b = _by_namespace(left), _by_namespace(right)
    return any(a[n] != b[n] for n in a.keys() & b.keys())


def different_cases(left, right) -> bool:
    """Both sides carry cases and they share no pair or conflict in a namespace."""
    a = {tuple(p) for p in left or ()}
    b = {tuple(p) for p in right or ()}
    return bool(a and b) and (not a & b or case_conflict(a, b))
