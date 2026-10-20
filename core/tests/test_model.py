"""Tests for taskmine.model, written from docs/CONTRACTS.md (the `model` row), docs/LEARNING.md
section 4 and grouper-spec 7.1 rung 4 and 7.2.

No network and no sleeps. Every model gets a fake opener that records the request and returns a
scripted response, and an injected clock and date.
"""
import json
import urllib.error
import urllib.request
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from taskmine import model as model_module
from taskmine.model import (
    Answer,
    FakeModel,
    JevModel,
    MissingModelKey,
    ModelError,
    OpenAIChatModel,
    Question,
    RateLimited,
    apply_threshold,
    from_settings,
    guard,
)

JEV_URL = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
JEV_NAME = "typesafe-ai/jev"
KEY = "test-key-123"
BODY_CAP = 16_000
SECRET = "sk-proj" + "A1b2C3d4E5f6G7h8I9j0K1L2"  # matches the provider_tokens credential rule
RELATION_OPTIONS = ["same", "continue", "switch", "break"]


# Helpers


class Clock:
    """Monotonic clock under test control. `step` advances it after every read."""

    def __init__(self, t: float = 1000.0, step: float = 0.0):
        self.t = t
        self.step = step

    def __call__(self) -> float:
        now = self.t
        self.t += self.step
        return now


class Today:
    def __init__(self, day: date = date(2026, 10, 20)):
        self.day = day

    def __call__(self) -> date:
        return self.day


def jev_reply(payload: dict) -> tuple[int, bytes]:
    """Answer every question in a Jev request with its first option at 0.9."""
    answers = {}
    for qid, spec in payload["questions"].items():
        options = list(spec["criteria"])
        answers[qid] = {
            "type": "choice",
            "choice": options[0],
            "confidence": 0.9,
            "probabilities": {o: (0.9 if i == 0 else 0.1 / max(1, len(options) - 1)) for i, o in enumerate(options)},
        }
    return 200, json.dumps({"answers": answers}).encode()


class FakeOpener:
    """Records each request. `reply` is a callable(payload) -> (status, body), or a list of
    (status, body) pairs used in order, or an exception to raise."""

    def __init__(self, reply=jev_reply):
        self.reply = reply
        self.requests: list[dict] = []

    def __call__(self, request: urllib.request.Request):
        body = request.data
        record = {
            "url": request.full_url,
            "method": request.get_method(),
            "headers": {k.lower(): v for k, v in request.header_items()},
            "body": body,
            "json": json.loads(body),
        }
        self.requests.append(record)
        if isinstance(self.reply, BaseException):
            raise self.reply
        if isinstance(self.reply, list):
            return self.reply[len(self.requests) - 1]
        return self.reply(record["json"])


def q(qid: str, options=None, state=None, kind="relation", instructions="Do these sessions belong to one task?") -> Question:
    return Question(
        id=qid,
        kind=kind,
        options=list(options or RELATION_OPTIONS),
        state=state if state is not None else {"previous": {"app": "Mail", "steps": ["read"], "seconds": 40}},
        instructions=instructions,
    )


def jev(opener, **kwargs) -> JevModel:
    kwargs.setdefault("clock", Clock())
    kwargs.setdefault("today", Today())
    return JevModel(JEV_URL, JEV_NAME, KEY, opener=opener, **kwargs)


def openai(opener, url="https://api.example.test/v1", name="gpt-4o-mini", **kwargs) -> OpenAIChatModel:
    kwargs.setdefault("clock", Clock())
    kwargs.setdefault("today", Today())
    return OpenAIChatModel(url, name, KEY, opener=opener, **kwargs)


def by_id(answers) -> dict:
    return {a.id: a for a in answers}


def jev_answer(choice, confidence, probabilities, type_="choice") -> dict:
    return {"type": type_, "choice": choice, "confidence": confidence, "probabilities": probabilities}


def respond(answers: dict) -> tuple[int, bytes]:
    return 200, json.dumps({"answers": answers}).encode()


