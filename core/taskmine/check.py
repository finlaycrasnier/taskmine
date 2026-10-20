"""Text safety and tidy rules: hide secrets, code private data, tidy text, exclude surfaces.

The rules come from data/rules (reader spec 4 to 6, grouper spec 3.3). Nothing here logs
or returns a value it removed; only counts and fixed reason codes leave this module.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

from taskmine._coding import code
from taskmine._rules import compile_rule, load_rules

__all__ = [
    "SANITIZER_VERSION", "Hidden", "app_reason", "clean_title", "code", "contains_credential",
    "hide", "is_clock_line", "is_private_window", "sanitize_url", "tidy", "tidy_title", "tidy_url",
    "url_reason",
]

_CRED = load_rules("credential_patterns")
_NORM = load_rules("normalization_rules")
_TITLE = load_rules("title_rules")
_BLOCK = load_rules("blocklist")

SANITIZER_VERSION = 2
REDACTED = _CRED["redacted"]
_LIMITS = _CRED["limits"]
MAX_INPUT = _LIMITS["max_input_scalars"]
MAX_URL = _LIMITS["max_url_scalars"]

_CLEAN = {key: re.compile(_CRED["normalize"][key])
          for key in ("invisible_class", "space_class", "line_break_class")}
_EXCLUDE = {key: compile_rule(key, p) for key, p in _CRED["exclusion_patterns"].items()}
_RULE = {key: compile_rule(key, p) for key, p in _CRED["redaction_patterns"].items()
         if key not in ("label_core", "label")}
_PASSWORD_HOSTS = tuple(_CRED["password_manager_hosts_for_urls"])


@dataclass(frozen=True)
class Hidden:
    """The result of hide: text is None when the whole record is excluded."""

    text: str | None
    redactions: int
    excluded: bool
    reason: str | None


# ---- credentials (reader spec 5.2) ------------------------------------------------------------


def normalize(text: str) -> str:
    """The checking form: NFKC, no invisible characters, plain spaces and line breaks."""
    value = unicodedata.normalize(_CRED["normalize"]["unicode_form"], text[:MAX_INPUT])
    value = _CLEAN["invisible_class"].sub("", value)
    value = _CLEAN["space_class"].sub(" ", value)
    return _CLEAN["line_break_class"].sub("\n", value)


def _decode(token: str) -> str:
    """Percent-decode up to max_decode_rounds times, then normalise."""
    for _ in range(_LIMITS["max_decode_rounds"]):
        decoded = unquote(token)
        if decoded == token:
            break
        token = decoded
    return normalize(token)


def _encoded_private_key(value: str) -> bool:
    """True when a percent-encoded token decodes to a private key header."""
    if "%" not in value:
        return False
    for index, match in enumerate(_RULE["percent_encoded_token"].finditer(value)):
        if index >= _LIMITS["max_decoded_tokens"]:
            return False
        token = match.group(0)
        if len(token) <= MAX_URL and _EXCLUDE["private_key"].search(_decode(token)):
            return True
    return False


def _names_secret_file(value: str, title: bool) -> bool:
    """True when the text names a credential document or the recorder's own files."""
    return bool(
        _EXCLUDE["recorder_files"].search(value)
        or _EXCLUDE["secret_files"].search(value)
        or _EXCLUDE["path_credentials"].search(value)
        or (title and _EXCLUDE["title_credentials_titles_only"].search(value))
    )


def _exclusion(value: str, title: bool = False) -> str | None:
    """The reason the whole record is dropped, or None."""
    if _EXCLUDE["private_key"].search(value) or _encoded_private_key(value):
        return "private_key"
    if _names_secret_file(value, title):
        return "credential_document"
    return None


def _strong_token(token: str) -> bool:
    """True when a long token holds enough digits, upper- and lower-case letters."""
    digits = sum(c.isdigit() for c in token)
    upper = sum("A" <= c <= "Z" for c in token)
    lower = sum("a" <= c <= "z" for c in token)
    return (digits >= _LIMITS["generic_min_digits"] and upper >= _LIMITS["generic_min_upper"]
            and lower >= _LIMITS["generic_min_lower"])


