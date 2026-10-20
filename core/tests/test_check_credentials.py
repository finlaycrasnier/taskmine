"""Credential rules of taskmine.check (reader spec 5.2, fixtures per section 10).

Sources: data/fixtures/credential_cases.json (495 cases) and
data/fixtures/sanitizer_golden.json (494 cases). Failures name the case id, never a value.
"""
import json
from collections import Counter
from pathlib import Path

import pytest
from taskmine import check

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"
CRED = json.loads((FIXTURES / "credential_cases.json").read_text(encoding="utf-8"))
GOLDEN = json.loads((FIXTURES / "sanitizer_golden.json").read_text(encoding="utf-8"))
VALUES = CRED["values"]
CASES = CRED["cases"]
GOLDEN_CASES = GOLDEN["cases"]

REDACTED = "[redacted]"


def value(name: str) -> str:
    return "".join(VALUES[name])


def build(parts: list[str]) -> str:
    # A part is a value reference only when the text after "$" names a value.
    # Literal parts such as "$ deploy\nPassword: " start with "$" but are kept as typed.
    out = []
    for part in parts:
        if part.startswith("$") and part[1:] in VALUES:
            out.append(value(part[1:]))
        else:
            out.append(part)
    return "".join(out)


def by_kind(kind: str, expect: str | None = None) -> list[dict]:
    return [
        c for c in CASES if c["kind"] == kind and (expect is None or c["expect"] == expect)
    ]


def ids(cases: list[dict]) -> list[str]:
    return [c["id"] for c in cases]


TEXT_TITLE = [c for c in CASES if c["kind"] in ("text", "title")]
URL_CASES = by_kind("url")
REDACT_CASES = [c for c in TEXT_TITLE if c["expect"] == "redact"]
SECRET_CASES = [c for c in CASES if "secret" in c]


def run(case: dict) -> "check.Hidden":
    return check.hide(build(case["parts"]), mode=case["kind"], limit=case.get("limit"))


# ---- counts -------------------------------------------------------------------------------


def test_sanitizer_version():
    assert check.SANITIZER_VERSION == 2
    assert CRED["sanitizer_version"] == 2
    assert GOLDEN["sanitizer_version"] == 2
    assert GOLDEN["min_supported_version"] == 2


def test_credential_case_counts():
    assert len(CASES) == 495
    assert Counter(c["kind"] for c in CASES) == {"text": 288, "title": 198, "url": 9}
    assert Counter(c["expect"] for c in CASES) == {
        "redact": 301,
        "exclude": 158,
        "keep": 27,
        "drop": 6,
        "url": 3,
    }
    assert len(VALUES) == 22
    assert len(set(ids(CASES))) == 495


def test_golden_counts():
    assert len(GOLDEN_CASES) == 494
    assert Counter(c["kind"] for c in GOLDEN_CASES) == {"text": 287, "title": 198, "url": 9}
    assert sum(c["output"] is None for c in GOLDEN_CASES) == 164
    assert len(set(ids(GOLDEN_CASES))) == 494


# ---- credential_cases: text and title ----------------------------------------------------


@pytest.mark.parametrize("case", TEXT_TITLE, ids=ids(TEXT_TITLE))
def test_credential_case(case):
    source = build(case["parts"])
    hidden = run(case)
    expect = case["expect"]

    if expect == "exclude":
        assert hidden.excluded is True
        assert hidden.text is None
        if "reason" in case:
            assert hidden.reason == case["reason"]
        return

    assert hidden.excluded is False
    assert hidden.text is not None

    if expect == "keep":
        assert hidden.text == source
        assert hidden.redactions == 0
    elif expect == "redact":
        assert hidden.redactions >= 1
        assert REDACTED in hidden.text
    else:
        pytest.fail(f"unknown expect {expect!r} for a {case['kind']} case")

    for kept in case.get("keep", []):
        assert kept in hidden.text, f"{case['id']}: a listed substring did not survive"

    if "secret" in case:
        secret = value(case["secret"])
        assert secret not in hidden.text, f"{case['id']}: secret value survived"
        if "absent_prefix" in case:
            prefix = secret[: case["absent_prefix"]]
            assert prefix not in hidden.text, f"{case['id']}: secret prefix survived"

    if case.get("limit") is not None:
        assert len(hidden.text) <= case["limit"]

    # Idempotence: the output holds no credential and a second pass changes nothing.
    assert check.contains_credential(hidden.text) is False
    again = check.hide(hidden.text, mode=case["kind"], limit=case.get("limit"))
    assert again.excluded is False
    assert again.text == hidden.text


@pytest.mark.parametrize("case", REDACT_CASES, ids=ids(REDACT_CASES))
def test_redact_input_is_detected(case):
    # contains_credential runs the same rules as hide (spec 5.2), so any input that
    # hide redacts must be detected.
    assert check.contains_credential(build(case["parts"])) is True


@pytest.mark.parametrize("case", SECRET_CASES, ids=ids(SECRET_CASES))
def test_secret_value_never_in_output(case):
    if case["kind"] == "url":
        out = check.sanitize_url(build(case["parts"]))
    else:
        out = run(case).text
    if out is not None:
        assert value(case["secret"]) not in out, f"{case['id']}: secret value survived"


# ---- credential_cases: url ---------------------------------------------------------------


@pytest.mark.parametrize("case", URL_CASES, ids=ids(URL_CASES))
def test_credential_url_case(case):
    source = build(case["parts"])
    expected = case["url"] if case["expect"] == "url" else None
    assert case["expect"] in ("url", "drop")

    assert check.sanitize_url(source) == expected

    hidden = check.hide(source, mode="url")
    assert hidden.text == expected
    assert hidden.excluded is (expected is None)

    if expected is not None:
        assert check.sanitize_url(expected) == expected


# ---- sanitizer_golden ---------------------------------------------------------------------


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=ids(GOLDEN_CASES))
def test_golden(case):
    source = "".join(case["input_parts"])
    expected = case["output"]
    if case["kind"] == "url":
        assert check.sanitize_url(source) == expected
        hidden = check.hide(source, mode="url")
    else:
        hidden = check.hide(source, mode=case["kind"])
    assert hidden.text == expected
    assert hidden.excluded is (expected is None)
