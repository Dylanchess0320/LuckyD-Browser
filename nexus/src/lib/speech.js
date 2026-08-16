// Unified speech layer — native (Capacitor) STT/TTS with web fallbacks.
// The Android WebView implements neither webkitSpeechRecognition nor a
// reliable speechSynthesis, so on device we go through the native plugins.
import { Capacitor } from '@capacitor/core'

export const isNative = (() => {
  try {
    return !!Capacitor?.isNativePlatform?.()
  } catch {
    return false
  }
})()

const WebSpeechRecognition =
  typeof window !== 'undefined'
    ? window.SpeechRecognition || window.webkitSpeechRecognition
    : null

// Lazily imported so the web bundle never touches native bridges
let sttModP = null
function sttPlugin() {
  if (!sttModP) sttModP = import('@capgo/capacitor-speech-recognition').then((m) => ({ SR: m.SpeechRecognition }))
  return sttModP
}
let ttsModP = null
function ttsPlugin() {
  if (!ttsModP) ttsModP = import('@capacitor-community/text-to-speech').then((m) => ({ TTS: m.TextToSpeech }))
  return ttsModP
}

// ---------------------------------------------------------------- STT ----

let sttCache = null

/** Is speech-to-text usable at all on this device/browser? */
export async function sttAvailable() {
  if (sttCache !== null) return sttCache
  if (isNative) {
    try {
      const { SR } = await sttPlugin()
      const res = await SR.available()
      sttCache = !!res?.available
    } catch {
      sttCache = false
    }
  } else {
    sttCache = !!WebSpeechRecognition
  }
  return sttCache
}

/** Ask for the microphone permission up front (native no-ops on web). */
export async function ensureSttPermission() {
  if (!isNative) return true // browsers prompt when recognition starts
  try {
    const { SR } = await sttPlugin()
    const cur = await SR.checkPermissions()
    if (cur.speechRecognition === 'granted') return true
    const req = await SR.requestPermissions()
    return req.speechRecognition === 'granted'
  } catch {
    return false
  }
}

let activeWebRec = null

function webListenOnce({ onPartial } = {}) {
  return new Promise((resolve, reject) => {
    if (!WebSpeechRecognition) {
      reject(new Error('Voice input is not supported in this browser'))
      return
    }
    const rec = new WebSpeechRecognition()
    rec.lang = navigator.language || 'en-US'
    rec.interimResults = true
    rec.continuous = false
    let latest = ''
    rec.onresult = (e) => {
      let said = ''
      for (const r of e.results) said += r[0].transcript
      latest = said
      onPartial?.(said)
    }
    rec.onerror = (e) => {
      if (e.error === 'no-speech' || e.error === 'aborted') {
        resolve(latest)
      } else if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        reject(new Error('Microphone permission denied'))
      } else {
        reject(new Error(e.error || 'Microphone error'))
      }
    }
    rec.onend = () => resolve(latest)
    activeWebRec = rec
    try {
      rec.start()
    } catch {
      activeWebRec = null
      reject(new Error('Could not start the microphone'))
    }
  })
}

async function nativeListenOnce({ onPartial } = {}) {
  const { SR } = await sttPlugin()
  await SR.removeAllListeners().catch(() => {})
  return new Promise((resolve, reject) => {
    let done = false
    let latest = ''
    let poll = null
    let settleTimer = null
    let hardTimer = null

    const cleanup = async () => {
      if (poll) clearInterval(poll)
      if (settleTimer) clearTimeout(settleTimer)
      if (hardTimer) clearTimeout(hardTimer)
      try { await SR.stop() } catch { /* not listening */ }
      try { await SR.removeAllListeners() } catch { /* noop */ }
    }
    const finish = (text) => {
      if (done) return
      done = true
      cleanup().finally(() => resolve((text || '').trim()))
    }

    ;(async () => {
      try {
        await SR.addListener('partialResults', (d) => {
          const m = d?.matches?.[0]
          if (m) {
            latest = m
            onPartial?.(m)
          }
        })
        await SR.addListener('listeningState', (s) => {
          if (s?.status === 'stopped' && !settleTimer) {
            // End of speech � give the final result a beat to arrive, then close the turn
            settleTimer = setTimeout(() => finish(latest), 700)
          }
        })
        await SR.start({
          language: navigator.language || 'en-US',
          maxResults: 3,
          partialResults: true,
          popup: false,
          muteRecognizerBeep: true, // Capgo fork only — best-effort start/stop beep suppression
        })
        // Watchdog: some error paths emit no event, so poll the listening flag
        poll = setInterval(async () => {
          try {
            const { listening } = await SR.isListening()
            if (!listening && !done && !settleTimer) {
              settleTimer = setTimeout(() => finish(latest), 700)
            }
          } catch {
            finish(latest)
          }
        }, 500)
        hardTimer = setTimeout(() => finish(latest), 60000) // absolute cap
      } catch (err) {
        await cleanup()
        if (done) return
        done = true
        reject(new Error(err?.message || 'Could not start the microphone'))
      }
    })()
  })
}

