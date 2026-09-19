'use strict';

const $ = (id) => document.getElementById(id);

const el = {
  buildList: $('build-list'),
  newBuild: $('new-build'),
  openDir: $('open-dir'),

  name: $('f-name'),
  race: $('f-race'),
  vs: $('f-vs'),
  slot: $('f-slot'),
  notes: $('f-notes'),
  filename: $('f-filename'),

  rows: $('rows'),
  stepsEmpty: $('steps-empty'),
  orderWarning: $('order-warning'),
  addStep: $('add-step'),
  addAtClock: $('add-at-clock'),
  sortSteps: $('sort-steps'),
  clockChip: $('clock-chip'),

  preview: $('preview'),
  previewMeta: $('preview-meta'),
  problems: $('problems'),

  importPanel: $('import-panel'),
  importText: $('import-text'),
  doImport: $('do-import'),

  openExport: $('open-export'),
  exportSource: $('export-source'),
  branchBox: $('branch-box'),
  branchList: $('branch-list'),
  impNotes: $('imp-notes'),
  impSituational: $('imp-situational'),
  impFiller: $('imp-filler'),
  importReport: $('import-report'),

  replayPanel: $('replay-panel'),
  replaySetupBox: $('replay-setup-box'),
  replaySetupWhy: $('replay-setup-why'),
  replaySetup: $('replay-setup'),
  replayGetPython: $('replay-get-python'),
  replayRecheck: $('replay-recheck'),
  replaySetupState: $('replay-setup-state'),
  replayOpenBox: $('replay-open-box'),
  openReplay: $('open-replay'),
  replaySource: $('replay-source'),
  replayPick: $('replay-pick'),
  replayPlayers: $('replay-players'),
  replayTrim: $('replay-trim'),
  replayMinutes: $('replay-minutes'),
  replayChrono: $('replay-chrono'),
  replayMule: $('replay-mule'),
  replaySwap: $('replay-swap'),
  replayReport: $('replay-report'),

  status: $('status'),
  save: $('save-build'),
  remove: $('delete-build'),
};

const state = {
  /** Filename currently loaded from disk; null for an unsaved new build. */
  replacing: null,
  steps: [],
  dirty: false,
  clock: { connected: false, inGame: false, displayTime: 0, mock: false },
  /** Last build list from the main process, for labelling the slot dropdown. */
  builds: [],
  /** Branch currently chosen from an opened export, so options can re-convert. */
  branch: null,
  /** Player currently chosen from an opened replay, for the same reason. */
  replay: null,
  /** Whether the replay panel has looked for Python yet. */
  replayProbed: false,
};

