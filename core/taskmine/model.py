"""Decision model: asks an outside model the questions the local scorer is unsure about."""
from __future__ import annotations

import http.client
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from taskmine.contracts import MissingModelKey, Settings

BODY_CAP = 16_000
BATCH_SIZE = 16
MIN_CUT = 40
CUT_FLOOR = 5_000
TIMEOUT_S = 20
THRESHOLD = 0.8
SYSTEM_PROMPT = (
    "You answer questions about how a person's computer activity groups into tasks. "
    "Each question has an id, instructions, options and a state. Reply with one JSON object: "
    '{"answers": [{"id": <id>, "choice": <one of the options>, "confidence": <0 to 1>}]}.'
)

Opener = Callable[[Request], tuple[int, bytes]]


class ModelError(RuntimeError):
    """The model call failed or returned something unreadable."""


class RateLimited(ModelError):
    """Spacing, the daily cap or the provider refused the call."""


@dataclass(frozen=True)
class Question:
    """One question for the model; state holds no text the person marked not shareable."""
    id: str
    kind: str
    options: list[str]
    state: dict[str, Any]
    instructions: str = ""


@dataclass(frozen=True)
class Answer:
    """The model's choice for one question."""
    id: str
    choice: str
    confidence: float


def apply_threshold(answers: list[Answer], threshold: float = THRESHOLD) -> list[Answer]:
    """Keep answers at or above the confidence threshold."""
    return [a for a in answers if a.confidence >= threshold]


def _not_shareable(value: Any) -> bool:
    """True when any nested mapping has external_ok set to False."""
    if isinstance(value, dict):
        if value.get("external_ok") is False:
            return True
        return any(_not_shareable(v) for v in value.values())
    if isinstance(value, list | tuple):
        return any(_not_shareable(v) for v in value)
    return False


def guard(questions: list[Question]) -> list[Question]:
    """Drop questions whose state is not shareable or holds a credential."""
    from taskmine.check import contains_credential

    return [
        q for q in questions
        if not _not_shareable(q.state)
        and not contains_credential(json.dumps(q.state, ensure_ascii=False))
    ]


class DecisionModel:
    """Base model: guards questions, then answers them."""

    def __init__(self) -> None:
        self.dropped = 0

    def ask(self, questions: list[Question]) -> list[Answer]:
        """Answer the questions that pass the guard."""
        allowed = guard(questions)
        self.dropped += len(questions) - len(allowed)
        return self._answer(allowed) if allowed else []

    def _answer(self, questions: list[Question]) -> list[Answer]:
        """Answer guarded questions."""
        raise NotImplementedError


class FakeModel(DecisionModel):
    """Scripted model for tests: maps question id to (choice, confidence)."""

    def __init__(self, script: dict[str, tuple[str, float]]):
        super().__init__()
        self.script = dict(script)
        self.calls: list[list[Question]] = []

    def ask(self, questions: list[Question]) -> list[Answer]:
        """Record the call, then answer from the script."""
        self.calls.append(questions)
        return super().ask(questions)

    def _answer(self, questions: list[Question]) -> list[Answer]:
        """Return scripted answers for known ids."""
        return [Answer(q.id, *self.script[q.id]) for q in questions if q.id in self.script]


def _default_opener(request: Request) -> tuple[int, bytes]:
    """Send a request with urlopen and return (status, body)."""
    try:
        with urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, b""


def _strings(value: Any, path: tuple = ()) -> list[tuple[tuple, str]]:
    """List (path, string) pairs for every string value in a nested state."""
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, list):
        value = dict(enumerate(value))
    if not isinstance(value, dict):
        return []
    return [pair for k, v in value.items() for pair in _strings(v, (*path, k))]


def _replace(value: Any, path: tuple, new: str) -> Any:
    """Return a copy of value with the string at path replaced."""
    if not path:
        return new
    head, rest = path[0], path[1:]
    copy = dict(value) if isinstance(value, dict) else list(value)
    copy[head] = _replace(value[head], rest, new)
    return copy


