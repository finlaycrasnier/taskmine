"""Private-data coding (reader spec 5.6): personal values become typed codes.

A code is [KIND·LABEL·HASH]. HASH is the first two bytes of HMAC-SHA256(key, KIND NUL value);
without a key it is ????. Data and rules: data/rules/private_data_tables.json.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

from taskmine._rules import compile_rule, load_rules

_T = load_rules("private_data_tables")
_RX = {key: compile_rule(key, pattern) for key, pattern in _T["regexes"].items()}
_SEP = _T["separator"]
_PRIORITY = _T["priority_on_overlap"]
_IBAN_LENGTHS = _T["ibanLengths"]
_CALLING = _T["callingCodes"]
_WEBMAIL = frozenset(_T["webmail_domains"])
_OTHER_TLDS = frozenset({"gov", "edu", "mil", "int", "org"})
_ODD = {**{str(d): v for d, v in enumerate(_T["codice_fiscale_odd_values"]["digits_0_to_9"])},
        **{chr(65 + i): v for i, v in enumerate(_T["codice_fiscale_odd_values"]["letters_A_to_Z"])}}

_IBAN_HEAD = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]{2}[0-9]{2}")
_CARD_HEAD = re.compile(r"(?<![A-Za-z0-9_.,+\-/])(?=[0-9])")
_CARD_DIGITS = re.compile(r"[0-9](?:[ \-]?[0-9]){0,18}")


def _label_alternation(words: list[str]) -> str:
    """Label words longest first; spaces match runs of blanks, other characters are literal."""
    ordered = sorted(words, key=lambda word: (-len(word), word))
    return "|".join(r"[ \t]+".join(map(re.escape, word.split(" "))) for word in ordered)


def _label_patterns(template_key: str) -> list[tuple[str, bool, re.Pattern]]:
    """(kind, checked, pattern) for every label list, plain lists first."""
    template = _T["label_regex_template"][template_key]
    lists = [(kind, False, words) for kind, words in _T["field_labels"].items()]
    lists += [(kind, True, words) for kind, words in _T["checked_field_labels"].items()
              if kind != "rule"]
    return [(kind, checked, compile_rule(template_key, re.sub(
        r"<words[^>]*>", lambda _m, w=words: _label_alternation(w), template)))
        for kind, checked, words in lists]


_FIELDS = _label_patterns("field")
_LABEL_ONLY = _label_patterns("label_only_line")


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    kind: str
    label: str
    normal: str


def _alnum(text: str) -> str:
    """The ASCII letters and digits of text."""
    return re.sub(r"[^A-Za-z0-9]", "", text)


def _digits(text: str) -> str:
    """The ASCII digits of text."""
    return re.sub(r"[^0-9]", "", text)


def _mod97(iban: str) -> int:
    """The ISO 7064 remainder of an IBAN; a valid one gives 1."""
    return int("".join(str(int(c, 36)) for c in iban[4:] + iban[:4])) % 97


def _luhn(digits: str) -> bool:
    """True when the digits pass the Luhn check."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char) * (2 if index % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def _card_brand(digits: str) -> str | None:
    """The brand a card number's prefix and length name, or None."""
    size = len(digits)
    if not 13 <= size <= 19:
        return None
    two, three, four = int(digits[:2]), int(digits[:3]), int(digits[:4])
    rules = [
        ("VISA", digits[0] == "4" and size in (16, 19)),
        ("MC", size == 16 and (51 <= two <= 55 or 2221 <= four <= 2720)),
        ("AMEX", size == 15 and two in (34, 37)),
        ("DISCOVER", size >= 16 and (four == 6011 or two == 65 or 644 <= three <= 649)),
        ("DINERS", size >= 14 and (300 <= three <= 305 or four == 3095 or two in (36, 38, 39))),
        ("JCB", size >= 16 and 3528 <= four <= 3589),
        ("UNIONPAY", size >= 16 and two == 62),
    ]
    return next((brand for brand, fits in rules if fits), None)