function formatTime(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** Mirrors parseTime in the main process: `M:SS` or bare seconds. */
function parseTime(input) {
  const text = String(input == null ? '' : input).trim();
  if (!text) return null;
  const clock = /^(\d{1,3}):([0-5]?\d)$/.exec(text);
  if (clock) return Number(clock[1]) * 60 + Number(clock[2]);
  if (/^\d{1,5}$/.test(text)) return Number(text);
  return null;
}

function setStatus(text, kind = '') {
  el.status.textContent = text;
  el.status.className = `status ${kind}`;
}

function markDirty() {
  state.dirty = true;
  refreshPreview();
}

// ---------------------------------------------------------------- step rows

/**
 * Rows own their step object and write straight into it, so typing never
 * triggers a re-render (which would steal focus mid-word). The table is only
 * rebuilt when the list changes shape: add, delete, sort, import, load.
 *
 * `step.section` has no column: it is carried through untouched so that saving
 * a hand-written file keeps its [phase] markers. New steps inherit the section of
 * the step above, and the preview is where the markers are visible.
 */
function makeRow(step) {
  const row = document.createElement('div');
  row.className = 'trow';

  const time = document.createElement('input');
  time.className = 't-time';
  time.type = 'text';
  time.placeholder = '0:00';
  time.value = step.at == null ? '' : formatTime(step.at);
  time.addEventListener('input', () => {
    const parsed = parseTime(time.value);
    time.classList.toggle('bad', time.value.trim() !== '' && parsed === null);
    step.at = parsed == null ? null : parsed;
    markDirty();
  });
  time.addEventListener('blur', () => {
    if (step.at != null) time.value = formatTime(step.at);
    refreshOrderWarning();
  });

  const supply = document.createElement('input');
  supply.className = 't-supply';
  supply.type = 'text';
  supply.placeholder = '—';
  supply.value = step.supply == null ? '' : step.supply;
  supply.addEventListener('input', () => {
    const text = supply.value.trim();
    const n = Number(text);
    const bad = text !== '' && (!Number.isInteger(n) || n < 1 || n > 200);
    supply.classList.toggle('bad', bad);
    step.supply = text === '' || bad ? null : n;
    markDirty();
  });

  const action = document.createElement('input');
  action.className = 't-action';
  action.type = 'text';
  action.placeholder = 'e.g. Barracks';
  action.value = step.action || '';
  action.addEventListener('input', () => {
    step.action = action.value;
    markDirty();
  });

  /* Suggests the words that put a picture on the overlay. Returns true when the
     key belonged to the popup, which is how Enter can mean "take this
     suggestion" while the list is open and "next row" when it is not. */
  const suggestKey = window.suggest.attach(action, () => {
    step.action = action.value;
    markDirty();
  });

  // Enter at the end of a row adds the next one — the common authoring rhythm.
  // `isComposing` first: the Enter that commits a Korean syllable is the IME's,
  // and treating it as "next row" added a row every time a step name ended on a
  // character still being composed.
  action.addEventListener('keydown', (e) => {
    if (e.isComposing || suggestKey(e)) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      addStep({ at: (step.at || 0) + 15, section: step.section });
    }
  });

  const note = document.createElement('input');
  note.type = 'text';
  note.placeholder = 'Optional';
  note.value = step.note || '';
  note.addEventListener('input', () => {
    step.note = note.value.trim() || null;
    markDirty();
  });

  const del = document.createElement('button');
  del.type = 'button';
  del.className = 'row-del';
  del.title = 'Delete this step';
  del.textContent = '✕';
  del.addEventListener('click', () => {
    state.steps = state.steps.filter((s) => s !== step);
    renderRows();
    markDirty();
  });

  row.append(time, supply, action, note, del);
  return row;
}

function renderRows() {
  // Every field the popup could be attached to is about to be replaced.
  window.suggest.close();
  el.rows.replaceChildren(...state.steps.map(makeRow));
  el.stepsEmpty.classList.toggle('gone', state.steps.length > 0);
  refreshOrderWarning();
}

/**
 * A section that reappears after another one has intervened will be written out
 * twice, because the file is always sorted by time. Usually that means a step's
 * time was moved past a section boundary without updating its section.
 */
function splitSections(sortedSteps) {
  const runs = [];
  let previous;
  for (const step of sortedSteps) {
    const section = step.section || null;
    if (runs.length === 0 || section !== previous) runs.push(section);
    previous = section;
  }
  const counts = new Map();
  runs.forEach((s) => counts.set(s, (counts.get(s) || 0) + 1));
  return [...counts].filter(([section, n]) => section && n > 1).map(([section]) => section);
}

function refreshOrderWarning() {
  const timed = state.steps.filter((s) => s.at != null);
  const outOfOrder = timed.some((s, i) => i > 0 && s.at < timed[i - 1].at);
  const untimed = state.steps.filter((s) => s.at == null && String(s.action || '').trim()).length;
  const split = splitSections([...timed].sort((a, b) => a.at - b.at));

  const notes = [];
  if (outOfOrder) notes.push('The times are out of order. They are sorted by time automatically on save.');
  if (untimed) notes.push(`${untimed} steps have no time and will not be saved.`);
  if (split.length) {
    notes.push(
      `Phase ${split.map((s) => `[${s}]`).join(', ')} splits into two places once sorted by time. ` +
        'Check it in the preview below (phases can be fixed in a text editor).'
    );
  }
  el.orderWarning.textContent = notes.join('\n');
}

