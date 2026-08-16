import React, { memo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import hljs from 'highlight.js/lib/common'
import { toast } from '../lib/toast'
import { speak, stopSpeak } from '../lib/util'
import ImageLightbox from './ImageLightbox'

/* ---------- Message shape helpers (content may be string or parts array) ---------- */
export const textOf = (m) =>
  typeof m?.content === 'string'
    ? m.content
    : Array.isArray(m?.content)
      ? m.content
          .filter((p) => p.type === 'text')
          .map((p) => p.text)
          .join('\n')
      : ''

export const imagesOf = (m) =>
  Array.isArray(m?.content)
    ? m.content
        .filter((p) => p.type === 'image_url')
        .map((p) => p.image_url?.url)
        .filter(Boolean)
    : []

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Fallback for non-secure (http://) contexts where clipboard API is unavailable
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      return true
    } catch {
      return false
    }
  }
}

/* ---------- Code block: header bar + language + copy + hljs ---------- */
function CodeBlock({ language, children }) {
  const [copied, setCopied] = useState(false)
  const code = String(children).replace(/\n$/, '')
  let highlighted = null
  if (language && hljs.getLanguage(language)) {
    try {
      highlighted = hljs.highlight(code, { language }).value
    } catch {
      /* fall through to plain */
    }
  }
  const copy = async () => {
    if (await copyText(code)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } else {
      toast.error('Copy failed')
    }
  }
  return (
    <div className="codeblock">
      <div className="code-header">
        <span>{language || 'text'}</span>
        <button className="code-copy" onClick={copy}>
          {copied ? (
            '✓ Copied'
          ) : (
            <>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="9" y="9" width="13" height="13" rx="2" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
              Copy
            </>
          )}
        </button>
      </div>
      <pre className="!m-0 !border-0 !rounded-none !bg-transparent">
        {highlighted ? (
          <code
            className={`hljs language-${language}`}
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        ) : (
          <code>{code}</code>
        )}
      </pre>
    </div>
  )
}

/*
 * react-markdown v9-safe renderers.
 * NOTE: v9 no longer provides an `inline` boolean on code nodes — a code node
 * is a block when it carries a `language-*` class or its text spans lines.
 */
const mdComponents = {
  code({ node, className, children, ...props }) {
    const text = String(children ?? '')
    const isBlock = /language-[\w-]+/.test(className || '') || text.includes('\n')
    if (isBlock) {
      const language = /language-([\w-]+)/.exec(className || '')?.[1]
      return <CodeBlock language={language}>{text}</CodeBlock>
    }
    return (
      <code className={className} {...props}>
        {children}
      </code>
    )
  },
  // CodeBlock renders its own wrapper; unwrap the default <pre>
  pre({ children }) {
    return <>{children}</>
  },
  a({ href, children }) {
    return (
      <a href={href} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    )
  },
  table({ children }) {
    return (
      <div className="overflow-x-auto -mx-1 px-1">
        <table>{children}</table>
      </div>
    )
  },
}

/* ---------- Collapsible reasoning ("Thinking") ---------- */
function ReasoningBlock({ text, streaming }) {
  return (
    <details className="reasoning" open={!!streaming}>
      <summary>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 2a7 7 0 0 1 7 7c0 2.4-1.2 4.2-2.5 5.7-.9 1-1.5 1.8-1.5 3.3h-6c0-1.5-.6-2.3-1.5-3.3C6.2 13.2 5 11.4 5 9a7 7 0 0 1 7-7z" />
          <line x1="9" y1="22" x2="15" y2="22" />
        </svg>
        {streaming ? 'Thinking…' : 'Thought process'}
      </summary>
      <div className="reasoning-body">{text}</div>
    </details>
  )
}

