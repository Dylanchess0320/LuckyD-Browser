/* LuckyD YouTube AdBlock — background service worker (MV3).
 * Owns the declarativeNetRequest ruleset and tracks a per-tab blocked count
 * (via matched-rule feedback) so the popup can show live numbers. */

const RULESET_ID = "yt_ads";

// Per-tab blocked counts (cleared on navigation).
const blockedByTab = new Map();

async function enableRuleset() {
  try {
    await chrome.declarativeNetRequest.updateEnabledRulesets({
      enableRulesetIds: [RULESET_ID],
    });
  } catch (e) {
    console.warn("[LuckyD AdBlock] enable ruleset failed", e);
  }
}

chrome.runtime.onInstalled.addListener(() => {
  enableRuleset();
  chrome.storage.local.set({ ldEnabled: true });
});
chrome.runtime.onStartup.addListener(enableRuleset);

// Count blocked requests when feedback permission is available.
if (chrome.declarativeNetRequest.onRuleMatchedDebug) {
  chrome.declarativeNetRequest.onRuleMatchedDebug.addListener((info) => {
    const tabId = info.request && info.request.tabId;
    if (typeof tabId === "number" && tabId >= 0) {
      blockedByTab.set(tabId, (blockedByTab.get(tabId) || 0) + 1);
    }
  });
}

// Reset count when a tab navigates.
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "loading") blockedByTab.delete(tabId);
});
chrome.tabs.onRemoved.addListener((tabId) => blockedByTab.delete(tabId));

// Popup asks for the current count.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "ldGetBlocked" && typeof msg.tabId === "number") {
    sendResponse({ blocked: blockedByTab.get(msg.tabId) || 0 });
    return true;
  }
  if (msg && msg.type === "ldSetEnabled") {
    const on = !!msg.enabled;
    chrome.storage.local.set({ ldEnabled: on });
    if (on) enableRuleset();
    else
      chrome.declarativeNetRequest.updateEnabledRulesets({
        disableRulesetIds: [RULESET_ID],
      });
    sendResponse({ ok: true });
    return true;
  }
  return false;
});
