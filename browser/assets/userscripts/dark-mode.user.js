// ==UserScript==
// @name        Dark Mode Everywhere
// @match       *://*/*
// @run-at      document-start
// ==/UserScript==
// LuckyD built-in: invert-based dark theme on every site.
// Toggle via Tools -> Extensions…
(() => {
  const css = `
    html { background: #121212 !important; }
    html, body { filter: invert(0.93) hue-rotate(180deg) !important; }
    img, picture, video, canvas, svg image,
    [style*="background-image"] {
      filter: invert(1) hue-rotate(180deg) !important;
    }
  `;
  const add = () => {
    if (document.getElementById('ld-dark-mode')) return;
    const style = document.createElement('style');
    style.id = 'ld-dark-mode';
    style.textContent = css;
    (document.head || document.documentElement).appendChild(style);
  };
  if (document.head) {
    add();
  } else {
    const obs = new MutationObserver(() => {
      if (document.head) { add(); obs.disconnect(); }
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
  }
})();
