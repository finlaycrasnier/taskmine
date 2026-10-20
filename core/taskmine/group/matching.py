"""Process matching over this pass and the stored episodes (grouper spec 6.1 to 6.6, 6.9)."""
from __future__ import annotations

from collections import Counter

from taskmine.group.common import iso, seconds
from taskmine.group.name import compose
from taskmine.group.processes import assign, candidates, describe, pair_key, runs
from taskmine.group.steps import Signature

RULE_MATCH = 0.8


def stored_member(row: dict, titles: dict[str, str]) -> dict:
    """A stored episode as a match member; artifacts whose evidence is gone are dropped."""
    sig = row["signature"] or {}
    steps = [{"kind": s["kind"], "app": s["app"], "label": s.get("label") or s["app"],
              "evidence": list(s.get("evidence") or ()), "session": s.get("session", 0),
              "document": ""} for s in sig.get("steps", [])]
    artifacts = [{**a, "title": titles[a["ref"]]} for a in sig.get("artifacts", [])
                 if titles.get(a.get("ref"))]
    apps = sig.get("apps") or list(dict.fromkeys(s["app"] for s in steps))
    return {"id": row["id"], "task_id": row["task_id"], "process_id": row.get("process_id"),
            "process": row.get("process"), "sig": Signature(steps, list(apps), artifacts),
            "start": seconds(row["started_at"]), "end": seconds(row["ended_at"]), "stored": True}


def _name(members: list[dict], names: dict, described: dict) -> str:
    """Parts shared by most of the process's tasks with its case count, else a rule name."""
    tasks = list(dict.fromkeys(m["task_id"] for m in members))
    parts = Counter((names[t]["name"].verb, names[t]["name"].object, names[t]["name"].system)
                    for t in tasks if t in names)
    if parts:
        (verb, obj, system_name), count = parts.most_common(1)[0]
        if count * 2 > len(tasks):
            return compose(verb, obj, system_name, count=len(tasks))
    output = next((t["text"] for t in described["templates"] if t["output"]), None)
    if output:
        return f"Repeated task: {output}"
    labels = list(dict.fromkeys(s["label"] for m in members for s in m["sig"].steps))[:3]
    return "Repeated task in " + ", ".join(labels)


def _pairs(fresh: list[dict], stored: list[dict], answers: dict) -> tuple[list, list, set]:
    """Same pairs (with a model flag), and the pairs the rules cannot settle."""
    by_id = {m["id"]: m for m in fresh + stored}
    same, asks = [], []
    found = candidates([(m["id"], m["sig"]) for m in fresh], [(m["id"], m["sig"]) for m in stored])
    for a, b, score in found:
        key = pair_key(a, b)
        if key in answers:
            if answers[key][0] == "same":
                same.append((a, b, score, True))
        elif score >= RULE_MATCH:
            same.append((a, b, score, False))
        else:
            asks.append((key, by_id[a]["sig"], by_id[b]["sig"], score))
    return same, asks, by_id


def _row(pid: str, members: list[dict], names: dict, score: float, basis: str, now: float) -> dict:
    """One processes row with its templates, wording and runs."""
    members.sort(key=lambda m: (m["start"], m["id"]))
    described = describe([{"id": m["id"], "signature": m["sig"], "started_at": m["start"]}
                          for m in members])
    repeats = runs([{"id": m["id"], "task_id": m["task_id"], "started_at": iso(m["start"]),
                     "ended_at": iso(m["end"]), "steps": [[s["kind"], s["app"]] for s in m["sig"].steps]}
                    for m in members])
    old = next((m["process"] for m in members if m.get("process")), None) or {}
    return {"id": pid, "name": _name(members, names, described),
            "signature": {"text": described["signature"]}, "steps": described["steps"],
            "templates": described["templates"], "score": score, "basis": basis,
            "uncertain": basis != "model", "created_at": old.get("created_at") or iso(now),
            "updated_at": iso(now), "episode_ids": [m["id"] for m in members], "runs": repeats}


def match(fresh: list[dict], stored: list[dict], names: dict, answers: dict, now: float) -> dict:
    """Process rows touched by this pass, fresh episode -> process, stored links and open pairs."""
    fresh = [m for m in fresh if not m["trivial"] and m["sig"].steps]
    own = {m["id"] for m in fresh}
    stored = [m for m in stored if m["id"] not in own and m["sig"].steps]
    same, asks, by_id = _pairs(fresh, stored, answers)
    existing = {m["id"]: m["process_id"] for m in stored if m["process_id"]}
    owner, scores = assign([p[:3] for p in same], existing)
    model = {owner[p[0]] for p in same if p[3]}
    groups: dict[str, list] = {}
    for eid, pid in owner.items():
        groups.setdefault(pid, []).append(by_id[eid])
    rows, kept, links = [], {}, {}
    for pid, members in sorted(groups.items()):
        if len(members) < 2 or not any(m["id"] in own for m in members):
            continue
        old = next((m["process"] for m in members if m.get("process")), None) or {}
        score = min([scores.get(pid, 1.0)] + ([old["score"]] if old.get("score") is not None else []))
        basis = "model" if pid in model or old.get("basis") == "model" else "rules"
        rows.append(_row(pid, members, names, score, basis, now))
        kept.update({m["id"]: pid for m in members if m["id"] in own})
        links.update({m["id"]: pid for m in members if m["id"] not in own and not m["process_id"]})
    return {"processes": rows, "owner": kept, "links": links, "asks": asks}