# Jev request shape


def test_jev_posts_to_url_with_bearer_and_json_headers():
    opener = FakeOpener()
    jev(opener).ask([q("r1")])
    [req] = opener.requests
    assert req["url"] == JEV_URL
    assert req["method"] == "POST"
    assert req["headers"]["authorization"] == f"Bearer {KEY}"
    assert req["headers"]["content-type"] == "application/json"


def test_jev_body_shape_is_exact():
    opener = FakeOpener()
    state_r1 = {"previous": {"app": "Mail", "steps": ["read"], "seconds": 40}, "gap_seconds": 12}
    state_v1 = {"system": "Xero", "steps": ["edit", "save"], "text": "Invoice 42"}
    questions = [
        q("r1", state=state_r1, instructions="One task or two?"),
        q("v1", options=["review", "update"], state=state_v1, kind="verb", instructions="Pick the verb."),
    ]
    jev(opener).ask(questions)
    [req] = opener.requests
    assert req["json"] == {
        "model": JEV_NAME,
        "state": {"r1": state_r1, "v1": state_v1},
        "questions": {
            "r1": {
                "type": "choice",
                "instructions": "One task or two?",
                "criteria": {o: o for o in RELATION_OPTIONS},
            },
            "v1": {
                "type": "choice",
                "instructions": "Pick the verb.",
                "criteria": {"review": "review", "update": "update"},
            },
        },
    }


def test_jev_returns_answers_for_each_question():
    opener = FakeOpener()
    answers = jev(opener).ask([q("r1"), q("m1", options=["same", "different"], kind="match")])
    got = by_id(answers)
    assert set(got) == {"r1", "m1"}
    assert all(isinstance(a, Answer) for a in answers)
    assert got["r1"].choice == "same"
    assert got["m1"].choice == "same"
    assert got["r1"].confidence == pytest.approx(0.9)


# Jev response parsing


def test_jev_confidence_is_min_of_confidence_and_choice_probability():
    opener = FakeOpener(lambda p: respond({
        "r1": jev_answer("same", 0.95, {"same": 0.85, "continue": 0.05, "switch": 0.05, "break": 0.05}),
        "r2": jev_answer("break", 0.7, {"same": 0.05, "continue": 0.05, "switch": 0.0, "break": 0.9}),
    }))
    got = by_id(jev(opener).ask([q("r1"), q("r2")]))
    assert got["r1"].choice == "same"
    assert got["r1"].confidence == pytest.approx(0.85)
    assert got["r2"].choice == "break"
    assert got["r2"].confidence == pytest.approx(0.7)


def test_jev_drops_answer_whose_choice_is_not_an_option():
    opener = FakeOpener(lambda p: respond({
        "r1": jev_answer("maybe", 0.99, {"maybe": 0.99}),
        "r2": jev_answer("switch", 0.9, {"switch": 0.9}),
    }))
    got = by_id(jev(opener).ask([q("r1"), q("r2")]))
    assert set(got) == {"r2"}


def test_jev_drops_off_schema_answers():
    opener = FakeOpener(lambda p: respond({
        "r1": {"type": "choice", "choice": "same", "probabilities": {"same": 0.9}},  # no confidence
        "r2": jev_answer("same", 0.9, {"same": 0.9}, type_="text"),  # wrong type
        "r3": "same",  # not an object
        "r4": jev_answer("same", "high", {"same": 0.9}),  # confidence not a number
        "zz": jev_answer("same", 0.9, {"same": 0.9}),  # not asked
        "r5": jev_answer("continue", 0.9, {"continue": 0.9}),
    }))
    got = by_id(jev(opener).ask([q("r1"), q("r2"), q("r3"), q("r4"), q("r5")]))
    assert set(got) == {"r5"}
    assert got["r5"].choice == "continue"


# Batching


