// Taskmine review page. No framework, no build step. Data is shown with textContent only.
(() => {
  'use strict';

  const POLL_MS = 15000;
  const PAGE = 100;
  const TEXT = {
    nameSaved: 'Name saved. Taskmine uses it for this kind of task from now on.',
    corrected: 'Correction saved. Taskmine learns from it. Review the updated case before sharing.',
    stale: 'This case changed, so Taskmine reloaded it. Check it, then choose your correction again.',
    paused: 'Capture paused.',
    resumed: 'Capture resumed under your current settings.',
    personal: 'Deleted from this Mac. Only its minutes total stays, and only you see it.',
    one: 'Saved: one task. Taskmine joined them and will remember this.',
    two: 'Saved: two tasks. Taskmine keeps them apart and will remember this.',
    draft: 'Review this draft before you use it.',
  };
  const NAME_BASIS = {
    rules: 'Named from its steps.',
    model: "Named from its steps, with a model's help.",
    person: 'Your name for this kind of task.',
  };
  // Headings by action code (review-page-spec 1.4.2).
  const ACTION_LABELS = {
    click: 'Clicked', clicked: 'Clicked', edit: 'Edited text', typed: 'Edited text',
    edited: 'Edited text', content_change: 'Edited text', focus: 'Switched focus',
    focused: 'Switched focus', element_focus: 'Switched focus', window_focus: 'Switched window',
    navigation: 'Opened page', navigated: 'Opened page', scroll: 'Scrolled',
    shortcut: 'Used a shortcut', opened: 'Opened', saved: 'Saved', resumed: 'Resumed',
    completed: 'Completed',
  };
  const APP_NAMES = {
    'com.microsoft.Excel': 'Excel', 'com.apple.iWork.Numbers': 'Numbers',
    'com.google.Chrome': 'Chrome', 'com.apple.Safari': 'Safari',
    'com.tinyspeck.slackmacgap': 'Slack', 'com.microsoft.VSCode': 'VS Code',
    'com.apple.Terminal': 'Terminal', 'com.apple.finder': 'Finder', 'com.apple.mail': 'Mail',
    'com.apple.TextEdit': 'TextEdit',
  };
  const VERBS = {
    edit: 'Edit', browse: 'Browse', search: 'Search', create: 'Create', save: 'Save',
    view: 'View', read: 'Read', send: 'Send', click: 'Work', open: 'Open',
  };

  const state = {
    view: 'activity',
    tasks: [], cursor: null, coverage: null, loaded: false, listError: '',
    listSeq: 0, listLoading: false,
    questions: [], processes: null, drafts: null,
    activeTaskId: null, manual: false, autoOpened: false,
    taskActions: {}, actionSeq: {},
    open: new Set(), nameDrafts: {}, related: {}, chosen: {},
    busy: false, notice: '', noticeKind: '', activeDraft: null,
  };

  // ------------------------------------------------------------ helpers

  const $ = (sel) => document.querySelector(sel);
  const enc = encodeURIComponent;

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'text') node.textContent = value;
      else if (key === 'class') node.className = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key === 'value') node.value = value;
      else if (key === 'checked') node.checked = Boolean(value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function parseDate(value) {
    const d = value ? new Date(value) : null;
    return d && !Number.isNaN(d.getTime()) ? d : null;
  }

  function hhmm(value) {
    const d = parseDate(value);
    if (!d) return 'Time unavailable';
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  function dayLabel(value) {
    const d = parseDate(value);
    if (!d) return 'Date unavailable';
    const today = new Date();
    const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return 'Today';
    if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
    return d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  }

  function plural(n, word) {
    return `${n} ${word}${n === 1 ? '' : 's'}`;
  }

  function appName(app, name) {
    return name || APP_NAMES[app] || app || 'Application';
  }

  function stepText(step) {
    const verb = VERBS[step.kind] || String(step.kind || 'work').replace(/_/g, ' ');
    const label = verb.charAt(0).toUpperCase() + verb.slice(1);
    return `${label} in ${appName(step.app, null)}`;
  }

  // Only http and https, no user name or password, no query or fragment.
  function activityUrl(raw) {
    let url;
    try { url = new URL(raw); } catch { return null; }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    if (url.username || url.password) return null;
    return { href: url.origin + url.pathname, label: url.host + url.pathname };
  }

  function sortTasks(list) {
    const byId = new Map();
    for (const task of list) byId.set(task.id, task);
    return [...byId.values()].sort((a, b) =>
      (b.last_seen_at || '').localeCompare(a.last_seen_at || '') || (b.id < a.id ? -1 : b.id > a.id ? 1 : 0));
  }

  function sortActions(list) {
    const byId = new Map();
    for (const action of list) byId.set(action.event_id, action);
    const at = (a) => a.observed_at || a.first_seen_at || '';
    return [...byId.values()].sort((a, b) =>
      at(a).localeCompare(at(b)) || (a.event_id < b.event_id ? -1 : a.event_id > b.event_id ? 1 : 0));
  }

  class ApiFailure extends Error {
    constructor(status, code, message) {
      super(message);
      this.status = status;
      this.code = code;
    }
  }

  async function api(method, path, body) {
    const options = { method, credentials: 'same-origin', headers: {} };
    if (method !== 'GET') options.headers['X-Taskmine-Request'] = 'page';
    if (method === 'POST' || method === 'PATCH') {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body || {});
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch {
      throw new ApiFailure(0, 'unreachable', 'Taskmine is not answering. Check that the core is running.');
    }
    let data = null;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      const error = (data && data.error) || {};
      throw new ApiFailure(response.status, error.code || '', error.message || `Request failed (${response.status}).`);
    }
    return data;
  }

  function notify(message, kind = '') {
    state.notice = message;
    state.noticeKind = kind;
    renderNotice();
  }

  // ------------------------------------------------------------ capture strip

  function captureStatus(cov) {
    if (!cov) {
      return { label: 'Recorder offline', kind: 'offline',
        detail: 'Recorder status is unavailable. Previously saved tasks remain here.' };
    }
    if (cov.enabled === false || cov.capture_state === 'paused') {
      return { label: 'Paused', kind: 'paused',
        detail: 'New activity is not being saved. Your earlier tasks remain available.' };
    }
    if (cov.online === true && cov.capture_state === 'revoked') {
      return { label: 'Paused', kind: 'paused',
        detail: 'Recorder permission has been withdrawn. Review desktop permissions before recording again.' };
    }
    if (cov.online === true && cov.capture_state === 'pending') {
      return { label: 'Paused', kind: 'paused',
        detail: 'Recorder setup is awaiting approval. Review desktop permissions to begin recording.' };
    }
    if (cov.enabled === true && cov.online === true
        && ['recording', 'capturing', 'active'].includes(cov.capture_state)) {
      return { label: 'Recording', kind: 'recording',
        detail: 'Saving permitted actions and visible context as you work.' };
    }
    return { label: 'Recorder offline', kind: 'offline',
      detail: 'No active recorder is reporting capture. Previously saved tasks remain here.' };
  }

  function isPaused(cov) {
    return Boolean(cov) && (cov.enabled === false || cov.capture_state === 'paused');
  }

  async function pause() {
    const cov = state.coverage;
    if (!cov || typeof cov.enabled !== 'boolean' || state.busy) return false;
    const paused = !isPaused(cov);
    state.busy = true;
    render();
    try {
      await api('POST', '/v1/policy', { paused });
      notify(paused ? TEXT.paused : TEXT.resumed);
      await loadTasks();
      return true;
    } catch (err) {
      notify(err.message, 'error');
      return false;
    } finally {
      state.busy = false;
      render();
    }
  }

  // ------------------------------------------------------------ loading

  async function loadTasks(more = false) {
    const seq = ++state.listSeq;
    state.listLoading = true;
    let path = `/v1/tasks?limit=${PAGE}`;
    if (more && state.cursor) path += `&before=${enc(state.cursor)}`;
    try {
      const data = await api('GET', path);
      if (seq !== state.listSeq) return;
      state.tasks = sortTasks(more ? state.tasks.concat(data.tasks) : data.tasks);
      state.cursor = data.next_cursor || null;
      state.coverage = data.coverage || null;
      state.listError = '';
      state.loaded = true;
      afterTasks();
    } catch (err) {
      if (seq === state.listSeq) state.listError = err.message;
    } finally {
      if (seq === state.listSeq) {
        state.listLoading = false;
        render();
      }
    }
  }

  function afterTasks() {
    const ids = new Set(state.tasks.map((t) => t.id));
    for (const id of Object.keys(state.taskActions)) if (!ids.has(id)) delete state.taskActions[id];
    if (state.activeTaskId && !ids.has(state.activeTaskId)) state.activeTaskId = null;
    if (!state.manual && !state.autoOpened && !state.activeTaskId && state.tasks.length) {
      state.autoOpened = true;
      state.activeTaskId = state.tasks[0].id;
    }
    const task = activeTask();
    const cached = task && state.taskActions[task.id];
    if (task && (!cached || cached.revision !== task.revision || cached.count !== task.action_count)) {
      loadTaskActions(task.id);
    }
  }

  async function loadTaskActions(taskId, more = false) {
    const seq = (state.actionSeq[taskId] || 0) + 1;
    state.actionSeq[taskId] = seq;
    const entry = state.taskActions[taskId]
      || (state.taskActions[taskId] = { actions: [], episodes: [], cursor: null, loading: false, error: '' });
    entry.loading = true;
    let path = `/v1/tasks/${enc(taskId)}/actions?limit=${PAGE}`;
    if (more && entry.cursor) path += `&before=${enc(entry.cursor)}`;
    const current = () => seq === state.actionSeq[taskId] && state.activeTaskId === taskId
      && state.taskActions[taskId] === entry;
    try {
      const data = await api('GET', path);
      if (!current()) return;
      entry.actions = sortActions(more ? entry.actions.concat(data.actions) : data.actions);
      entry.episodes = data.episodes || [];
      entry.cursor = data.next_cursor || null;
      entry.revision = data.task.revision;
      entry.count = data.task.action_count;
      entry.error = '';
    } catch (err) {
      if (current()) entry.error = err.message;
    } finally {
      if (seq === state.actionSeq[taskId]) entry.loading = false;
      if (current()) render();
    }
  }

  async function loadQuestions() {
    try {
      const data = await api('GET', '/v1/questions');
      state.questions = data.questions || [];
    } catch {
      // Questions never block reading saved activity.
    }
    renderQuestions();
  }

  async function loadProcesses() {
    try {
      state.processes = (await api('GET', '/v1/processes')).processes || [];
    } catch (err) {
      state.processes = { error: err.message };
    }
    renderView();
  }

  async function loadDrafts() {
    try {
      state.drafts = (await api('GET', '/v1/drafts')).drafts || [];
    } catch (err) {
      state.drafts = { error: err.message };
    }
    renderView();
  }

  function activeTask() {
    return state.tasks.find((t) => t.id === state.activeTaskId) || null;
  }

  function selectTask(taskId) {
    state.manual = true;
    state.activeTaskId = state.activeTaskId === taskId ? null : taskId;
    if (state.activeTaskId) {
      const entry = state.taskActions[taskId];
      if (!entry || (!entry.loading && entry.revision === undefined)) loadTaskActions(taskId);
    }
    render();
  }

  // ------------------------------------------------------------ corrections

  async function correctTask(task, operation) {
    if (state.busy || !Number.isInteger(task.revision)) return false;
    state.busy = true;
    render();
    try {
      const data = await api('POST', `/v1/tasks/${enc(task.id)}/correct`,
        { expected_revision: task.revision, operation });
      delete state.nameDrafts[task.id];
      delete state.chosen[task.id];
      delete state.taskActions[task.id];
      if (data.task && data.task.id !== task.id) {
        delete state.taskActions[data.task.id];
        state.activeTaskId = data.task.id;
      }
      await loadTasks();
      notify(operation.op === 'rename' ? TEXT.nameSaved : TEXT.corrected);
      return true;
    } catch (err) {
      if (err.status === 409) {
        delete state.nameDrafts[task.id];
        delete state.taskActions[task.id];
        await loadTasks();
        notify(TEXT.stale, 'error');
      } else {
        notify(err.message, 'error');
      }
      return false;
    } finally {
      state.busy = false;
      render();
    }
  }

  async function markPersonal(task) {
    if (state.busy) return false;
    state.busy = true;
    render();
    try {
      await api('POST', `/v1/tasks/${enc(task.id)}/personal`, {});
      await loadTasks();
      notify(TEXT.personal);
      return true;
    } catch (err) {
      notify(err.message, 'error');
      return false;
    } finally {
      state.busy = false;
      render();
    }
  }

  async function answerQuestion(question, answer) {
    if (state.busy) return false;
    state.busy = true;
    renderQuestions();
    try {
      const data = await api('POST', `/v1/questions/${enc(question.key)}/answer`, { answer });
      state.questions = data.questions || [];
      notify(answer === 'one' ? TEXT.one : TEXT.two);
      await loadTasks();
      return true;
    } catch (err) {
      notify(err.message, 'error');
      await loadTasks();
      await loadQuestions();
      return false;
    } finally {
      state.busy = false;
      render();
    }
  }

  // ------------------------------------------------------------ write-ups

  async function reviewTask(task) {
    if (state.busy) return false;
    state.busy = true;
    render();
    try {
      let reply = null;
      for (let attempt = 0; attempt < 2 && !reply; attempt += 1) {
        try {
          reply = await api('POST', `/v1/tasks/${enc(task.id)}/draft`, {});
        } catch (err) {
          const retry = err.status === 409 && attempt === 0
            && ['instance_changed', 'evidence_changed', 'evidence_expired'].includes(err.code);
          if (!retry) throw err;
          await loadTasks();
        }
      }
      const data = await api('GET', `/v1/drafts/${enc(reply.draft_id)}`);
      openDraft(data.draft);
      notify(TEXT.draft);
      loadTasks();
      return true;
    } catch (err) {
      notify(err.message, 'error');
      return false;
    } finally {
      state.busy = false;
      render();
    }
  }

  const DRAFT_FIELDS = ['outcome', 'owner', 'inputs', 'steps', 'exceptions', 'completion'];

  function openDraft(draft) {
    state.activeDraft = draft;
    for (const name of DRAFT_FIELDS) $(`#draft-${name}`).value = draft.workflow[name] || '';
    $('#draft-notice').textContent = '';
    const dialog = $('#draft-dialog');
    if (!dialog.open) dialog.showModal();
  }

  function closeDraft() {
    const dialog = $('#draft-dialog');
    if (dialog.open) dialog.close();
  }

  function clearDraft() {
    state.activeDraft = null;
    for (const name of DRAFT_FIELDS) $(`#draft-${name}`).value = '';
    if (state.view === 'drafts') loadDrafts();
  }

  async function saveDraft() {
    const draft = state.activeDraft;
    if (!draft) return;
    const body = {};
    for (const name of DRAFT_FIELDS) body[name] = $(`#draft-${name}`).value;
    try {
      const data = await api('PATCH', `/v1/drafts/${enc(draft.id)}`, body);
      state.activeDraft = data.draft;
      $('#draft-notice').textContent = 'Changes saved on this Mac.';
    } catch (err) {
      $('#draft-notice').textContent = err.message;
    }
  }

  async function deleteDraft() {
    const draft = state.activeDraft;
    if (!draft) return;
    try {
      await api('DELETE', `/v1/drafts/${enc(draft.id)}`);
      closeDraft();
      notify('Draft deleted.');
      loadTasks();
    } catch (err) {
      $('#draft-notice').textContent = err.message;
    }
  }

  // ------------------------------------------------------------ rendering

  function render() {
    const focused = document.activeElement && document.activeElement.id;
    renderStrip();
    renderNotice();
    renderTasks();
    renderQuestions();
    renderView();
    if (focused && document.activeElement && document.activeElement.id !== focused) {
      const again = document.getElementById(focused);
      if (again) again.focus();
    }
  }

  function renderStrip() {
    const status = state.loaded || state.listError
      ? captureStatus(state.coverage)
      : { label: 'Checking recorder', detail: '', kind: 'checking' };
    $('#strip').className = `strip is-${status.kind}`;
    $('#strip-label').textContent = status.label;
    $('#strip-detail').textContent = status.detail;
    const button = $('#pause');
    const cov = state.coverage;
    button.textContent = isPaused(cov) ? 'Resume' : 'Pause';
    button.disabled = !cov || typeof cov.enabled !== 'boolean' || state.busy;

    const line = $('#personal-time');
    const pt = cov && cov.personal_time;
    if (pt && pt.today_tasks > 0) {
      const minutes = pt.today_minutes > 0 ? `${pt.today_minutes} min` : 'under 1 min';
      line.textContent = `Personal time today: ${minutes}. Deleted from this Mac after grouping. Only you see this total.`;
      line.hidden = false;
    } else {
      line.hidden = true;
    }
    renderCoverage(cov);
  }

  function renderCoverage(cov) {
    const lines = [
      'Saved actions include the visible context supplied by connected sources. This is not a complete record of every keystroke or action.',
      'Password fields and private browsing are configured exclusions.',
      'When a field loses focus, only its final value is saved, never keys or pauses. Personal data in it is replaced by a code first. Nothing is saved from password fields, secure input, or blocked apps or sites.',
      'The credential filters can miss an unlabelled password, an unknown token format, unusual encoding or an OCR error. They can also redact ordinary text that looks like a credential.',
      ...((cov && cov.limitations) || []),
    ];
    if (cov && cov.excluded_count > 0) {
      lines.push(`${plural(cov.excluded_count, 'capture attempt')} ${cov.excluded_count === 1 ? 'was' : 'were'} excluded before saving.`);
    }
    if (cov && Number.isInteger(cov.retention_days)) {
      lines.push(`Saved actions are deleted from this Mac after ${plural(cov.retention_days, 'day')}.`);
    }
    $('#coverage-body').replaceChildren(...lines.map((text) => el('p', { text })));
  }

  function renderNotice() {
    const node = $('#notice');
    node.textContent = state.notice;
    node.className = `notice${state.noticeKind ? ` is-${state.noticeKind}` : ''}`;
  }

  function taskMeta(task) {
    const parts = [];
    if (task.apps && task.apps.length) parts.push([...new Set(task.apps)].join(', '));
    const count = Number.isInteger(task.action_count) ? task.action_count : 0;
    parts.push(`${count} saved action${count === 1 ? '' : 's'}`);
    if (task.lifecycle === 'completed') parts.push('Complete');
    if (task.possibly_personal) parts.push('Possibly personal');
    return parts.join(' · ');
  }

  function renderTasks() {
    const list = $('#task-list');
    list.setAttribute('aria-busy', String(state.listLoading));
    $('#more-tasks').hidden = !state.cursor;
    if (!state.loaded) {
      list.replaceChildren(state.listError
        ? el('div', { class: 'empty' }, el('p', { class: 'empty-title', text: 'Activity is unavailable' }), el('p', { text: state.listError }))
        : el('p', { class: 'empty', text: 'Loading activity…' }));
      return;
    }
    if (!state.tasks.length) {
      list.replaceChildren(el('div', { class: 'empty' },
        el('p', { class: 'empty-title', text: 'No saved activity yet' }),
        el('p', { text: 'Tasks will appear as your connected recorder saves permitted activity.' })));
      return;
    }
    const nodes = [];
    let lastDay = null;
    for (const task of state.tasks) {
      const when = task.started_at || task.last_seen_at;
      const day = dayLabel(when);
      if (day !== lastDay) {
        nodes.push(el('h3', { class: 'day', text: day }));
        lastDay = day;
      }
      nodes.push(renderTask(task, when));
    }
    if (state.listError) nodes.unshift(el('p', { class: 'notice is-error', text: state.listError }));
    list.replaceChildren(...nodes);
  }

  function renderTask(task, when) {
    const open = task.id === state.activeTaskId;
    const panelId = `activity-task-${enc(task.id)}`;
    const title = task.title || 'Unlabelled task';
    const process = task.process && task.process.name && task.process.name !== title
      ? (task.process.name.startsWith('Repeated task') ? task.process.name : `Repeated task: ${task.process.name}`)
      : null;
    const row = el('button', {
      type: 'button', class: 'task-row', 'aria-expanded': String(open), 'aria-controls': panelId,
      onclick: () => selectTask(task.id),
    },
    el('span', { class: 'time', text: hhmm(when) }),
    el('span', { class: 'row-main' },
      el('span', { class: 'title', text: title }),
      el('span', { class: 'meta', text: taskMeta(task) }),
      process && el('span', { class: 'meta', text: process })),
    el('span', { class: 'marker', 'aria-hidden': 'true', text: open ? '−' : '+' }));
    return el('article', { class: `task${open ? ' is-open' : ''}` }, row,
      open && renderPanel(task, panelId));
  }

  function disclosure(key, summary, ...children) {
    const node = el('details', { 'data-key': key, open: state.open.has(key) },
      el('summary', { text: summary }), ...children);
    node.addEventListener('toggle', () => {
      if (node.open) state.open.add(key); else state.open.delete(key);
    });
    return node;
  }

  function renderPanel(task, panelId) {
    const entry = state.taskActions[task.id];
    const panel = el('div', { class: 'task-panel', id: panelId });
    panel.append(el('p', { class: 'basis', text: NAME_BASIS[task.name_basis] || NAME_BASIS.rules }));
    if (task.summary) panel.append(el('p', { text: task.summary }));
    if (task.possibly_personal) {
      panel.append(el('div', { class: 'personal-box' },
        el('p', { text: 'A window title named a personal site, but no web address confirmed it, so Taskmine kept this task. If it was personal, delete it from this Mac. Only a minutes total stays, and only you see it.' }),
        el('button', { type: 'button', disabled: state.busy, onclick: () => markPersonal(task), text: 'This was personal' })));
    }
    panel.append(el('h4', { text: 'Saved actions' }), renderActions(task, entry));
    panel.append(renderCorrections(task, entry));
    panel.append(el('div', { class: 'review' },
      el('button', {
        type: 'button', disabled: state.busy, 'aria-busy': String(state.busy),
        onclick: () => reviewTask(task), text: task.draft_id ? 'Open write-up' : 'Write up this task',
      }),
      el('p', { class: 'note', text: 'Review makes a local draft on this machine. Nothing is shared.' })));
    return panel;
  }

  function renderActions(task, entry) {
    if (!entry || (entry.loading && !entry.actions.length)) {
      return el('p', { class: 'muted', text: 'Loading saved actions…' });
    }
    if (entry.error && !entry.actions.length) {
      return el('div', {}, el('p', { class: 'notice is-error', text: entry.error }),
        el('button', { type: 'button', onclick: () => loadTaskActions(task.id), text: 'Try again' }));
    }
    if (!entry.actions.length) {
      return el('p', { class: 'muted', text: 'No retained action context is available for this task.' });
    }
    const wrap = el('div', {});
    if (entry.cursor) {
      wrap.append(el('button', { type: 'button', onclick: () => loadTaskActions(task.id, true), text: 'Load earlier actions' }));
    }
    wrap.append(el('ol', { class: 'actions' }, entry.actions.map(renderAction)));
    return wrap;
  }

  function actionHeading(action) {
    const code = String(action.interaction || '').toLowerCase();
    if (ACTION_LABELS[code]) return ACTION_LABELS[code];
    if (code) return code.charAt(0).toUpperCase() + code.slice(1).replace(/_/g, ' ');
    if (action.kind === 'capture_gap') return 'Capture gap';
    if (action.kind === 'interaction') return 'Interaction';
    if (action.provenance === 'screen_ocr') return 'Saved screen text';
    return 'Saved visible context';
  }

  function renderAction(action) {
    const heading = actionHeading(action);
    const link = action.url ? activityUrl(action.url) : null;
    const lastSeen = action.last_seen_at && action.last_seen_at !== action.first_seen_at
      ? `Last seen at ${hhmm(action.last_seen_at)}.` : null;
    const item = el('li', { class: 'action' },
      el('div', { class: 'action-head' },
        el('time', { datetime: action.observed_at || '', text: hhmm(action.observed_at) }),
        el('strong', { text: heading })),
      el('p', { class: 'app', text: appName(action.app, action.app_name) }),
      action.title && el('p', { class: 'window', text: action.title }),
      action.sightings > 1 && el('p', { class: 'muted', text: `Seen ${action.sightings} times.` }));
    if (action.text || link) {
      const context = disclosure(`action:${action.event_id}`, 'View saved context',
        action.text && el('pre', { class: 'context-text', text: action.text }),
        link && el('p', {}, el('a', { href: link.href, target: '_blank', rel: 'noopener noreferrer', text: link.label })),
        lastSeen && el('p', { class: 'muted', text: lastSeen }));
      context.classList.add('activity-context');
      item.append(context);
    }
    return item;
  }

  function renderCorrections(task, entry) {
    const idPart = enc(task.id);
    const nameId = `name-${idPart}`;
    const nameInput = el('input', {
      id: nameId, type: 'text', maxlength: '240', required: true, autocomplete: 'off',
      value: state.nameDrafts[task.id] ?? (task.title || ''),
      oninput: (e) => { state.nameDrafts[task.id] = e.target.value; },
    });
    const rename = el('form', {
      class: 'rename', novalidate: true,
      onsubmit: (e) => {
        e.preventDefault();
        const title = nameInput.value.trim();
        if (!title) { notify('Enter a task name up to 240 characters.', 'error'); return; }
        correctTask(task, { op: 'rename', title });
      },
    },
    el('label', { for: nameId, text: 'Task name' }), nameInput,
    el('p', { class: 'note', text: "Taskmine reuses your name for the same kind of task, unless it holds a person's name, a number or an amount." }),
    el('button', { type: 'submit', disabled: state.busy, text: 'Save name' }));

    const relatedId = `related-${idPart}`;
    const others = state.tasks.filter((t) => t.id !== task.id);
    const chosenCase = () => state.related[task.id] || '';
    const select = el('select', {
      id: relatedId, onchange: (e) => { state.related[task.id] = e.target.value; render(); },
    }, el('option', { value: '', text: 'Choose a case' }),
    others.map((t) => el('option', { value: t.id, text: t.title || 'Untitled case', selected: t.id === chosenCase() })));
    select.value = others.some((t) => t.id === chosenCase()) ? chosenCase() : '';
    const needCase = state.busy || !select.value;
    const related = el('div', { class: 'related' },
      el('label', { for: relatedId, text: 'Related case' }), select,
      el('div', { class: 'buttons' },
        el('button', { type: 'button', disabled: needCase, text: 'Same process, separate cases',
          onclick: () => correctTask(task, { op: 'same_process', other_task_id: select.value }) }),
        el('button', { type: 'button', disabled: needCase, text: 'Merge into this case',
          onclick: () => correctTask(task, { op: 'merge', other_task_id: select.value }) })));

    const chosen = state.chosen[task.id] || (state.chosen[task.id] = new Set());
    const episodes = (entry && entry.episodes) || [];
    let fieldset = null;
    if (episodes.length) {
      const boxes = episodes.map((ep) => {
        const boxId = `episode-${idPart}-${enc(ep.id)}`;
        return el('div', { class: 'check' },
          el('input', { type: 'checkbox', id: boxId, checked: chosen.has(ep.id),
            onchange: (e) => { if (e.target.checked) chosen.add(ep.id); else chosen.delete(ep.id); render(); } }),
          el('label', { for: boxId, text: `${(ep.steps || []).map(stepText).slice(0, 2).join(', ') || 'Activity'} (${hhmm(ep.started_at)})` }));
      });
      const none = state.busy || chosen.size === 0;
      fieldset = el('fieldset', {}, el('legend', { text: 'Activity episodes' }), boxes,
        el('div', { class: 'buttons' },
          el('button', { type: 'button', disabled: none, text: 'Split selected into a new case',
            onclick: () => correctTask(task, { op: 'split', episode_ids: [...chosen] }) }),
          el('button', { type: 'button', disabled: none || !select.value, text: 'Move selected to related case',
            onclick: () => correctTask(task, { op: 'move', episode_ids: [...chosen], target_task_id: select.value }) })));
    }
    const done = task.lifecycle === 'completed';
    const lifecycle = el('button', {
      type: 'button', disabled: state.busy, text: done ? 'Reopen case' : 'Mark case complete',
      onclick: () => correctTask(task, { op: done ? 'reopen' : 'complete' }),
    });
    const intro = task.locked
      ? 'Your grouping correction is locked. Automatic grouping keeps it.'
      : 'Keep repeated cases separate. You can link them to the same process.';
    return disclosure(`correct:${task.id}`, 'Correct this case',
      el('p', { class: 'note', text: intro }), rename, related, fieldset, lifecycle);
  }

  function renderQuestions() {
    const box = $('#questions');
    if (!state.questions.length) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    box.hidden = false;
    box.replaceChildren(
      el('h2', { id: 'questions-heading', text: 'One task or two?' }),
      el('p', { class: 'note', text: 'Taskmine was not sure where these tasks start and end. Your answer fixes them now and teaches it for next time.' }),
      el('ul', {}, state.questions.slice(0, 3).map((q) => el('li', { class: 'question' },
        el('p', { text: `Is “${q.right_title || 'Unconfirmed activity'}” at ${hhmm(q.at)} part of “${q.left_title || 'Unconfirmed activity'}”?` }),
        el('div', { class: 'buttons' },
          el('button', { type: 'button', disabled: state.busy, text: 'Yes, one task', onclick: () => answerQuestion(q, 'one') }),
          el('button', { type: 'button', disabled: state.busy, text: 'No, two tasks', onclick: () => answerQuestion(q, 'two') }))))));
  }

  function renderView() {
    for (const name of ['activity', 'processes', 'drafts']) {
      $(`#view-${name}`).hidden = state.view !== name;
    }
    for (const link of document.querySelectorAll('nav [data-view]')) {
      if (link.dataset.view === state.view) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    // Views that are not shown hold no content.
    $('#view-processes').replaceChildren(...(state.view === 'processes' ? renderProcesses() : []));
    $('#view-drafts').replaceChildren(...(state.view === 'drafts' ? renderDrafts() : []));
  }

  function openTaskFromView(taskId) {
    location.hash = '#activity';
    state.manual = true;
    state.activeTaskId = taskId;
    if (!state.taskActions[taskId]) loadTaskActions(taskId);
    render();
  }

  function renderProcesses() {
    const head = el('h2', { id: 'processes-heading', text: 'Repeated work' });
    const intro = el('p', { class: 'note', text: 'Tasks with the same steps for different cases. Each case keeps its own actions.' });
    if (state.processes === null) return [head, el('p', { class: 'muted', text: 'Loading repeated work…' })];
    if (state.processes.error) return [head, el('p', { class: 'notice is-error', text: state.processes.error })];
    if (!state.processes.length) {
      return [head, intro, el('p', { class: 'muted', text: 'No repeated work yet. It appears when two or more cases follow the same steps.' })];
    }
    return [head, intro, ...state.processes.map((p) => el('article', { class: 'process' },
      el('h3', { text: p.name || 'Unnamed repeated task' }),
      el('p', { class: 'note', text: `Taskmine grouped ${plural(p.cases.length, 'case')} as the same process${p.basis === 'person' ? ' because you said so' : ''}.` }),
      (p.steps || []).length > 0 && el('p', { class: 'signature', text: p.steps.map(stepText).join(' → ') }),
      (p.templates || []).length > 0 && el('p', { class: 'muted', text: `Title pattern: ${p.templates.join(', ')}` }),
      el('ul', { class: 'cases' }, p.cases.map((c) => el('li', {},
        el('button', { type: 'button', class: 'link', text: c.title || 'Untitled case', onclick: () => openTaskFromView(c.task_id) }),
        ` ${dayLabel(c.started_at)}, ${hhmm(c.started_at)} to ${hhmm(c.ended_at)}`))))),
    ];
  }

  function renderDrafts() {
    const head = el('h2', { id: 'drafts-heading', text: 'Write-ups' });
    const intro = el('p', { class: 'note', text: 'Drafts you made from your tasks. They stay on this Mac.' });
    if (state.drafts === null) return [head, el('p', { class: 'muted', text: 'Loading write-ups…' })];
    if (state.drafts.error) return [head, el('p', { class: 'notice is-error', text: state.drafts.error })];
    if (!state.drafts.length) {
      return [head, intro, el('p', { class: 'muted', text: 'No write-ups yet. Open a task and choose Write up this task.' })];
    }
    return [head, intro, el('ul', { class: 'drafts' }, state.drafts.map((d) => el('li', { class: 'draft' },
      el('button', { type: 'button', class: 'link', text: d.workflow.outcome || 'Untitled write-up', onclick: () => openDraft(d) }),
      el('span', { class: 'muted', text: ` Made ${dayLabel(d.created_at)} at ${hhmm(d.created_at)}. ${d.status === 'pending_review' ? 'Not reviewed yet.' : ''}` }))))];
  }

  // ------------------------------------------------------------ views, polling, start

  function viewFromHash() {
    const hash = location.hash.replace('#', '');
    state.view = ['processes', 'drafts'].includes(hash) ? hash : 'activity';
    if (state.view === 'processes') loadProcesses();
    if (state.view === 'drafts') loadDrafts();
    render();
  }

  function shouldSkipTick() {
    const focus = document.activeElement;
    const typing = focus && focus.matches && focus.matches('input, textarea, select');
    return document.hidden || state.view !== 'activity' || state.listLoading || state.busy
      || $('#draft-dialog').open || typing || (focus && $('#task-list').contains(focus));
  }

  async function tick() {
    if (shouldSkipTick()) return;
    await loadTasks();
    await loadQuestions();
  }

  async function bootstrap() {
    $('#pause').addEventListener('click', pause);
    $('#refresh').addEventListener('click', () => { loadTasks(); loadQuestions(); });
    $('#more-tasks').addEventListener('click', () => loadTasks(true));
    $('#draft-form').addEventListener('submit', (e) => e.preventDefault());
    $('#draft-keep').addEventListener('click', closeDraft);
    $('#draft-save').addEventListener('click', saveDraft);
    $('#draft-delete').addEventListener('click', deleteDraft);
    $('#draft-dialog').addEventListener('close', clearDraft);
    window.addEventListener('hashchange', viewFromHash);
    viewFromHash();
    await loadTasks();
    await loadQuestions();
    setInterval(tick, POLL_MS);
  }

  window.taskmine = {
    state, bootstrap, captureStatus, activityUrl, sortTasks, sortActions, loadTasks,
    loadTaskActions, selectTask, correctTask, reviewTask, answerQuestion, pause, render,
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bootstrap);
  else bootstrap();
})();
