// App settings — persisted to localStorage, reactive via useSyncExternalStore
import { useSyncExternalStore } from 'react'
import { safeJson } from './util'

const KEY = 'nexus_settings_v1'

const DEFAULTS = {
  theme: 'system',       // 'system' | 'dark' | 'light'
  instructions: '',      // custom system instructions applied to every chat
  temperature: 1,        // 0–2 (1 = provider default, not sent)
  enterToSend: true,
  reduceMotion: false,
  voiceURI: '',          // '' = system default TTS voice
  voiceRate: 1,          // 0.5 – 2
  voicePitch: 1,         // 0.5 – 2
}

let cache = { ...DEFAULTS, ...safeJson(localStorage.getItem(KEY), {}) }
const listeners = new Set()

export function getSettings() {
  return cache
}

export function saveSettings(patch) {
  cache = { ...cache, ...patch }
  try {
    localStorage.setItem(KEY, JSON.stringify(cache))
  } catch { /* quota */ }
  if (patch.theme !== undefined) applyTheme(cache.theme)
  if (patch.reduceMotion !== undefined) applyMotion(cache.reduceMotion)
  listeners.forEach((fn) => fn())
}

export function useSettings() {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    () => cache,
  )
}

// ---- Theme ----
const media =
  typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: light)') : null

export function applyTheme(theme) {
  const resolved = theme === 'system' ? (media?.matches ? 'light' : 'dark') : theme
  document.documentElement.dataset.theme = resolved
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', resolved === 'light' ? '#ffffff' : '#0d0d10')
}

function applyMotion(reduced) {
  document.documentElement.dataset.motion = reduced ? 'reduced' : 'ok'
}

media?.addEventListener?.('change', () => {
  if (getSettings().theme === 'system') applyTheme('system')
})

// Apply persisted prefs ASAP on module load to avoid flashes
applyTheme(cache.theme)
applyMotion(!!cache.reduceMotion)
