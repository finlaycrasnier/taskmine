"""Check for Receive: the steps one wire event passes before the store keeps it.

Order: surfaces, hide (title and text), code with the coding key, tidy, policy, retention.
A rejection carries a fixed reason code, never the text that caused it.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from taskmine import check
from taskmine.contracts import Policy, parse_time

__all__ = ["check_event"]

# Metadata keys with a fixed pattern or a closed set; every other string is hidden.
STRUCTURED = {"app_bundle_id", "window_ref", "document_id", "browser_domain", "shortcut",
              "sighting_count", "duration_s", "text_revision", "value_omitted", "codes",
              "furniture", "document_url", "private_browsing"}
CODED = ("window_title", "focus_name", "focus_path", "element_id", "control_role", "case_ref",
         "gap_reason")


def _reject(event: dict, code: str, reason: str | None) -> dict:
    out = {"event_id": event["event_id"], "revision": event["revision"], "code": code}
    if reason:
        out["reason"] = reason[:200]
    return out


def _host_of(url: str | None) -> str:
    try:
        return (urlsplit(url).hostname or "") if url else ""
    except ValueError:
        return ""


def _anchors(event: dict, salt: bytes) -> dict:
    """Salted anchor hashes of a checked event, as store columns."""
    try:
        from taskmine.group import anchors
    except ImportError:
        return {}  # anchors filled by group.extract when available
    meta = event["content"]["metadata"]
    action = {"source_id": event.get("source_id"), "app": meta.get("app_bundle_id"),
              "app_name": meta.get("app_name"), "title": meta.get("window_title"),
              "url": event.get("url"), "window_ref": meta.get("window_ref"),
              "document_id": meta.get("document_id"), "case_ref": meta.get("case_ref"),
              "focus_name": meta.get("focus_name")}
    found = anchors.extract(action, salt)
    return {"case_keys": [list(pair) for pair in found.case_keys],
            "artifact_key": found.artifact_key, "surface_key": found.surface_key,
            "window_key": found.window_key}


def check_event(event: dict, *, policy: Policy, key: bytes | None, salt: bytes, now: datetime,
                retention_days: int) -> tuple[dict | None, dict | None]:
    """The event as it may be stored, or its rejection. The steps run in the order below."""
    content = event["content"]
    meta = dict(content["metadata"])
    bundle, title = meta["app_bundle_id"], meta.get("window_title") or ""
    url, domain = meta.get("document_url"), meta.get("browser_domain")
    # 1. Surfaces: blocked apps, private windows, sign-in and sensitive sites.
    reason = check.app_reason(bundle)
    if not reason and (meta.get("private_browsing") is True or check.is_private_window(title)):
        reason = "private_window"
    reason = reason or (url and check.url_reason(url)) or (
        domain and check.url_reason(f"https://{domain}/"))
    if reason:
        return None, _reject(event, "policy_denied", reason)
    # 2. Hide: drop the record on a secret document or key; redact other secrets.
    for name, value in list(meta.items()):
        if name in STRUCTURED or not isinstance(value, str):
            continue
        hidden = check.hide(value, "title" if name == "window_title" else "text")
        if hidden.excluded:
            return None, _reject(event, "credential_detected", hidden.reason)
        meta[name] = hidden.text
    hidden = check.hide(content["text"], "text")
    if hidden.excluded:
        return None, _reject(event, "credential_detected", hidden.reason)
    safe_url = check.hide(url, "url").text if url else None
    meta.pop("document_url", None)
    if safe_url:
        meta["document_url"] = safe_url
    # 3. Code personal values with the key from coding.key (???? without one).
    text = check.code(hidden.text, key)[0]
    for name in CODED:
        if isinstance(meta.get(name), str):
            meta[name] = check.code(meta[name], key)[0]
    # 4. Tidy.
    text = check.tidy(text)
    # 5. Policy: paused, allowed apps and provenance, excluded domains.
    host = (domain or _host_of(safe_url)).lower().rstrip(".")
    if policy.state != "active":
        reason = "paused"
    elif "*" not in policy.allowed_apps and bundle not in policy.allowed_apps:
        reason = "app_not_allowed"
    elif event["provenance"] not in policy.allowed_provenance:
        reason = "provenance_not_allowed"
    elif host and any(host == d or host.endswith("." + d) for d in policy.excluded_domains):
        reason = "excluded_domain"
    if reason:
        return None, _reject(event, "policy_denied", reason)
    # 6. Retention.
    if parse_time(event["observed_at"]) < now - timedelta(days=retention_days):
        return None, _reject(event, "event_expired", "older_than_retention")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    out = {**event, "url": safe_url,
           "content": {**content, "text": text, "text_sha256": digest, "metadata": meta}}
    for column in ("sighting_count", "duration_s"):
        raw = meta.get(column)
        if isinstance(raw, str | int) and str(raw).isdigit():
            out["sightings" if column == "sighting_count" else "duration_s"] = int(raw)
    out.update(_anchors(out, salt))
    return out, None
