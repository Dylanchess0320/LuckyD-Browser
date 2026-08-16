import { useEffect, useState } from 'react'
import { subscribeToasts } from '../lib/toast'

export default function Toasts() {
  const [items, setItems] = useState([])

  useEffect(
    () =>
      subscribeToasts((t) => {
        setItems((prev) => [...prev, t])
        if (t.duration !== Infinity) {
          setTimeout(
            () => setItems((prev) => prev.filter((x) => x.id !== t.id)),
            t.duration || 3200,
          )
        }
      }),
    [],
  )

  const dismiss = (id) => setItems((prev) => prev.filter((x) => x.id !== id))

  if (!items.length) return null

  return (
    <div className="fixed bottom-24 sm:bottom-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-2 px-4 w-full max-w-md pointer-events-none">
      {items.map((t) => (
        <div
          key={t.id}
          className={`toast pointer-events-auto flex items-center gap-3 pl-4 pr-2 py-2.5 rounded-xl border shadow-2xl text-sm max-w-full ${
            t.type === 'error'
              ? 'bg-red-950/95 border-red-800/50 text-red-200'
              : 'bg-bg-panel border-bg-border text-content'
          }`}
        >
          <span className="min-w-0 leading-snug">{t.message}</span>
          {t.action && (
            <button
              onClick={() => {
                t.action.fn?.()
                dismiss(t.id)
              }}
              className="shrink-0 font-semibold text-accent hover:text-accent-hover px-2 py-1"
            >
              {t.action.label}
            </button>
          )}
          <button
            onClick={() => dismiss(t.id)}
            className="shrink-0 p-1.5 text-content-faint hover:text-content rounded-lg"
            aria-label="Dismiss"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      ))}
    </div>
  )
}
