import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useSettings, saveSettings } from '../lib/settings'
import { AUTO_FREE_ID, AUTO_PAID_ID } from '../lib/auto'

const POPULAR = ['openai', 'anthropic', 'google', 'x-ai', 'meta-llama', 'deepseek', 'mistralai', 'qwen']

const fmtCtx = (n) =>
  !n ? '' : n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : `${Math.round(n / 1000)}k`

export default function ModelPicker({ models, value, onChange }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef(null)
  const settings = useSettings()
  const favorites = settings.favoriteModels || []
  const recents = settings.recentModels || []

  const current = models.find((m) => m.id === value) ||
    (value === AUTO_FREE_ID
      ? { id: AUTO_FREE_ID, name: 'Auto (Free)' }
      : value === AUTO_PAID_ID
        ? { id: AUTO_PAID_ID, name: 'Auto (Paid)' }
        : null)

  useEffect(() => {
    if (!open) return
    const close = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    const esc = (e) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', close)
    window.addEventListener('touchstart', close)
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('touchstart', close)
      window.removeEventListener('keydown', esc)
    }
  }, [open])

  const autoEntries = useMemo(() => {
    const hasPaid = models.some((m) => !m.free)
    const list = [
      { id: AUTO_FREE_ID, name: 'Auto (Free)', description: 'Picks the best free model for each message, and switches models automatically if one is rate-limited.' },
    ]
    if (hasPaid) {
      list.push({ id: AUTO_PAID_ID, name: 'Auto (Paid)', description: 'Picks the strongest paid model for each message. Separate from Auto (Free) — never mixes in free models.' })
    }
    return list
  }, [models])

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (q) {
      const autoHits = autoEntries.filter((m) => m.name.toLowerCase().includes(q) || m.id.includes(q))
      const hits = models.filter(
        (m) =>
          m.name.toLowerCase().includes(q) ||
          m.id.toLowerCase().includes(q) ||
          m.provider.toLowerCase().includes(q),
      )
      const g = []
      if (autoHits.length) g.push(['Auto', autoHits])
      if (hits.length) g.push(['Results', hits.slice(0, 80)])
      return g
    }
    const g = [['Auto', autoEntries]]
    const pinnedIds = new Set()
    const favs = favorites.map((id) => models.find((m) => m.id === id)).filter(Boolean)
    if (favs.length) {
      g.push(['Favorites', favs])
      favs.forEach((m) => pinnedIds.add(m.id))
    }
    const rec = recents
      .map((id) => models.find((m) => m.id === id))
      .filter(Boolean)
      .filter((m) => !pinnedIds.has(m.id))
      .slice(0, 5)
    if (rec.length) {
      g.push(['Recent', rec])
      rec.forEach((m) => pinnedIds.add(m.id))
    }
    // Split the rest into FREE and PAID (the user asked free be separated from paid)
    const free = []
    const paid = []
    for (const m of models) {
      if (pinnedIds.has(m.id)) continue
      ;(m.free ? free : paid).push(m)
    }
    free.sort((a, b) => a.provider.localeCompare(b.provider) || a.name.localeCompare(b.name))
    if (free.length) g.push([`Free models (${free.length})`, free.slice(0, 60)])
    const popular = []
    const rest = []
    for (const m of paid) {
      ;(POPULAR.includes(m.provider) ? popular : rest).push(m)
    }
    if (popular.length) g.push(['Popular providers', popular.slice(0, 50)])
    if (rest.length) g.push([`All paid models (${rest.length})`, rest.slice(0, 200)])
    return g
  }, [models, query, favorites, recents, autoEntries])

  const pick = (id) => {
    onChange(id)
    saveSettings({ recentModels: [id, ...recents.filter((r) => r !== id)].slice(0, 8) })
    setOpen(false)
    setQuery('')
  }

  const toggleFav = (e, id) => {
    e.stopPropagation()
    saveSettings({
      favoriteModels: favorites.includes(id)
        ? favorites.filter((f) => f !== id)
        : [...favorites, id],
    })
  }

  const renderAutoRow = (m) => (
    <button
      key={m.id}
      onClick={() => pick(m.id)}
      className={`w-full flex items-start gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-bg-hover ${
        m.id === value ? 'bg-accent-muted' : ''
      }`}
    >
      <span className="text-base leading-none mt-0.5 shrink-0">✨</span>
      <div className="min-w-0">
        <div className="text-sm font-medium">{m.name}</div>
        <div className="text-[11px] text-content-faint mt-0.5 leading-snug">{m.description}</div>
      </div>
    </button>
  )

  const renderRow = (m) => (
    <button
      key={m.id}
      onClick={() => pick(m.id)}
      className={`w-full flex items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-bg-hover ${
        m.id === value ? 'bg-accent-muted' : ''
      }`}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm truncate">{m.name}</span>
          {m.vision && (
            <span title="Can see images" className="text-[10px] leading-none opacity-80">
              👁
            </span>
          )}
          {m.free && (
            <span className="text-[9px] font-bold px-1 py-px rounded bg-emerald-500/15 text-emerald-400 leading-tight">
              FREE
            </span>
          )}
        </div>
        <div className="text-[11px] text-content-faint truncate">
          {m.provider}
          {m.context ? ` · ${fmtCtx(m.context)} ctx` : ''}
        </div>
      </div>
      <span
        role="button"
        tabIndex={0}
        aria-label={favorites.includes(m.id) ? 'Unfavorite' : 'Favorite'}
        onClick={(e) => toggleFav(e, m.id)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') toggleFav(e, m.id)
        }}
        className={`p-1.5 shrink-0 rounded transition-colors ${
          favorites.includes(m.id) ? 'text-amber-400' : 'text-content-faint hover:text-content'
        }`}
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill={favorites.includes(m.id) ? 'currentColor' : 'none'}
          stroke="currentColor"
          strokeWidth="2"
        >
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
        </svg>
      </span>
    </button>
  )

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg hover:bg-bg-hover transition-colors max-w-[62vw] sm:max-w-[320px]"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="text-sm font-medium truncate">
          {current?.name || (value ? value.split('/').pop() : 'Select model')}
        </span>
        <svg
          className={`shrink-0 text-content-faint transition-transform ${open ? 'rotate-180' : ''}`}
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1.5 z-50 w-[min(400px,calc(100vw-1.5rem))] bg-bg-panel border border-bg-border rounded-xl shadow-2xl overflow-hidden fade-in">
          <div className="p-2 border-b border-bg-border">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Search ${models.length} models…`}
              className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm outline-none focus:border-accent placeholder:text-content-faint text-content"
            />
          </div>
          <div className="max-h-[55vh] overflow-y-auto py-1" role="listbox">
            {groups.length === 0 && (
              <div className="px-4 py-6 text-sm text-content-faint text-center">
                No models match “{query}”
              </div>
            )}
            {groups.map(([label, items]) => (
              <div key={label}>
                <div className="px-3 pt-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-content-faint">
                  {label}
                </div>
                {items.map(label === 'Auto' ? renderAutoRow : renderRow)}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
