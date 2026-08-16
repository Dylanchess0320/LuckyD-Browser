import React, { useRef, useState } from 'react'
import { useSettings, saveSettings } from '../lib/settings'
import { hasOpenRouter, storedOpenRouterKey, setOpenRouterKey } from '../lib/openrouter'
import { hasGemini, storedGeminiKey, setGeminiKey } from '../lib/gemini'
import { listVoices, speak } from '../lib/speech'
import { supabase } from '../lib/db'
import { toast } from '../lib/toast'
import Auth from './Auth'

function Section({ title, description, children }) {
  return (
    <div className="px-5 py-4 border-b border-bg-border">
      <h3 className={`text-[11px] font-semibold uppercase tracking-wider text-content-faint ${description ? 'mb-1' : 'mb-3'}`}>
        {title}
      </h3>
      {description && <p className="text-xs text-content-faint mb-3 leading-relaxed">{description}</p>}
      {children}
    </div>
  )
}

function Row({ label, hint, children }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <div className="min-w-0">
        <div className="text-sm">{label}</div>
        {hint && <div className="text-xs text-content-faint mt-0.5">{hint}</div>}
      </div>
      {children}
    </div>
  )
}

// A small label above a control group within a Section, for sections that
// bundle more than one distinct setting (e.g. Model Behavior).
function FieldLabel({ children, hint }) {
  return (
    <div className="mb-1.5">
      <div className="text-sm font-medium">{children}</div>
      {hint && <div className="text-xs text-content-faint mt-0.5">{hint}</div>}
    </div>
  )
}