def test_jev_sends_17_questions_in_2_requests():
    opener = FakeOpener()
    questions = [q(f"r{i}") for i in range(17)]
    answers = jev(opener, min_interval_s=0, clock=Clock(step=1000.0)).ask(questions)
    assert len(opener.requests) == 2
    sent = [list(r["json"]["questions"]) for r in opener.requests]
    assert all(1 <= len(ids) <= 16 for ids in sent)
    flat = [i for ids in sent for i in ids]
    assert sorted(flat) == sorted(x.id for x in questions)
    assert len(flat) == len(set(flat))
    for r in opener.requests:
        assert set(r["json"]["state"]) == set(r["json"]["questions"])
    assert {a.id for a in answers} == {x.id for x in questions}


def test_jev_sends_16_questions_in_1_request():
    opener = FakeOpener()
    jev(opener).ask([q(f"r{i}") for i in range(16)])
    assert len(opener.requests) == 1
    assert len(opener.requests[0]["json"]["questions"]) == 16


def test_ask_with_no_questions_sends_nothing():
    opener = FakeOpener()
    assert jev(opener).ask([]) == []
    assert opener.requests == []


# Body size cap


def test_jev_halves_long_strings_until_body_fits():
    long_text = "x" * 20_000
    opener = FakeOpener()
    answers = jev(opener).ask([q("r1", state={"app": "Mail", "text": long_text})])
    [req] = opener.requests
    assert len(req["body"]) <= BODY_CAP
    sent = req["json"]["state"]["r1"]
    assert sent["app"] == "Mail"
    assert 5_000 <= len(sent["text"]) < 20_000
    assert {a.id for a in answers} == {"r1"}


def test_jev_halving_shortens_the_longest_string_first():
    opener = FakeOpener()
    state = {"short": "s" * 200, "long": "y" * 18_000}
    jev(opener).ask([q("r1", state=state)])
    [req] = opener.requests
    assert len(req["body"]) <= BODY_CAP
    sent = req["json"]["state"]["r1"]
    assert sent["short"] == "s" * 200
    assert len(sent["long"]) < 18_000


def test_jev_drops_question_that_cannot_fit_alone():
    # 600 keys of 36 characters: the keys alone exceed the cap, so no halving of values helps.
    huge = {f"k{i:05d}_" + "a" * 30: "v" for i in range(600)}
    opener = FakeOpener()
    answers = jev(opener).ask([q("big", state=huge), q("r1")])
    [req] = opener.requests
    assert len(req["body"]) <= BODY_CAP
    assert set(req["json"]["questions"]) == {"r1"}
    assert {a.id for a in answers} == {"r1"}


def test_jev_sends_nothing_when_the_only_question_cannot_fit():
    huge = {f"k{i:05d}_" + "a" * 30: "v" for i in range(600)}
    opener = FakeOpener()
    assert jev(opener).ask([q("big", state=huge)]) == []
    assert opener.requests == []


# Spacing and daily cap


def test_second_request_within_interval_raises_rate_limited_without_sending():
    clock = Clock(1000.0)
    opener = FakeOpener()
    m = jev(opener, min_interval_s=60, clock=clock)
    m.ask([q("r1")])
    clock.t = 1030.0
    with pytest.raises(RateLimited):
        m.ask([q("r2")])
    assert len(opener.requests) == 1
    clock.t = 1060.0
    m.ask([q("r3")])
    assert len(opener.requests) == 2


def test_default_spacing_is_60_seconds():
    clock = Clock(1000.0)
    opener = FakeOpener()
    m = jev(opener, clock=clock)
    m.ask([q("r1")])
    clock.t = 1059.0
    with pytest.raises(RateLimited):
        m.ask([q("r2")])
    clock.t = 1061.0
    m.ask([q("r3")])
    assert len(opener.requests) == 2