function addStep(seed = {}) {
  const last = state.steps[state.steps.length - 1];
  const step = {
    at: seed.at != null ? seed.at : last && last.at != null ? last.at + 15 : 0,
    supply: seed.supply != null ? seed.supply : null,
    action: seed.action || '',
    note: seed.note || null,
    section: seed.section !== undefined ? seed.section : (last && last.section) || null,
  };
  state.steps.push(step);
  renderRows();
  markDirty();

  // Focus the new row's action field so typing continues uninterrupted.
  const fresh = el.rows.lastElementChild;
  if (fresh) fresh.querySelector('.t-action').focus();
}

// ---------------------------------------------------------------- form <-> data

function currentBuild() {
  return {
    name: el.name.value.trim(),
    race: el.race.value || null,
    vs: el.vs.value || '*',
    slot: el.slot.value ? Number(el.slot.value) : null,
    notes: el.notes.value.trim() || null,
    steps: state.steps
      .filter((s) => s.at != null && String(s.action || '').trim())
      .map((s) => ({
        at: s.at,
        supply: s.supply,
        action: String(s.action).trim(),
        note: s.note,
        section: s.section,
      })),
  };
}

function fillForm(build) {
  el.name.value = build.name || '';
  el.race.value = build.race || '';
  el.vs.value = build.vs || '*';
  el.slot.value = build.slot ? String(build.slot) : '';
  el.notes.value = build.notes || '';
  state.steps = (build.steps || []).map((s) => ({
    at: s.at,
    supply: s.supply,
    action: s.action,
    note: s.note,
    section: s.section,
  }));
  renderRows();
}

/** Turns a build name into a usable filename, keeping Hangul intact. */
function suggestFilename() {
  const build = currentBuild();
  const base = (build.name || 'build')
    .toLowerCase()
    .replace(/[\\/:*?"<>|]/g, '')
    .replace(/\s+/g, '-')
    .replace(/^-+|-+$/g, '');
  const prefix = build.slot ? `${build.slot}-` : '';
  return `${prefix}${base || 'build'}.txt`;
}

let previewTimer = null;
let previewSeq = 0;
function refreshPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const build = currentBuild();
    // Previews are async, so a slower earlier request must not overwrite a
    // newer one — only the latest request is allowed to paint.
    const seq = (previewSeq += 1);
    const { text, problems, steps } = await window.editor.preview(build);
    if (seq !== previewSeq) return;

    el.preview.textContent = text;
    el.previewMeta.textContent = `${steps} steps · ${new TextEncoder().encode(text).length} bytes`;
    el.problems.textContent = problems.length
      ? problems.map((p) => `line ${p.line}: ${p.message}`).join('\n')
      : '';
    el.problems.classList.toggle('bad', problems.length > 0);
    el.filename.placeholder = suggestFilename();
    if (state.dirty) setStatus(state.replacing ? `${state.replacing} · unsaved` : 'New build · unsaved');
  }, 120);
}

// ---------------------------------------------------------------- library

/**
 * Labels each slot with whoever holds it, so taking a used number is a visible
 * choice rather than a silent collision. Picking a used one swaps: the other
 * build inherits this build's old slot.
 *
 * The build being edited labels its own selected slot too, so the dropdown
 * reads the way the list will after saving — but only when that slot is
 * otherwise free. Overwriting a holder's name would hide who is about to be
 * displaced, which is the one thing worth seeing before saving.
 */
function refreshSlotOptions() {
  const holders = new Map();
  (state.builds || []).forEach((b) => {
    if (b.declaredSlot && b.source !== state.replacing) holders.set(b.declaredSlot, b.name);
  });

  const mine = el.slot.value ? Number(el.slot.value) : null;
  if (mine && !holders.has(mine)) holders.set(mine, el.name.value.trim() || 'this build');

  [...el.slot.options].forEach((opt) => {
    if (!opt.value) return;
    const held = holders.get(Number(opt.value));
    opt.textContent = held ? `${opt.value} — ${held}` : opt.value;
  });
}

