import React, { useEffect, useRef, useState } from 'react'
import Message, { textOf } from './Message'
import Composer from './Composer'
import ModelPicker from './ModelPicker'

// A big pool of funnier starter prompts — 4 are randomly picked each time
// the home screen mounts (app open / page refresh), so it doesn't go stale
// after the first week like a fixed set of 4 would.
const SUGGESTIONS = [
  { icon: '🦠', label: 'Roast', prompt: 'Roast my daily routine like a disappointed but loving grandma.' },
  { icon: '🕳️', label: 'Explain', prompt: 'Explain how black holes work, but every sentence has to sound like a conspiracy theory.' },
  { icon: '🧀', label: 'Code', prompt: 'Write a Python function that checks if a string is a palindrome, and name every variable after a type of cheese.' },
  { icon: '🗺️', label: 'Plan', prompt: 'Plan a 3-day trip to Tokyo assuming my only skill is complaining about airport food.' },
  { icon: '🥣', label: 'Debate', prompt: 'Argue passionately that cereal is a soup, then argue the opposite just as passionately.' },
  { icon: '⏰', label: 'Write', prompt: 'Write a break-up letter to my alarm clock.' },
  { icon: '🛡️', label: 'Roleplay', prompt: 'Pretend to be a medieval knight discovering a smartphone for the first time.' },
  { icon: '👻', label: 'Explain', prompt: "Explain quantum entanglement as if you're two exes texting about who ghosted who first." },
  { icon: '🦝', label: 'Write', prompt: 'Write a Yelp review for my own kitchen, from the perspective of a very judgmental raccoon.' },
  { icon: '📉', label: 'Advise', prompt: 'Give me terrible life advice, but say it with total unwavering confidence.' },
  { icon: '🃏', label: 'Code', prompt: "Write a JavaScript function to shuffle an array, and comment every line like it's a heist movie." },
  { icon: '🍄', label: 'Explain', prompt: 'Explain how vaccines work using only Mario Kart analogies.' },
  { icon: '🎸', label: 'Write', prompt: 'Write overly dramatic power-ballad lyrics about losing my phone charger.' },
  { icon: '🐱', label: 'Roleplay', prompt: 'You are my cat, and you have just learned to speak. Tell me what you really think of me.' },
  { icon: '🌪️', label: 'Plan', prompt: 'Plan the most chaotic possible birthday party for someone who insists they "don\'t want a fuss."' },
  { icon: '🐶', label: 'Explain', prompt: 'Explain how the internet works to a golden retriever who just wants to know why the squirrel video keeps buffering.' },
]

// Fisher–Yates shuffle — used both for the plain random draw and for
// shuffling within a matched/unmatched partition so ties don't always
// resolve in array order.
function shuffle(arr) {
  const pool = [...arr]
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[pool[i], pool[j]] = [pool[j], pool[i]]
  }
  return pool
}

function sampleSuggestions(n) {
  return shuffle(SUGGESTIONS).slice(0, n)
}

// ---- personalization: bias the draw toward what the user actually asks ----
// Each suggestion's `label` doubles as its topic bucket (Code, Explain,
// Write, Plan, Debate, Roleplay, Advise, Roast). These keyword sets are
// intentionally loose/cheap — this only needs to be "probably relevant",
// not a precise classifier, since it's just steering which 4 of 16 jokes
// show up.
const CATEGORY_KEYWORDS = {
  code: /\b(code|function|debug|python|javascript|typescript|api|script|bug|program|regex|sql|html|css)\b/gi,
  explain: /\b(explain|how does|how do|what is|why does|understand)\b/gi,
  write: /\b(write|draft|essay|letter|email|story|poem|lyrics)\b/gi,
  plan: /\b(plan|itinerary|trip|travel|schedule|organize|vacation)\b/gi,
  debate: /\b(argue|debate|pros and cons|convince|persuade|versus)\b/gi,
  roleplay: /\b(pretend|roleplay|role-play|act as|you are a|imagine you)\b/gi,
  advise: /\b(advice|should i|recommend|suggest|help me decide)\b/gi,
  roast: /\b(roast|make fun of|insult|burn)\b/gi,
}

// Pull recent user-message text across chats (most-recent chats first, since
// `chats` is already sorted that way) — capped so this stays cheap even with
// a long chat history.
function recentUserText(chats, maxChats = 10, maxCharsPerChat = 600, maxTotalChars = 4000) {
  let out = ''
  for (const c of (chats || []).slice(0, maxChats)) {
    const userMsgs = (c.messages || []).filter((m) => m.role === 'user').slice(-6)
    for (const m of userMsgs) {
      const t = textOf(m)
      if (!t) continue
      out += ' ' + t.slice(0, maxCharsPerChat)
      if (out.length > maxTotalChars) return out.slice(0, maxTotalChars)
    }
  }
  return out
}

// Pick 4 suggestions, biased toward whatever topics show up most in the
// user's own history. Falls back to a plain random draw when there's no
// history yet, or nothing in it matches a known category — a brand-new user
// still gets a full, varied home screen instead of an empty/narrow one.
function pickSuggestions(chats) {
  const text = recentUserText(chats)
  if (!text.trim()) return sampleSuggestions(4)
  const ranked = Object.entries(CATEGORY_KEYWORDS)
    .map(([cat, re]) => [cat, (text.match(re) || []).length])
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([cat]) => cat)
  if (!ranked.length) return sampleSuggestions(4)
  const topCats = new Set(ranked.slice(0, 3))
  const matched = shuffle(SUGGESTIONS.filter((s) => topCats.has(s.label.toLowerCase())))
  const rest = shuffle(SUGGESTIONS.filter((s) => !topCats.has(s.label.toLowerCase())))
  return [...matched, ...rest].slice(0, 4)
}