def test_daily_cap_raises_rate_limited_and_resets_next_day():
    clock = Clock(1000.0, step=0.0)
    today = Today(date(2026, 10, 20))
    opener = FakeOpener()
    m = jev(opener, calls_per_day=2, min_interval_s=0, clock=clock, today=today)
    m.ask([q("r1")])
    clock.t += 100
    m.ask([q("r2")])
    clock.t += 100
    with pytest.raises(RateLimited):
        m.ask([q("r3")])
    assert len(opener.requests) == 2
    today.day += timedelta(days=1)
    clock.t += 100
    m.ask([q("r4")])
    assert len(opener.requests) == 3


def test_daily_cap_counts_requests_not_asks():
    opener = FakeOpener()
    m = jev(opener, calls_per_day=1, min_interval_s=0, clock=Clock(step=1000.0))
    with pytest.raises(RateLimited):
        m.ask([q(f"r{i}") for i in range(17)])
    assert len(opener.requests) == 1


# HTTP errors


def test_http_429_raises_rate_limited():
    opener = FakeOpener([(429, b'{"error": "slow down"}')])
    with pytest.raises(RateLimited):
        jev(opener).ask([q("r1")])


def test_http_500_raises_model_error():
    opener = FakeOpener([(500, b"internal error")])
    with pytest.raises(ModelError):
        jev(opener).ask([q("r1")])


def test_timeout_raises_model_error():
    opener = FakeOpener(TimeoutError("timed out"))
    with pytest.raises(ModelError):
        jev(opener).ask([q("r1")])


def test_connection_error_raises_model_error():
    opener = FakeOpener(urllib.error.URLError("refused"))
    with pytest.raises(ModelError):
        jev(opener).ask([q("r1")])


def test_non_json_response_raises_model_error():
    opener = FakeOpener([(200, b"<html>gateway</html>")])
    with pytest.raises(ModelError):
        jev(opener).ask([q("r1")])


class _FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, fake):
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(model_module, "urlopen", fake, raising=False)


def test_default_opener_uses_20_second_timeout(monkeypatch):
    seen = {}

    def fake_urlopen(request, *args, **kwargs):
        seen["timeout"] = kwargs.get("timeout", args[1] if len(args) > 1 else None)
        seen["url"] = request.full_url
        status, body = jev_reply(json.loads(request.data))
        return _FakeResponse(status, body)

    _patch_urlopen(monkeypatch, fake_urlopen)
    m = JevModel(JEV_URL, JEV_NAME, KEY, clock=Clock(), today=Today())
    answers = m.ask([q("r1")])
    assert seen["url"] == JEV_URL
    assert seen["timeout"] == 20
    assert {a.id for a in answers} == {"r1"}


def test_default_opener_maps_http_429_to_rate_limited(monkeypatch):
    def fake_urlopen(request, *args, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

    _patch_urlopen(monkeypatch, fake_urlopen)
    m = JevModel(JEV_URL, JEV_NAME, KEY, clock=Clock(), today=Today())
    with pytest.raises(RateLimited):
        m.ask([q("r1")])


def test_default_opener_maps_http_500_to_model_error(monkeypatch):
    def fake_urlopen(request, *args, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 500, "Server Error", {}, None)

    _patch_urlopen(monkeypatch, fake_urlopen)
    m = JevModel(JEV_URL, JEV_NAME, KEY, clock=Clock(), today=Today())
    with pytest.raises(ModelError):
        m.ask([q("r1")])


# OpenAI-compatible path


def openai_reply(content) -> tuple[int, bytes]:
    text = content if isinstance(content, str) else json.dumps(content)
    return 200, json.dumps({
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
    }).encode()


def openai_answer_all(payload: dict) -> tuple[int, bytes]:
    content = json.loads(payload["messages"][-1]["content"])
    ids = _ids_in(content)
    return openai_reply({"answers": [{"id": i, "choice": "same", "confidence": 0.9} for i in ids]})


def _ids_in(content) -> list[str]:
    """Question ids in the user message, whether it holds a list or a mapping of questions."""
    questions = content.get("questions", content) if isinstance(content, dict) else content
    if isinstance(questions, dict):
        return list(questions)
    return [item["id"] for item in questions]


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://api.example.test/v1", "https://api.example.test/v1/chat/completions"),
        ("https://api.example.test/v1/", "https://api.example.test/v1/chat/completions"),
        ("https://api.example.test/v1/chat/completions", "https://api.example.test/v1/chat/completions"),
    ],
)
def test_openai_posts_to_chat_completions(url, expected):
    opener = FakeOpener(openai_answer_all)
    openai(opener, url=url).ask([q("r1")])
    [req] = opener.requests
    assert req["url"] == expected
    assert req["method"] == "POST"
    assert req["headers"]["authorization"] == f"Bearer {KEY}"
    assert req["headers"]["content-type"] == "application/json"


