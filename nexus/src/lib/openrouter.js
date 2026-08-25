// OpenRouter API — model catalog + streaming chat (reasoning, web search, citations)
const BASE = 'https://openrouter.ai/api/v1'
const LS_KEY = 'nexus_openrouter_key_v1'

// Auth priority: localStorage-pasted key only. Deliberately no build-time
// env var fallback here — VITE_-prefixed vars get inlined into the client
// bundle in plain text, which ships inside the APK for anyone to read.
const getKey = () => {
  try {
    const stored = localStorage.getItem(LS_KEY)
    if (stored && stored.length > 10) return stored
  } catch { /* ignore */ }
  return ''
}

export const hasOpenRouter = () => getKey().length > 10
export const storedOpenRouterKey = () => {
  try { return localStorage.getItem(LS_KEY) || '' } catch { return '' }
}

export function setOpenRouterKey(key) {
  try {
    const trimmed = (key || '').trim()
    if (trimmed) localStorage.setItem(LS_KEY, trimmed)
    else localStorage.removeItem(LS_KEY)
  } catch { /* ignore */ }
}

// FREE-ONLY: default must be a $0 model so Nexus can never spend money by
// accident. Free ids churn — if this 404s/429s, re-list and update.
export const DEFAULT_MODEL = 'google/gemma-4-31b-it:free'

const headers = () => ({
  Authorization: `Bearer ${getKey()}`,
  'Content-Type': 'application/json',
  'HTTP-Referer': window.location.origin,
  'X-Title': 'Nexus',
})

// Fetch full model catalog (cached 5 min)
let modelCache = null
let modelCacheTime = 0
export async function fetchModels() {
  if (!hasOpenRouter()) return []
  if (modelCache && Date.now() - modelCacheTime < 5 * 60 * 1000) return modelCache
  const res = await fetch(`${BASE}/models`, { headers: headers() })
  if (!res.ok) throw new Error(`OpenRouter models: HTTP ${res.status}`)
  const data = await res.json()
  modelCache = (data.data || []).map((m) => {
    const prompt = parseFloat(m.pricing?.prompt || 0)
    const completion = parseFloat(m.pricing?.completion || 0)
    return {
      id: m.id,
      name: m.name || m.id,
      provider: m.id.split('/')[0],
      context: m.context_length || 0,
      promptPrice: prompt * 1e6, // per 1M tokens
      completionPrice: completion * 1e6,
      free: prompt === 0 && completion === 0,
      vision: !!m.architecture?.input_modalities?.includes('image'),
      description: m.description || '',
    }
  })
  modelCache.sort((a, b) => a.name.localeCompare(b.name))
  modelCacheTime = Date.now()
  return modelCache
}

// Stream a chat completion.
// onToken(delta, full) · onReasoning(delta, full) · onCitations(list)
// Returns { content, reasoning, usage, citations } when done.
export async function streamChat({
  model, messages, signal, onToken, onReasoning, onCitations, temperature, webSearch,
}) {
  const res = await fetch(`${BASE}/chat/completions`, {
    method: 'POST',
    headers: headers(),
    signal,
    body: JSON.stringify({
      // :online enables OpenRouter's web-search plugin (Perplexity-style grounding)
      model: webSearch ? `${model}:online` : model,
      messages,
      stream: true,
      usage: { include: true },
      ...(temperature !== undefined ? { temperature } : {}),
    }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    const e = new Error(err?.error?.message || `OpenRouter HTTP ${res.status}`)
    e.status = res.status
    e.provider = 'OpenRouter'
    throw e
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let content = ''
  let reasoning = ''
  let usage = null
  let citations = []
  const citationUrls = new Set()

  const addCitations = (list) => {
    let changed = false
    for (const c of list) {
      if (!c?.url || citationUrls.has(c.url)) continue
      citationUrls.add(c.url)
      citations.push({
        url: c.url,
        title: c.title || c.url.replace(/^https?:\/\//, '').split('/')[0],
      })
      changed = true
    }
    if (changed) onCitations?.(citations)
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      const t = line.trim()
      if (!t.startsWith('data:')) continue
      const payload = t.slice(5).trim()
      if (payload === '[DONE]') continue
      try {
        const json = JSON.parse(payload)
        const choice = json.choices?.[0]
        const delta = choice?.delta
        if (delta?.content) {
          content += delta.content
          onToken?.(delta.content, content)
        }
        // Reasoning models (R1, o-series, QwQ…) stream a separate reasoning channel
        if (delta?.reasoning) {
          reasoning += delta.reasoning
          onReasoning?.(delta.reasoning, reasoning)
        }
        // Web-search citations arrive as url_citation annotations
        const anns = choice?.message?.annotations || delta?.annotations
        if (anns?.length) {
          addCitations(
            anns
              .filter((a) => a.type === 'url_citation')
              .map((a) => ({ url: a.url_citation?.url, title: a.url_citation?.title })),
          )
        }
        if (json.usage) usage = json.usage
      } catch { /* partial JSON chunk, skip */ }
    }
  }
  return { content, reasoning, usage, citations }
}

// Short non-streaming call (title generation)
export async function quickChat({ model = DEFAULT_MODEL, messages, max_tokens = 30, signal }) {
  const res = await fetch(`${BASE}/chat/completions`, {
    method: 'POST',
    headers: headers(),
    signal,
    body: JSON.stringify({ model, messages, max_tokens, stream: false }),
  })
  if (!res.ok) throw new Error(`OpenRouter HTTP ${res.status}`)
  const data = await res.json()
  return data.choices?.[0]?.message?.content?.trim() || ''
}

// Rough $ cost of one call, using catalog prices
export function estimateCost(model, usage) {
  if (!model || !usage) return 0
  const inCost = ((usage.prompt_tokens || 0) * (model.promptPrice || 0)) / 1e6
  const outCost = ((usage.completion_tokens || 0) * (model.completionPrice || 0)) / 1e6
  return inCost + outCost
}
