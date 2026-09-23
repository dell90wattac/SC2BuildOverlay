'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// Display only: the clock is started and stopped from the main process.
contextBridge.exposeInMainWorld('away', {
  onStart: (cb) => ipcRenderer.on('away-start', (_e, startedAt) => cb(startedAt)),
  onStop: (cb) => ipcRenderer.on('away-stop', () => cb()),
});