async function refreshList(activeSource) {
  const builds = await window.editor.list();
  state.builds = builds;
  refreshSlotOptions();
  el.buildList.replaceChildren();

  builds.forEach((b) => {
    const li = document.createElement('li');
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'build-item';
    if (b.source === (activeSource || state.replacing)) item.classList.add('active');

    const slot = document.createElement('span');
    slot.className = 'slot';
    slot.textContent = b.slot || '·';

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = b.name;

    const count = document.createElement('span');
    count.className = 'count';
    count.textContent = `${b.steps}`;

    item.append(slot, name, count);
    item.addEventListener('click', () => loadBuild(b.source));
    li.append(item);
    el.buildList.append(li);
  });
}

function confirmDiscard() {
  if (!state.dirty) return true;
  return window.confirm('There are unsaved changes. Discard them and move on?');
}

async function loadBuild(source) {
  if (!confirmDiscard()) return;
  try {
    const result = await window.editor.read(source);
    fillForm(result.build);
    el.filename.value = result.filename;
    state.replacing = result.filename;
    state.dirty = false;
    el.remove.disabled = false;

    const warnings = [];
    if (result.hasComments) warnings.push('This file has # comments. Saving from the editor drops them.');
    if (result.problems.length) {
      warnings.push(...result.problems.map((p) => `line ${p.line}: ${p.message}`));
    }
    el.orderWarning.textContent = warnings.join('\n');

    setStatus(`Loaded ${result.filename}`, 'ok');
    refreshPreview();
    await refreshList(result.filename);
  } catch (err) {
    setStatus(`Could not load: ${err.message}`, 'bad');
  }
}

function newBuild() {
  if (!confirmDiscard()) return;
  fillForm({ name: '', race: 'T', vs: '*', slot: null, notes: null, steps: [] });
  el.filename.value = '';
  state.replacing = null;
  state.dirty = false;
  el.remove.disabled = true;
  setStatus('New build');
  refreshPreview();
  refreshList(null);
  el.name.focus();
}

async function save() {
  const build = currentBuild();
  if (!build.name) {
    setStatus('Enter a build name.', 'bad');
    el.name.focus();
    return;
  }
  if (build.steps.length === 0) {
    setStatus('Not one step has both a time and an action.', 'bad');
    return;
  }

  const filename = el.filename.value.trim() || suggestFilename();
  const result = await window.editor.save({ filename, build, replacing: state.replacing });
  if (!result.ok) {
    setStatus(result.message, 'bad');
    return;
  }

  state.replacing = result.filename;
  state.dirty = false;
  el.filename.value = result.filename;
  el.remove.disabled = false;

  const swap = result.swapped
    ? ` · ${result.swapped.name} → ${result.swapped.slot ? `slot ${result.swapped.slot}` : 'no slot'}`
    : '';
  setStatus(`Saved ${result.filename} · ${result.steps} steps${swap}`, 'ok');
  await refreshList(result.filename);
}

async function remove() {
  if (!state.replacing) return;
  const result = await window.editor.remove(state.replacing);
  if (!result.ok) {
    setStatus(result.message, 'bad');
    return;
  }
  setStatus('Moved to the trash.', 'ok');
  state.dirty = false;
  newBuild();
  await refreshList(null);
}

// ---------------------------------------------------------------- export import

function importOptions() {
  return {
    notes: el.impNotes.checked,
    situational: el.impSituational.checked,
    filler: el.impFiller.checked ? 'drop' : 'collapse',
  };
}