class _Scan:
    """One redaction pass over normalised text, counting what it redacts."""

    def __init__(self, blocks: bool, decode: bool = True):
        self.blocks, self.decode, self.hits, self.seen = blocks, decode, 0, 0

    def hit(self, replacement: str = REDACTED) -> str:
        """Count one redaction and return its replacement."""
        self.hits += 1
        return replacement

    def run(self, value: str) -> str | None:
        """The redacted text, or None when an unterminated quoted value excludes it."""
        if self.decode and _encoded_private_key(value):
            return self.hit()
        if self.blocks:
            value = _EXCLUDE["private_key_block"].sub(lambda m: self.hit(), value)
        if self.decode:
            value = _RULE["percent_encoded_token"].sub(self._encoded, value)
        value = _RULE["userinfo"].sub(self._userinfo, value)
        value = _RULE["query_secret"].sub(self._query, value)
        value = _RULE["bearer"].sub(self._bearer, value)
        value = _RULE["provider_tokens"].sub(lambda m: self.hit(), value)
        value = self._labelled(value)
        if value is None:
            return None
        value = self._split_labels(value)
        return _RULE["generic_long_token"].sub(self._generic, value)

    def _encoded(self, match: re.Match) -> str:
        token = match.group(0)
        self.seen += 1
        if self.seen > _LIMITS["max_decoded_tokens"] or len(token) > MAX_URL:
            return self.hit()
        decoded = _decode(token)
        if _RULE["percent_run"].search(decoded):
            return self.hit()
        if decoded != token:
            inner = _Scan(blocks=False, decode=False)
            inner.run(decoded)
            if inner.hits:
                return self.hit()
        return token

    def _userinfo(self, match: re.Match) -> str:
        info = match.group(2)
        if info == REDACTED or (":" not in info and "%3a" not in info.lower() and len(info) < 20):
            return match.group(0)
        return self.hit(match.group(1) + REDACTED + "@")

    def _query(self, match: re.Match) -> str:
        if match.group(0).endswith("=" + REDACTED):
            return match.group(0)
        return self.hit(match.group(1) + REDACTED)

    def _bearer(self, match: re.Match) -> str:
        token = match.group(2)
        if token == REDACTED or token.isalpha():
            return match.group(0)
        return self.hit(match.group(1) + REDACTED)

    def _generic(self, match: re.Match) -> str:
        token = match.group(0)
        return self.hit() if _strong_token(token) else token

    def _labelled(self, value: str) -> str | None:
        """Redact the value after each label assignment; None on an unterminated quote."""
        matches = list(_RULE["header_label_assignment"].finditer(value))
        pieces, cursor = [], 0
        for index, match in enumerate(matches):
            start = match.end()
            if start < cursor or start == len(value):
                continue
            span = _label_value(value, matches, index)
            if span is None:
                self.hits += 1
                return None
            end, quote = span
            raw = value[start:end]
            if _keep_label_value(raw, quote, match):
                continue
            pieces += [value[cursor:start], self.hit(quote + REDACTED + quote)]
            cursor = end
        pieces.append(value[cursor:])
        return "".join(pieces)

    def _split_labels(self, value: str) -> str:
        """A label alone on a line redacts the next non-empty line within two lines."""
        lines = value.split("\n")
        label_line = _RULE["label_only_line"]
        for index, line in enumerate(lines):
            if not label_line.match(line):
                continue
            for following in range(index + 1, min(index + 3, len(lines))):
                candidate = lines[following].strip()
                if not candidate:
                    continue
                if (candidate != REDACTED and len(candidate) <= _LIMITS["max_split_value_scalars"]
                        and not label_line.match(candidate)):
                    lines[following] = self.hit()
                break
        return "\n".join(lines)


