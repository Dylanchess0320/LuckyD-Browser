/* ============================================================================
 * LuckyD YouTube AdBlock — MAIN world content script (document_start)
 * Runs in the page's own world before any YouTube script, so it can:
 *   1. Patch fetch / XHR to strip ad placements from player responses.
 *   2. Patch Object.defineProperty to scrub adSlots/adPlacements anywhere.
 *   3. Short-circuit any ad that still plays (jump to end, mute, skip).
 *   4. Remove ad DOM + dismiss YouTube's "ad blockers violate ToS" popup.
 * Reports stats to the isolated world via CustomEvent so the popup shows a
 * live blocked count.
 * ========================================================================== */
(() => {
  "use strict";
  if (window.__ldYtExt) return;
  window.__ldYtExt = true;

  const TAG = "[LuckyD AdBlock]";
  let shortCircuits = 0;
  let scrubs = 0;

  const AD_HOSTS = [
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "googletagservices.com",
    "adservice.google.com",
    "google-analytics.com",
    "ads.youtube.com",
  ];
  const AD_PATHS = [
    "/pagead/",
    "/ptracking",
    "/api/stats/ads",
    "/api/stats/watchtime",
    "/get_midroll_info",
    "/adformat-",
    "ad_type=",
    "adformat=",
    "&ad_",
  ];
  const lower = (s) => (s || "").toString().toLowerCase();
  const isAdUrl = (u) => {
    try {
      const x = lower(u);
      return (
        AD_HOSTS.some((h) => x.includes(h)) || AD_PATHS.some((p) => x.includes(p))
      );
    } catch (_) {
      return false;
    }
  };

  function report() {
    try {
      window.dispatchEvent(
        new CustomEvent("__ldAdBlockStats", { detail: { shortCircuits, scrubs } })
      );
    } catch (_) {}
  }

  // ---- recursive ad-slot scrubber ------------------------------------------
  const AD_KEYS = new Set([
    "adSlots",
    "adPlacements",
    "adBreakHeartbeatParams",
    "adThumbnails",
    "playerAds",
    "adSlotAndLayoutMetadata",
    "adLayoutMetadata",
  ]);
  function scrub(obj, depth) {
    if (!obj || typeof obj !== "object" || depth > 9) return;
    for (const key of Object.keys(obj)) {
      if (AD_KEYS.has(key)) {
        try {
          obj[key] = Array.isArray(obj[key]) ? [] : undefined;
          scrubs++;
        } catch (_) {}
        continue;
      }
      scrub(obj[key], depth + 1);
    }
  }

  // ---- fetch patch ---------------------------------------------------------
  try {
    const origFetch = window.fetch;
    window.fetch = function (input, init) {
      const url = typeof input === "string" ? input : input && input.url;
      if (url && isAdUrl(url)) {
        return Promise.resolve(
          new Response(JSON.stringify({ error: "blocked" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          })
        );
      }
      const p = origFetch.apply(this, arguments);
      if (url && /\/youtubei\/v1\/(player|next)/.test(url)) {
        return p.then((resp) => {
          try {
            return resp
              .clone()
              .json()
              .then((data) => {
                scrub(data, 0);
                report();
                return new Response(JSON.stringify(data), {
                  status: resp.status,
                  statusText: resp.statusText,
                  headers: resp.headers,
                });
              })
              .catch(() => resp);
          } catch (_) {
            return resp;
          }
        });
      }
      return p;
    };
  } catch (_) {}

  // ---- XHR patch -----------------------------------------------------------
  try {
    const origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url) {
      if (url && isAdUrl(url)) this.__ldBlocked = true;
      return origOpen.apply(this, arguments);
    };
    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function () {
      if (this.__ldBlocked) {
        try {
          Object.defineProperty(this, "status", { get: () => 200 });
          Object.defineProperty(this, "responseText", { get: () => "{}" });
          Object.defineProperty(this, "response", { get: () => "{}" });
        } catch (_) {}
        if (this.onload) this.onload();
        return;
      }
      return origSend.apply(this, arguments);
    };
  } catch (_) {}

  // ---- defineProperty trap (catches ytInitialPlayerResponse etc.) ----------
  try {
    const origDefine = Object.defineProperty;
    Object.defineProperty = function (target, prop, descriptor) {
      if (target && descriptor && "value" in descriptor) {
        const v = descriptor.value;
        if (v && typeof v === "object" && (v.adPlacements || v.adSlots || v.playerAds)) {
          scrub(v, 0);
          report();
        }
      }
      return origDefine.apply(this, arguments);
    };
  } catch (_) {}

  // ---- scrub already-present initial data ----------------------------------
  try {
    if (window.ytInitialPlayerResponse) scrub(window.ytInitialPlayerResponse, 0);
    if (window.ytplayer && window.ytplayer.config) scrub(window.ytplayer.config, 0);
    if (window.ytInitialData) scrub(window.ytInitialData, 0);
  } catch (_) {}


  // ---- short-circuit + skip + mute -----------------------------------------
  const videoEl = () =>
    document.querySelector("video.html5-main-video") || document.querySelector("video");
  const playerEl = () =>
    document.querySelector(".html5-video-player") || document.getElementById("movie_player");

  function isAdPlaying(p) {
    if (!p) return false;
    if (p.classList.contains("ad-showing") || p.classList.contains("ad-interrupting"))
      return true;
    return !!document.querySelector(
      ".ytp-ad-player-overlay,.ytp-ad-player-overlay-instream-info,.ytp-ad-text-overlay,.ytp-ad-image-overlay"
    );
  }

  function finishAd() {
    const v = videoEl();
    const p = playerEl();
    if (!v) return;
    const skip = document.querySelector(
      ".ytp-ad-skip-button,.ytp-ad-skip-button-modern,.ytp-skip-ad-button," +
        ".ytp-ad-skip-button-container button,.ytp-ad-skip-button-slot button"
    );
    if (skip && skip.offsetParent !== null) {
      skip.click();
      return;
    }
    if (isAdPlaying(p)) {
      try {
        v.muted = true;
        if (isFinite(v.duration) && v.duration > 0 && v.currentTime < v.duration - 0.15) {
          v.currentTime = Math.max(0, v.duration - 0.1);
          shortCircuits++;
          report();
        }
        if (v.paused) v.play().catch(() => {});
      } catch (_) {}
    }
    const close = document.querySelector(".ytp-ad-overlay-close-button");
    if (close) close.click();
  }

  // ---- cosmetic removal + ToS popup dismissal -------------------------------
  const AD_SELECTORS = [
    "#masthead-ad", ".ytd-video-masthead-ad-v3-renderer", ".ytd-ad-slot-renderer",
    ".ytd-in-feed-ad-layout-renderer", ".ytd-promoted-sparkles-web-renderer",
    ".ytd-promoted-sparkles-text-search-renderer", ".ytd-statement-banner-renderer",
    ".ytd-companion-slot-renderer", ".ytd-search-pyv-renderer",
    ".ytd-compact-promoted-video-renderer", ".ytd-promoted-video-renderer",
    ".ytd-display-ad-renderer", ".video-ads", ".ytp-ad-player-overlay",
    ".ytp-ad-image-overlay", ".ytp-ad-text-overlay", ".ytp-ad-action-interstitial",
    "#player-ads", ".ad-container", ".ytd-merch-shelf-renderer",
    "ytd-ad-slot-renderer", "ytd-in-feed-ad-layout-renderer", "ytd-promoted-video-renderer",
    'iframe[src*="doubleclick.net"]', 'iframe[src*="googlesyndication.com"]',
  ];
  function removeAdDom() {
    for (const s of AD_SELECTORS) {
      try {
        document.querySelectorAll(s).forEach((e) => e.remove());
      } catch (_) {}
    }
  }
  function dismissBlockPopup() {
    try {
      const dlg = document.querySelector(
        "ytd-enforcement-message-view-model,tp-yt-paper-dialog.yt-popup,ytd-popup-container tp-yt-paper-dialog"
      );
      if (dlg) {
        const btn = dlg.querySelector(
          'button[aria-label*="lose" i],button[aria-label*="ismiss" i],#dismiss-button button'
        );
        if (btn) btn.click();
        else dlg.remove();
      }
      document.querySelectorAll("ytd-enforcement-message-view-model").forEach((e) => e.remove());
    } catch (_) {}
  }

  // ---- observers + timers ----------------------------------------------------
  removeAdDom();
  new MutationObserver(() => {
    removeAdDom();
    dismissBlockPopup();
  }).observe(document.documentElement, { childList: true, subtree: true });

  setInterval(() => {
    finishAd();
    dismissBlockPopup();
  }, 150);
  setInterval(removeAdDom, 1500);

  window.__ldYtStats = () => ({ shortCircuits, scrubs });
  console.info(TAG + " active (main world)");
})();