function renderBranches(branches, selected) {
  el.branchList.replaceChildren();

  branches.forEach((b) => {
    const li = document.createElement('li');
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'branch-item';
    if (b.id === selected) item.classList.add('active');

    const mark = document.createElement('span');
    mark.className = 'mark';
    mark.textContent = b.id === selected ? '▶' : '';

    const games = document.createElement('span');
    games.className = 'num';
    games.textContent = b.games != null ? `${b.games} games` : '—';

    const wr = document.createElement('span');
    wr.className = 'wr';
    wr.textContent = b.winRate != null ? `${Math.round(b.winRate * 100)}% WR` : 'WR —';

    const steps = document.createElement('span');
    steps.className = 'num';
    steps.textContent = `${b.steps} steps`;

    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = b.label;

    item.append(mark, games, wr, steps, label);
    item.addEventListener('click', () => useBranch(b.id, branches));
    li.append(item);
    el.branchList.append(li);
  });
}

/** Converts the chosen branch and drops the result into the form. */
async function useBranch(branchId, branches) {
  const result = await window.editor.convertExport({ branchId, options: importOptions() });
  if (!result.ok) {
    setStatus(result.message, 'bad');
    return;
  }

  state.branch = { id: result.selected, branches };
  fillForm(result.build);
  el.filename.value = '';
  state.replacing = null;
  state.dirty = true;
  el.remove.disabled = true;

  renderBranches(branches, result.selected);
  el.importReport.textContent = result.notes.join('\n');
  el.importReport.classList.toggle('bad', result.missing.length > 0);

  setStatus(`Imported ${result.build.steps.length} steps · name the file and save`, 'ok');
  refreshPreview();
}

async function openExport() {
  if (!confirmDiscard()) return;
  const result = await window.editor.openExport();
  if (result.canceled) return;
  if (!result.ok) {
    setStatus(result.message, 'bad');
    return;
  }

  // Naming the tree matters: an export carries every race the site had open, and
  // only this one's branches are listed.
  const race = result.branches.length ? result.branches[0].race : null;
  const tree = race ? ` · ${race} tree, ${result.branches.length} branches` : '';
  el.exportSource.textContent =
    `${result.filename}${result.title ? ` — ${result.title}` : ''}${tree}`;
  el.branchBox.hidden = false;
  state.branch = { id: result.selected, branches: result.branches };
  renderBranches(result.branches, result.selected);
  el.importReport.textContent = '';

  // Convert the default branch straight away so there is something to look at.
  await useBranch(result.selected, result.branches);
}

async function importText() {
  const raw = el.importText.value;
  if (!raw.trim()) return;
  const { build, problems } = await window.editor.importText(raw);
  fillForm(build);
  state.dirty = true;
  el.importPanel.open = false;
  el.orderWarning.textContent = problems.length
    ? problems.map((p) => `line ${p.line}: ${p.message}`).join('\n')
    : '';
  setStatus(`Imported ${build.steps.length} steps`, 'ok');
  refreshPreview();
}

// ---------------------------------------------------------------- replay import

/**
 * Shows either the setup prompt or the open button, never both.
 *
 * Reading a replay needs Python, which the app does not ship. Rather than a
 * button that fails, the panel says what is missing and — when the missing part
 * is something the app can fetch — offers to do it.
 */
async function refreshReplayState(refresh) {
  const got = await window.editor.replayState(refresh);
  const ready = got.state === 'ready';

  el.replayOpenBox.hidden = !ready;
  el.replaySetupBox.hidden = ready;

  if (!ready) {
    el.replaySetupWhy.textContent = got.message || '';
    // Nothing to press when there is no Python to build on — offer the
    // download instead, so the message is something the user can act on.
    el.replaySetup.hidden = got.state !== 'needs-setup';
    el.replayGetPython.hidden = got.state !== 'no-python';
    el.replaySetupState.textContent = got.state === 'setting-up' ? 'Setting up…' : '';
  }
  return ready;
}

async function runReplaySetup() {
  el.replaySetup.disabled = true;
  el.replaySetupState.textContent = 'Setting up…';
  const result = await window.editor.replaySetup();
  el.replaySetup.disabled = false;

  if (!result.ok) {
    el.replaySetupWhy.textContent = result.message || 'Setup failed.';
    el.replaySetupState.textContent = '';
    return;
  }
  el.replaySetupState.textContent = '';
  await refreshReplayState(true);
}