def _codice_fiscale_valid(value: str) -> bool:
    """True when the sixteenth character is the check letter of the first fifteen."""
    text = value.upper()
    if len(text) != 16:
        return False
    total = 0
    for index, char in enumerate(text[:15]):
        if index % 2 == 0:
            if char not in _ODD:
                return False
            total += _ODD[char]
        else:
            total += int(char) if char.isdigit() else ord(char) - 65
    return text[15] == chr(65 + total % 26)


def _calling_country(digits: str) -> str:
    """The country of a calling code: three digits, then two, then one; else INT."""
    return next((_CALLING[digits[:n]] for n in (3, 2, 1) if digits[:n] in _CALLING), "INT")


def _email_type(address: str) -> str:
    """webmail, other (public bodies and schools) or corp."""
    domain = address.rpartition("@")[2].lower()
    if domain in _WEBMAIL:
        return "webmail"
    labels = domain.split(".")
    if labels[-1] in _OTHER_TLDS or (
            len(labels) >= 3 and labels[-2] in ("gov", "edu", "ac") and len(labels[-1]) == 2):
        return "other"
    return "corp"


def _phone_normal(value: str) -> tuple[str, str]:
    """The phone number as digits with country code, and its country."""
    text = value.strip()
    digits = _digits(text)
    if text.startswith("+"):
        return digits, _calling_country(digits)
    if text.startswith("00") and len(digits) > 4:
        return digits[2:], _calling_country(digits[2:])
    if _RX["italianMobileDigits"].match(digits) or _RX["italianLandlineDigits"].match(digits):
        return "39" + digits, "IT"
    return digits, "INT"


def _looks_like(kind: str, text: str) -> bool:
    """Whether a line after a lone label reads as a value of that kind."""
    if _RX["startsWithCode"].match(text):
        return False
    digits, alnum = len(_digits(text)), len(_alnum(text))
    return {
        "IBAN": alnum >= 10 and digits >= 4,
        "SALARY": digits >= 1 and alnum <= 24,
        "CARD": digits >= 4,
        "PHONE": digits >= 6,
        "EMAIL": "@" in text,
    }.get(kind, alnum >= 9 and digits >= 1)


def _iban_at(line: str, start: int) -> _Span | None:
    """A checked IBAN starting at ``start``, spaces or hyphens allowed inside."""
    size = _IBAN_LENGTHS.get(line[start:start + 2].upper())
    if not size:
        return None
    body = rf"[A-Za-z0-9](?:[ \-]?[A-Za-z0-9]){{{size - 1}}}(?![A-Za-z0-9_])"
    match = re.compile(body).match(line, start)
    if not match:
        return None
    iban = _alnum(match.group(0)).upper()
    return _Span(start, match.end(), "IBAN", iban[:2], iban) if _mod97(iban) == 1 else None


def _ibans(line: str) -> list[_Span]:
    """Every checked IBAN in a line, left to right."""
    spans: list[_Span] = []
    for head in _IBAN_HEAD.finditer(line):
        if spans and head.start() < spans[-1].end:
            continue
        span = _iban_at(line, head.start())
        if span:
            spans.append(span)
    return spans


def _card_at(line: str, start: int) -> _Span | None:
    """The longest branded, Luhn-valid card number of 13 to 19 digits at ``start``."""
    group = _CARD_DIGITS.match(line, start).group(0)
    positions = [start + i for i, char in enumerate(group) if char.isdigit()]
    for count in range(len(positions), 12, -1):
        end = positions[count - 1] + 1
        following = line[end:end + 1]
        if following and (following.isascii() and following.isalnum() or following == "_"):
            continue
        if following in (".", ",") and re.match(r"[0-9]", line[end + 1:end + 2]):
            continue
        number = "".join(line[p] for p in positions[:count])
        brand = _card_brand(number)
        if brand and _luhn(number):
            return _Span(start, end, "CARD", brand, number)
    return None