/* ---------- Web-search citation chips ---------- */
function Sources({ citations }) {
  if (!citations?.length) return null
  return (
    <div className="sources">
      <div className="sources-title">Sources</div>
      <div className="sources-list">
        {citations.map((c, i) => {
          let domain = ''
          try {
            domain = new URL(c.url).hostname
          } catch { /* ignore */ }
          return (
            <a
              key={c.url + i}
              className="source-chip"
              href={c.url}
              target="_blank"
              rel="noreferrer noopener"
              title={c.title}
            >
              {domain && (
                <img
                  src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=32`}
                  alt=""
                  onError={(e) => {
                    e.currentTarget.style.display = 'none'
                  }}
                />
              )}
              <span>{c.title || domain}</span>
            </a>
          )
        })}
      </div>
    </div>
  )
}

/* ---------- Small icon action button ---------- */
function ActionBtn({ label, onClick, active, children }) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className={`p-1.5 rounded-lg transition-colors ${
        active ? 'text-accent' : 'text-content-faint hover:text-content hover:bg-bg-hover'
      }`}
    >
      {children}
    </button>
  )
}

/* ---------- Message row ---------- */
function Message({ message: m, isUser, isStreaming, model, showRegenerate, onRegenerate, onEdit }) {
  const [copied, setCopied] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [preview, setPreview] = useState(null) // image src shown full-screen

  const text = textOf(m)
  const images = imagesOf(m)

  const copy = async () => {
    if (await copyText(text)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } else {
      toast.error('Copy failed')
    }
  }

  const toggleSpeak = () => {
    if (speaking) {
      stopSpeak()
      setSpeaking(false)
      return
    }
    const ok = speak(text, { onEnd: () => setSpeaking(false) })
    if (ok) setSpeaking(true)
    else toast.error('Read-aloud is not supported on this device')
  }

  const saveEdit = () => {
    const t = draft.trim()
    if (!t) return toast.error('Message cannot be empty')
    setEditing(false)
    onEdit(t)
  }

  return (
    <div className={`msg msg-enter flex gap-3 px-3 sm:px-4 py-3.5 ${isUser ? 'flex-row-reverse' : ''}`}>
      {isUser ? (
        <div className="shrink-0 w-7 h-7 rounded-full bg-accent flex items-center justify-center text-white mt-0.5">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
        </div>
      ) : (
        <div className="shrink-0 w-7 h-7 rounded-full bg-bg-panel border border-bg-border flex items-center justify-center text-xs font-semibold text-accent mt-0.5">
          {(model?.name || 'AI').trim().charAt(0).toUpperCase()}
        </div>
      )}

      <div className={`min-w-0 flex flex-col ${isUser ? 'items-end max-w-[85%]' : 'flex-1'}`}>
        {images.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {images.map((src, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setPreview(src)}
                title="Tap to preview"
                aria-label={`Preview attachment ${i + 1}`}
                className="rounded-xl focus:outline-none focus:ring-2 focus:ring-accent"
              >
                <img
                  src={src}
                  alt={`attachment ${i + 1}`}
                  className="max-w-[200px] max-h-48 rounded-xl border border-bg-border object-cover cursor-zoom-in hover:opacity-90 transition-opacity"
                />
              </button>
            ))}
          </div>
        )}

        {!isUser && m.reasoning && (
          <ReasoningBlock text={m.reasoning} streaming={isStreaming && !text} />
        )}
        {isUser ? (
          editing ? (
            <div className="w-full" style={{ minWidth: 'min(420px, 72vw)' }}>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                autoFocus
                rows={Math.min(10, Math.max(2, draft.split('\n').length))}
                className="w-full bg-bg-panel border border-bg-border rounded-xl px-3 py-2 text-sm outline-none focus:border-accent resize-y text-content"
              />
              <div className="flex gap-2 mt-2 justify-end">
                <button
                  onClick={() => setEditing(false)}
                  className="px-3 py-1.5 text-xs rounded-lg border border-bg-border text-content-dim hover:bg-bg-hover"
                >
                  Cancel
                </button>
                <button
                  onClick={saveEdit}
                  className="px-3 py-1.5 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover font-medium"
                >
                  Save &amp; resubmit
                </button>
              </div>
            </div>
          ) : (
            <div className="bg-accent-muted border border-accent/25 rounded-2xl px-4 py-2.5 text-[0.9375rem] leading-relaxed whitespace-pre-wrap break-words">
              {text}
            </div>
          )
        ) : (
          <div className={`prose-nexus min-w-0 w-full ${isStreaming ? 'streaming-cursor' : ''}`}>
            {text ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                {text}
              </ReactMarkdown>
            ) : isStreaming ? (
              <span className="text-content-dim">Thinking…</span>
            ) : null}
          </div>
        )}

        {!isUser && <Sources citations={m.citations} />}
        {!isStreaming && !editing && (text || images.length > 0) && (
          <div className={`msg-actions flex items-center gap-0.5 mt-1.5 ${isUser ? 'flex-row-reverse' : ''}`}>
            {text && (
              <ActionBtn label="Copy" onClick={copy}>
                {copied ? (
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                ) : (
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="9" y="9" width="13" height="13" rx="2" />
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                  </svg>
                )}
              </ActionBtn>
            )}
            {isUser && onEdit && (
              <ActionBtn label="Edit & resubmit" onClick={() => { setDraft(text); setEditing(true) }}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                  <path d="M18.5 2.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                </svg>
              </ActionBtn>
            )}
            {!isUser && showRegenerate && (
              <ActionBtn label="Regenerate response" onClick={onRegenerate}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="23 4 23 10 17 10" />
                  <path d="M20.5 15a9 9 0 1 1-2-9.4L23 10" />
                </svg>
              </ActionBtn>
            )}
            {!isUser && text && (
              <ActionBtn label={speaking ? 'Stop reading' : 'Read aloud'} onClick={toggleSpeak} active={speaking}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                  {speaking ? <line x1="22" y1="9" x2="16" y2="15" /> : <path d="M15.5 8.5a5 5 0 0 1 0 7" />}
                </svg>
              </ActionBtn>
            )}
            {!isUser && (
              <span className="text-[11px] text-content-faint ml-1.5 select-none truncate">
                {m.modelName || model?.name || ''}
                {m.usage?.total_tokens ? ` · ${m.usage.total_tokens.toLocaleString()} tok` : ''}
                {m.cost ? ` · $${m.cost < 0.01 ? m.cost.toFixed(4) : m.cost.toFixed(3)}` : ''}
              </span>
            )}
          </div>
        )}
        {preview && (
          <ImageLightbox src={preview} alt="attachment" onClose={() => setPreview(null)} />
        )}
      </div>
    </div>
  )
}

export default memo(Message)

