"""Page journeys for the review page in headless Chrome, written from review-page-spec 1.2,
1.3, 1.4, 1.6, 1.7a, 1.11 and 5 (behaviours 3, 4, 7, 8c, 13).

Drives the page with the `chrome-devtools-axi` CLI. Skipped when the CLI is not on PATH or
when taskmine.review does not import. The page is found by visible text and labels only
("Task name", "Save name", "One task or two?", "Yes, one task", "Pause", "Paused"), so the
DOM structure stays the implementer's choice.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import time

import pytest
from review_world import XSS_TEXT, Running, seed_world

AXI = shutil.which("chrome-devtools-axi")
pytestmark = pytest.mark.skipif(AXI is None, reason="chrome-devtools-axi is not on PATH")
pytest.importorskip("taskmine.review")

# Helpers defined inside every evaluated function. They look up elements by visible text.
HELPERS = """
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
  const visibleText = () => norm(document.body.innerText);
  const allText = () => norm(document.body.textContent);
  const byText = (sel, text) => [...document.querySelectorAll(sel)]
      .filter(el => norm(el.textContent).includes(text));
  const button = text => byText('button', text)[0] || null;
  const fieldFor = label => {
    for (const input of document.querySelectorAll('input, textarea')) {
      const names = [input.getAttribute('aria-label'), input.getAttribute('placeholder'),
                     ...[...(input.labels || [])].map(l => l.textContent)];
      if (names.some(n => norm(n).includes(label))) return input;
    }
    return null;
  };
"""


def axi(*args: str, timeout: float = 60) -> str:
    out = subprocess.run([AXI, *args], capture_output=True, text=True, timeout=timeout,
                         check=False)
    if out.returncode != 0 or out.stdout.lstrip().startswith("error:"):
        raise AssertionError(f"chrome-devtools-axi {args[0]} failed: {out.stdout}{out.stderr}")
    return out.stdout


def _decode(raw: str):
    value = raw
    for _ in range(4):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except ValueError:
            break
    return value


def js(body: str):
    """Run `body` (statements ending in `return <value>`) in the page; return the value."""
    fn = "() => {" + HELPERS + body + "}"
    out = axi("eval", fn)
    for line in out.splitlines():
        if line.startswith("result:"):
            return _decode(line[len("result:"):].strip())
    raise AssertionError(f"no result from eval: {out}")


def wait_for(body: str, timeout: float = 10.0, what: str = ""):
    """Poll a JS predicate until it returns a truthy value."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = js(body)
        if last:
            return last
        time.sleep(0.25)
    raise AssertionError(f"timed out waiting for {what or body!r}; last value {last!r}")


@pytest.fixture(scope="module")
def browser():
    yield
    with contextlib.suppress(AssertionError, OSError, subprocess.TimeoutExpired):
        axi("stop", timeout=30)


@pytest.fixture
def page(tmp_path, browser):
    running = Running(tmp_path)
    world = seed_world(running.client, running.store)
    axi("open", f"{running.origin}/")
    wait_for("return allText().includes('Invoice review A');", 15, "the task list")
    yield running, world
    running.close()


def open_task(title: str) -> None:
    js(f"const b = byText('button', {json.dumps(title)})[0]; b.click(); return true;")


def rename(title: str) -> None:
    ok = js(f"""
      const input = fieldFor('Task name');
      const save = button('Save name');
      if (!input || !save) return false;
      input.value = {json.dumps(title)};
      input.dispatchEvent(new Event('input', {{bubbles: true}}));
      input.dispatchEvent(new Event('change', {{bubbles: true}}));
      save.click();
      return true;
    """)
    assert ok, "no 'Task name' field and 'Save name' button in the opened task"


# ---------------------------------------------------------------- journeys


def test_page_shows_task_titles_newest_first(page):
    order = js("""
      const t = allText();
      return ['Personal D', 'Notes C', 'Invoice review A', 'Order B'].map(x => t.indexOf(x));
    """)
    assert all(i >= 0 for i in order), order
    assert order == sorted(order), order


def test_opened_task_shows_actions_as_plain_text(page):
    open_task("Invoice review A")
    wait_for(f"return allText().includes({json.dumps(XSS_TEXT)});", 10, "the action text")
    result = js("""
      return {
        injected: [...document.images].filter(i => i.getAttribute('src') === 'x').length,
        ran: window.__taskmineXss === undefined ? 0 : 1,
        ids: ['desktop-a1', 'desktop-a2', 'desktop-a3'].filter(id => allText().includes(id)),
      };
    """)
    assert result == {"injected": 0, "ran": 0, "ids": []}


def test_rename_posts_expected_revision_and_updates_the_title(page):
    running, world = page
    open_task("Invoice review A")
    rename("Approve supplier invoice")
    wait_for("return visibleText().includes('Approve supplier invoice');", 10, "the new title")
    task = running.store.task(world.A)
    assert task["title"] == "Approve supplier invoice"
    assert task["revision"] == 2


def test_stale_rename_reloads_and_says_so(page):
    running, world = page
    open_task("Invoice review A")
    wait_for("return !!fieldFor('Task name');", 10, "the rename field")
    running.store.correct(world.A, 1, {"op": "rename", "title": "Changed elsewhere"})
    rename("My stale name")
    wait_for("return visibleText().includes('reloaded');", 10, "the reload notice")
    wait_for("return visibleText().includes('Changed elsewhere');", 10, "the server title")
    notice = js("""
      const hits = [...document.querySelectorAll('body *')]
          .filter(el => norm(el.textContent).includes('reloaded'));
      const deepest = hits.filter(el => !hits.some(o => o !== el && el.contains(o)));
      return deepest.map(el => norm(el.textContent));
    """)
    assert notice and all("Refresh" not in n for n in notice), notice
    task = running.store.task(world.A)
    assert task["title"] == "Changed elsewhere" and task["revision"] == 2


def test_question_block_answer_removes_it(page):
    running, _ = page
    wait_for("return visibleText().includes('One task or two?');", 10, "the question block")
    assert js("const b = button('Yes, one task'); if (!b) return false; b.click(); return true;")
    wait_for("return !visibleText().includes('One task or two?');", 10, "the block to go")
    assert running.store.questions() == []


def test_pause_flips_the_strip_to_paused(page):
    running, _ = page
    wait_for("return visibleText().includes('Recording');", 10, "the Recording strip")
    assert js("""
      const b = [...document.querySelectorAll('button')].find(x => norm(x.textContent) === 'Pause');
      if (!b) return false; b.click(); return true;
    """)
    wait_for("return visibleText().includes('Paused');", 10, "the Paused strip")
    wait_for("return [...document.querySelectorAll('button')]"
             ".some(x => norm(x.textContent) === 'Resume');", 10, "the Resume button")
    assert running.client.get("/v1/policy").json["state"] == "paused"
