'use strict';

/**
 * Counts up from when the main process says the clock started.
 *
 * Colour bands, in seconds away: green to 15, yellow to 30, orange to 40, red
 * (pulsing) from 40 on.
 */
const BANDS = [
  { until: 15, cls: 'green' },
  { until: 30, cls: 'yellow' },
  { until: 40, cls: 'orange' },
  { until: Infinity, cls: 'red' },
];

const clock = document.getElementById('clock');
const time = document.getElementById('time');

let startedAt = null;
let timer = null;

function format(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function render() {
  if (startedAt === null) return;
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  time.textContent = format(seconds);
  const band = BANDS.find((b) => seconds < b.until);
  clock.className = `clock ${band.cls}`;
}

window.away.onStart((at) => {
  startedAt = at;
  render();
  clearInterval(timer);
  // Well under a second, so the display never lags the true count by much.
  timer = setInterval(render, 200);
});

window.away.onStop(() => {
  startedAt = null;
  clearInterval(timer);
  timer = null;
  time.textContent = '0:00';
  clock.className = 'clock green';
});
