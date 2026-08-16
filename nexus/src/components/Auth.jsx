import React, { useState } from 'react'
import { sendOtp, verifyOtp, signOut } from '../lib/auth'
import { toast } from '../lib/toast'

// Compact sign-in control for the Settings panel. Signed-out users keep
// using the app normally (localStorage) — this is opt-in sync, not a
// login wall.
export default function Auth({ user }) {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)

  if (user) {
    return (
      <div className="flex items-center justify-between gap-3 px-1">
        <div className="min-w-0">
          <div className="text-sm text-content truncate">{user.email}</div>
          <div className="text-[11px] text-content-faint">Synced to your account</div>
        </div>
        <button
          onClick={async () => {
            await signOut()
            toast('Signed out — chats on this device switch back to local-only')
          }}
          className="shrink-0 text-[13px] px-3 py-1.5 rounded-lg text-content-dim hover:bg-bg-hover hover:text-content transition-colors"
        >
          Sign out
        </button>
      </div>
    )
  }

  if (sent) {
    return (
      <form
        onSubmit={async (e) => {
          e.preventDefault()
          if (!code.trim() || busy) return
          setBusy(true)
          try {
            await verifyOtp(email, code)
            toast.success('Signed in')
          } catch (err) {
            toast.error(err.message || 'Could not verify that code')
          } finally {
            setBusy(false)
          }
        }}
        className="flex flex-col gap-2 px-1"
      >
        <div className="text-[13px] text-content-dim">
          Enter the 8-digit code sent to <span className="text-content">{email}</span>.
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            inputMode="numeric"
            autoFocus
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="12345678"
            className="flex-1 min-w-0 bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm tracking-widest outline-none focus:border-accent placeholder:text-content-faint text-content"
          />
          <button
            type="submit"
            disabled={busy}
            className="shrink-0 px-3 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent-hover transition-colors disabled:opacity-60"
          >
            {busy ? 'Verifying…' : 'Verify'}
          </button>
        </div>
        <button
          type="button"
          onClick={() => { setSent(false); setCode('') }}
          className="text-left text-accent hover:underline text-[13px]"
        >
          Use a different email
        </button>
      </form>
    )
  }

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault()
        if (!email.trim() || busy) return
        setBusy(true)
        try {
          await sendOtp(email)
          setSent(true)
        } catch (err) {
          toast.error(err.message || 'Could not send sign-in code')
        } finally {
          setBusy(false)
        }
      }}
      className="flex flex-col gap-2 px-1"
    >
      <div className="text-[13px] text-content-dim">
        Sign in to sync chats across devices.
      </div>
      <div className="flex gap-2">
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          className="flex-1 min-w-0 bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm outline-none focus:border-accent placeholder:text-content-faint text-content"
        />
        <button
          type="submit"
          disabled={busy}
          className="shrink-0 px-3 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent-hover transition-colors disabled:opacity-60"
        >
          {busy ? 'Sending…' : 'Send code'}
        </button>
      </div>
    </form>
  )
}
