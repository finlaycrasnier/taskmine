"""Title templates of repeated work (grouper spec 6.7): the variable part becomes a slot."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from taskmine.group.common import EXTENSION, related, words

TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?|[^\w\s]")
SLOT = "\x00slot"
NO_SPACE_BEFORE = frozenset(",.;:!?)’'")
NO_SPACE_AFTER = frozenset("(/")
NUMERIC = re.compile(r"^[\d\s/.:-]+$")
DATE = re.compile(r"\d{1,4}[/.-]\d{1,2}")
TEMPLATE_MEMBERS = 12


def tokens(title: str) -> list[str]:
    """Words (with an inner apostrophe) and single symbols, without a file extension."""
    return TOKEN.findall(EXTENSION.sub("", (title or "").strip()))


def fold(items: list[str]) -> list[str]:
    """Casefold every token except a slot."""
    return [t if t == SLOT else t.casefold() for t in items]


def render(items: list[str]) -> str:
    """Join tokens with single spaces, none before closing marks or after opening ones."""
    out = ""
    for i, token in enumerate(items):
        if i and token not in NO_SPACE_BEFORE and items[i - 1] not in NO_SPACE_AFTER:
            out += " "
        out += token
    return out


def merge_template(template: list[str], other: list[str]) -> list[str]:
    """Keep the equal spans of the template; every other span becomes one slot."""
    out: list[str] = []
    matcher = SequenceMatcher(None, fold(template), fold(other), autojunk=False)
    for op, i1, i2, _, _ in matcher.get_opcodes():
        if op == "equal":
            out.extend(template[i1:i2])
        elif not out or out[-1] != SLOT:
            out.append(SLOT)
    return out


def template_ok(template: list[str]) -> bool:
    """At least one literal token with a letter."""
    return any(t != SLOT and any(c.isalpha() for c in t) for t in template)


def template_match(a: str, b: str) -> bool:
    """True when two titles align to a template with a literal word."""
    return template_ok(merge_template(tokens(a), tokens(b)))


def slot_values(template: list[str], title: str) -> list[str]:
    """The text that fills each slot of the template in one title."""
    other = tokens(title)
    values: list[str] = []
    matcher = SequenceMatcher(None, fold(template), fold(other), autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        slots = sum(1 for t in template[i1:i2] if t == SLOT)
        if slots:
            values += [render(other[j1:j2])] + [""] * (slots - 1)
    return values


def _best(template: list[str], member: list[dict], used: set, m: int, app: str):
    """The unused same-app artifact of a member that aligns best with the template."""
    best = None
    for a, art in enumerate(member):
        if (m, a) in used or art["app"] != app:
            continue
        other = tokens(art["title"])
        merged = merge_template(template, other)
        if not template_ok(merged):
            continue
        ratio = SequenceMatcher(None, fold(template), fold(other)).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, a, merged)
    return best


def _grow(members: list[list[dict]], used: set, m: int, a: int) -> tuple[list[str], dict]:
    """Align one artifact against every other member; return the template and matches."""
    art = members[m][a]
    template, matched = tokens(art["title"]), {m: a}
    for n, member in enumerate(members):
        if n == m:
            continue
        best = _best(template, member, used, n, art["app"])
        if best:
            template, matched[n] = best[2], best[1]
    return template, matched


def _kind(entry: dict, k: int, found: list[dict]) -> str:
    """Slot type from its values: name, date, number or value."""
    present, recurring = [], 0
    for m, values in entry["values"].items():
        if k >= len(values) or not values[k]:
            continue
        value = values[k]
        present.append(value)
        others = [o for e in found if e is not entry for o in e["values"].get(m, ())]
        if len(value) >= 3 and any(len(o) >= 3 and related(words(value), words(o)) for o in others):
            recurring += 1
    if recurring and recurring * 2 >= len(present):
        return "name"
    if present and all(NUMERIC.match(v) and any(c.isdigit() for c in v) for v in present):
        return "date" if all(DATE.search(v) for v in present) else "number"
    return "value"


def _type_slots(found: list[dict], members: list[list[dict]]) -> None:
    """Give every slot a type and render each template's text."""
    for entry in found:
        entry["values"] = {m: slot_values(entry["tokens"], members[m][a]["title"])
                           for m, a in entry["matches"].items()}
    for entry in found:
        count = sum(1 for t in entry["tokens"] if t == SLOT)
        seen: dict[str, int] = {}
        names = []
        for k in range(count):
            kind = _kind(entry, k, found)
            seen[kind] = seen.get(kind, 0) + 1
            names.append(kind if seen[kind] == 1 else f"{kind}{seen[kind]}")
        slots = iter(names)
        entry["slots"] = names
        entry["text"] = render(["{" + next(slots) + "}" if t == SLOT else t for t in entry["tokens"]])


def build_templates(members: list[list[dict]]) -> list[dict]:
    """Templates over artifact lists, one list per repeat, the most complete first (6.7)."""
    members = [list(m) for m in members[:TEMPLATE_MEMBERS]]
    if len(members) < 2:
        return []
    used: set = set()
    found = []
    for m, member in enumerate(members):
        for a, art in enumerate(member):
            if (m, a) in used:
                continue
            template, matched = _grow(members, used, m, a)
            output = any(members[n][i]["output"] for n, i in matched.items())
            if (len(matched) >= 2 and len(matched) * 2 >= len(members) and template_ok(template)
                    and (SLOT in template or output)):
                used |= set(matched.items())
                found.append({"tokens": template, "app": art["app"], "output": output,
                              "matches": matched})
    _type_slots(found, members)
    return [{"text": e["text"], "app": e["app"], "output": e["output"], "slots": e["slots"],
             "matches": e["matches"]} for e in found]
