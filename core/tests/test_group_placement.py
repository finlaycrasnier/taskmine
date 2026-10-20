"""data/replay/native_window_cases.json through `run_batch` and its task placement (grouper spec 2.4, 9.5).

`run_batch` places each episode with `join_tasks`: same explicit case, else same artifact within
7200 s, else same window within 120 s and span 900 s, else a new task. A task is read as the set of
event ids whose episodes carry its id.

Ops are emulated between batches. `existing` is the task list of earlier PassResults (`tasks`
rows, as dicts), changed the way the store's correction would change it:
- `complete_group_containing`: that task gets `lifecycle = "completed"` and `locked = 1` (8.1).
- `split`: the chosen actions go through their own batch with no existing tasks, the rest through
  another; every task is then locked (8.1, a correction locks both sides).

Cases skipped, as agreed in the design:
- pause_resume_weak_continuity: tests arrival timing around pause and resume ops.
- generation_change: tests a generation reset; the core has no generations.

Cases marked xfail: their expected groups are layer-1 instance groups that the layer-2 rules of
this spec join before any task placement runs. A task holds whole episodes (episodes.task_id), so
`join_tasks` cannot split them. The reason on each names the rule that joins the events.
"""
from __future__ import annotations

import pytest

pytest.importorskip("taskmine.learn", reason="taskmine.learn (rule_choice, decide) is not built yet")
pytest.importorskip("taskmine.model", reason="taskmine.model (FakeModel) is not built yet")

from replay_support import case_actions, load, rows, run_replay
from taskmine.group import join_tasks  # noqa: F401  (fails loudly while the group is missing)

CASES = {case["name"]: case for case in load("native_window_cases.json")}

SKIPPED = {
    "pause_resume_weak_continuity": "arrival timing around pause and resume ops (agreed skip)",
    "generation_change": "generation reset; the core has no generations (agreed skip)",
}
LAYER1_ONLY = {
    "different_terminal_windows":
        "3.1: g1, g2, g3 share app and title within 120 s, so they are one session and one episode",
    "case_conflict_overrides_document":
        "3.1: a and b share app and document 1 s apart, so they are one session and one episode",
    "different_documents_same_window":
        "4.4 shared_anchor: b shares window-a with a, a hard join, so a, b, a2 are one episode",
    "same_window_gap_boundary":
        "4.4 shared_anchor: c shares window-a with b, a hard join after the 121 s session break",
    "missing_window_identity_not_wildcard":
        "3.1: an item with no surface key joins the open session of its app, so the click joins a",
}


def play(case: dict, *, each: bool = False) -> dict[str, str]:
    """Event id -> task id over the case's steps; `each` sends every observe as its own batch."""
    tasks: dict[str, dict] = {}
    task_of: dict[str, str] = {}

    def flush(steps: list[dict], existing: list[dict]) -> None:
        if not steps:
            return
        result = run_replay(case_actions(steps), existing=existing)
        tasks.update({t["id"]: dict(t) for t in rows(result.tasks)})
        episode_task = {e["id"]: e["task_id"] for e in rows(result.episodes)}
        task_of.update({event: episode_task[episode] for event, episode in result.placements.items()})

    pending: list[dict] = []
    for step in case["steps"]:
        if step["op"] == "observe":
            pending.append(step)
            if each:
                flush(pending, list(tasks.values()))
                pending = []
            continue
        if step["op"] == "split":
            moved = [s for s in pending if s["id"] in step["ids"]]
            flush([s for s in pending if s not in moved], list(tasks.values()))
            flush(moved, [])
            for task in tasks.values():
                task["locked"] = 1
        elif step["op"] == "complete_group_containing":
            flush(pending, list(tasks.values()))
            task = tasks[task_of[step["id"]]]
            task["lifecycle"], task["locked"] = "completed", 1
        else:
            raise AssertionError(f"unexpected op {step['op']}")
        pending = []
    flush(pending, list(tasks.values()))
    return task_of


def groups(task_of: dict[str, str]) -> set[frozenset[str]]:
    out: dict[str, set[str]] = {}
    for event, task in task_of.items():
        out.setdefault(task, set()).add(event)
    return {frozenset(ids) for ids in out.values()}


def params():
    for name, case in CASES.items():
        marks = []
        if name in SKIPPED:
            marks.append(pytest.mark.skip(reason=SKIPPED[name]))
        elif name in LAYER1_ONLY:
            marks.append(pytest.mark.xfail(reason="layer-1 only: " + LAYER1_ONLY[name], strict=False))
        yield pytest.param(case, id=name, marks=marks)


def test_case_list_is_complete():
    assert len(CASES) == 19  # 9.5: 19 layer-1 placement cases
    assert set(SKIPPED) | set(LAYER1_ONLY) <= set(CASES)


@pytest.mark.parametrize("case", list(params()))
def test_expected_groups(case):
    # 9.5 Test: expected_groups per case (sets of event ids per task).
    expected = {frozenset(group) for group in case["expected_groups"]}
    assert groups(play(case)) == expected


def test_same_case_joins_an_existing_task():
    # 2.4 rule 1 across passes: same explicit case, task still joinable.
    case = dict(CASES["completed_case_repeats"])
    case["steps"] = [s for s in case["steps"] if s["op"] == "observe"]
    task_of = play(case, each=True)
    assert task_of["a"] == task_of["b"]


def test_same_document_joins_an_existing_task():
    # 2.4 rule 2 across passes: same artifact within 7200 s.
    task_of = play(CASES["finder_textedit_shared_document"], each=True)
    assert task_of["f"] == task_of["t"]


def test_no_shared_signal_starts_a_new_task_across_passes():
    # 2.4 rule 5: no case, artifact or window in common.
    task_of = play(CASES["finder_textedit_no_relationship"], each=True)
    assert task_of["f"] != task_of["t"]