def _closing_quote(value: str, start: int, quote: str) -> int:
    """The index after the closing quote, or -1 when the quote never closes."""
    end = start + 1
    while end < len(value):
        if value[end] == "\\" and quote != "`":
            end += 2
            continue
        if value[end] == quote:
            return end + 1
        end += 1
    return -1


def _label_value(value: str, matches: list[re.Match], index: int) -> tuple[int, str] | None:
    """The end of the value after label match ``index`` and its quote; None if unterminated."""
    start = matches[index].end()
    quote = value[start] if value[start] in "\"'`" else ""
    end = start
    if quote:
        end = _closing_quote(value, start, quote)
        if end < 0:
            return None
        limit = len(value)
    else:
        limit = value.find("\n", start)
        limit = len(value) if limit < 0 else limit
    for following in matches[index + 1:]:
        if following.start() < end:
            continue
        boundary = following.start()
        while boundary > end and value[boundary - 1] in "\\\"'":
            boundary -= 1
        while boundary > end and value[boundary - 1] in ",; \t&":
            boundary -= 1
        limit = min(limit, boundary)
        break
    field = _RULE["field_boundary"].search(value, end, limit)
    if field:
        limit = field.start()
    if quote and value[end:limit].strip(" \t\n})];,"):
        return None
    if not quote:
        end = limit
    while end > start and value[end - 1].isspace():
        end -= 1
    return end, quote


def _keep_label_value(raw: str, quote: str, match: re.Match) -> bool:
    """True for an empty, already redacted or code-like value after a label."""
    if not raw or raw == REDACTED:
        return True
    if quote:
        return raw[1:-1] in ("", REDACTED)
    return bool(match.group(1) and _RULE["code_value_not_a_secret"].fullmatch(raw))


def clip(text: str, limit: int | None) -> str:
    """Clip to ``limit`` scalars without leaving half a token at the end."""
    if limit is None or len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    cut = text[:limit]
    if not text[limit].isspace() and not cut[-1].isspace():
        tail = re.search(r"\S+$", cut)
        cut = cut[: tail.start()] if tail else cut
    return cut


def hide(text: str, mode: str = "text", limit: int | None = None) -> Hidden:
    """Remove secrets from text, a title or a URL (mode "text", "title" or "url")."""
    if mode == "url":
        safe = sanitize_url(text)
        return Hidden(safe, 0, safe is None, None if safe is not None else "credential_url")
    value = normalize(text) if len(text) <= MAX_INPUT else None
    if value is None or len(value) > MAX_INPUT:
        return Hidden(None, 1, True, _CRED["exclusion_reasons"]["oversize_or_unparseable"])
    reason = _exclusion(value, title=mode == "title")
    if reason:
        return Hidden(None, 0, True, reason)
    scan = _Scan(blocks=False)
    result = scan.run(value)
    if result is None:
        return Hidden(None, scan.hits, True, "password_label")
    return Hidden(clip(result, limit), scan.hits, False, None)


def contains_credential(text: str) -> bool:
    """Detection only: the same rules as hide plus private-key blocks."""
    if len(text) > MAX_INPUT:
        return True
    value = normalize(text)
    if len(value) > MAX_INPUT or _EXCLUDE["private_key"].search(value):
        return True
    scan = _Scan(blocks=True)
    scan.run(value)
    return scan.hits > 0