def _cards(line: str) -> list[_Span]:
    """Every card number in a line, left to right."""
    spans: list[_Span] = []
    for head in _CARD_HEAD.finditer(line):
        if spans and head.start() < spans[-1].end:
            continue
        span = _card_at(line, head.start())
        if span:
            spans.append(span)
    return spans


def _free_spans(line: str) -> list[_Span]:
    """Every value free-text detection finds in a line, overlaps included."""
    spans = _ibans(line) + _cards(line)
    for m in _RX["email"].finditer(line):
        address = m.group(0).lower()
        spans.append(_Span(m.start(), m.end(), "EMAIL", _email_type(address), address))
    for m in _RX["codiceFiscale"].finditer(line):
        if _codice_fiscale_valid(m.group(0)):
            spans.append(_Span(m.start(), m.end(), "TAXID", "IT", m.group(0).upper()))
    for m in _RX["partitaIVA"].finditer(line):
        if _luhn(m.group(1)):
            spans.append(_Span(m.start(), m.end(), "TAXID", "IT", m.group(1)))
    for m in _RX["phoneInternational"].finditer(line):
        digits = _digits(m.group(0))
        if 8 <= len(digits) <= 15:
            spans.append(_Span(m.start(), m.end(), "PHONE", _calling_country(digits), digits))
    for m in _RX["phoneItalianMobile"].finditer(line):
        spans.append(_Span(m.start(), m.end(), "PHONE", "IT", "39" + _digits(m.group(0))))
    return spans


def _checked_extent(value: str) -> tuple[int, int] | None:
    """The leading partita IVA or codice fiscale of a value after a checked label, or None."""
    match = _RX["leadingTaxID"].match(value)
    if not match:
        return None
    normal = _alnum(match.group(0)).upper()
    digits = normal.removeprefix("IT")
    if (_RX["elevenDigits"].match(digits) and _luhn(digits)) or _codice_fiscale_valid(normal):
        return 0, match.end()
    return None


def _field_extent(kind: str, value: str) -> tuple[int, int]:
    """The leading value of the right shape after a label, else the whole value."""
    if kind in ("IBAN", "CARD"):
        spans = _ibans(value) if kind == "IBAN" else _cards(value)
        if spans and spans[0].start == 0:
            return 0, spans[0].end
    leading = {"CARD": "leadingCard", "EMAIL": "email", "PHONE": "leadingPhone",
               "TAXID": "leadingTaxID"}.get(kind)
    match = leading and _RX[leading].match(value)
    return (0, match.end()) if match else (0, len(value))


def _field_label_and_normal(kind: str, value: str) -> tuple[str, str]:
    """The code label and normal form of a labelled value of any shape."""
    if kind == "IBAN":
        normal = _alnum(value).upper()
        return (normal[:2] if _RX["ibanPrefix"].match(normal) else "??"), normal
    if kind == "CARD":
        digits = _digits(value)
        return _card_brand(digits) or "OTHER", digits or _alnum(value).upper()
    if kind == "EMAIL":
        address = value.strip().lower()
        return (_email_type(address) if "@" in address else "other"), address
    if kind == "PHONE":
        normal, country = _phone_normal(value)
        return country, normal
    if kind == "TAXID":
        normal = _alnum(value).upper()
        if _RX["italianVAT"].match(normal):
            normal = normal[2:]
        foreign = (_RX["foreignTaxID"].match(normal) and normal[:2] in _IBAN_LENGTHS
                   and normal[:2] != "IT")
        return (normal[:2] if foreign else "IT"), normal
    return "", _alnum(value).lower()


