"""One grouping pass (grouper spec 4.8, 4.9, 7.1): actions in, episodes, tasks and processes out."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from taskmine.group import noise
from taskmine.group.anchors import extract
from taskmine.group.common import JOINS, digest, iso, order_key, seconds, title_of, untitled
from taskmine.group.features import features, furniture_words, weigh
from taskmine.group.matching import match
from taskmine.group.name import Name, compose, facts, name, parse, reusable, summary
from taskmine.group.questions import (
    ask_model,
    ask_person,
    match_questions,
    name_questions,
    relation_candidates,
    relation_questions,
    shape,
)
from taskmine.group.segment import attach_gaps, segment
from taskmine.group.sessions import app_labels, sessions, trivial
from taskmine.group.steps import public, signature
from taskmine.group.tasks import anchors_of, join_tasks, possibly_personal
from taskmine.learn import decide
from taskmine.model import THRESHOLD
from taskmine.store import PassResult, name_key

QUIET = 300.0
DEFAULTS = {"interaction": None, "title": None, "text": "", "url": None, "document_id": None,
            "window_ref": None, "browser": False, "withheld": False, "sightings": 1,
            "focus_name": None, "shortcut": None, "furniture_lines": [], "case_ref": None,
            "external_ok": True}


def to_seconds(now) -> float:
    """Epoch seconds from a datetime or ISO text."""
    if hasattr(now, "timestamp"):
        return now.timestamp()
    return seconds(str(now))


def prepare(actions: list[dict], salt: bytes) -> list[dict]:
    """Copies with defaults, anchors, one display name per app, and unsaved documents untied."""
    items = []
    for action in actions:
        item = {**DEFAULTS, **action}
        item["first_seen_at"] = item.get("first_seen_at") or item["at"]
        item["last_seen_at"] = item.get("last_seen_at") or item["at"]
        if "case_keys" not in action:
            found = extract(item, salt)
            item.update(case_keys=[list(p) for p in found.case_keys], artifact_key=found.artifact_key,
                        surface_key=found.surface_key, window_key=found.window_key)
        items.append(item)
    labels = app_labels(items)
    for item in items:
        item["app_label"] = labels[item["app"]]
        if untitled(title_of(item)):
            item["document_id"] = item["artifact_key"] = None
    return items


@dataclass
class Batch:
    """The state of one pass."""

    items: list
    gaps: list
    sessions: list
    units: dict
    now: float
    settled: bool
    reason: str | None
    existing: list
    reopened: dict | None
    stored: list = field(default_factory=list)
    names: dict = field(default_factory=dict)
    decisions: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    contexts: dict = field(default_factory=dict)
    basis: dict = field(default_factory=dict)
    keys: dict = field(default_factory=dict)


def _decide(batch: Batch) -> None:
    """Rule-table decisions at every switch, with the context of 4.6.1."""
    sess = batch.sessions
    furniture, weights = furniture_words(sess), weigh(sess)
    episode = [sess[0]] if sess else []
    for session in sess[1:]:
        f = features(episode, sess[session.index - 1], session, furniture, weights)
        d = decide(f)
        batch.decisions[session.index], batch.features[session.index] = d, f
        batch.contexts[session.index] = [s.index for s in episode]
        batch.basis[session.index] = "rule"
        batch.keys[session.index] = digest(sess[session.index - 1].items[0]["id"], session.items[0]["id"])
        episode = episode + [session] if d.choice in JOINS else [session]


def _info(ep, reopened: dict | None) -> dict:
    """Ids, times, anchors and signature of one episode."""
    work = [i for s in ep.sessions for i in s.items]
    actions = work + ep.gaps
    ids = sorted(a["id"] for a in actions)
    info = {"id": digest(*ids), "episode": ep, "actions": actions, "work": work,
            "sig": signature(ep), "trivial": trivial(ep), "revision": 1,
            "first_id": min(work, key=order_key)["id"],
            "start": min(seconds(i["first_seen_at"]) for i in work),
            "end": max(seconds(i["last_seen_at"]) for i in work),
            "first_at": min(seconds(i["at"]) for i in work),
            "last_at": max(seconds(i["at"]) for i in work), **anchors_of(work)}
    if reopened and set(reopened["action_ids"]) & set(ids):
        info.update(id=reopened["id"], task_id=reopened["task_id"],
                    revision=int(reopened.get("revision") or 1) + 1)
    return info


def _latest_key(infos: list[dict]) -> str:
    """Name key of the newest episode of a task."""
    latest = max(infos, key=lambda i: (i["last_at"], i["id"]))
    return name_key(public(latest["sig"]))


def _resolve(n: Name, record: dict | None, labels: list[str], answers: dict, tid: str) -> str:
    """Apply the 5.3 resolution order to a rule name; return its basis."""
    if record and record["basis"] == "person" and record.get("reusable") \
            and reusable(record.get("title") or "", labels):
        n.verb, n.object = parse(record["title"]) or (n.verb, n.object)
        n.title, n.verb_sure, n.object_sure = record["title"], True, True
        return "person"
    basis = "rules"
    if record and record["basis"] == "model":
        n.verb, n.object = record.get("verb") or n.verb, record.get("object") or n.object
        n.verb_sure = n.object_sure = True
        basis = "model"
    for part in ("verb", "object"):
        found = answers.get(digest(part, tid))
        if found:
            setattr(n, part, found[0])
            setattr(n, part + "_sure", True)
            basis = "model"
    n.title = compose(n.verb, n.object, n.system)
    return basis


def _names(batch: Batch, infos: list[dict], placed: dict, answers: dict) -> tuple[dict, list]:
    """Names per task by the 5.3 resolution order, and the unsure parts to ask about."""
    by_task: dict[str, list] = {}
    for info in infos:
        by_task.setdefault(placed[info["id"]], []).append(info)
    out, unsure = {}, []
    for tid, own in by_task.items():
        actions = [a for info in own for a in info["actions"]]
        noise.keep(actions, batch.units)
        f, key = facts(actions), _latest_key(own)
        labels = sorted({a["app_label"] for a in actions if a.get("app_label")})
        n = name(f)
        basis = _resolve(n, batch.names.get(key), labels, answers, tid)
        for part in ("verb", "object"):
            if not getattr(n, part + "_sure"):
                view = {"system": f.system, "steps": sorted(f.kinds)[:12],
                        "titles": [shape(t) for t in f.titles[:4]], "fields": f.fields[:12],
                        "rules": {"verb": n.verb, "object": n.object}}
                unsure.append((digest(part, tid), part, view))
        record = {"signature": key, "verb": n.verb, "object": n.object, "system": n.system,
                  "title": n.title, "basis": basis, "reusable": int(reusable(n.title, labels)),
                  "updated_at": iso(batch.now)}
        out[tid] = {"name": n, "summary": summary(f), "basis": basis, "record": record,
                    "personal": possibly_personal(actions)}
    return out, unsure


def assemble(batch: Batch, answers: dict) -> dict:
    """Episodes, tasks, names and processes for the current decisions."""
    episodes = segment(batch.sessions, batch.decisions)
    attach_gaps(episodes, batch.gaps)
    for ep in episodes:
        if ep.close_reason is None:
            ep.close_reason = batch.reason
    infos = [_info(ep, batch.reopened) for ep in episodes]
    placed, records = join_tasks(infos, batch.existing)
    for info in infos:
        info["task_id"] = placed[info["id"]]
    names, unsure = _names(batch, infos, placed, answers)
    found = match(infos, batch.stored, names, answers, batch.now)
    return {"infos": infos, "records": records, "placed": placed, "names": names,
            "unsure": unsure, **found}


def _episode_row(info: dict, owner: dict) -> dict:
    """An episodes table row."""
    ep = info["episode"]
    return {"id": info["id"], "task_id": info["task_id"], "started_at": iso(info["start"]),
            "ended_at": iso(info["end"]), "first_action_at": iso(info["first_at"]),
            "last_action_at": iso(info["last_at"]), "close_reason": ep.close_reason,
            "trivial": int(info["trivial"]), "diversion": int(ep.diversion),
            "signature": public(info["sig"]), "process_id": owner.get(info["id"]),
            "revision": info["revision"], "own_case": int(ep.own_case),
            "session_count": len(ep.sessions)}


def _task_row(record: dict, named: dict | None) -> dict:
    """A tasks table row; a locked task keeps its title and summary."""
    row = {"id": record["id"], "title": record.get("title"), "name_basis": record.get("name_basis", "rules"),
           "summary": record.get("summary"), "lifecycle": record.get("lifecycle", "active"),
           "locked": int(record.get("locked") or 0), "revision": record.get("revision", 1),
           "possibly_personal": int(record.get("possibly_personal") or 0),
           "started_at": record["started_at"], "last_seen_at": record["last_seen_at"],
           "case_keys": record["case_keys"], "artifact_keys": record["artifact_keys"],
           "window_keys": record["window_keys"], "draft_id": record.get("draft_id")}
    if named and not row["locked"]:
        row.update(title=named["name"].title, summary=named["summary"], name_basis=named["basis"],
                   possibly_personal=int(named["personal"]))
    return row


def _decision_rows(batch: Batch, asked: dict, answered: set) -> list[dict]:
    """Decisions table rows, with the question state of switches put to the person."""
    rows = []
    for index, d in sorted(batch.decisions.items()):
        left, right = batch.sessions[index - 1], batch.sessions[index]
        basis = "model" if index in answered else batch.basis.get(index, "rule")
        if d.reason == "saved_as_search":
            basis = "rule"
        rows.append({"key": batch.keys[index], "left_event_id": left.items[0]["id"],
                     "right_event_id": right.items[0]["id"], "at": iso(right.first_at),
                     "features": batch.features[index], "model_confidence": d.model_confidence,
                     "choice": d.choice,
                     "basis": basis, "reason": d.reason, "hard": int(d.hard),
                     "asked_at": iso(batch.now) if index in asked else None,
                     "ask_context": asked.get(index), "question_status": "open" if index in asked else None,
                     "answer": None, "answered_at": None})
    return rows


def _ask(batch: Batch, draft: dict, model, notes: str) -> tuple[dict, dict, set]:
    """One model request for a settled pass; apply sure answers and re-assemble.

    A relation answer at or above the threshold replaces the decision. One below keeps the
    rule-table choice, stays unsure and records the model's confidence for the asker.
    """
    candidates_ = relation_candidates(batch.sessions, batch.decisions, batch.keys)
    labels = {s.app: s.app_label for s in batch.sessions}
    questions = (relation_questions(batch.sessions, candidates_, batch.contexts, batch.keys)
                 + match_questions(draft["asks"], labels) + name_questions(draft["unsure"]))
    answers = ask_model(model, questions, notes)
    sure = {k: v for k, v in answers.items() if v[1] >= THRESHOLD}
    answered = set()
    for index in candidates_:
        found = answers.get(batch.keys[index])
        if not found:
            continue
        d = batch.decisions[index]
        if found[1] >= THRESHOLD:
            batch.decisions[index] = replace(d, choice=found[0], confidence=found[1],
                                             hard=False, model_confidence=found[1], unsure=False)
            answered.add(index)
        else:
            d.model_confidence = found[1]
    if sure:
        draft = assemble(batch, sure)
    return draft, sure, answered


def run_batch(actions, asker, model, now, *, force, salt, notes: str = "", existing=None,
              reopened=None, stored=None, names=None, open_questions: int = 0) -> PassResult:
    """One in-memory pass over `actions`; nothing is read from or written to a store."""
    now_s = to_seconds(now)
    items = prepare(actions, salt)
    if not items:
        return PassResult(settled=bool(force))
    work = [i for i in items if not i["withheld"]] or items
    gaps = [i for i in items if i["withheld"] and i not in work]
    quiet = now_s - max(seconds(i["last_seen_at"]) for i in items) >= QUIET
    settled = bool(quiet or force)
    batch = Batch(items, gaps, sessions(work), noise.score(work), now_s, settled,
                  "quiet" if quiet else "paused" if force else None, list(existing or ()), reopened,
                  list(stored or ()), dict(names or {}))
    _decide(batch)
    draft = assemble(batch, {})
    asked, answered = {}, set()
    if settled:
        draft, _, answered = _ask(batch, draft, model, notes)
        pending = [i for i in relation_candidates(batch.sessions, batch.decisions, batch.keys)
                   if i not in answered]
        asked = ask_person(asker, batch.decisions, pending, now_s, open_questions)
    tasks = {t["id"]: t for t in draft["records"]}
    touched = sorted(set(draft["placed"].values()))
    return PassResult(
        episodes=[_episode_row(i, draft["owner"]) for i in draft["infos"]],
        tasks=[_task_row(tasks[t], draft["names"].get(t)) for t in touched],
        processes=draft["processes"],
        decisions=_decision_rows(batch, asked, answered),
        placements={a["id"]: i["id"] for i in draft["infos"] for a in i["actions"]},
        settled=settled, links=draft["links"],
        names=[draft["names"][t]["record"] for t in touched if draft["names"][t]["basis"] != "person"])
