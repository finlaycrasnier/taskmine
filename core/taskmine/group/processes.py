"""Repeated processes (grouper spec 6): similarity, matching, process ids, wording and runs."""
from __future__ import annotations

import statistics
from difflib import SequenceMatcher

from taskmine.group.common import digest, seconds
from taskmine.group.templates import build_templates, template_match

CANDIDATE_SCORE, RULE_MATCH_SCORE, PREFILTER = 0.6, 0.8, 0.5
OUTPUT_CAP = RULE_MATCH_SCORE - 0.1
RUN_GAP = (-120.0, 600.0)
INTERRUPTION = 120.0
NO_TEMPLATE = frozenset({"create", "type", "close", "copy", "paste", "click"})


def _get(item, name: str):
    """Read a field from a dict or an object."""
    return item[name] if isinstance(item, dict) else getattr(item, name)


def pairs_of(sig) -> list[tuple[str, str]]:
    """The (kind, app) pairs of a signature's steps."""
    return [(_get(s, "kind"), _get(s, "app")) for s in _get(sig, "steps")]


def _shape(a_arts: list, b_arts: list) -> float:
    """Share of A's artifacts that align to a same-app artifact of B; 0.5 when none can."""
    scores = []
    for x in a_arts:
        same = [y for y in b_arts if y["app"] == x["app"]]
        if same:
            scores.append(1.0 if any(template_match(x["title"], y["title"]) for y in same) else 0.0)
    return sum(scores) / len(scores) if scores else 0.5


def similarity(a, b) -> float:
    """0.6 sequence + 0.2 apps + 0.2 artifact shape, capped at 0.7 for different outputs (6.3)."""
    left, right = pairs_of(a), pairs_of(b)
    if not left or not right:
        return 0
    sequence = SequenceMatcher(None, left, right, autojunk=False).ratio()
    apps_a, apps_b = set(_get(a, "apps")), set(_get(b, "apps"))
    apps = len(apps_a & apps_b) / max(1, len(apps_a | apps_b))
    arts_a, arts_b = list(_get(a, "artifacts")), list(_get(b, "artifacts"))
    score = 0.6 * sequence + 0.2 * apps + 0.2 * _shape(arts_a, arts_b)
    out_a = [x for x in arts_a if x.get("output")]
    out_b = [y for y in arts_b if y.get("output")]
    if out_a and out_b and not any(x["app"] == y["app"] and template_match(x["title"], y["title"])
                                   for x in out_a for y in out_b):
        score = min(score, OUTPUT_CAP)
    return round(score, 3)


def candidates(fresh: list[tuple[str, object]], stored: list[tuple[str, object]] = ()) -> list:
    """(id a, id b, score) for every pair at or above the candidate score (6.4)."""
    found = []
    for i, (a_id, a_sig) in enumerate(fresh):
        for b_id, b_sig in list(stored) + fresh[:i]:
            ratio = SequenceMatcher(None, pairs_of(a_sig), pairs_of(b_sig), autojunk=False).ratio()
            if ratio < PREFILTER:
                continue
            score = similarity(a_sig, b_sig)
            if score >= CANDIDATE_SCORE:
                found.append((a_id, b_id, score))
    return found


def pair_key(a: str, b: str) -> str:
    """Content key of a pair of episodes."""
    return digest("match", *sorted((a, b)))


def assign(pairs: list[tuple], existing: dict | None = None) -> tuple[dict, dict]:
    """Episode -> process id and process -> lowest score; two processes never merge (6.6)."""
    owner = dict(existing or {})
    scores: dict[str, float] = {}
    for a, b, score in sorted(pairs, key=lambda p: (-p[2], pair_key(p[0], p[1]))):
        pa, pb = owner.get(a), owner.get(b)
        if pa and pb and pa != pb:
            continue
        pid = pa or pb or digest("process", *sorted((a, b)))
        scores[pid] = min(score, scores.get(pid, score))
        owner.setdefault(a, pid)
        owner.setdefault(b, pid)
    return owner, scores


def build_processes(pairs: list[tuple]) -> dict[str, str]:
    """Episode id -> process id over `same` pairs (a, b, score)."""
    return assign(pairs)[0]