export default function Settings({ open, onClose, onExport, onImport, onDeleteAll, authUser }) {
  const settings = useSettings()
  const fileRef = useRef(null)
  const [confirmWipe, setConfirmWipe] = useState(false)
  const [geminiConfigured, setGeminiConfigured] = useState(hasGemini)
  const [geminiInput, setGeminiInput] = useState('')
  const [orConfigured, setOrConfigured] = useState(hasOpenRouter)
  const [orInput, setOrInput] = useState('')
  const [voices, setVoices] = useState([])

  // Load available TTS voices when the panel opens.
  // NOTE: must be declared before the `if (!open) return null` early return
  // below — otherwise the hook count changes between renders and React throws.
  React.useEffect(() => {
    if (!open) return
    let live = true
    listVoices().then((v) => { if (live) setVoices(v) }).catch(() => {})
    return () => { live = false }
  }, [open])

  if (!open) return null

  const saveGeminiKey = () => {
    const hadKey = geminiInput.trim()
    setGeminiKey(geminiInput)
    setGeminiInput('')
    setGeminiConfigured(hasGemini())
    toast(hadKey ? 'Gemini key saved. Reload to load Gemini models.' : 'Gemini key removed.', {
      duration: 5000,
      action: { label: 'Reload now', fn: () => window.location.reload() },
    })
  }

  const saveOpenRouterKey = () => {
    const hadKey = orInput.trim()
    setOpenRouterKey(orInput)
    setOrInput('')
    setOrConfigured(hasOpenRouter())
    toast(hadKey ? 'OpenRouter key saved. Reload to load OpenRouter models.' : 'OpenRouter key removed.', {
      duration: 5000,
      action: { label: 'Reload now', fn: () => window.location.reload() },
    })
  }

  const importFile = async (file) => {
    try {
      const data = JSON.parse(await file.text())
      if (data.app !== 'nexus' || !Array.isArray(data.chats)) {
        toast.error('This file is not a valid Nexus backup.')
        return
      }
      await onImport(data.chats)
    } catch {
      toast.error('Could not read that file.')
    }
  }

  const tempLabel =
    settings.temperature <= 0.3 ? 'Precise' : settings.temperature >= 1.2 ? 'Creative' : 'Balanced'

  const Toggle = ({ on, onClick, label }) => (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onClick}
      className={`toggle ${on ? 'on' : ''}`}
    />
  )

  return (
    <div className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/55 fade-in" onClick={onClose} />
      <div className="relative w-full sm:max-w-lg max-h-[90vh] sm:max-h-[82vh] bg-bg-panel border border-bg-border rounded-t-2xl sm:rounded-2xl shadow-2xl flex flex-col fade-in">
        <div className="flex items-center justify-between px-5 py-4 border-b border-bg-border shrink-0">
          <h2 className="font-semibold">Settings</h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-content-faint hover:text-content hover:bg-bg-hover"
            aria-label="Close settings"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="overflow-y-auto flex-1">
          {/* ---------- AI Providers ---------- */}
          <Section title="AI Providers" description="Connect at least one provider to start chatting.">
            <Row label="OpenRouter" hint={orConfigured ? 'Connected' : 'Not connected — add a key below'}>
              <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${orConfigured ? 'bg-emerald-400' : 'bg-red-400'}`} />
            </Row>
            <div className="flex gap-2 mt-1">
              <input
                type="password"
                value={orInput}
                onChange={(e) => setOrInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && saveOpenRouterKey()}
                placeholder={storedOpenRouterKey() ? 'Replace your saved OpenRouter key…' : 'Enter your OpenRouter API key…'}
                className="flex-1 min-w-0 bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm outline-none focus:border-accent placeholder:text-content-faint text-content"
              />
              <button
                onClick={saveOpenRouterKey}
                className="px-3 py-2 rounded-lg border border-bg-border text-sm text-content-dim hover:text-content hover:border-content-faint transition-colors shrink-0"
              >
                Save
              </button>
            </div>

            <div className="h-px bg-bg-border my-3" />

            <Row label="Google Gemini" hint={geminiConfigured ? 'Connected — used as a free fallback' : 'Free fallback — add a key below'}>
              <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${geminiConfigured ? 'bg-emerald-400' : 'bg-red-400'}`} />
            </Row>
            <div className="flex gap-2 mt-1">
              <input
                type="password"
                value={geminiInput}
                onChange={(e) => setGeminiInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && saveGeminiKey()}
                placeholder={storedGeminiKey() ? 'Replace your saved Gemini key…' : 'Enter your Gemini API key…'}
                className="flex-1 min-w-0 bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm outline-none focus:border-accent placeholder:text-content-faint text-content"
              />
              <button
                onClick={saveGeminiKey}
                className="px-3 py-2 rounded-lg border border-bg-border text-sm text-content-dim hover:text-content hover:border-content-faint transition-colors shrink-0"
              >
                Save
              </button>
            </div>

            {!orConfigured && !geminiConfigured && (
              <p className="text-xs text-amber-400 mt-3">
                No provider is configured. Add a key above to start chatting — it's stored only on this device, never in the app itself.
              </p>
            )}
          </Section>

          {/* ---------- Appearance ---------- */}
          <Section title="Appearance">
            <div className="flex gap-2 mb-3">
              {['system', 'light', 'dark'].map((t) => (
                <button
                  key={t}
                  onClick={() => saveSettings({ theme: t })}
                  className={`flex-1 px-3 py-2 rounded-lg border text-sm capitalize transition-colors ${
                    settings.theme === t
                      ? 'border-accent bg-accent-muted text-accent font-medium'
                      : 'border-bg-border text-content-dim hover:border-content-faint'
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
            <Row label="Reduce motion" hint="Minimize interface animations and transitions">
              <Toggle
                on={settings.reduceMotion}
                onClick={() => saveSettings({ reduceMotion: !settings.reduceMotion })}
                label="Reduce motion"
              />
            </Row>
          </Section>

          {/* ---------- Chat Behavior ---------- */}
          <Section title="Chat Behavior">
            <Row label="Send message on Enter" hint="When off, use Ctrl+Enter to send — Enter adds a new line">
              <Toggle
                on={settings.enterToSend}
                onClick={() => saveSettings({ enterToSend: !settings.enterToSend })}
                label="Send message on Enter"
              />
            </Row>
          </Section>

          {/* ---------- Model Behavior ---------- */}
          <Section title="Model Behavior" description="Controls how the assistant responds across every chat.">
            <FieldLabel hint="Applied automatically to every conversation">Custom instructions</FieldLabel>
            <textarea
              value={settings.instructions}
              onChange={(e) => saveSettings({ instructions: e.target.value })}
              placeholder={'e.g. “Answer concisely. I live in Berlin.”'}
              rows={4}
              className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2.5 text-sm outline-none focus:border-accent placeholder:text-content-faint text-content resize-y"
            />

            <div className="mt-4">
              <FieldLabel>Response creativity</FieldLabel>
              <input
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={settings.temperature}
                onChange={(e) => saveSettings({ temperature: +e.target.value })}
                className="slider w-full"
                aria-label="Response creativity"
              />
              <div className="flex justify-between text-[11px] text-content-faint mt-1">
                <span>Precise</span>
                <span className="text-accent font-medium">
                  {settings.temperature.toFixed(1)} · {tempLabel}
                </span>
                <span>Creative</span>
              </div>
            </div>
          </Section>

          {/* ---------- Voice & Speech ---------- */}
          <Section title="Voice & Speech">
            <FieldLabel hint={voices.length ? `${voices.length} voices available on this device` : 'Uses your device default voice'}>
              Voice
            </FieldLabel>
            <select
              value={settings.voiceURI}
              onChange={(e) => saveSettings({ voiceURI: e.target.value })}
              className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm outline-none focus:border-accent text-content mb-3"
            >
              <option value="">System default</option>
              {voices.map((v) => (
                <option key={v.id} value={v.id}>{v.name}{v.lang ? ` (${v.lang})` : ''}</option>
              ))}
            </select>

            <div className="text-xs text-content-faint mb-1">Speed · {settings.voiceRate.toFixed(1)}x</div>
            <input
              type="range" min="0.5" max="2" step="0.1"
              value={settings.voiceRate}
              onChange={(e) => saveSettings({ voiceRate: +e.target.value })}
              className="slider w-full" aria-label="Voice speed"
            />
            <div className="text-xs text-content-faint mt-3 mb-1">Pitch · {settings.voicePitch.toFixed(1)}</div>
            <input
              type="range" min="0.5" max="2" step="0.1"
              value={settings.voicePitch}
              onChange={(e) => saveSettings({ voicePitch: +e.target.value })}
              className="slider w-full" aria-label="Voice pitch"
            />
            <button
              onClick={() => speak('Hi, this is Nexus. This is the voice I will use when talking to you.')}
              className="mt-3 px-3.5 py-2 rounded-lg border border-bg-border text-sm text-content-dim hover:text-content hover:border-content-faint transition-colors"
            >
              Preview voice
            </button>
          </Section>

          {/* ---------- Sync ---------- */}
          {supabase && (
            <Section
              title="Sync"
              description={authUser ? 'Signed in — chats sync to your account.' : 'Sign in to keep chats across devices and reinstalls.'}
            >
              <Auth user={authUser} />
            </Section>
          )}

          {/* ---------- Data & Privacy ---------- */}
          <Section
            title="Data & Privacy"
            description={
              supabase && authUser
                ? 'Chats sync automatically to your account.'
                : 'Chats are stored locally on this device only.'
            }
          >
            <div className="flex flex-wrap gap-2">
              <button
                onClick={onExport}
                className="px-3.5 py-2 rounded-lg border border-bg-border text-sm text-content-dim hover:text-content hover:border-content-faint transition-colors"
              >
                Export all chats
              </button>
              <button
                onClick={() => fileRef.current?.click()}
                className="px-3.5 py-2 rounded-lg border border-bg-border text-sm text-content-dim hover:text-content hover:border-content-faint transition-colors"
              >
                Import backup
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="application/json,.json"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) importFile(f)
                  e.target.value = ''
                }}
              />
            </div>
            <div className="mt-3">
              {confirmWipe ? (
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      setConfirmWipe(false)
                      onDeleteAll()
                      onClose()
                    }}
                    className="flex-1 px-3 py-2 rounded-lg bg-red-500 text-white text-sm font-medium hover:bg-red-600 transition-colors"
                  >
                    Yes, delete everything
                  </button>
                  <button
                    onClick={() => setConfirmWipe(false)}
                    className="px-4 py-2 rounded-lg border border-bg-border text-sm text-content-dim hover:text-content"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmWipe(true)}
                  className="px-3.5 py-2 rounded-lg border border-red-500/40 text-red-400 text-sm hover:bg-red-500/10 transition-colors"
                >
                  Delete all chats
                </button>
              )}
            </div>
          </Section>
          <div className="pb-safe" />
        </div>
      </div>
    </div>
  )
}
