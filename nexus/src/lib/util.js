// Shared helpers used across Nexus
import { useSyncExternalStore } from 'react'

// UUID that also works in non-secure contexts (http://, Android WebView)
export function uid() {
  try {
    if (crypto?.randomUUID) return crypto.randomUUID()
  } catch { /* fall through */ }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

export function safeJson(str, fallback) {
  try {
    const v = JSON.parse(str)
    return v ?? fallback
  } catch {
    return fallback
  }
}

export const cx = (...parts) => parts.filter(Boolean).join(' ')

export const isTouch =
  typeof window !== 'undefined' && !!window.matchMedia?.('(pointer: coarse)').matches

// Reactive media query hook (responsive layouts)
export function useMedia(query) {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(query)
      m.addEventListener('change', cb)
      return () => m.removeEventListener('change', cb)
    },
    () => window.matchMedia(query).matches,
  )
}

export function formatTime(ts) {
  return new Date(ts).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

export function formatDateLabel(ts) {
  const d = new Date(ts)
  const now = new Date()
  const diff = now - d
  if (diff < 86400000 && d.getDate() === now.getDate()) return formatTime(ts)
  if (diff < 604800000) return d.toLocaleDateString([], { weekday: 'short' })
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

export function download(filename, text, type = 'text/plain') {
  const blob = new Blob([text], { type })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

// Export a chat thread as a readable Markdown document
const msgText = (m) =>
  typeof m.content === 'string'
    ? m.content
    : Array.isArray(m.content)
      ? m.content.filter((p) => p.type === 'text').map((p) => p.text).join('\n')
      : ''
const msgImages = (m) =>
  Array.isArray(m.content) ? m.content.filter((p) => p.type === 'image_url').length : 0

export function chatToMarkdown(chat) {
  const lines = [
    `# ${chat.title || 'Chat'}`,
    '',
    `_Exported from Nexus · ${new Date().toLocaleString()}_`,
    '',
  ]
  for (const m of chat.messages || []) {
    if (m.role === 'user') {
      const imgs = msgImages(m)
      lines.push('## You', '', [msgText(m), imgs ? `_(${imgs} image${imgs > 1 ? 's' : ''} attached)_` : ''].filter(Boolean).join('\n\n'), '')
    } else if (m.role === 'assistant') {
      lines.push(`## Nexus${m.model ? ` · ${m.model}` : ''}`, '')
      if (m.reasoning) {
        lines.push('> **Thinking**', ...m.reasoning.split('\n').map((l) => `> ${l}`), '')
      }
      lines.push(msgText(m) || '', '')
      if (m.citations?.length) {
        lines.push('**Sources**')
        m.citations.forEach((c, i) => lines.push(`${i + 1}. [${c.title || c.url}](${c.url})`))
        lines.push('')
      }
    }
  }
  return lines.join('\n')
}

// Read an image file and downscale it so it fits comfortably in storage
export async function fileToDataUrl(file, maxDim = 1024, quality = 0.85) {
  const dataUrl = await new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result)
    r.onerror = reject
    r.readAsDataURL(file)
  })
  if (!file.type.startsWith('image/') || file.type === 'image/svg+xml' || file.type === 'image/gif') {
    return { dataUrl, type: file.type || 'image/png', name: file.name || 'image' }
  }
  const img = await new Promise((resolve, reject) => {
    const i = new Image()
    i.onload = () => resolve(i)
    i.onerror = reject
    i.src = dataUrl
  })
  let { width, height } = img
  if (Math.max(width, height) > maxDim) {
    const scale = maxDim / Math.max(width, height)
    width = Math.round(width * scale)
    height = Math.round(height * scale)
  }
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  canvas.getContext('2d').drawImage(img, 0, 0, width, height)
  const type = file.type === 'image/png' ? 'image/png' : 'image/jpeg'
  return { dataUrl: canvas.toDataURL(type, quality), type, name: file.name || 'image', width, height }
}

// ---- Context-window management ----
// Rough token estimate (~4 chars/token, English-text average — good enough
// for a budget check, not for billing).
export function estimateTokens(text) {
  if (!text) return 0
  return Math.ceil(text.length / 4)
}

function messageTokens(m) {
  const text = msgText(m) + (m.reasoning || '')
  // Flat per-image estimate (OpenRouter/Gemini both resample images into a
  // few hundred–ish tokens regardless of source size).
  const imgTokens = msgImages(m) * 300
  return estimateTokens(text) + imgTokens
}

// Trim the oldest messages so the thread fits under maxTokens, always
// keeping the most recent turns (and never dropping the last message —
// that's the one the user just sent). Returns { messages, trimmedCount }.
export function trimMessagesToBudget(messages, maxTokens) {
  if (!messages.length) return { messages, trimmedCount: 0 }
  let total = 0
  const costs = messages.map((m) => messageTokens(m))
  for (const c of costs) total += c
  if (total <= maxTokens) return { messages, trimmedCount: 0 }

  // Walk from the end, keep adding messages until the budget is spent.
  let budget = maxTokens
  let cut = messages.length
  for (let i = messages.length - 1; i >= 0; i--) {
    budget -= costs[i]
    if (budget < 0 && i !== messages.length - 1) {
      cut = i + 1
      break
    }
    cut = i
  }
  const kept = messages.slice(cut)
  return { messages: kept, trimmedCount: messages.length - kept.length }
}

// ---- Text-to-speech ----
// Re-exported from the unified speech layer so the same import sites keep
// working — on Android these now use the native TTS engine under the hood.
export { plainText, speak, stopSpeak } from './speech'
