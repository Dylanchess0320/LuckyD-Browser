// Email OTP auth on top of Supabase. No-ops cleanly when Supabase isn't
// configured (db.js falls back to localStorage in that case).
import { supabase } from './db'

let currentUser = null
const listeners = new Set()

function notify() {
  for (const cb of listeners) cb(currentUser)
}

// Populate currentUser on load and keep it fresh as the session changes
// (sign-in after code verification, sign-out, token refresh).
let initPromise = null
export function initAuth() {
  if (!supabase) return Promise.resolve(null)
  if (initPromise) return initPromise
  initPromise = supabase.auth.getSession().then(({ data }) => {
    currentUser = data?.session?.user || null
    notify()
    return currentUser
  })
  supabase.auth.onAuthStateChange((_event, session) => {
    currentUser = session?.user || null
    notify()
  })
  return initPromise
}

export function getCurrentUser() {
  return currentUser
}

// Subscribe to auth state; returns an unsubscribe function.
export function onAuthChange(cb) {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

// Sends a 6-digit code by email. No emailRedirectTo — this app runs inside
// a Capacitor WebView (origin https://localhost), which isn't a reachable
// redirect target, so we use the code-entry flow instead of a clickable link.
export async function sendOtp(email) {
  if (!supabase) throw new Error('Sync is not configured for this build')
  const { error } = await supabase.auth.signInWithOtp({
    email: email.trim(),
  })
  if (error) throw error
}

// Completes sign-in with the 6-digit code from that email.
export async function verifyOtp(email, code) {
  if (!supabase) throw new Error('Sync is not configured for this build')
  const { data, error } = await supabase.auth.verifyOtp({
    email: email.trim(),
    token: code.trim(),
    type: 'email',
  })
  if (error) throw error
  return data.user
}

export async function signOut() {
  if (!supabase) return
  await supabase.auth.signOut()
}