def test_openai_body_shape():
    opener = FakeOpener(openai_answer_all)
    state = {"previous": {"app": "Numbers", "title": "Budget 2026", "seconds": 300}}
    openai(opener, name="gpt-4o-mini").ask([q("r1", state=state, instructions="One task or two?")])
    [req] = opener.requests
    body = req["json"]
    assert set(body) == {"model", "messages", "temperature", "response_format"}
    assert body["model"] == "gpt-4o-mini"
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    messages = body["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert isinstance(messages[0]["content"], str) and messages[0]["content"]
    user = messages[1]["content"]
    parsed = json.loads(user)  # the user message is JSON
    assert _ids_in(parsed) == ["r1"]
    assert "Budget 2026" in user
    assert "One task or two?" in user
    for option in RELATION_OPTIONS:
        assert option in user


def test_openai_parses_answers_and_drops_bad_ones():
    content = {"answers": [
        {"id": "r1", "choice": "switch", "confidence": 0.83},
        {"id": "r2", "choice": "maybe", "confidence": 0.99},  # not an option
        {"id": "r3", "choice": "same"},  # no confidence
        {"id": "zz", "choice": "same", "confidence": 0.9},  # not asked
        {"choice": "same", "confidence": 0.9},  # no id
    ]}
    opener = FakeOpener(lambda p: openai_reply(content))
    got = by_id(openai(opener).ask([q("r1"), q("r2"), q("r3")]))
    assert set(got) == {"r1"}
    assert got["r1"].choice == "switch"
    assert got["r1"].confidence == pytest.approx(0.83)


def test_openai_batches_17_questions_into_2_requests():
    opener = FakeOpener(openai_answer_all)
    questions = [q(f"r{i}") for i in range(17)]
    answers = openai(opener, min_interval_s=0, clock=Clock(step=1000.0)).ask(questions)
    assert len(opener.requests) == 2
    assert {a.id for a in answers} == {x.id for x in questions}


def test_openai_429_and_500():
    with pytest.raises(RateLimited):
        openai(FakeOpener([(429, b"{}")])).ask([q("r1")])
    with pytest.raises(ModelError):
        openai(FakeOpener([(500, b"{}")])).ask([q("r1")])


def test_openai_unparseable_content_raises_model_error():
    opener = FakeOpener(lambda p: openai_reply("not json at all"))
    with pytest.raises(ModelError):
        openai(opener).ask([q("r1")])


def test_openai_spacing_applies():
    clock = Clock(1000.0)
    opener = FakeOpener(openai_answer_all)
    m = openai(opener, min_interval_s=60, clock=clock)
    m.ask([q("r1")])
    clock.t = 1010.0
    with pytest.raises(RateLimited):
        m.ask([q("r2")])
    assert len(opener.requests) == 1


# from_settings


def settings(name=JEV_NAME, key=KEY, url=JEV_URL, calls=120):
    return SimpleNamespace(model_url=url, model_name=name, model_key=key, model_calls_per_day=calls)


def test_from_settings_picks_jev_for_typesafe_names():
    assert isinstance(from_settings(settings(name="typesafe-ai/jev")), JevModel)
    assert isinstance(from_settings(settings(name="typesafe-ai/other")), JevModel)


def test_from_settings_picks_openai_for_other_names():
    m = from_settings(settings(name="gpt-4o-mini", url="https://api.openai.com/v1"))
    assert isinstance(m, OpenAIChatModel)
    assert not isinstance(m, JevModel)


def test_from_settings_raises_without_key():
    with pytest.raises(MissingModelKey):
        from_settings(settings(key=""))
    with pytest.raises(MissingModelKey):
        from_settings(settings(name="gpt-4o-mini", key=""))


def test_missing_model_key_is_the_contracts_error():
    from taskmine import contracts

    assert issubclass(MissingModelKey, contracts.MissingModelKey) or MissingModelKey is contracts.MissingModelKey


# FakeModel


def test_fake_model_returns_scripted_answers_and_records_calls():
    m = FakeModel({"r1": ("same", 0.9), "v1": ("review", 0.6)})
    first = [q("r1"), q("r2")]
    second = [q("v1", options=["review", "update"], kind="verb")]
    got1 = by_id(m.ask(first))
    got2 = by_id(m.ask(second))
    assert set(got1) == {"r1"}
    assert got1["r1"].choice == "same"
    assert got1["r1"].confidence == pytest.approx(0.9)
    assert got2["v1"].choice == "review"
    assert got2["v1"].confidence == pytest.approx(0.6)
    assert m.calls == [first, second]


def test_fake_model_applies_guard():
    m = FakeModel({"r1": ("same", 0.9), "r2": ("same", 0.9)})
    got = by_id(m.ask([q("r1", state={"text": f"key {SECRET}"}), q("r2")]))
    assert set(got) == {"r2"}
    assert m.dropped == 1


# guard


def test_guard_drops_state_with_credential():
    secret_q = q("r1", state={"previous": {"text": f"export OPENAI_API_KEY={SECRET}"}})
    ok_q = q("r2")
    assert guard([secret_q, ok_q]) == [ok_q]


def test_guard_drops_state_with_external_ok_false_anywhere():
    nested = q("r1", state={"previous": {"items": [{"title": "Payroll", "external_ok": False}]}})
    top = q("r2", state={"external_ok": False, "app": "Mail"})
    allowed = q("r3", state={"previous": {"items": [{"title": "Notes", "external_ok": True}]}})
    assert guard([nested, top, allowed]) == [allowed]


def test_jev_ask_never_sends_guarded_questions():
    opener = FakeOpener()
    m = jev(opener)
    answers = m.ask([
        q("r1", state={"text": f"token {SECRET}"}),
        q("r2", state={"previous": {"external_ok": False}}),
        q("r3"),
    ])
    [req] = opener.requests
    assert set(req["json"]["questions"]) == {"r3"}
    assert set(req["json"]["state"]) == {"r3"}
    assert SECRET not in req["body"].decode()
    assert {a.id for a in answers} == {"r3"}
    assert m.dropped == 2


def test_ask_sends_nothing_when_every_question_is_guarded():
    opener = FakeOpener()
    m = jev(opener)
    assert m.ask([q("r1", state={"text": SECRET})]) == []
    assert opener.requests == []
    assert m.dropped == 1


def test_openai_ask_never_sends_guarded_questions():
    opener = FakeOpener(openai_answer_all)
    m = openai(opener)
    m.ask([q("r1", state={"text": SECRET}), q("r2")])
    [req] = opener.requests
    assert SECRET not in req["body"].decode()
    assert m.dropped == 1


# apply_threshold


def test_apply_threshold_keeps_answers_at_or_above_0_8():
    answers = [
        Answer(id="a", choice="same", confidence=0.8),
        Answer(id="b", choice="same", confidence=0.7999),
        Answer(id="c", choice="break", confidence=0.95),
        Answer(id="d", choice="switch", confidence=0.0),
    ]
    assert [a.id for a in apply_threshold(answers)] == ["a", "c"]


def test_apply_threshold_custom_value():
    answers = [Answer(id="a", choice="same", confidence=0.5), Answer(id="b", choice="same", confidence=0.49)]
    assert [a.id for a in apply_threshold(answers, threshold=0.5)] == ["a"]