class _Coder:
    """Codes one text under one key and counts the codes it makes."""

    def __init__(self, key: bytes | None):
        self.key = key or None
        self.counts: dict[str, int] = {}

    def code_for(self, kind: str, label: str, normal: str) -> str:
        """The code string; counts it."""
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if self.key is None:
            digest = _T["keyless_suffix"]
        else:
            message = (kind + "\0" + normal).encode("utf-8")
            digest = hmac.new(self.key, message, hashlib.sha256).hexdigest()[:4]
        return "[" + _SEP.join([kind] + ([label] if label else []) + [digest]) + "]"

    def field_value(self, kind: str, value: str, checked: bool) -> str | None:
        """The value after a label with its leading part coded; None leaves it as it is."""
        extent = _checked_extent(value) if checked else _field_extent(kind, value)
        if extent is None:
            return None
        start, end = extent
        label, normal = _field_label_and_normal(kind, value[start:end])
        return value[:start] + self.code_for(kind, label, normal) + value[end:]

    def labelled_line(self, line: str) -> str | None:
        """The line with every labelled value coded, or None when nothing changed."""
        found = sorted((m.start(), m.end(), kind, checked)
                       for kind, checked, pattern in _FIELDS for m in pattern.finditer(line))
        output, cursor = [], 0
        for position, (_start, value_start, kind, checked) in enumerate(found):
            if value_start < cursor or value_start >= len(line):
                continue
            stop = len(line)
            boundary = _RX["nextField"].search(line, value_start)
            if boundary:
                stop = boundary.start()
            later = next((s for s, *_ in found[position + 1:] if s > value_start), None)
            if later is not None:
                stop = min(stop, later)
            value = line[value_start:stop].rstrip()
            if not value or _RX["startsWithCode"].match(value):
                continue
            replacement = self.field_value(kind, value, checked)
            if replacement is None:
                continue
            output += [line[cursor:value_start], replacement]
            cursor = value_start + len(value)
        return "".join(output) + line[cursor:] if output else None

    def labelled(self, lines: list[str]) -> list[str]:
        """Code labelled values; a label alone on a line codes the next non-empty line."""
        for index, line in enumerate(lines):
            coded = self.labelled_line(line)
            if coded is not None:
                lines[index] = coded
                continue
            only = next(((k, c) for k, c, p in _LABEL_ONLY if p.match(line)), None)
            if only:
                self.code_next_line(lines, index, *only)
        return lines

    def code_next_line(self, lines: list[str], index: int, kind: str, checked: bool) -> None:
        """Code the first non-empty line within two lines when it looks like a ``kind`` value."""
        for following in range(index + 1, min(index + 3, len(lines))):
            candidate = lines[following].strip()
            if not candidate:
                continue
            if _looks_like(kind, candidate):
                replacement = self.field_value(kind, candidate, checked)
                if replacement is not None:
                    raw = lines[following]
                    lines[following] = raw[:len(raw) - len(raw.lstrip())] + replacement
            return

    def free(self, line: str) -> str:
        """Code values found without a label, skipping spans that touch an existing code."""
        taken = [(m.start(), m.end()) for m in _RX["codePattern"].finditer(line)]
        spans = [s for s in _free_spans(line)
                 if not any(s.start < end and start < s.end for start, end in taken)]
        spans.sort(key=lambda s: (s.start, _PRIORITY[s.kind], s.start - s.end))
        output, cursor = [], 0
        for span in spans:
            if span.start < cursor:
                continue
            output += [line[cursor:span.start], self.code_for(span.kind, span.label, span.normal)]
            cursor = span.end
        return "".join(output) + line[cursor:]


def code(text: str, key: bytes | None, field: str | None = None) -> tuple[str, dict[str, int]]:
    """Text with personal values replaced by codes, and how many codes of each kind were made.

    ``field`` is a form field label: it is put in front for labelled detection, then removed.
    """
    if field:
        prefix = f"{field}: "
        coded, counts = code(prefix + text, key)
        return coded.removeprefix(prefix), counts
    if not text:
        return text, {}
    coder = _Coder(key)
    lines = coder.labelled(text.split("\n"))
    return "\n".join(coder.free(line) for line in lines), coder.counts