/**
 * One listen-until-silence turn. Resolves with the heard text ('' = silence).
 * Rejects only on hard failures (no permission, no mic, engine missing).
 */
export function listenOnce({ onPartial } = {}) {
  return isNative ? nativeListenOnce({ onPartial }) : webListenOnce({ onPartial })
}

/** Stop the in-flight listenOnce turn early (it resolves with whatever was heard). */
export async function stopStt() {
  if (isNative) {
    try {
      const { SR } = await sttPlugin()
      await SR.stop()
    } catch { /* noop */ }
  } else {
    try {
      activeWebRec?.stop()
    } catch { /* noop */ }
    activeWebRec = null
  }
}

// ---------------------------------------------------------------- TTS ----

/** Strip markdown so spoken replies sound natural */
export function plainText(md) {
  return String(md || '')
    .replace(/```[\s\S]*?```/g, ' code block ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/[*_~>#]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

let nativeSpeakDone = null // resolves the pending native speak() on stopSpeak()

/**
 * Speak `text`. Returns false when TTS is unavailable.
 * onEnd fires on completion, error, or when stopSpeak() interrupts.
 */
export function speak(text, { onEnd } = {}) {
  const clean = plainText(text).slice(0, 4000)
  if (!clean) {
    onEnd?.()
    return true
  }
  let voicePrefs = { voiceURI: '', voiceRate: 1, voicePitch: 1 }
  try {
    const raw = localStorage.getItem('nexus_settings_v1')
    if (raw) voicePrefs = { ...voicePrefs, ...JSON.parse(raw) }
  } catch {}
  voicePrefs.rate = Math.min(10, Math.max(0.1, Number(voicePrefs.voiceRate) || 1))
  voicePrefs.pitch = Math.min(2, Math.max(0, Number(voicePrefs.voicePitch) || 1))
  voicePrefs.voiceURI = voicePrefs.voiceURI || ''
  if (isNative) {
    let ended = false
    const finish = () => {
      if (ended) return
      ended = true
      if (nativeSpeakDone === finish) nativeSpeakDone = null
      onEnd?.()
    }
    nativeSpeakDone = finish
    ttsPlugin()
      .then(({ TTS }) =>
        TTS.speak({
          text: clean,
          lang: navigator.language || 'en-US',
          rate: voicePrefs.rate,
          pitch: voicePrefs.pitch,
          volume: 1.0,
          category: 'playback',
          ...(voicePrefs.voiceURI ? { voice: Number.isNaN(+voicePrefs.voiceURI) ? voicePrefs.voiceURI : +voicePrefs.voiceURI } : {}),
        }),
      )
      .then(finish)
      .catch(finish)
    return true
  }
  if (!('speechSynthesis' in window)) return false
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(clean)
  u.rate = voicePrefs.rate
  u.pitch = voicePrefs.pitch
  if (voicePrefs.voiceURI) {
    const vv = window.speechSynthesis.getVoices().find((x) => x.voiceURI === voicePrefs.voiceURI)
    if (vv) u.voice = vv
  }
  if (onEnd) {
    u.onend = onEnd
    u.onerror = onEnd
  }
  window.speechSynthesis.speak(u)
  return true
}

export function stopSpeak() {
  if (isNative) {
    const done = nativeSpeakDone
    nativeSpeakDone = null
    ttsPlugin()
      .then(({ TTS }) => TTS.stop())
      .catch(() => {})
    done?.() // release the pending onEnd so voice loops keep moving
  } else {
    try {
      window.speechSynthesis?.cancel()
    } catch { /* noop */ }
  }
}

/** List available TTS voices, normalized across native + web. */
export async function listVoices() {
  if (isNative) {
    try {
      const { TTS } = await ttsPlugin()
      const res = await TTS.getSupportedVoices()
      return (res?.voices || []).map((v, i) => ({
        id: String(v.id ?? i),
        name: v.name || v.id || `Voice ${i + 1}`,
        lang: v.lang || '',
      }))
    } catch {
      return []
    }
  }
  if (!('speechSynthesis' in window)) return []
  let vs = window.speechSynthesis.getVoices()
  if (!vs.length) {
    await new Promise((r) => {
      const tm = setTimeout(r, 400)
      window.speechSynthesis.onvoiceschanged = () => { clearTimeout(tm); r() }
    })
    vs = window.speechSynthesis.getVoices()
  }
  return vs.map((v) => ({ id: v.voiceURI, name: v.name, lang: v.lang }))
}