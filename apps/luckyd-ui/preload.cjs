// Preload: bridge the backend URL + token into the UI via window.__LUCKYD__
// without ever exposing Node to the renderer.
// electron-main reads .luckyd-code/hq_token from disk and passes it here
// via additionalArguments (--luckyd-token=…); the renderer only ever sees
// the value, never the filesystem.
const { contextBridge } = require('electron');

function argValue(name) {
  const prefix = `--${name}=`;
  for (const a of process.argv) {
    if (a.startsWith(prefix)) return a.slice(prefix.length);
  }
  return '';
}

contextBridge.exposeInMainWorld('__LUCKYD__', {
  baseUrl: argValue('luckyd-url') || 'http://127.0.0.1:8000',
  token: argValue('luckyd-token') || '',
});

