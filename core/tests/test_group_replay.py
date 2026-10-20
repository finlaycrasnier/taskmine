"""The four recorded days through `run_batch`, rules only (grouper spec section 9).

Each pass is forced (as by Pause), 600 s after the last event, with the rule scorer, an asker that
never asks and a model that answers nothing. Comments name the spec item each assertion proves;
"Test" items are binding, "derived" items follow from the rules in sections 1 to 6.

Readings of a PassResult (taskmine.store):
- `placements` maps every event id to its episode id.
- `episodes`, `tasks`, `processes` hold rows shaped like the tables of docs/CONTRACTS.md section 3.
  JSON columns (signature, steps, templates) may be JSON text or decoded values.
- An episode's `signature` holds `steps` with `kind`, `app` and `evidence` (action ids).
- A process's `signature` is the signature text of grouper spec 5.2 (or a JSON object with `text`),
  its `steps` are dicts with `kind`, `app` (bundle id or app label) and `label`, and its
  `templates` are strings or dicts with `text`.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("taskmine.learn", reason="taskmine.learn (rule_choice, decide) is not built yet")
pytest.importorskip("taskmine.model", reason="taskmine.model (FakeModel) is not built yet")

from replay_support import (
    load,
    members,
    parsed,
    replay_actions,
    rows,
    run_replay,
    task_groups,
)
from taskmine.group import run_batch  # noqa: F401  (fails loudly while the group is missing)

LABELS = {"com.google.Chrome": "Google Chrome", "com.apple.TextEdit": "TextEdit",
          "org.libreoffice.script": "LibreOffice"}
CASE = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,9})-(\d{1,7})(?![A-Za-z0-9])")
NAME = re.compile(r"^(Enter|Check|Approve|Reconcile|Reply to|Update|Create|Review|File) "
                  r"(?P<object>[a-z][a-z ]*) in \S.*?( · \d+ [a-z ]+)?$")


def label(app: str) -> str:
    return LABELS.get(app, app)


class Day:
    """One replayed day: its actions and the pass result, with readers."""

    def __init__(self, actions, result):
        self.actions, self.result = actions, result
        self.by_id = {a["id"]: a for a in actions}
        self.rows = {e["id"]: e for e in rows(result.episodes)}
        self.tasks = {t["id"]: t for t in rows(result.tasks)}
        self.processes = rows(result.processes)
        # Episodes in time order of their first readable action.
        self.episodes = sorted(members(result).items(), key=lambda item: min(
            (self.by_id[i]["at"], i) for i in item[1] if not self.by_id[i]["withheld"]))

    def work_ids(self, ids) -> set[str]:
        return {i for i in ids if not self.by_id[i]["withheld"]}

    def titles(self, ids) -> list[str]:
        return [self.by_id[i]["title"] or "" for i in ids]

    def episode_row(self, index: int) -> dict:
        return self.rows[self.episodes[index][0]]

    def process_of(self, index: int):
        return self.episode_row(index).get("process_id")


def run_day(name: str, variant: str | None = None) -> Day:
    actions = replay_actions(name, variant)
    return Day(actions, run_replay(actions))


@pytest.fixture(scope="module")
def stage2() -> Day:
    return run_day("stage2_replay.json")


@pytest.fixture(scope="module")
def timesheet() -> Day:
    return run_day("timesheet_replay.json")


@pytest.fixture(scope="module")
def three_tasks() -> Day:
    return run_day("three_tasks.json")


@pytest.fixture(scope="module")
def companies() -> Day:
    return run_day("three_companies.json")


@pytest.fixture(scope="module")
def companies_meeting() -> Day:
    return run_day("three_companies.json", "plus_meeting")


def process_steps(process: dict) -> list[tuple[str, str]]:
    return [(step["kind"], label(step["app"])) for step in parsed(process["steps"])]


def signature_text(process: dict) -> str:
    value = parsed(process["signature"])
    return value["text"] if isinstance(value, dict) else value


def template_texts(process: dict) -> list[str]:
    return [t if isinstance(t, str) else t["text"] for t in parsed(process["templates"])]


def cases(day: Day, ids) -> set[str]:
    found = set()
    for i in ids:
        action = day.by_id[i]
        for source in ((action["title"] or "")[:500], action["focus_name"] or ""):
            found |= {f"{prefix}-{int(number)}" for prefix, number in CASE.findall(source)}
    return found


# ---------------------------------------------------------------- 9.1 stage2_replay.json

STAGE2_WINDOWS = [("00:16:56", "00:17:38", "northwind"), ("00:18:22", "00:19:09", "brightline"),
                  ("00:19:35", "00:20:20", "harbour")]


def test_stage2_places_every_action(stage2):
    assert len(stage2.actions) == load("expected_outputs.json")["stage2_replay.json"]["input"]["events"]
    assert set(stage2.result.placements) == set(stage2.by_id)  # 9.1 Test: no unplaced action


def test_stage2_three_non_trivial_episodes(stage2):
    assert len(stage2.episodes) == 3  # 9.1 Test: 3 task episodes; 1.4: no gap forms its own episode
    assert all(not stage2.rows[e]["trivial"] for e, _ in stage2.episodes)  # 9.1 Test: none trivial


def test_stage2_boundaries(stage2):
    # 9.1 table: episode membership is Test, the times are derived.
    for (episode, ids), (start, end, _) in zip(stage2.episodes, STAGE2_WINDOWS):
        expected = {a["id"] for a in stage2.actions
                    if not a["withheld"] and start <= a["at"][11:19] <= end}
        assert stage2.work_ids(ids) == expected


def test_stage2_one_company_and_both_apps_each(stage2):
    for (episode, ids), (_, _, company) in zip(stage2.episodes, STAGE2_WINDOWS):
        work = stage2.work_ids(ids)
        # 9.1 Test: actions from both apps in every episode.
        assert {stage2.by_id[i]["app"] for i in work} == {"com.google.Chrome", "com.apple.TextEdit"}
        # 9.1 Test: one company per episode, in the order northwind, brightline, harbour.
        words = set(re.findall(r"[a-z]+", " ".join(stage2.titles(work)).casefold()))
        others = {"northwind", "brightline", "harbour"} - {company}
        assert company in words and not others & words


def test_stage2_gaps_attach_to_the_first_episode(stage2):
    gaps = {a["id"] for a in stage2.actions if a["withheld"]}
    assert len(gaps) == 2
    # Derived from 1.4: gap 1 starts within 600 s before episode 1, gap 2 inside its span.
    assert gaps <= stage2.episodes[0][1]


def test_stage2_close_reasons(stage2):
    # 9.1 table (derived): new_search_saved breaks twice; the trailing episode closes with the pass.
    reasons = [stage2.episode_row(i)["close_reason"] for i in range(3)]
    assert reasons[:2] == ["break", "break"]
    assert reasons[2] in ("quiet", "paused")


def test_stage2_one_process_of_three(stage2):
    assert len(stage2.processes) == 1  # 9.1 Test: 1 process
    process = stage2.processes[0]
    assert [stage2.process_of(i) for i in range(3)] == [process["id"]] * 3  # 9.1 Test: 3 episodes
    tasks = {stage2.episode_row(i)["task_id"] for i in range(3)}
    assert len(tasks) == 3  # 9.1 Test: 3 distinct instances


def test_stage2_process_steps_and_template(stage2):
    process = stage2.processes[0]
    # 9.1 Test: step kinds and apps.
    assert process_steps(process) == [("search", "Google Chrome")] + [
        (kind, "TextEdit") for kind in ("create", "type", "save", "close")]
    # 9.1 Test: the signature starts with the search and the new document.
    assert signature_text(process).startswith(
        "search(Google Chrome {name} - Google Search) → create(TextEdit)")
    # 9.1 Test: output template, compared case-insensitively.
    assert "onboarding - {name}" in {t.casefold() for t in template_texts(process)}


def test_stage2_rules_only_basis(stage2):
    assert stage2.processes[0]["basis"] == "rules"  # 9.1 Test: match_basis = rules
    for i in range(3):  # 9.1 Test: wording basis rules without a model
        assert stage2.tasks[stage2.episode_row(i)["task_id"]]["name_basis"] == "rules"


# ---------------------------------------------------------------- 9.2 timesheet_replay.json

def test_timesheet_places_every_action(timesheet):
    assert len(timesheet.actions) == 534
    assert set(timesheet.result.placements) == set(timesheet.by_id)


def test_timesheet_two_non_trivial_episodes(timesheet):
    assert len(timesheet.episodes) == 2  # 9.2 Test: 2 task episodes
    assert all(not timesheet.rows[e]["trivial"] for e, _ in timesheet.episodes)  # 9.2 Test: none trivial


def test_timesheet_one_case_per_episode(timesheet):
    # 9.2 Test: each episode has exactly one case, by title or focus name; Kestrel, then Northgate.
    assert [cases(timesheet, ids) for _, ids in timesheet.episodes] == [{"CS-1042"}, {"CS-1057"}]


def test_timesheet_documents_in_each_episode(timesheet):
    for _, ids in timesheet.episodes:  # 9.2 Test: Gmail, "Client rate card" and "Billing" in both
        titles = " ".join(timesheet.titles(ids))
        assert "Gmail" in titles and "Client rate card" in titles and "Billing" in titles


def test_timesheet_one_process_of_two(timesheet):
    assert len(timesheet.processes) == 1  # 9.2 Test: 1 process
    process = timesheet.processes[0]
    assert [timesheet.process_of(i) for i in range(2)] == [process["id"]] * 2  # 9.2 Test: 2 instances
    assert len({timesheet.episode_row(i)["task_id"] for i in range(2)}) == 2  # 9.2 Test: 2 distinct
    assert process["basis"] == "rules"  # 9.2 Test: match_basis = rules


def test_timesheet_rate_card_step_comes_before_billing_save(timesheet):
    # 9.2 Test and 5.2 step 4: the rate card is its own step, before the billing save step,
    # and no step mixes the two documents.
    for index in range(2):
        steps = parsed(timesheet.episode_row(index)["signature"])["steps"]
        documents = []
        for step in steps:
            titles = timesheet.titles(step["evidence"])
            found = {name for name in ("Client rate card", "Billing")
                     if any(name in title for title in titles)}
            assert len(found) <= 1, f"step {step['kind']} mixes {found}"
            documents.append((step["kind"], found))
        rate = next(i for i, (_, found) in enumerate(documents) if found == {"Client rate card"})
        save = next(i for i, (kind, found) in enumerate(documents)
                    if kind == "save" and found == {"Billing"})
        assert rate < save


# ---------------------------------------------------------------- 9.3 three_tasks.json

DOCUMENT_TASK = {"Invoice INV-4827": "invoice approval", "Approval INV-4827": "invoice approval",
                 "Ticket SUP-1187": "ticket reply", "Lofi study beats": "personal browse"}


def truth(day: Day) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for action in day.actions:
        task = next(t for prefix, t in DOCUMENT_TASK.items() if action["title"].startswith(prefix))
        out.setdefault(task, set()).add(action["id"])
    return out


def test_three_tasks_episodes(three_tasks):
    expected = truth(three_tasks)
    # 9.3 derived shape: {sessions 0, 1, 3, 4}, {session 2}, {session 5}.
    assert {frozenset(ids) for _, ids in three_tasks.episodes} == {frozenset(v) for v in expected.values()}
    by_set = {frozenset(ids): episode for episode, ids in three_tasks.episodes}
    ticket = three_tasks.rows[by_set[frozenset(expected["ticket reply"])]]
    personal = three_tasks.rows[by_set[frozenset(expected["personal browse"])]]
    assert ticket["close_reason"] == "break"  # 9.3 derived: the ticket closes with break
    assert personal["diversion"]  # 9.3 derived: the personal browse is a diversion


def test_three_tasks_tasks(three_tasks):
    # 9.3 Test: invoice approval is one task holding both invoice documents; the ticket reply
    # and the personal browse are tasks of their own.
    groups = {frozenset(ids) for ids in task_groups(three_tasks.result).values()}
    assert groups == {frozenset(ids) for ids in truth(three_tasks).values()}


def task_for(day: Day, ids: set[str]) -> dict:
    episode = day.result.placements[next(iter(ids))]
    return day.tasks[day.rows[episode]["task_id"]]


def test_three_tasks_possibly_personal(three_tasks):
    expected = truth(three_tasks)
    # 9.3 Test: the personal browse is kept and marked possibly personal (title names YouTube).
    assert task_for(three_tasks, expected["personal browse"])["possibly_personal"]
    for work in ("invoice approval", "ticket reply"):  # 9.3 Test: no work task is marked
        assert not task_for(three_tasks, expected[work])["possibly_personal"]


def test_three_tasks_names(three_tasks):
    expected = truth(three_tasks)
    for task in three_tasks.tasks.values():
        title = task["title"]
        assert NAME.match(title), title  # 9.3 Test: Verb object in System
        assert not re.search(r"\d", title)  # 9.3 Test: no digit in a name
        assert task["summary"]  # 9.3 Test: every task has a summary
    # 9.3 Test: the invoice task's object is invoice; the ticket task's object is ticket.
    assert NAME.match(task_for(three_tasks, expected["invoice approval"])["title"])["object"] == "invoice"
    assert NAME.match(task_for(three_tasks, expected["ticket reply"])["title"])["object"] == "ticket"


# ---------------------------------------------------------------- 9.4 three_companies.json

COMPANY_IDS = ("northwinddental", "harbourcafgroup", "bluebirdbakery")


def test_three_companies(companies):
    expected = load("three_companies.json")["expected"]["three_companies"]
    assert len(companies.episodes) == expected["non_trivial_task_episodes"]  # 9.4 Test: 3 episodes
    assert all(not companies.rows[e]["trivial"] for e, _ in companies.episodes)
    task_of = {i: companies.rows[e]["task_id"] for e, ids in companies.episodes for i in ids}
    for company in COMPANY_IDS:  # 9.4 Test: -open, -saved and -close in one instance
        assert len({task_of[f"{company}-{part}"] for part in ("open", "saved", "close")}) == 1
    assert len(set(task_of.values())) == 3  # 9.4 Test: 3 different instance ids
    assert len(companies.processes) == 1  # 9.4 Test: one process
    process = companies.processes[0]
    assert [companies.process_of(i) for i in range(3)] == [process["id"]] * 3  # 9.4 Test: 3 instances
    assert process["basis"] == expected["match_basis"]  # 9.4 Test: rules only


def test_three_companies_signature_templates_and_labels(companies):
    expected = load("three_companies.json")["expected"]["three_companies"]
    process = companies.processes[0]
    assert signature_text(process) == expected["signature_text"]  # 9.4 Test: signature text
    assert set(template_texts(process)) == set(expected["templates"])  # 9.4: templates
    assert [step["label"] for step in parsed(process["steps"])] == expected["step_labels"]  # 9.4 Test


def test_three_companies_with_meeting(companies_meeting):
    expected = load("three_companies.json")["expected"]["three_companies_plus_meeting"]
    day = companies_meeting
    # 9.4 Test: 4 non-trivial episodes, still 1 process of 3; the meeting is not in it (cap 0.7).
    assert len(day.episodes) == expected["non_trivial_task_episodes"]
    assert all(not day.rows[e]["trivial"] for e, _ in day.episodes)
    assert len(day.processes) == 1
    process_id = day.processes[0]["id"]
    in_process = [ids for episode, ids in day.episodes if day.rows[episode].get("process_id") == process_id]
    assert len(in_process) == expected["process_members"]
    assert not any(i.startswith("meet-") for ids in in_process for i in ids)


# ---------------------------------------------------------------- reruns (4.9 regroup)

@pytest.mark.parametrize("name, variant", [
    ("stage2_replay.json", None), ("timesheet_replay.json", None), ("three_tasks.json", None),
    ("three_companies.json", "plus_meeting"),
])
def test_second_run_gives_identical_ids(name, variant):
    # 9.1 and 9.2 Test, 4.9: ids come from content, so a second run writes the same records.
    actions = replay_actions(name, variant)
    first, second = run_replay(actions), run_replay(actions)
    assert second.placements == first.placements
    assert sorted(e["id"] for e in rows(second.episodes)) == sorted(e["id"] for e in rows(first.episodes))
    assert sorted(t["id"] for t in rows(second.tasks)) == sorted(t["id"] for t in rows(first.tasks))
    assert sorted(p["id"] for p in rows(second.processes)) == sorted(p["id"] for p in rows(first.processes))
    assert {e["id"]: e["task_id"] for e in rows(second.episodes)} == {
        e["id"]: e["task_id"] for e in rows(first.episodes)}
