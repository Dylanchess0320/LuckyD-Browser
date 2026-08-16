// Gemini direct API — free-tier fallback, or primary when no OpenRouter key is set
const BASE = 'https://generativelanguage.googleapis.com/v1beta/models'

// Auth priority: localStorage-pasted key only. Deliberately no build-time
// env var fallback here — VITE_-prefixed vars get inlined into the client
// bundle in plain text, which ships inside the APK for anyone to read.
const LS_KEY = 'nexus_gemini_key_v1'
export const getGeminiKey = () => {
  try {
    const stored = localStorage.getItem(LS_KEY)
    if (stored?.length > 10) return stored
  } catch {
    /* ignore */
  }
  return ''
}
export const hasGemini = () => !!getGeminiKey()
export const storedGeminiKey = () => {
  try {
    return localStorage.getItem(LS_KEY) || ''
  } catch {
    return ''
  }
}

export function setGeminiKey(key) {
  try {
    const trimmed = (key || '').trim()
    if (trimmed) localStorage.setItem(LS_KEY, trimmed)
    else localStorage.removeItem(LS_KEY)
  } catch {
    /* ignore */
  }
}

// Curated list of models verified to work on the free tier with this API key
// (probed live against /v1beta/models:generateContent — 2026-08).
// 2.5-flash-lite / 2.5-pro / *-image return 404/429 on this tier, so they're out.
const gm = (id, name, description, context = 1048576) => ({
  id,
  name,
  provider: 'Google (direct)',
  context,
  promptPrice: 0,
  completionPrice: 0,
  free: true,
  vision: true,
  description,
})
export const GEMINI_MODELS = [
  gm('gemini-3.7-flash', 'Gemini 3.7 Flash', 'Newest Flash — best balance of speed and smarts.'),
  gm('gemini-3.6-flash', 'Gemini 3.6 Flash', 'Previous-gen Flash, still very capable.'),
  gm('gemini-3.5-flash', 'Gemini 3.5 Flash', 'Reliable workhorse for everyday chat.'),
  gm('gemini-3.5-flash-lite', 'Gemini 3.5 Flash-Lite', 'Smallest Gemini 3.5 — snappy answers.'),
  gm('gemini-3.1-flash-lite', 'Gemini 3.1 Flash-Lite', 'Ultra-fast lite model.'),
  gm('gemini-2.5-flash', 'Gemini 2.5 Flash', 'Proven stable free-tier model.'),
]

// Ordered cascade — if the picked model 404s/429s/5xx, silently try the next one.
const FALLBACK_ORDER = [
  'gemini-3.7-flash',
  'gemini-3.6-flash',
  'gemini-3.5-flash',
  'gemini-3.5-flash-lite',
  'gemini-3.1-flash-lite',
  'gemini-2.5-flash',
  'gemini-3-flash-preview',
  'gemini-flash-lite-latest',
]

const tagError = (msg, status) => {
  const e = new Error(msg)
  e.status = status
  e.provider = 'Gemini'
  return e
}
const isRetriable = (e) => [404, 408, 409, 429, 500, 502, 503].includes(e?.status)

// Bare `gemini-*` ids always mean the direct Gemini API (OpenRouter/Cline
// spell theirs `google/gemini-*`). The membership test covers curated picks.
export const isGeminiDirect = (id) => /^gemini-/.test(id || '') || GEMINI_MODELS.some((m) => m.id === id)

// Convert OpenAI-style messages (string or parts-array content) to Gemini format
function toGeminiContents(messages) {
  const system = messages.find((m) => m.role === 'system')
  const rest = messages.filter((m) => m.role !== 'system')
  return {
    systemInstruction: system ? { parts: [{ text: system.content }] } : undefined,
    contents: rest.map((m) => {
      const parts = []
      if (typeof m.content === 'string') {
        parts.push({ text: m.content })
      } else if (Array.isArray(m.content)) {
        for (const p of m.content) {
          if (p.type === 'text') {
            parts.push({ text: p.text })
          } else if (p.type === 'image_url' && p.image_url?.url?.startsWith('data:')) {
            const [meta, data] = p.image_url.url.split(',', 2)
            parts.push({ inline_data: { mime_type: meta.slice(5).split(';')[0], data } })
          }
        }
      }
      return { role: m.role === 'assistant' ? 'model' : 'user', parts }
    }),
  }
}

