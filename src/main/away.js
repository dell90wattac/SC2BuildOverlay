'use strict';

const { BrowserWindow, screen } = require('electron');
const path = require('path');
const { safeSend } = require('./send');

/**
 * The away clock: press a key when you leave your base, and a timer counts up
 * from 0:00 until you press it again.
 *
 * A tool of its own rather than part of the build overlay. It has its own
 * window, needs no build loaded and no Start pressed, and nothing here knows
 * the overlay exists — so either can be used without the other.
 *
 * The main process only records when the clock started; the window counts from
 * that itself, so nothing is pushed per tick.
 */

const WIDTH = 240;
const HEIGHT = 120;

function defaultBounds() {
  const { workArea } = screen.getPrimaryDisplay();
  return {
    // Top centre, clear of SC2's menu buttons (top left) and resources (top
    // right), and low enough not to sit on the very edge of the screen.
    x: Math.round(workArea.x + (workArea.width - WIDTH) / 2),
    y: Math.round(workArea.y + workArea.height * 0.12),
    width: WIDTH,
    height: HEIGHT,
  };
}

function setupAwayClock() {
  let win = null;
  let startedAt = null;

  function create() {
    win = new BrowserWindow({
      ...defaultBounds(),
      show: false,
      frame: false,
      transparent: true,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      hasShadow: false,
      focusable: false, // never steal focus from the game
      webPreferences: {
        preload: path.join(__dirname, 'away-preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        // Sits over a fullscreen game, which can count as occluding it; the
        // count would stall without this.
        backgroundThrottling: false,
      },
    });
    win.setIgnoreMouseEvents(true);
    win.loadFile(path.join(__dirname, '..', 'renderer', 'away.html'));
    // A reload mid-count picks the running clock back up.
    win.webContents.on('did-finish-load', () => {
      if (startedAt !== null) safeSend(win, 'away-start', startedAt);
    });
  }

  function start() {
    startedAt = Date.now();
    safeSend(win, 'away-start', startedAt);
    win.showInactive();
    // The level that gets it above a game in windowed fullscreen.
    win.setAlwaysOnTop(true, 'screen-saver');
  }

  function stop() {
    startedAt = null;
    safeSend(win, 'away-stop');
    win.hide();
  }

  // Created up front, hidden, so the first press shows a clock at once rather
  // than after a page load.
  create();

  return {
    toggle: () => (startedAt === null ? start() : stop()),
    isRunning: () => startedAt !== null,
  };
}

module.exports = { setupAwayClock };