export default function ChatView({
  chat,
  chats,
  models,
  isStreaming,
  draft,
  setDraft,
  onSend,
  onStop,
  onRegenerate,
  onEditMessage,
  onSelectModel,
  currentModelId,
  onOpenSidebar,
  onOpenSettings,
  webSearch,
  onToggleWebSearch,
  imageMode,
  onToggleImageMode,
  onOpenVoice,
}) {
  const scrollRef = useRef(null)
  const pinnedRef = useRef(true) // user is at (near) the bottom
  const [showJump, setShowJump] = useState(false)
  // Picked once when the home screen mounts (app open / refresh) — stays
  // stable for the rest of the session so it doesn't shuffle mid-scroll.
  // Chat history usually hasn't loaded from storage yet at this exact
  // instant, so this first pass is a plain random draw; the effect below
  // upgrades it to a personalized draw the moment history is available.
  const [suggestions, setSuggestions] = useState(() => sampleSuggestions(4))
  const personalizedRef = useRef(false)

  // Re-pick once, biased toward the user's own history, as soon as chats
  // have loaded — guarded so later chat/message updates (e.g. streaming
  // tokens ticking chats on every render) don't keep reshuffling the home
  // screen out from under the user.
  useEffect(() => {
    if (personalizedRef.current) return
    if (!chats || !chats.length) return
    personalizedRef.current = true
    setSuggestions(pickSuggestions(chats))
  }, [chats])

  const scrollToBottom = (smooth) => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }

  // Auto-scroll while streaming only if the user hasn't scrolled up to read
  useEffect(() => {
    if (pinnedRef.current) scrollToBottom(false)
  }, [chat?.messages, isStreaming])

  // Snap to bottom when switching chats
  useEffect(() => {
    pinnedRef.current = true
    setShowJump(false)
    scrollToBottom(false)
  }, [chat?.id])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight
    pinnedRef.current = gap < 80
    setShowJump(gap > 240)
  }

  const model = models.find((m) => m.id === currentModelId)
  const messages = chat?.messages || []
  const lastIdx = messages.length - 1

  return (
    <div className="relative flex flex-col h-full min-w-0">
      {/* Header */}
      <div className="flex items-center gap-1 px-2 sm:px-4 py-2 border-b border-bg-border shrink-0 pt-safe">
        <button
          onClick={onOpenSidebar}
          className="md:hidden p-2 -ml-1 rounded-lg text-content-dim hover:bg-bg-hover hover:text-content"
          aria-label="Open chats"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        <ModelPicker models={models} value={currentModelId} onChange={onSelectModel} compact />
        <div className="flex-1" />
        <button
          onClick={onOpenSettings}
          className="md:hidden p-2 rounded-lg text-content-dim hover:bg-bg-hover hover:text-content"
          aria-label="Settings"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>

      {/* Messages / hero */}
      <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto overscroll-contain">
        {messages.length === 0 ? (
          <div className="min-h-full flex flex-col items-center justify-center px-4 py-10">
            <div className="w-14 h-14 rounded-2xl bg-accent-muted border border-accent/30 flex items-center justify-center text-3xl mb-4 select-none">
              ◈
            </div>
            <h1 className="text-xl font-semibold mb-1">How can I help?</h1>
            <p className="text-sm text-content-dim mb-8 text-center">
              One app for every AI — {models.length || '…'} models, one conversation.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full max-w-lg">
              {suggestions.map((s) => (
                <button key={s.prompt} className="chip" onClick={() => onSend(s.prompt, [])}>
                  <span className="mr-1.5">{s.icon}</span>
                  <span className="font-medium text-content">{s.label}</span>
                  <span className="block text-xs text-content-faint mt-1 leading-snug">{s.prompt}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="max-w-chat mx-auto py-3">
            {messages.map((m, i) => (
              <Message
                key={m.id || i}
                message={m}
                isUser={m.role === 'user'}
                isStreaming={isStreaming && i === lastIdx && m.role === 'assistant'}
                model={model}
                showRegenerate={!isStreaming && i === lastIdx && m.role === 'assistant'}
                onRegenerate={onRegenerate}
                onEdit={m.role === 'user' && !isStreaming ? (text) => onEditMessage(i, text) : undefined}
              />
            ))}
            <div className="h-2" />
          </div>
        )}
      </div>

      {/* Scroll-to-bottom */}
      {showJump && (
        <button
          onClick={() => {
            pinnedRef.current = true
            setShowJump(false)
            scrollToBottom(true)
          }}
          className="absolute left-1/2 -translate-x-1/2 bottom-32 z-10 p-2.5 rounded-full bg-bg-panel border border-bg-border shadow-xl text-content-dim hover:text-content fade-in"
          aria-label="Scroll to latest"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      )}

      <Composer
        value={draft}
        onChange={setDraft}
        onSend={onSend}
        onStop={onStop}
        isStreaming={isStreaming}
        vision={model?.vision ?? true}
        webSearch={webSearch}
        onToggleWebSearch={onToggleWebSearch}
        imageMode={imageMode}
        onToggleImageMode={onToggleImageMode}
        onOpenVoice={onOpenVoice}
      />
    </div>
  )
}
