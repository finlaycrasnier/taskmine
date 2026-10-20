"""Rule files from data/rules, and the ICU-to-Python translation their patterns need."""
from __future__ import annotations

import json
import re
from pathlib import Path

RULES = Path(__file__).resolve().parents[2] / "data" / "rules"

# ICU constructs that Python re lacks, by rule key: (ICU text, Python text).
ICU_TO_PYTHON = {
    "private_key_block": (r"\z", r"\Z"),
    "label_only_line": (r"\z", r"\Z"),
}


def load_rules(name: str) -> dict:
    """One rule file from data/rules."""
    return json.loads((RULES / f"{name}.json").read_text(encoding="utf-8"))


def compile_rule(key: str, pattern: str, flags: int = 0) -> re.Pattern:
    """Compile an ICU rule pattern, translating the constructs listed in ICU_TO_PYTHON."""
    if key in ICU_TO_PYTHON:
        icu, python = ICU_TO_PYTHON[key]
        pattern = pattern.replace(icu, python)
    return re.compile(pattern, flags)
