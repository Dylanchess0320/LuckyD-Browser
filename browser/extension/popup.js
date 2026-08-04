/* LuckyD AdBlock popup — shows live stats + the enable toggle. */

const enabledEl = document.getElementById("enabled");
const netEl = document.getElementById("netBlocked");
const scEl = document.getElementById("shortCircuits");

function currentTab() {
  return chrome.tabs
    .query({ active: true, currentWindow: true })
    .then((tabs) => tabs && tabs[0]);
}

// Load persisted toggle state.
chrome.storage.local.get({ ldEnabled: true }, (res) => {
  enabledEl.checked = !!res.ldEnabled;
});

enabledEl.addEventListener("change", () => {
  chrome.runtime.sendMessage({ type: "ldSetEnabled", enabled: enabledEl.checked });
});

// Pull network-block count from the service worker + video-ad count from the
// page's main-world script (executed in MAIN world via chrome.scripting).
async function refresh() {
  const tab = await currentTab();
  if (!tab || typeof tab.id !== "number") return;

  chrome.runtime.sendMessage({ type: "ldGetBlocked", tabId: tab.id }, (res) => {
    if (chrome.runtime.lastError) return;
    netEl.textContent = (res && res.blocked) || 0;
  });

  if (tab.url && /youtube\.com|youtube-nocookie\.com/.test(tab.url)) {
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: () =>
          window.__ldYtStats ? window.__ldYtStats() : { shortCircuits: 0 },
      });
      const val = results && results[0] && results[0].result;
      scEl.textContent = (val && val.shortCircuits) || 0;
    } catch (_) {
      /* scripting not allowed on this page — leave as-is */
    }
  }
}

refresh();
const timer = setInterval(refresh, 1500);
window.addEventListener("unload", () => clearInterval(timer));
