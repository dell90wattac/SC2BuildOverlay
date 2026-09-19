'use strict';

const $ = (id) => document.getElementById(id);

const el = {
  runDot: $('run-dot'),
  runLabel: $('run-label'),
  runDetail: $('run-detail'),
  runToggle: $('run-toggle'),

  mockBanner: $('mock-banner'),
  sClient: $('s-client'),
  sGame: $('s-game'),
  sClock: $('s-clock'),
  sNext: $('s-next'),

  buildList: $('build-list'),
  buildEmpty: $('build-empty'),
  buildProblems: $('build-problems'),
  autoPick: $('auto-pick'),
  reload: $('reload'),
  openEditor: $('open-editor'),

  toggleVisible: $('toggle-visible'),
  toggleLocked: $('toggle-locked'),
  modeAuto: $('mode-auto'),
  modeManual: $('mode-manual'),
  iconsNone: $('icons-none'),
  iconsSmall: $('icons-small'),
  iconsLarge: $('icons-large'),
  iconsHint: $('icons-hint'),
  themeSwatches: $('theme-swatches'),
  gauge: $('gauge'),
  showHeader: $('show-header'),
  showFooter: $('show-footer'),
  iconsFetchRow: $('icons-fetch-row'),
  iconsFetchState: $('icons-fetch-state'),
  iconsFetch: $('icons-fetch'),

  lead: $('lead'),
  leadValue: $('lead-value'),
  opacity: $('opacity'),
  opacityValue: $('opacity-value'),
  scale: $('scale'),
  scaleValue: $('scale-value'),
  stepScale: $('step-scale'),
  stepScaleValue: $('step-scale-value'),
  widthScale: $('width-scale'),
  widthScaleValue: $('width-scale-value'),
  lookbehind: $('lookbehind'),
  lookahead: $('lookahead'),

  corners: {
    'top-left': $('pos-tl'),
    'top-right': $('pos-tr'),
    'bottom-left': $('pos-bl'),
    'bottom-right': $('pos-br'),
  },

  soundEnabled: $('sound-enabled'),
  soundVolume: $('sound-volume'),
  soundVolumeValue: $('sound-volume-value'),
  testSound: $('test-sound'),
  soundFile: $('sound-file'),
  soundProblem: $('sound-problem'),
  soundPick: $('sound-pick'),
  soundDefault: $('sound-default'),

  myName: $('my-name'),
  playersHint: $('players-hint'),
  autoStart: $('auto-start'),
  autoStartGame: $('auto-start-game'),

  openDir: $('open-dir'),
  quit: $('quit'),
};

const RACE_LABEL = { T: 'Terran', Z: 'Zerg', P: 'Protoss', R: 'Random', '*': 'Any race' };

