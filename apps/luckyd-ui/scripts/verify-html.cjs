// Headless UI-load verifier: proves the built dist/index.html renders under
// Electron's file:// protocol with the exact packaged webPreferences.
// Usage: electron scripts/verify-html.cjs   (exit 0 = UI rendered)
const { app, BrowserWindow } = require('electron');
const path = require('path');

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    show: false,
    backgroundColor: '#0b0f1a',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
      additionalArguments: [
        '--luckyd-url=http://127.0.0.1:8000',
        '--luckyd-token=verify-test',
      ],
    },
  });

  const errors = [];
  win.webContents.on('did-fail-load', (e, code, desc, url) => {
    errors.push(`${desc} (${code}) ${url}`);
  });
  win.webContents.on('console-message', (e) => {
    if (e.level >= 2) errors.push(`console: ${e.message}`);
  });

  try {
    await win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
    await new Promise((r) => setTimeout(r, 6000)); // let React render
    const res = await win.webContents.executeJavaScript(`JSON.stringify({
      title: document.title,
      rootChildren: document.getElementById('root')?.children.length ?? 0,
      hasNav: !!document.querySelector('nav'),
      luckyd: typeof window.__LUCKYD__,
      scriptSrc: document.querySelector('script')?.getAttribute('src') ?? ''
    })`);
    console.log('VERIFY', res);
    const parsed = JSON.parse(res);
    const ok = parsed.rootChildren > 0 && parsed.hasNav && parsed.luckyd === 'object';
    console.log('ERRORS', JSON.stringify(errors.slice(0, 5)));
    app.exit(ok && errors.length === 0 ? 0 : 2);
  } catch (e) {
    console.log('VERIFY FAILED', String(e));
    console.log('ERRORS', JSON.stringify(errors));
    app.exit(3);
  }
});