def sanitize_url(url: str) -> str | None:
    """Scheme, host, port and path of a URL; None for a credential-bearing or unusable URL."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL:
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").casefold().rstrip(".")
        _ = parts.port
    except ValueError:
        return None
    if not parts.scheme or not (parts.netloc or parts.path):
        return None
    if any(host == known or host.endswith("." + known) for known in _PASSWORD_HOSTS):
        return None
    path = _decode(parts.path)
    if (_exclusion(path) or _RULE["provider_tokens"].search(path)
            or _RULE["header_label_assignment"].search(path) or _RULE["percent_run"].search(path)
            or _RULE["provider_tokens"].search(host)):
        return None
    netloc = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# ---- tidy (reader spec 6.1) -------------------------------------------------------------------

_MARK = _NORM["markers"]
_TIDY = {key: re.compile(_NORM[key]) for key in ("line_break_class", "invisible_run", "space_run")}
_WEB_ADDRESS = re.compile(_NORM["web_address"], re.IGNORECASE if "i" in _NORM["web_address_flags"] else 0)
_CLOCK_LINE = re.compile(_NORM["clock_line"], re.IGNORECASE)
_TRACKING = frozenset(_NORM["tracking_parameters_exact"])
_TRAILING = _NORM["trailing_punctuation_not_part_of_address"]


def tidy_url(url: str) -> str:
    """The URL without tracking parameters (one marker instead) and with a marked fragment."""
    address, _, fragment = url.partition("#")
    base, mark, query = address.partition("?")
    result = address
    if mark:
        kept, tracked = [], False
        for parameter in filter(None, query.split("&")):
            key = parameter.split("=", 1)[0].lower()
            if key.startswith(_NORM["tracking_parameter_prefix"]) or key in _TRACKING:
                tracked = True
            else:
                kept.append(parameter)
        if tracked:
            kept.append(_MARK["tracking"])
        result = base + ("?" + "&".join(kept) if kept else "")
    if fragment:
        result += "#" + _MARK["fragment"]
    return result


def _tidy_addresses(line: str) -> str:
    """Rewrite every web address in a line; trailing punctuation stays outside it."""
    if "http" not in line.lower():
        return line

    def rewrite(match: re.Match) -> str:
        found = match.group(0)
        address = found.rstrip(_TRAILING)
        return tidy_url(address) + found[len(address):]

    return _WEB_ADDRESS.sub(rewrite, line)


def is_clock_line(line: str) -> bool:
    """True for a line that shows only a time, a relative time or a day name."""
    return _CLOCK_LINE.search(line) is not None


def tidy(text: str) -> str:
    """Lossless tidy: markers for invisible runs, tracking, fragments, repeats and clock lines."""
    text = _TIDY["line_break_class"].sub("\n", text)
    text = _TIDY["invisible_run"].sub(_MARK["invisible"], text)
    runs: list[list] = []
    for raw in text.split("\n"):
        line = _TIDY["space_run"].sub(" ", _tidy_addresses(raw)).strip(" ")
        if not line:
            continue
        if runs and runs[-1][0] == line:
            runs[-1][1] += 1
        else:
            runs.append([line, 1])
    kept: list[str] = []
    for line, times in runs:
        if is_clock_line(line):
            if not kept or kept[-1] != _MARK["clock"]:
                kept.append(_MARK["clock"])
            continue
        kept.append(line)
        if times > 1:
            kept.append(_MARK["repeat"].replace("N", str(times)))
    return "\n".join(kept)


# ---- surfaces (reader spec 4) -----------------------------------------------------------------

_APPS = {reason: tuple(prefixes) for reason, prefixes in _BLOCK["apps"].items()}
_HOSTS = _BLOCK["hosts"]
_HOST_LABELS = _BLOCK["host_label_rules"]
_SIGN_IN_SEGMENTS = frozenset(_BLOCK["sign_in_path_segments"])
_PRIVATE = _BLOCK["private_window"]


def app_reason(bundle_id: str) -> str | None:
    """The blocklist reason for an app bundle ID (prefix match, lower case), or None."""
    value = bundle_id.lower() if isinstance(bundle_id, str) else ""
    if not value:
        return None
    return next((reason for reason, prefixes in _APPS.items() if value.startswith(prefixes)), None)


def _host_reason(host: str) -> str | None:
    """The blocklist reason for a host, in the order of blocklist host_order."""
    host = host.lower().strip(".")
    if not host:
        return None

    def listed(items: list[str]) -> bool:
        return any(host == item or host.endswith("." + item) for item in items)

    labels = host.split(".")
    if listed(_HOSTS["password_manager"]):
        return "password_manager"
    if labels[-1] in _BLOCK["sensitive_top_level_domains"] or listed(_HOSTS["sensitive_domain"]):
        return "sensitive_domain"
    if len(labels) > 2:
        for reason in ("sensitive_domain", "sign_in_page"):
            if labels[0] in _HOST_LABELS[reason]:
                return reason
    return None


def url_reason(url: str) -> str | None:
    """The blocklist reason for an http(s) address: host rules, then sign-in path segments."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    reason = _host_reason(host)
    if reason:
        return reason
    segments = unquote(parts.path).lower().split("/")
    return "sign_in_page" if _SIGN_IN_SEGMENTS.intersection(segments) else None