// One streaming attempt against a specific model id. Throws a status-tagged
// error; `emitted()` tells the caller whether any tokens reached the UI yet
// (fallback retries are only safe before the first token).
async function streamOnce({ model, messages, signal, onToken, temperature, emitted }) {
  const key = getGeminiKey()
  if (!key) throw tagError('No Gemini API key configured', 401)
  const body = { ...toGeminiContents(messages) }
  if (temperature !== undefined) body.generationConfig = { temperature }
  const res = await fetch(`${BASE}/${model}:streamGenerateContent?alt=sse&key=${key}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw tagError(err?.error?.message || `Gemini HTTP ${res.status}`, res.status)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let content = ''
  let usage = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      const t = line.trim()
      if (!t.startsWith('data:')) continue
      try {
        const json = JSON.parse(t.slice(5).trim())
        const parts = json.candidates?.[0]?.content?.parts || []
        for (const p of parts) {
          if (p.text) {
            content += p.text
            emitted.v = true
            onToken?.(p.text, content)
          }
        }
        if (json.usageMetadata) {
          usage = {
            prompt_tokens: json.usageMetadata.promptTokenCount || 0,
            completion_tokens: json.usageMetadata.candidatesTokenCount || 0,
            total_tokens: json.usageMetadata.totalTokenCount || 0,
          }
        }
      } catch { /* skip partial chunk */ }
    }
  }
  return { content, reasoning: '', usage, citations: [], usedModel: model }
}

// Stream with an automatic cross-model fallback cascade: if the requested
// model is unavailable on this key/tier (404/429/5xx) before any token was
// emitted, the next working model takes over transparently.
export async function streamGemini({
  model = 'gemini-3.7-flash',
  messages,
  signal,
  onToken,
  temperature,
}) {
  const candidates = [model, ...FALLBACK_ORDER.filter((m) => m !== model)]
  let lastErr = null
  for (const m of candidates) {
    const emitted = { v: false }
    try {
      return await streamOnce({ model: m, messages, signal, onToken, temperature, emitted })
    } catch (e) {
      if (e?.name === 'AbortError') throw e
      lastErr = e
      // Mid-stream failures can't be retried without duplicating tokens
      if (emitted.v || !isRetriable(e)) throw e
    }
  }
  throw lastErr
}

// Live model discovery — Gemini's ListModels endpoint. Curated entries keep
// their hand-picked name/description; anything new Google ships shows up
// automatically with a generic label instead of silently being invisible.
let geminiModelCache = null
let geminiModelCacheTime = 0
export async function fetchGeminiModels() {
  const key = getGeminiKey()
  if (!key) return []
  if (geminiModelCache && Date.now() - geminiModelCacheTime < 5 * 60 * 1000) return geminiModelCache
  const res = await fetch(`${BASE}?key=${key}&pageSize=1000`)
  if (!res.ok) throw tagError(`Gemini models: HTTP ${res.status}`, res.status)
  const data = await res.json()
  const curated = new Map(GEMINI_MODELS.map((m) => [m.id, m]))
  const seen = new Set()
  const list = []
  for (const m of data.models || []) {
    const methods = m.supportedGenerationMethods || m.supportedActions || []
    if (!methods.includes('generateContent')) continue
    const id = (m.name || '').replace(/^models\//, '')
    if (!id.startsWith('gemini-') || seen.has(id)) continue
    seen.add(id)
    list.push(
      curated.get(id) || {
        id,
        name: m.displayName || id,
        provider: 'Google (direct)',
        context: m.inputTokenLimit || 1048576,
        promptPrice: 0,
        completionPrice: 0,
        free: true,
        vision: true,
        description: m.description || 'Newly listed by Google — not yet hand-verified.',
      },
    )
  }
  if (!list.length) return GEMINI_MODELS // fetch worked but returned nothing usable — fall back
  list.sort((a, b) => {
    const ai = GEMINI_MODELS.findIndex((x) => x.id === a.id)
    const bi = GEMINI_MODELS.findIndex((x) => x.id === b.id)
    if (ai !== -1 && bi !== -1) return ai - bi
    if (ai !== -1) return -1
    if (bi !== -1) return 1
    return a.id.localeCompare(b.id)
  })
  geminiModelCache = list
  geminiModelCacheTime = Date.now()
  return list
}
// Short non-streaming call (title generation without OpenRouter)
export async function quickGemini({ model = 'gemini-3.7-flash', messages, maxTokens = 30, signal }) {
  if (!hasGemini()) return ''
  const key = getGeminiKey()
  const candidates = [model, ...FALLBACK_ORDER.filter((m) => m !== model)]
  let lastErr = null
  for (const m of candidates) {
    const body = {
      ...toGeminiContents(messages),
      generationConfig: { maxOutputTokens: maxTokens },
    }
    let res
    try {
      res = await fetch(`${BASE}/${m}:generateContent?key=${key}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal,
        body: JSON.stringify(body),
      })
    } catch (e) {
      if (e?.name === 'AbortError') throw e
      lastErr = tagError(e?.message || 'Network error', 0)
      continue
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      lastErr = tagError(err?.error?.message || `Gemini HTTP ${res.status}`, res.status)
      if (isRetriable(lastErr)) continue
      throw lastErr
    }
    const data = await res.json()
    return (data.candidates?.[0]?.content?.parts || [])
      .map((p) => p.text || '')
      .join('')
      .trim()
  }
  throw lastErr || new Error('Gemini request failed')
}