function replayMinutes() {
  if (!el.replayTrim.checked) return null;
  const value = Number(el.replayMinutes.value);
  return Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * The optional step kinds. Off by default: they are extra detail, and a build
 * order is easier to follow without them until you want them.
 */
function replayExtras() {
  return [
    el.replayChrono.checked ? 'chrono' : null,
    el.replayMule.checked ? 'mule' : null,
    el.replaySwap.checked ? 'swap' : null,
  ].filter(Boolean);
}

function renderReplayPlayers(players, chosen) {
  el.replayPlayers.replaceChildren();

  players.forEach((p) => {
    const li = document.createElement('li');
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'branch-item';
    if (p.id === chosen) item.classList.add('active');

    const mark = document.createElement('span');
    mark.className = 'mark';
    mark.textContent = p.id === chosen ? '▶' : '';

    const race = document.createElement('span');
    race.className = 'num';
    race.textContent = p.race || '—';

    const who = document.createElement('span');
    who.className = 'wr';
    who.textContent = p.human ? 'Human' : 'AI';

    const result = document.createElement('span');
    result.className = 'num';
    result.textContent = p.won ? 'W' : 'L';

    const name = document.createElement('span');
    name.className = 'label';
    name.textContent = p.name;

    item.append(mark, race, who, result, name);
    item.addEventListener('click', () => useReplayPlayer(p.id, players));
    li.append(item);
    el.replayPlayers.append(li);
  });
}

/** Converts the chosen player's build and drops it into the form. */
async function useReplayPlayer(playerId, players) {
  el.replayReport.classList.remove('bad');
  el.replayReport.textContent = 'Reading…';

  const result = await window.editor.convertReplay({
    player: playerId,
    minutes: replayMinutes(),
    extras: replayExtras(),
  });
  if (!result.ok) {
    el.replayReport.textContent = result.message || 'Could not read it.';
    el.replayReport.classList.add('bad');
    return;
  }

  state.replay = { player: playerId, players };
  state.branch = null;
  fillForm(result.build);
  el.filename.value = '';
  state.replacing = null;
  state.dirty = true;
  el.remove.disabled = true;

  renderReplayPlayers(players, playerId);

  // Only what the user can act on. Which of the three ways each time was
  // worked out is a question for whoever is debugging the extraction, and it
  // lives in the command line's report; here it was just noise.
  const notes = [`${result.steps} steps`];
  if (result.missing.length) notes.push(`Names not in the dictionary: ${result.missing.join(', ')}`);
  if (result.noBuildTime.length) {
    notes.push(`Build time unknown, so the time it appeared was used: ${result.noBuildTime.join(', ')}`);
  }
  el.replayReport.textContent = notes.join(' · ');
  el.replayReport.classList.toggle('bad',
    result.missing.length > 0 || result.noBuildTime.length > 0);

  setStatus(`Imported ${result.build.steps.length} steps · name the file and save`, 'ok');
  refreshPreview();
}

async function openReplay() {
  if (!confirmDiscard()) return;

  el.openReplay.disabled = true;
  el.replaySource.textContent = 'Reading…';
  const result = await window.editor.openReplay();
  el.openReplay.disabled = false;

  if (result.canceled) {
    el.replaySource.textContent = '';
    return;
  }
  if (!result.ok) {
    el.replaySource.textContent = '';
    // Python may have been removed since the panel last looked.
    if (result.needsSetup) {
      await refreshReplayState(true);
      return;
    }
    setStatus(result.message, 'bad');
    return;
  }

  const r = result.replay;
  const length = `${Math.floor(r.seconds / 60)}:${String(r.seconds % 60).padStart(2, '0')}`;
  el.replaySource.textContent = `${r.name} · ${r.map} · SC2 ${r.version} · ${length}`;
  el.replayPick.hidden = false;
  el.replayReport.textContent = '';

  // A replay from a patch newer than the installed decoder is read with the
  // newest one we have. It nearly always works, but the user should know the
  // numbers rest on that rather than find out from a build that reads oddly.
  if (r.fellBackTo) {
    el.replaySource.textContent
      += ` · no decoder for this patch, so it was read as ${r.fellBackTo}`;
  }

  // The human by default, which is what someone reviewing their own game wants.
  const mine = r.players.find((p) => p.human) || r.players[0];
  renderReplayPlayers(r.players, mine ? mine.id : null);
  if (mine) await useReplayPlayer(mine.id, r.players);
}

// ---------------------------------------------------------------- wiring

[el.name, el.notes].forEach((input) => input.addEventListener('input', markDirty));
[el.race, el.vs, el.slot].forEach((input) => input.addEventListener('change', markDirty));

// The dropdown shows this build's own name against its slot, so both inputs
// have to re-label it.
el.name.addEventListener('input', refreshSlotOptions);
el.slot.addEventListener('change', refreshSlotOptions);
el.filename.addEventListener('input', markDirty);

el.addStep.addEventListener('click', () => addStep());
el.sortSteps.addEventListener('click', () => {
  state.steps.sort((a, b) => (a.at == null ? 1 : b.at == null ? -1 : a.at - b.at));
  renderRows();
  markDirty();
});
el.addAtClock.addEventListener('click', () => addStep({ at: Math.round(state.clock.displayTime) }));

el.newBuild.addEventListener('click', newBuild);
el.openDir.addEventListener('click', () => window.editor.openDir());
el.save.addEventListener('click', save);
el.remove.addEventListener('click', remove);
el.doImport.addEventListener('click', importText);
el.openExport.addEventListener('click', openExport);

el.openReplay.addEventListener('click', openReplay);
el.replaySetup.addEventListener('click', runReplaySetup);
el.replayGetPython.addEventListener('click', () => window.editor.openPythonSite());
el.replayRecheck.addEventListener('click', async () => {
  el.replayRecheck.disabled = true;
  el.replaySetupState.textContent = 'Checking…';
  await refreshReplayState(true);
  el.replaySetupState.textContent = '';
  el.replayRecheck.disabled = false;
});
window.editor.onReplayProgress((line) => {
  el.replaySetupState.textContent = line;
});

// Any option re-converts the player already chosen, the way the JSON options do.
[el.replayTrim, el.replayMinutes, el.replayChrono, el.replayMule,
 el.replaySwap].forEach((input) =>
  input.addEventListener('change', () => {
    if (state.replay) useReplayPlayer(state.replay.player, state.replay.players);
  }));

// Probed when the panel is first opened rather than at start-up: it spawns a
// process, and most sessions never touch this panel.
el.replayPanel.addEventListener('toggle', () => {
  if (el.replayPanel.open && !state.replayProbed) {
    state.replayProbed = true;
    refreshReplayState(true);
  }
});

// Changing an option re-converts the branch already chosen.
[el.impNotes, el.impSituational, el.impFiller].forEach((input) =>
  input.addEventListener('change', () => {
    if (state.branch) useBranch(state.branch.id, state.branch.branches);
  })
);

document.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.key === 's') {
    e.preventDefault();
    save();
  }
});

window.editor.onClock((clock) => {
  state.clock = clock;
  const live = clock.inGame;
  el.clockChip.textContent = live
    ? `${formatTime(clock.displayTime)}${clock.mock ? ' (mock)' : ''}`
    : clock.connected
      ? 'Waiting for a game'
      : 'Waiting for SC2';
  el.clockChip.classList.toggle('live', live);
  el.addAtClock.disabled = !live;
});

newBuild();
refreshList(null);

/* Fetched once. The manifest does not change while the app runs, and a failure
   here only costs the suggestions — the field still takes any text. */
window.editor
  .terms()
  .then((list) => window.suggest.setTerms(list))
  .catch(() => {});