def _completeness(member: dict) -> tuple:
    """Sort key: most outputs, most artifacts, most steps, earliest."""
    arts = member["signature"].artifacts
    return (-sum(1 for a in arts if a["output"]), -len(arts), -len(member["signature"].steps),
            member["started_at"], member["id"])


def wording(kind: str, label: str, template: str | None) -> str:
    """Rule label of a process step (5.3)."""
    quoted = f" “{template}”" if template else ""
    labels = {
        "search": f"Search for {{name}} in {label}" if template and "{name}" in template
        else f"Search in {label}",
        "browse": f"Open a page in {label}{quoted}", "create": f"Create a new document in {label}",
        "type": f"Write in {label}", "copy": f"Copy details from {label}",
        "paste": f"Paste into {label}", "save": f"Save the document{' as' + quoted if template else ''}",
        "send": f"Send it from {label}", "close": f"Close the document in {label}",
        "view": f"Review {label}{quoted}", "click": f"Use {label}",
    }
    return labels.get(kind, f"Use {label}")


def _step_templates(base, templates: list[dict]) -> list[str | None]:
    """The template each base step shows: its session's artifact, or the output for a save."""
    by_artifact = {e["matches"][0]: e["text"] for e in templates if 0 in e["matches"]}
    output = next((e["text"] for e in templates if e["output"]), None)
    out = []
    for step in base.steps:
        own = [by_artifact[i] for i, a in enumerate(base.artifacts)
               if a["session"] == step["session"] and i in by_artifact]
        if step["kind"] == "save" and output:
            out.append(output)
        elif step["kind"] in NO_TEMPLATE:
            out.append(None)
        else:
            out.append(own[0] if own else None)
    return out


def describe(members: list[dict]) -> dict:
    """Base steps, templates, labels and signature text of one process."""
    from taskmine.group.steps import text

    ordered = sorted(members, key=_completeness)
    templates = build_templates([m["signature"].artifacts for m in ordered])
    base = ordered[0]["signature"]
    shown = _step_templates(base, templates)
    steps = [{"kind": s["kind"], "app": s["app"], "label": wording(s["kind"], s["label"], t)}
             for s, t in zip(base.steps, shown)]
    public = [{"text": t["text"], "app": t["app"], "output": t["output"], "slots": t["slots"]}
              for t in templates]
    return {"steps": steps, "templates": public, "signature": text(base, shown)}


def _between(previous: dict, member: dict, others: list[dict]) -> bool:
    """True when a long episode of another process starts between two members."""
    start, end = seconds(previous["started_at"]), seconds(member["started_at"])
    return any(start < seconds(o["started_at"]) < end and o.get("seconds", 0) > INTERRUPTION
               and not o.get("trivial") and not o.get("diversion") for o in others)


def _continues(previous: dict, member: dict, others: list[dict]) -> bool:
    """The 6.9 conditions for a member to continue the current run."""
    steps = [tuple(s) for s in member["steps"]]
    gap = seconds(member["started_at"]) - seconds(previous["ended_at"])
    return (bool(steps) and steps == [tuple(s) for s in previous["steps"]]
            and member["task_id"] != previous["task_id"] and RUN_GAP[0] <= gap <= RUN_GAP[1]
            and not _between(previous, member, others))


def _run(group: list[dict]) -> dict:
    """One run record: member ids, count and median length."""
    lengths = [seconds(m["ended_at"]) - seconds(m["started_at"]) for m in group]
    return {"members": [m["id"] for m in group], "count": len(group),
            "median_seconds": round(statistics.median(lengths), 1),
            "started_at": group[0]["started_at"], "ended_at": group[-1]["ended_at"]}


def runs(members: list[dict], others: list[dict] = ()) -> list[dict]:
    """Back-to-back repeats of one step sequence for different tasks (6.9)."""
    ordered = sorted(members, key=lambda m: (seconds(m["started_at"]), m["id"]))
    out, group = [], []
    for member in ordered:
        if group and _continues(group[-1], member, list(others)):
            group.append(member)
            continue
        if len(group) >= 2:
            out.append(_run(group))
        group = [member]
    if len(group) >= 2:
        out.append(_run(group))
    return out