def _cut_once(state: dict) -> dict | None:
    """Halve the longest string that may shrink, or None when none may."""
    eligible = [(p, s) for p, s in _strings(state) if len(s) >= MIN_CUT and len(s) > CUT_FLOOR]
    if not eligible:
        return None
    path, text = max(eligible, key=lambda pair: len(pair[1]))
    return _replace(state, path, text[: max(CUT_FLOOR, len(text) // 2)])


def _number(value: Any) -> bool:
    """True for an int or float that is not a bool."""
    return isinstance(value, int | float) and not isinstance(value, bool)


class _HttpModel(DecisionModel):
    """Shared HTTP path: fitting, batching, spacing, cap, errors; subclasses add _body, _parse."""
    def __init__(
        self, url: str, name: str, key: str, *, opener: Opener | None = None,
        clock: Callable[[], float] | None = None, today: Callable[[], date] | None = None,
        min_interval_s: float = 60, calls_per_day: int = 120,
    ):
        super().__init__()
        self.url, self.name, self.key = url, name, key
        self.opener = opener or _default_opener
        self.clock = clock or time.monotonic
        self.today = today or date.today
        self.min_interval_s, self.calls_per_day = min_interval_s, calls_per_day
        self._last: float | None = None
        self._day: date | None = None
        self._count = 0

    def _answer(self, questions: list[Question]) -> list[Answer]:
        """Send fitted questions in batches and collect valid answers."""
        answers: list[Answer] = []
        for batch in self._batches(self._fit_all(questions)):
            options = {q.id: q.options for q in batch}
            answers += self._parse(self._send(self._body(batch)), options)
        return answers

    def _fit_all(self, questions: list[Question]) -> list[Question]:
        """Fit each question under the cap alone; drop those that cannot fit."""
        fitted = [self._fit(q) for q in questions]
        self.dropped += fitted.count(None)
        return [q for q in fitted if q is not None]

    def _fit(self, question: Question) -> Question | None:
        """Halve long strings in the state until a one-question body fits."""
        state = question.state
        while state is not None:
            candidate = replace(question, state=state)
            if len(self._body([candidate])) <= BODY_CAP:
                return candidate
            state = _cut_once(state)
        return None

    def _batches(self, questions: list[Question]) -> list[list[Question]]:
        """Pack questions into batches of at most 16 whose bodies fit the cap."""
        batches: list[list[Question]] = []
        for q in questions:
            last = batches[-1] if batches else None
            if last and len(last) < BATCH_SIZE and len(self._body([*last, q])) <= BODY_CAP:
                last.append(q)
            else:
                batches.append([q])
        return batches

    def _take_slot(self) -> None:
        """Count one request against spacing and the daily cap, or raise RateLimited."""
        day = self.today()
        if day != self._day:
            self._day, self._count = day, 0
        if self._count >= self.calls_per_day:
            raise RateLimited(f"daily cap of {self.calls_per_day} calls reached")
        now = self.clock()
        if self._last is not None and now - self._last < self.min_interval_s:
            raise RateLimited(f"calls are spaced {self.min_interval_s} s apart")
        self._last, self._count = now, self._count + 1

    def _send(self, body: bytes) -> Any:
        """POST one body and return the decoded JSON reply."""
        self._take_slot()
        request = Request(self._endpoint(), data=body, method="POST", headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
        })
        try:
            status, raw = self.opener(request)
        except (OSError, http.client.HTTPException) as error:
            raise ModelError(f"model request failed: {type(error).__name__}") from None
        if status == 429:
            raise RateLimited("the model provider returned 429")
        if not 200 <= status < 300:
            raise ModelError(f"the model provider returned {status}")
        return _loads(raw)

    def _endpoint(self) -> str:
        """URL the request goes to."""
        return self.url


def _loads(raw: bytes | str) -> Any:
    """Decode JSON or raise ModelError."""
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise ModelError("the model reply is not JSON") from None


def _to_answer(qid: Any, item: Any, options: dict[str, list[str]]) -> Answer | None:
    """Build an Answer from an item with choice and confidence, or None when off-schema."""
    if not isinstance(item, dict) or qid not in options:
        return None
    choice, confidence = item.get("choice"), item.get("confidence")
    if choice not in options[qid] or not _number(confidence):
        return None
    return Answer(qid, choice, float(confidence))


class JevModel(_HttpModel):
    """Typesafe Jev: state and questions keyed by id, choice answers with probabilities."""

    def _body(self, batch: list[Question]) -> bytes:
        """Encode a Jev request."""
        return json.dumps({
            "model": self.name,
            "state": {q.id: q.state for q in batch},
            "questions": {q.id: {
                "type": "choice", "instructions": q.instructions,
                "criteria": {o: o for o in q.options},
            } for q in batch},
        }, ensure_ascii=False).encode()

    def _parse(self, reply: Any, options: dict[str, list[str]]) -> list[Answer]:
        """Read Jev answers; confidence is the lower of confidence and choice probability."""
        items = reply.get("answers") if isinstance(reply, dict) else None
        if not isinstance(items, dict):
            raise ModelError("the Jev reply has no answers")
        answers = []
        for qid, item in items.items():
            answer = _to_answer(qid, item, options)
            if answer is None or item.get("type") != "choice":
                continue
            probs = item.get("probabilities")
            prob = probs.get(answer.choice) if isinstance(probs, dict) else None
            if _number(prob):
                answer = Answer(answer.id, answer.choice, min(answer.confidence, float(prob)))
            answers.append(answer)
        return answers


class OpenAIChatModel(_HttpModel):
    """Any OpenAI-compatible chat completions endpoint in JSON mode."""

    def _endpoint(self) -> str:
        """Base URL with /chat/completions appended once."""
        url = self.url.rstrip("/")
        return url if url.endswith("/chat/completions") else url + "/chat/completions"

    def _body(self, batch: list[Question]) -> bytes:
        """Encode a chat completions request."""
        user = {"questions": [{
            "id": q.id, "kind": q.kind, "instructions": q.instructions,
            "options": q.options, "state": q.state,
        } for q in batch]}
        return json.dumps({
            "model": self.name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }, ensure_ascii=False).encode()

    def _parse(self, reply: Any, options: dict[str, list[str]]) -> list[Answer]:
        """Read answers from the first choice's message content."""
        try:
            content = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ModelError("the chat reply has no message content") from None
        parsed = _loads(content)
        items = parsed.get("answers") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            raise ModelError("the chat reply has no answers list")
        found = (_to_answer(i.get("id"), i, options) for i in items if isinstance(i, dict))
        return [a for a in found if a is not None]


def from_settings(settings: Settings) -> DecisionModel:
    """Build the configured model; Jev for typesafe-ai names, else OpenAI-compatible."""
    if not settings.model_key:
        raise MissingModelKey("TASKMINE_MODEL_KEY is required; set it in .env")
    cls = JevModel if settings.model_name.startswith("typesafe-ai/") else OpenAIChatModel
    return cls(settings.model_url, settings.model_name, settings.model_key,
               calls_per_day=settings.model_calls_per_day)


def from_env() -> DecisionModel:
    """Build the configured model from the environment and .env."""
    return from_settings(Settings.from_env())