function formatTime(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** True while the user is interacting with a control, so we don't fight them. */
function busy(input) {
  return document.activeElement === input;
}

function renderRun(view) {
  const { running, game } = view;

  // Mock mode hardcodes connected/inGame and two fake players, so it must never
  // be able to read as a real connection.
  const mock = Boolean(game.mock);

  el.runDot.classList.toggle('on', running);
  el.runDot.classList.toggle('waiting', !running && Boolean(view.watching));
  el.runLabel.textContent = running ? 'Running' : view.watching ? 'Waiting' : 'Stopped';
  el.runDetail.textContent = !running
    ? view.watching
      ? 'Starts by itself once a game begins'
      : 'Press Start to read SC2'
    : mock
      ? 'Fake clock · not reading SC2'
      : 'Reading SC2 every 250ms';
  el.runToggle.textContent = running ? '■ Stop' : '▶ Start';
  el.runToggle.classList.toggle('running', running);
  el.mockBanner.hidden = !mock;

  el.sClient.textContent = !running
    ? '—'
    : mock
      ? 'Not reading · fake'
      : game.connected
        ? 'Connected · localhost:6119'
        : 'Waiting · SC2 not running';
  el.sClient.classList.toggle('live', running && !mock && game.connected);
  el.sClient.classList.toggle('mock', running && mock);

  if (!running) {
    el.sGame.textContent = '—';
    el.sClock.textContent = '—';
  } else if (game.inGame) {
    const me = game.me ? `${game.me.name} (${RACE_LABEL[game.me.race] || '?'})` : 'unknown';
    const opp = game.opponent ? `${game.opponent.name} (${RACE_LABEL[game.opponent.race] || '?'})` : 'unknown';
    el.sGame.textContent = mock ? `Fake: ${me} vs ${opp}` : `${me} vs ${opp}`;
    el.sClock.textContent = `${formatTime(game.displayTime)}${mock ? '  · fake' : ''}${game.isReplay ? '  replay' : ''}`;
  } else {
    el.sGame.textContent = 'Waiting for a game';
    el.sClock.textContent = '—';
  }
  el.sGame.classList.toggle('live', running && !mock && Boolean(game.inGame));
  el.sGame.classList.toggle('mock', running && mock);
  el.sClock.classList.toggle('mock', running && mock);

  const step = running ? view.nextStep : null;
  const noMatch = running ? view.noMatch : null;
  el.sNext.textContent = !running
    ? '—'
    : noMatch && noMatch.unknownPlayer
      ? "Can't tell which player is you — enter your name below"
      : noMatch
      ? `No ${noMatch.race || '?'}v${noMatch.vs || '?'} build`
      : step
        ? `${formatTime(step.at)}  ${step.supply ? `@${step.supply}  ` : ''}${step.action}`
        : view.totalSteps
          ? 'End of build'
          : '—';
  el.sNext.classList.toggle('warn-text', Boolean(noMatch));
}

function renderBuilds(view) {
  el.buildList.replaceChildren();
  el.buildEmpty.classList.toggle('gone', view.builds.length > 0);

  const favs = view.favorites || [];
  const pinned = view.pinnedSource || null;

  view.builds.forEach((b) => {
    const li = document.createElement('li');
    li.className = 'build-row';

    // A separate control, not part of the row button: starring a build is not
    // the same as choosing it.
    const star = document.createElement('button');
    star.type = 'button';
    star.className = 'star';
    const fav = favs.includes(b.source);
    star.classList.toggle('on', fav);
    star.textContent = fav ? '★' : '☆';
    star.title = fav ? 'Remove from favourites' : 'Make this the default for the matchup';
    star.addEventListener('click', () => window.control.toggleFavorite(b.source));

    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'build-item';
    if (b.source === view.activeSource) item.classList.add('active');
    if (b.source === pinned) item.classList.add('pinned');

    const slot = document.createElement('span');
    slot.className = 'slot';
    slot.textContent = b.slot || '·';

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = b.name;

    const meta = document.createElement('span');
    meta.className = 'meta';
    const matchup = `${b.race || '?'}v${b.vs === '*' ? 'X' : b.vs || 'X'}`;
    meta.textContent =
      b.source === pinned
        ? 'Pinned — click to release'
        : `${matchup} · ${b.steps} steps${b.problems ? ` · ⚠${b.problems}` : ''}`;

    item.append(slot, name, meta);
    // Clicking the pinned build again releases it back to auto-pick.
    item.addEventListener('click', () =>
      b.source === pinned ? window.control.clearPin() : window.control.pickBuild(b.source)
    );

    li.append(star, item);
    el.buildList.append(li);
  });

  const broken = view.builds.filter((b) => b.problems);
  el.buildProblems.textContent = broken.length
    ? `Files with lines that could not be read: ${broken.map((b) => b.name).join(', ')} — check them in the editor.`
    : '';
}

function renderOverlayControls(view) {
  const { ui } = view;

  // Showing, hiding, locking and moving the overlay all work while stopped —
  // that is the state you set it up in.
  el.toggleVisible.textContent = ui.visible ? 'Hide' : 'Show';
  el.toggleLocked.disabled = !ui.visible;
  el.toggleLocked.textContent = ui.locked ? 'Unlock' : 'Lock';

  el.modeAuto.classList.toggle('on', ui.mode === 'auto');
  el.modeManual.classList.toggle('on', ui.mode === 'manual');

  // Moving a hidden window would be invisible and confusing, so the presets
  // only work while there is an overlay on screen to move.
  const movable = ui.visible;
  Object.values(el.corners).forEach((b) => {
    b.disabled = !movable;
  });
}

/**
 * SC2 does not say which player is the local one, so the name field is how we
 * tell. Rather than have the user guess what the API calls them, show the names
 * it actually reported and mark the one that matches.
 */
function renderPlayersHint(view) {
  const { running, game, settings } = view;
  const players = (game && game.players) || [];
  const el2 = el.playersHint;

  el2.classList.toggle('live', Boolean(running && game.inGame && players.length));

  if (!running || !game.inGame || players.length === 0) {
    el2.textContent = 'Once you are in a game, the names SC2 reports show up here.';
    return;
  }

  const typed = String(settings.myName || '').trim().toLowerCase();
  el2.replaceChildren(document.createTextNode('Players in this game: '));

  players.forEach((p, i) => {
    if (i > 0) el2.append(document.createTextNode(' · '));
    const who = document.createElement('span');
    who.className = 'who';
    const name = p.name || '(no name)';
    if (typed && name.toLowerCase() === typed) who.classList.add('matched');
    who.textContent = name;
    el2.append(who);
  });

  const matched = typed && players.some((p) => (p.name || '').toLowerCase() === typed);
  const humans = players.filter((p) => p.type === 'user').length;
  el2.append(
    document.createTextNode(
      matched ? ' — recognised' : humans <= 1 ? ' — worked out automatically' : ' — type your own name above'
    )
  );
}

function renderSettings(view) {
  const { settings } = view;

  if (!busy(el.lead)) el.lead.value = settings.leadSeconds;
  el.leadValue.textContent = `${settings.leadSeconds}s`;

  const hue = settings.themeHue ?? 207;
  const sat = settings.themeSat ?? 1;
  for (const swatch of el.themeSwatches.children) {
    const mine = Number(swatch.dataset.hue) === hue && Number(swatch.dataset.sat) === sat;
    swatch.classList.toggle('on', mine);
  }

  el.gauge.checked = settings.gauge !== false;

  const iconMode = settings.iconMode || 'none';
  el.iconsNone.classList.toggle('on', iconMode === 'none');
  el.iconsSmall.classList.toggle('on', iconMode === 'small');
  el.iconsLarge.classList.toggle('on', iconMode === 'large');
  // Nothing to explain while it works — None/Small/Large says it. But an icon
  // folder that failed to load would leave the option looking simply broken.
  const iconsMissing = settings.iconsAvailable === false;
  const iconsShort = Number(settings.iconsShort) || 0;
  const anyToFetch = iconsMissing || iconsShort > 0;
  const fetch = settings.iconFetch;
  const fetching = Boolean(fetch && fetch.running);

  el.iconsHint.hidden = !anyToFetch || fetching;
  el.iconsHint.textContent = iconsMissing
    ? 'No picture files yet. Download them below and they appear beside each step.'
    : iconsShort
      ? `${iconsShort} pictures added in this release are still missing. Downloading fills them in.`
      : '';
  // Only an empty set is a warning. A couple of new pictures is an errand.
  el.iconsHint.classList.toggle('warn', iconsMissing && !fetching);

  // Only offered when there is something to do: nothing to fetch once the set
  // is complete, and no reason to mention the network otherwise. An update that
  // adds a term adds a picture nobody has yet, which is also something to do.
  el.iconsFetchRow.hidden = !anyToFetch && !fetching && !(fetch && fetch.message);
  el.iconsFetch.disabled = fetching;
  el.iconsFetch.textContent = fetching ? 'Downloading…' : 'Download pictures';
  el.iconsFetchState.textContent = fetching
    ? `${fetch.done} / ${fetch.total || '…'}`
    : (fetch && fetch.message) || '';
  el.iconsFetchState.classList.toggle('warn', Boolean(!fetching && fetch && fetch.message));

  // Lit when the part is shown, the way the icon buttons read.
  el.showHeader.classList.toggle('on', settings.showHeader !== false);
  el.showFooter.classList.toggle('on', settings.showFooter !== false);

  if (!busy(el.opacity)) el.opacity.value = settings.opacity;
  el.opacityValue.textContent = `${Math.round(settings.opacity * 100)}%`;

  if (!busy(el.scale)) el.scale.value = settings.scale;
  el.scaleValue.textContent = `${Math.round(settings.scale * 100)}%`;

  if (!busy(el.stepScale)) el.stepScale.value = settings.stepScale;
  el.stepScaleValue.textContent = `${Math.round(settings.stepScale * 100)}%`;

  // The multiplier on its own says nothing about how much text now fits, and
  // Size and Icons feed into the same number — so report the pixels it lands on.
  if (!busy(el.widthScale)) el.widthScale.value = settings.widthScale;
  el.widthScaleValue.textContent =
    `${Math.round(settings.widthScale * 100)}% · ${settings.overlayWidth}px`;

  if (!busy(el.lookbehind)) el.lookbehind.value = settings.lookbehind;
  if (!busy(el.lookahead)) el.lookahead.value = settings.lookahead;
  if (!busy(el.myName)) el.myName.value = settings.myName || '';

  if (!busy(el.soundVolume)) el.soundVolume.value = settings.soundVolume;
  el.soundVolumeValue.textContent = `${Math.round(settings.soundVolume * 100)}%`;
  el.soundVolume.disabled = !settings.soundEnabled;

  // Long filenames would push the field's layout apart, and the tail is the
  // part that identifies the file anyway.
  const custom = settings.soundFile;
  el.soundFile.textContent = custom
    ? (custom.length > 28 ? `…${custom.slice(-27)}` : custom)
    : 'Built-in default';
  el.soundFile.title = custom || '';
  el.soundDefault.disabled = !custom;

  el.soundProblem.textContent = settings.soundProblem || '';
  el.soundProblem.hidden = !settings.soundProblem;

  el.soundEnabled.checked = Boolean(settings.soundEnabled);
  el.autoPick.checked = Boolean(settings.autoPick);
  el.autoStart.checked = Boolean(settings.autoStart);
  el.autoStartGame.checked = Boolean(settings.autoStartOnGame);
}

/** Last state pushed from the main process; buttons read this, not the DOM. */
let current = null;

window.control.onView((view) => {
  current = view;
  renderRun(view);
  renderBuilds(view);
  renderOverlayControls(view);
  renderSettings(view);
  renderPlayersHint(view);
});

// ---------------------------------------------------------------- wiring

el.runToggle.addEventListener('click', () => {
  if (current && current.running) window.control.stop();
  else window.control.start();
});

el.toggleVisible.addEventListener('click', () => {
  if (current) window.control.setVisible(!current.ui.visible);
});
el.toggleLocked.addEventListener('click', () => {
  if (current) window.control.setLocked(!current.ui.locked);
});
el.modeAuto.addEventListener('click', () => window.control.setMode('auto'));
el.modeManual.addEventListener('click', () => window.control.setMode('manual'));

el.showHeader.addEventListener('click', () =>
  patchSettings({ showHeader: !(current && current.settings.showHeader !== false) })
);
el.showFooter.addEventListener('click', () =>
  patchSettings({ showFooter: !(current && current.settings.showFooter !== false) })
);

/**
 * The frame hues on offer.
 *
 * Warm angles are missing on purpose. 4° (due now), 40° (the row to do) and
 * 134° (time to spare) already mean something in the overlay, and a frame
 * sitting on one of them swallows the band that means it — a green frame hides
 * `time to spare` with no error to see. Every hue here clears all three, and
 * they clear each other.
 *
 * Grey is the same idea with the colour taken out rather than moved.
 */
const THEMES = [
  { name: 'Teal', hue: 175, sat: 1 },
  { name: 'Sky', hue: 193, sat: 1 },
  { name: 'Blue', hue: 207, sat: 1 },
  { name: 'Indigo', hue: 230, sat: 1 },
  { name: 'Violet', hue: 255, sat: 1 },
  { name: 'Purple', hue: 282, sat: 1 },
  { name: 'Magenta', hue: 310, sat: 1 },
  // The warm half is only reachable with the colour turned down. At full
  // saturation a tan frame is the gold row's own hue and swallows it; muted,
  // the contrast moves from hue to saturation and the gold still lands first.
  { name: 'Sand', hue: 35, sat: 0.35 },
  { name: 'Olive', hue: 85, sat: 0.35 },
  { name: 'Grey', hue: 210, sat: 0.12 },
];

for (const theme of THEMES) {
  const swatch = document.createElement('button');
  swatch.type = 'button';
  swatch.className = 'swatch';
  swatch.dataset.hue = theme.hue;
  swatch.dataset.sat = theme.sat;

  // Mixed from the same numbers the stylesheet uses, so the chip is the frame
  // it will produce rather than an approximation someone has to keep in step.
  const chip = document.createElement('i');
  chip.style.setProperty('--sw-edge', `hsl(${theme.hue} ${72 * theme.sat}% 64%)`);
  chip.style.setProperty('--sw-fill', `hsl(${theme.hue + 5} ${64 * theme.sat}% 22%)`);

  const label = document.createElement('span');
  label.textContent = theme.name;

  swatch.append(chip, label);
  swatch.addEventListener('click', () =>
    patchSettings({ themeHue: theme.hue, themeSat: theme.sat })
  );
  el.themeSwatches.append(swatch);
}

el.gauge.addEventListener('change', () => patchSettings({ gauge: el.gauge.checked }));

el.iconsNone.addEventListener('click', () => patchSettings({ iconMode: 'none' }));
el.iconsSmall.addEventListener('click', () => patchSettings({ iconMode: 'small' }));
el.iconsLarge.addEventListener('click', () => patchSettings({ iconMode: 'large' }));

const patchSettings = (patch) => window.control.updateSettings(patch);
el.lead.addEventListener('input', () => patchSettings({ leadSeconds: Number(el.lead.value) }));
el.opacity.addEventListener('input', () => patchSettings({ opacity: Number(el.opacity.value) }));
el.scale.addEventListener('input', () => patchSettings({ scale: Number(el.scale.value) }));
el.stepScale.addEventListener('input', () => patchSettings({ stepScale: Number(el.stepScale.value) }));
el.widthScale.addEventListener('input', () => patchSettings({ widthScale: Number(el.widthScale.value) }));
el.lookbehind.addEventListener('change', () => patchSettings({ lookbehind: Number(el.lookbehind.value) }));
el.lookahead.addEventListener('change', () => patchSettings({ lookahead: Number(el.lookahead.value) }));
el.myName.addEventListener('change', () => patchSettings({ myName: el.myName.value.trim() }));
// Re-mark the matching player as you type, not only on commit.
el.myName.addEventListener('input', () => {
  if (current) renderPlayersHint({ ...current, settings: { ...current.settings, myName: el.myName.value } });
});
el.autoPick.addEventListener('change', () => patchSettings({ autoPick: el.autoPick.checked }));
el.autoStart.addEventListener('change', () => patchSettings({ autoStart: el.autoStart.checked }));
el.autoStartGame.addEventListener('change', () =>
  patchSettings({ autoStartOnGame: el.autoStartGame.checked })
);

Object.entries(el.corners).forEach(([where, button]) => {
  button.addEventListener('click', () => window.control.moveOverlay(where));
});

el.soundEnabled.addEventListener('change', () => patchSettings({ soundEnabled: el.soundEnabled.checked }));
el.soundVolume.addEventListener('input', () => patchSettings({ soundVolume: Number(el.soundVolume.value) }));
// Play on release, not on every drag step, or dragging the slider machine-guns.
el.soundVolume.addEventListener('change', () => window.control.testSound());
el.testSound.addEventListener('click', () => window.control.testSound());
el.soundPick.addEventListener('click', () => window.control.pickSound());
el.soundDefault.addEventListener('click', () => window.control.resetSound());
el.iconsFetch.addEventListener('click', () => window.control.fetchIcons());

el.reload.addEventListener('click', () => window.control.reload());
el.openEditor.addEventListener('click', () => window.control.openEditor());
el.openDir.addEventListener('click', () => window.control.openDir());
el.quit.addEventListener('click', () => window.control.quit());
