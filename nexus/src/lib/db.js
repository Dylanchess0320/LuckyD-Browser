// Persistence layer — Supabase when configured, localStorage fallback
import { createClient } from '@supabase/supabase-js'
import { safeJson, uid } from './util'
import { toast } from './toast'

const URL = import.meta.env.VITE_SUPABASE_URL
const KEY = import.meta.env.VITE_SUPABASE_ANON_KEY

export const supabase = URL && KEY && URL.startsWith('http') ? createClient(URL, KEY) : null

// Anonymous device id — only used for the localStorage fallback below, and
// as the source rows for one-time migration into a signed-in account.
export function getUserId() {
  let id = localStorage.getItem('nexus_uid')
  if (!id) {
    id = uid()
    localStorage.setItem('nexus_uid', id)
  }
  return id
}

// The signed-in Supabase user, if any. Sync only happens when this is set —
// signed-out users work fully offline against localStorage (see below),
// same as if Supabase weren't configured at all.
let authUserId = null
export function setAuthUserId(id) {
  authUserId = id || null
}
const syncActive = () => !!supabase && !!authUserId

// ---------- localStorage fallback ----------
const LS_KEY = 'nexus_chats'

const lsLoad = () => {
  const rows = safeJson(localStorage.getItem(LS_KEY), [])
  return Array.isArray(rows) ? rows : []
}

let quotaWarned = false
const lsSave = (chats) => {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(chats))
  } catch {
    if (!quotaWarned) {
      quotaWarned = true
      toast.error(
        'Local storage is full — recent changes may not be saved. Export chats in Settings → Data.',
        { duration: 6000 },
      )
    }
  }
}

const byUpdated = (a, b) => b.updated_at - a.updated_at

// Columns that exist in Supabase (see supabase/schema.sql)
const COLUMNS = ['title', 'model', 'messages', 'pinned']

// ---------- Public API (same shape for either backend) ----------

export async function listChats() {
  if (!syncActive()) return lsLoad().sort(byUpdated)
  const { data, error } = await supabase
    .from('chats')
    .select('*')
    .eq('user_id', authUserId)
    .order('updated_at', { ascending: false })
  if (error) {
    console.warn('Supabase listChats failed, using localStorage:', error.message)
    return lsLoad().sort(byUpdated)
  }
  return data.map((c) => ({
    ...c,
    created_at: new Date(c.created_at).getTime(),
    updated_at: new Date(c.updated_at).getTime(),
  }))
}

export async function createChat(chat) {
  const row = {
    id: chat.id || uid(),
    title: chat.title || 'New chat',
    model: chat.model,
    messages: chat.messages || [],
    pinned: !!chat.pinned,
    created_at: chat.created_at || Date.now(),
    updated_at: Date.now(),
  }
  if (!syncActive()) {
    lsSave([...lsLoad(), row])
    return row
  }
  const { error } = await supabase.from('chats').insert({
    id: row.id,
    user_id: authUserId,
    title: row.title,
    model: row.model,
    messages: row.messages,
    pinned: row.pinned,
  })
  if (error) {
    console.warn('Supabase createChat failed, using localStorage:', error.message)
    lsSave([...lsLoad(), row])
  }
  return row
}

export async function updateChat(id, patch) {
  const updated = { ...patch, updated_at: Date.now() }
  if (!syncActive()) {
    const chats = lsLoad()
    const i = chats.findIndex((c) => c.id === id)
    if (i >= 0) {
      chats[i] = { ...chats[i], ...updated }
      lsSave(chats)
    }
    return
  }
  const dbPatch = { updated_at: new Date().toISOString() }
  for (const k of COLUMNS) if (k in patch) dbPatch[k] = patch[k]
  const { error } = await supabase.from('chats').update(dbPatch).eq('id', id)
  if (error) {
    console.warn('Supabase updateChat failed, using localStorage:', error.message)
    const chats = lsLoad()
    const i = chats.findIndex((c) => c.id === id)
    if (i >= 0) {
      chats[i] = { ...chats[i], ...updated }
      lsSave(chats)
    }
  }
}

export async function deleteChat(id) {
  if (!syncActive()) {
    lsSave(lsLoad().filter((c) => c.id !== id))
    return
  }
  const { error } = await supabase.from('chats').delete().eq('id', id)
  if (error) {
    console.warn('Supabase deleteChat failed:', error.message)
    lsSave(lsLoad().filter((c) => c.id !== id))
  }
}

export async function deleteAllChats() {
  if (!syncActive()) {
    lsSave([])
    return
  }
  const { error } = await supabase.from('chats').delete().eq('user_id', authUserId)
  if (error) {
    console.warn('Supabase deleteAllChats failed:', error.message)
    lsSave([])
  }
}

// One-time move of chats sitting in this device's localStorage (written
// before sign-in, or while signed out) up into the signed-in account.
// Local rows are left in place afterward — this is a copy, not a cut, so
// nothing is lost if something goes wrong mid-way.
export async function migrateLocalChatsToAccount() {
  if (!syncActive()) return 0
  const local = lsLoad()
  if (!local.length) return 0
  let moved = 0
  for (const chat of local) {
    const { error } = await supabase.from('chats').insert({
      id: chat.id,
      user_id: authUserId,
      title: chat.title,
      model: chat.model,
      messages: chat.messages,
      pinned: !!chat.pinned,
    })
    // Ignore duplicate-id conflicts (already migrated on a previous run);
    // surface anything else.
    if (!error) moved++
    else if (error.code !== '23505') console.warn('migrateLocalChatsToAccount:', error.message)
  }
  return moved
}

export async function exportAllChats() {
  const chats = await listChats()
  return JSON.stringify(
    { app: 'nexus', version: 1, exported_at: new Date().toISOString(), chats },
    null,
    2,
  )
}

