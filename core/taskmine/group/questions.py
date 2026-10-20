"""Questions of a settled pass (grouper spec 7.1, 7.2): one model request, then the asker."""
from __future__ import annotations

import re
from datetime import UTC, datetime

from taskmine.group import words as w
from taskmine.group.common import title_of
from taskmine.group.features import profile
from taskmine.group.sessions import trivial
from taskmine.model import Question, apply_threshold

MAX_RELATION, MAX_MATCH = 8, 8
RELATION_OPTIONS = ["same", "supporting", "break", "diversion"]
MATCH_OPTIONS = ["same", "different"]
APP_MAX, TITLE_MAX, STEPS_MAX = 80, 160, 8


def shape(title: str) -> str:
    """A title with digit runs replaced, casefolded, at most 160 characters."""
    return re.sub(r"\d+", "#", " ".join(title.split()).casefold())[:TITLE_MAX]


def _view(session) -> dict:
    """What the model may see of a session: app, cleaned title, step kinds and seconds."""
    shown = [i for i in session.items if i.get("external_ok", True) and title_of(i)]
    kinds = list(dict.fromkeys(profile(session).kinds))[:STEPS_MAX]
    return {"app": session.app_label[:APP_MAX],
            "title": title_of(shown[-1])[:TITLE_MAX] if shown else "",
            "steps": kinds, "seconds": int(session.duration)}


def relation_candidates(sessions, decisions: dict, keys: dict) -> list[int]:
    """Unsure soft switches between two non-trivial sessions, first eight in session order."""
    out = []
    for index in sorted(decisions):
        d = decisions[index]
        if d.hard or not d.unsure or keys.get(index) is None:
            continue
        if trivial(sessions[index - 1]) or trivial(sessions[index]):
            continue
        out.append(index)
    return out[:MAX_RELATION]


def relation_questions(sessions, candidates: list[int], contexts: dict, keys: dict) -> list:
    """One relation question per candidate switch."""
    out = []
    for index in candidates:
        context = [sessions[i] for i in contexts[index]][-3:]
        previous, session = sessions[index - 1], sessions[index]
        state = {"episode": [_view(s) for s in context], "previous": _view(previous),
                 "next": _view(session), "gap_seconds": int(session.first_seen - previous.last_seen)}
        out.append(Question(keys[index], "relation", list(RELATION_OPTIONS), state,
                            "Is the next session the same task, supporting it, a break, or a "
                            "short diversion?"))
    return out


def match_questions(asks: list[tuple], labels: dict) -> list:
    """Process match questions for candidate pairs the rules cannot settle."""
    out = []
    for key, a_sig, b_sig, score in asks[:MAX_MATCH]:
        state = {"a": [[s["kind"], labels.get(s["app"], s["app"])] for s in a_sig.steps],
                 "b": [[s["kind"], labels.get(s["app"], s["app"])] for s in b_sig.steps],
                 "similarity": score}
        out.append(Question(key, "match", list(MATCH_OPTIONS), state,
                            "Are these two episodes the same process done for different cases?"))
    return out


def name_questions(unsure: list[tuple]) -> list:
    """Verb and object questions for tasks the naming rules were not sure about."""
    out = []
    for key, kind, view in unsure:
        options = list(w.VERBS) if kind == "verb" else list(w.OBJECTS)
        out.append(Question(key, kind, options, view, f"Which {kind} describes this task?"))
    return out


def ask_model(model, questions: list) -> dict[str, tuple[str, float]]:
    """Question id -> (choice, confidence) for answers at or above the threshold."""
    if not questions or model is None:
        return {}
    try:
        answers = apply_threshold(model.ask(questions))
    except Exception:  # noqa: BLE001  any model failure leaves the rules' answers
        return {}
    allowed = {q.id: set(q.options) for q in questions}
    return {a.id: (a.choice, a.confidence) for a in answers if a.choice in allowed.get(a.id, ())}


def ask_person(asker, decisions: dict, candidates: list[int], now: float, open_count: int = 0
               ) -> dict[int, str | None]:
    """Session index -> ask context for the candidates the asker chooses to put to the person."""
    asked: dict[int, str | None] = {}
    if asker is None:
        return asked
    moment = datetime.fromtimestamp(now, UTC)
    for index in candidates:
        decision = decisions[index]
        if asker.should_ask(decision, open_count + len(asked), moment):
            context = None
            if hasattr(asker, "context_key") and decision.p_join is not None:
                context = asker.context_key(decision.p_join, moment, False)
            asked[index] = context
    return asked
