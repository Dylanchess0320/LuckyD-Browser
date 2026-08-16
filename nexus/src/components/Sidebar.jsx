import React, { useEffect, useRef, useState } from 'react'
import { formatDateLabel, chatToMarkdown, download } from '../lib/util'
import { supabase } from '../lib/db'
import { AUTO_LABEL } from '../lib/auto'

function MenuItem({ icon, label, danger, onClick }) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      className={`w-full flex items-center gap-2.5 px-3 py-2 text-left text-[13px] transition-colors ${
        danger ? 'text-red-400 hover:bg-red-400/10' : 'text-content-dim hover:bg-bg-hover hover:text-content'
      }`}
    >
      {icon}
      {label}
    </button>
  )
}

const ICONS = {
  pin: (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 17v5" />
      <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16h14v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76z" />
    </svg>
  ),
  pencil: (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  ),
  download: (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  ),
  trash: (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  ),
}

export default function Sidebar({
  chats,
  activeChatId,
  onSelectChat,
  onNewChat,
  onDeleteChat,
  onRenameChat,
  onTogglePin,
  onOpenSettings,
  open,
  onClose,
}) {
  const [query, setQuery] = useState('')
  const [menuFor, setMenuFor] = useState(null)
  const [renaming, setRenaming] = useState(null)
  const [renameValue, setRenameValue] = useState('')

  useEffect(() => {
    if (!menuFor) return
    const close = () => setMenuFor(null)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [menuFor])

  const q = query.trim().toLowerCase()
  const filtered = q ? chats.filter((c) => c.title.toLowerCase().includes(q)) : chats
  const pinned = filtered.filter((c) => c.pinned)
  const rest = filtered.filter((c) => !c.pinned)

  const exportChat = (chat) => {
    const safe = chat.title.replace(/[^\w\- ]+/g, '').trim() || 'chat'
    download(`${safe}.md`, chatToMarkdown(chat), 'text/markdown')
  }

  const commitRename = (id) => {
    const t = renameValue.trim()
    if (t) onRenameChat(id, t)
    setRenaming(null)
  }

  const renderRow = (chat) => {
    const active = chat.id === activeChatId
    const shortModel = chat.model ? (AUTO_LABEL[chat.model] || chat.model.split('/').pop().slice(0, 26)) : ''
    return (
      <div
        key={chat.id}
        onClick={() => {
          onSelectChat(chat.id)
          onClose()
        }}
        className={`group relative flex items-center gap-2 mx-2 px-3 py-2.5 rounded-lg cursor-pointer transition-colors ${
          active ? 'bg-bg-hover' : 'hover:bg-bg-hover/60'
        }`}
      >
        {renaming === chat.id ? (
          <input
            autoFocus
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename(chat.id)
              if (e.key === 'Escape') setRenaming(null)
            }}
            onBlur={() => commitRename(chat.id)}
            className="flex-1 min-w-0 bg-bg border border-accent rounded-md px-1.5 py-1 text-sm outline-none text-content"
          />
        ) : (
          <>
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate flex items-center gap-1.5">
                {!!chat.pinned && <span className="text-accent shrink-0">{ICONS.pin}</span>}
                <span className="truncate">{chat.title}</span>
              </div>
              <div className="text-[11px] text-content-faint truncate">
                {formatDateLabel(chat.updated_at)}
                {shortModel ? ` · ${shortModel}` : ''}
              </div>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation()
                setMenuFor(menuFor === chat.id ? null : chat.id)
              }}
              className="opacity-0 group-hover:opacity-100 [@media(pointer:coarse)]:opacity-70 p-1 rounded-md text-content-faint hover:text-content shrink-0 transition-opacity"
              aria-label="Chat options"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                <circle cx="12" cy="5" r="1.8" />
                <circle cx="12" cy="12" r="1.8" />
                <circle cx="12" cy="19" r="1.8" />
              </svg>
            </button>
          </>
        )}
        {menuFor === chat.id && (
          <div className="absolute right-2 top-9 z-30 w-44 bg-bg-panel border border-bg-border rounded-xl shadow-2xl py-1 fade-in">
            <MenuItem
              icon={ICONS.pin}
              label={chat.pinned ? 'Unpin' : 'Pin to top'}
              onClick={() => {
                setMenuFor(null)
                onTogglePin(chat.id)
              }}
            />
            <MenuItem
              icon={ICONS.pencil}
              label="Rename"
              onClick={() => {
                setMenuFor(null)
                setRenaming(chat.id)
                setRenameValue(chat.title)
              }}
            />
            <MenuItem
              icon={ICONS.download}
              label="Export (.md)"
              onClick={() => {
                setMenuFor(null)
                exportChat(chat)
              }}
            />
            <MenuItem
              icon={ICONS.trash}
              label="Delete"
              danger
              onClick={() => {
                setMenuFor(null)
                onDeleteChat(chat.id)
              }}
            />
          </div>
        )}
      </div>
    )
  }

  return (
    <>
      {/* Mobile backdrop */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/55 md:hidden fade-in"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside
        className={`fixed md:static inset-y-0 left-0 z-50 w-[280px] shrink-0 bg-bg-panel border-r border-bg-border transform transition-transform duration-200 ease-out md:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
        aria-label="Chat history"
      >
        <div
          className="flex flex-col h-full"
          style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.5rem)' }}
        >
          {/* Brand + new chat */}
          <div className="flex items-center justify-between px-4 pt-3 pb-2">
            <div className="flex items-center gap-2 select-none">
              <span className="text-lg text-accent">◈</span>
              <span className="font-semibold tracking-tight">Nexus</span>
              {supabase && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent-muted text-accent font-bold tracking-wide">
                  SYNC
                </span>
              )}
            </div>
            <button
              onClick={onClose}
              className="md:hidden p-1.5 rounded-lg text-content-faint hover:text-content hover:bg-bg-hover"
              aria-label="Close sidebar"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
          <div className="px-3 pb-2">
            <button
              onClick={() => {
                onNewChat()
                onClose()
              }}
              className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl bg-accent text-white text-sm font-medium hover:bg-accent-hover transition-colors"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              New chat
            </button>
          </div>
          <div className="px-3 pb-2">
            <div className="relative">
              <svg
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-content-faint pointer-events-none"
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search chats"
                className="w-full bg-bg border border-bg-border rounded-lg pl-8 pr-3 py-2 text-sm outline-none focus:border-accent placeholder:text-content-faint text-content"
              />
            </div>
          </div>

          {/* Chat list */}
          <div className="flex-1 overflow-y-auto py-1">
            {pinned.length > 0 && (
              <>
                <div className="px-5 pt-1.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-content-faint">
                  Pinned
                </div>
                {pinned.map(renderRow)}
                <div className="px-5 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-content-faint">
                  Recent
                </div>
              </>
            )}
            {rest.map(renderRow)}
            {filtered.length === 0 && (
              <div className="px-5 py-8 text-sm text-content-faint text-center">
                {q ? 'No chats match your search' : 'No chats yet — start one below'}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="border-t border-bg-border p-2">
            <button
              onClick={() => {
                onOpenSettings()
                onClose()
              }}
              className="w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm text-content-dim hover:bg-bg-hover hover:text-content transition-colors"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
              Settings
            </button>
            <div className="pb-safe" />
          </div>
        </div>
      </aside>
    </>
  )
}
