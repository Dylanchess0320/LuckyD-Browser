// ==UserScript==
// @name        Video Speed Controller
// @match       *://*/*
// @run-at      document-end
// ==/UserScript==
// LuckyD built-in: press ] to speed up, [ to slow down any HTML5 video
// (0.25x steps, 0.25x–4x). Shows a small on-screen badge.
(() => {
  const osd = document.createElement('div');
  osd.style.cssText =
    'position:fixed;top:12px;right:12px;background:rgba(0,0,0,.75);' +
    'color:#7CFC90;padding:4px 10px;border-radius:6px;font:14px monospace;' +
    'z-index:2147483647;display:none;pointer-events:none';
  document.body.appendChild(osd);
  let timer;
  const show = (t) => {
    osd.textContent = t;
    osd.style.display = 'block';
    clearTimeout(timer);
    timer = setTimeout(() => { osd.style.display = 'none'; }, 1200);
  };
  addEventListener('keydown', (e) => {
    if (e.target.matches('input, textarea, [contenteditable="true"]')) return;
    if (e.key !== ']' && e.key !== '[') return;
    const vids = [...document.querySelectorAll('video')];
    if (!vids.length) return;
    for (const v of vids) {
      const delta = e.key === ']' ? 0.25 : -0.25;
      v.playbackRate = Math.min(4, Math.max(
        0.25, Math.round((v.playbackRate + delta) * 100) / 100));
    }
    show((e.key === ']' ? '▶ ' : '◀ ') + vids[0].playbackRate + 'x');
  });
})();