def is_private_window(title: str, labels: list[str] = ()) -> bool:
    """True when the title or a toolbar label shows a private browsing window."""
    lowered = title.lower() if isinstance(title, str) else ""
    if any(word in lowered for word in _PRIVATE["title_contains"]):
        return True
    if lowered.endswith(tuple(_PRIVATE["title_suffixes"])):
        return True
    for label in labels:
        value = label.lower().strip() if isinstance(label, str) else ""
        if value in _PRIVATE["toolbar_labels_exact"]:
            return True
        if any(word in value for word in _PRIVATE["toolbar_label_contains"]):
            return True
    return False


# ---- titles (grouper spec 3.3, reader spec 6.2) -----------------------------------------------

_T = {key: re.compile(_TITLE[key]) for key in
      ("spinner", "unread_prefix", "unread_tail", "edited", "clock", "iso_datetime", "ago")}
_ZERO_WIDTH = re.compile(_TITLE["zero_width_class"])
_ZERO_WIDTH_RUN = re.compile(_TITLE["zero_width_class"] + "+")
_TITLE_MARK = _TITLE["markers"]
_ANY_MARKER = re.compile(
    "⟨(?:clock|count|busy|edited|tracking|fragment|invisible|repeat×[0-9]+(?: [^⟩]*)?)⟩")
_TITLE_MAX = _TITLE["max_scalars"]


def _collapse(value: str) -> str:
    """Whitespace runs to one space, trimmed."""
    return " ".join(value.split())


def tidy_title(title: str) -> str:
    """The display title: volatile parts replaced by markers, clipped to 240."""
    value = _ZERO_WIDTH_RUN.sub(_TITLE_MARK["invisible"], title)
    value = _collapse(unicodedata.normalize("NFC", value))
    value = _T["spinner"].sub(_TITLE_MARK["busy"] + " ", value, count=1)
    value = _T["unread_prefix"].sub(_TITLE_MARK["count"] + " ", value, count=1)
    value = _T["unread_tail"].sub(" " + _TITLE_MARK["count"], value, count=1)
    value = _T["edited"].sub(" " + _TITLE_MARK["edited"], value, count=1)
    for key in ("iso_datetime", "clock", "ago"):
        value = _T[key].sub(_TITLE_MARK["clock"], value)
    return _collapse(value)[:_TITLE_MAX]


def clean_title(title: str, app_name: str | None = None) -> str:
    """The grouping key of a title: markers and volatile parts removed, clipped to 240."""
    value = _ZERO_WIDTH.sub("", _ANY_MARKER.sub("", title))
    value = _collapse(unicodedata.normalize("NFC", value))
    for _ in range(3):
        before = value
        for key in ("spinner", "unread_prefix", "unread_tail", "edited"):
            value = _T[key].sub("", value)
        if value == before:
            break
    for key in ("iso_datetime", "clock", "ago"):
        value = _T[key].sub("", value)
    if app_name and app_name.strip():
        suffix = r"\s*[–—|-]\s*" + re.escape(app_name.strip()) + "$"
        value = re.sub(suffix, "", value, flags=re.IGNORECASE)
    return _collapse(value).strip(_TITLE["separators_trimmed"])[:_TITLE_MAX]
