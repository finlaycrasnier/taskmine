"""Private-data coding of taskmine.check (reader spec 5.6).

Oracle: private_data in data/fixtures/credential_cases.json (57 cases, 1 keyless).
"""
import json
import re
from pathlib import Path

import pytest
from taskmine import check

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"
PRIVATE = json.loads((FIXTURES / "credential_cases.json").read_text(encoding="utf-8"))[
    "private_data"
]
CASES = PRIVATE["cases"]
KEY = bytes.fromhex(PRIVATE["key_hex"])


def key_for(case: dict) -> bytes | None:
    return None if case.get("keyless") else KEY


def test_private_data_counts():
    assert len(CASES) == 57
    assert sum(1 for c in CASES if c.get("keyless")) == 1
    assert len({c["id"] for c in CASES}) == 57
    assert len(KEY) == 32


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_private_data_case(case):
    text, counts = check.code("".join(case["parts"]), key_for(case))
    assert text == case["coded"]
    assert counts == case["counts"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_coding_is_idempotent(case):
    coded = case["coded"]
    text, _ = check.code(coded, key_for(case))
    assert text == coded


def test_keyless_codes_hash_nothing():
    text, counts = check.code("Contact mario.rossi@acmecorp.it", None)
    assert text == "Contact [EMAIL·corp·????]"
    assert counts == {"EMAIL": 1}


# A form field label is put in front for labelled detection and removed again.
# Expected codes come from the labelled fixture cases with the same label and value.
FIELD_CASES = [
    ("salary", "Salary", "54,000 EUR per year", "[SALARY·331e]", {"SALARY": 1}),
    ("iban_any_value", "IBAN", "12345", "[IBAN·??·b6ab]", {"IBAN": 1}),
    ("email_any_value", "Email", "not an address", "[EMAIL·other·f247]", {"EMAIL": 1}),
    ("partita_iva_checked", "Partita IVA", "01234567897", "[TAXID·IT·1514]", {"TAXID": 1}),
    ("partita_iva_short_kept", "Partita IVA", "12345", "12345", {}),
    ("vat_rate_kept", "VAT", "22%", "22%", {}),
]


@pytest.mark.parametrize(
    "field,value,coded,counts",
    [c[1:] for c in FIELD_CASES],
    ids=[c[0] for c in FIELD_CASES],
)
def test_field_label(field, value, coded, counts):
    text, got = check.code(value, KEY, field=field)
    assert text == coded
    assert got == counts


def test_field_none_is_plain_coding():
    source = "Pay to IT60 X054 2811 1010 0000 0123 456 please"
    assert check.code(source, KEY, field=None) == check.code(source, KEY)


def test_code_shape():
    text, _ = check.code("cc sara.conti@gmail.com", KEY)
    assert re.fullmatch(r"cc \[EMAIL·webmail·[0-9a-f]{4}\]", text)
