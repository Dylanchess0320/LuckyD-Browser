import React, { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from '../lib/toast'
import {
  isNative,
  sttAvailable,
  ensureSttPermission,
  listenOnce,
  stopStt,
  speak,
  stopSpeak,
} from '../lib/speech'

// Full-screen hands-free mode: listen → send → speak the reply → listen again.
// STT/TTS go through the unified speech layer — native plugins on Android,
// Web Speech API in the browser.
export default function VoiceMode({ open, onClose, onSendMessage }) {
  const [phase, setPhase] = useState('idle') // idle | listening | thinking | speaking
  const [transcript, setTranscript] = useState('')
  const [reply, setReply] = useState('')
  const [interim, setInterim] = useState('')
  const [muted, setMuted] = useState(false)
  const [minimized, setMinimized] = useState(false)

  const stoppedRef = useRef(true) // true while the overlay is closed / torn down
  const phaseRef = useRef('idle')
  const mutedRef = useRef(false)
  const listenRef = useRef(() => {})

  useEffect(() => { mutedRef.current = muted }, [muted])

  const setPhaseAll = useCallback((p) => {
    phaseRef.current = p
    setPhase(p)
  }, [])

  const stopAll = useCallback(() => {
    stoppedRef.current = true
    stopStt()
    stopSpeak()
    setPhaseAll('idle')
    setTranscript('')
    setReply('')
    setInterim('')
  }, [setPhaseAll])

  // ---- send the heard text, wait for the reply, speak it, then listen again ----
  const send = useCallback(
    async (text) => {
      setPhaseAll('thinking')
      let result = null
      let failure = null
      try {
        // forceChat: voice mode always chats, even if image-generation mode is on
        result = await onSendMessage(text, [], { forceChat: true })
      } catch (err) {
        failure = err?.message || 'Something went wrong'
      }
      if (stoppedRef.current) return
      if (failure) {
        setReply(failure)
        toast.error(failure, { duration: 6000 })
        if (mutedRef.current) {
          listenRef.current()
          return
        }
        setPhaseAll('speaking')
        const ok = speak(failure, {
          onEnd: () => {
            if (!stoppedRef.current && phaseRef.current === 'speaking') listenRef.current()
          },
        })
        if (!ok) { setPhaseAll('idle'); listenRef.current() }
        return
      }
      const replyText = (result || '').trim()
      setReply(replyText)
      if (!replyText || mutedRef.current) {
        listenRef.current()
        return
      }
      setPhaseAll('speaking')
      const ok = speak(replyText, {
        onEnd: () => {
          if (!stoppedRef.current && phaseRef.current === 'speaking') listenRef.current()
        },
      })
      if (!ok) listenRef.current()
    },
    [onSendMessage, setPhaseAll],
  )

  // ---- one listen-until-silence turn ----
  const listen = useCallback(async () => {
    if (stoppedRef.current) return
    if (phaseRef.current === 'listening' || phaseRef.current === 'thinking') return
    setPhaseAll('listening')
    setInterim('')
    setReply('')
    let heard = ''
    try {
      heard = (
        (await listenOnce({
          onPartial: (t) => {
            if (!stoppedRef.current) setInterim(t)
          },
        })) || ''
      ).trim()
    } catch (err) {
      if (stoppedRef.current) return
      const msg = err?.message || 'Microphone error'
      toast.error(msg)
      setPhaseAll('idle')
      if (/permission|not allowed|denied/i.test(msg)) {
        onClose()
        return
      }
      // transient engine failure — brief pause, then keep the loop alive
      setTimeout(() => {
        if (!stoppedRef.current) listenRef.current()
      }, 800)
      return
    }
    if (stoppedRef.current) return
    setInterim('')
    if (!heard) {
      // silence — just listen again
      setPhaseAll('idle')
      listenRef.current()
      return
    }
    setTranscript(heard)
    send(heard)
  }, [send, onClose, setPhaseAll])

  useEffect(() => { listenRef.current = listen }, [listen])

  // ---- open/close lifecycle ----
  useEffect(() => {
    let cancelled = false
    if (open) {
      ;(async () => {
        if (!(await sttAvailable())) {
          if (!cancelled) {
            toast.error(
              isNative
                ? 'Speech recognition is not available on this device'
                : 'Voice mode needs Chrome or Edge on desktop/Android web',
            )
            onClose()
          }
          return
        }
        if (isNative && !(await ensureSttPermission())) {
          if (!cancelled) {
            toast.error('Microphone permission denied')
            onClose()
          }
          return
        }
        if (cancelled) return
        stoppedRef.current = false
        setMinimized(false)
        setPhaseAll('idle')
        listenRef.current()
      })()
    } else {
      stopAll()
    }
    return () => {
      cancelled = true
      stoppedRef.current = true
      stopStt()
      stopSpeak()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!open) return null

  // ---- minimized: a small floating pill so voice mode keeps running while
  // you go back and use the rest of the app (browse chats, type, etc.) ----
  if (minimized) {
    const dot =
      phase === 'listening'
        ? 'bg-accent animate-pulse'
        : phase === 'thinking'
          ? 'bg-content-faint animate-pulse'
          : phase === 'speaking'
            ? 'bg-accent'
            : 'bg-content-faint'
    return (
      <div className="fixed bottom-24 sm:bottom-6 right-4 z-[70] fade-in">
        <div className="flex items-center gap-1 bg-bg-panel border border-bg-border rounded-full shadow-2xl pl-1 pr-1 py-1">
          <button
            onClick={() => setMinimized(false)}
            className="flex items-center gap-2 pl-2 pr-3 py-1.5 rounded-full hover:bg-bg-hover transition-colors"
            aria-label="Expand voice mode"
            title="Expand voice mode"
          >
            <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${dot}`} />
            <span className="text-xs font-medium text-content-dim max-w-[9rem] truncate">
              {phase === 'thinking' ? 'Thinking…' : phase === 'speaking' ? 'Speaking…' : 'Voice mode'}
            </span>
          </button>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-full flex items-center justify-center text-content-faint hover:text-content hover:bg-bg-hover"
            aria-label="End voice mode"
            title="End voice mode"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>
    )
  }

  const orbBase = 'w-32 h-32 sm:w-40 sm:h-40 rounded-full flex items-center justify-center transition-all duration-300'
  const orbState =
    phase === 'listening'
      ? 'bg-accent-muted border-2 border-accent scale-100 animate-pulse'
      : phase === 'thinking'
        ? 'bg-bg-panel border-2 border-bg-border scale-95'
        : phase === 'speaking'
          ? 'bg-accent border-2 border-accent scale-105'
          : 'bg-bg-panel border-2 border-bg-border scale-90'

  const caption =
    phase === 'thinking'
      ? 'Thinking…'
      : phase === 'speaking'
        ? reply || '…'
        : interim || transcript || (phase === 'listening' ? 'Listening…' : 'Tap to talk')

  return (
    <div className="fixed inset-0 z-[70] flex flex-col items-center justify-between bg-bg fade-in" role="dialog" aria-modal="true" aria-label="Voice mode">
      <div className="flex items-center justify-between w-full px-5 pt-safe pt-5">
        <span className="text-sm font-medium text-content-dim">Voice mode</span>
        <button
          onClick={() => setMinimized(true)}
          className="p-2 rounded-lg text-content-faint hover:text-content hover:bg-bg-hover"
          aria-label="Minimize voice mode"
          title="Keep listening in the background"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>

      <div className="flex-1 flex flex-col items-center justify-center gap-8 px-6 min-h-0">
        <button
          onClick={() => {
            if (phase !== 'speaking') return
            // Claim the phase first: stopSpeak() releases the pending onEnd,
            // and its restart guard must see that we already left 'speaking'.
            setPhaseAll('idle')
            stopSpeak()
            listenRef.current()
          }}
          className={`${orbBase} ${orbState}`}
          aria-label={phase === 'speaking' ? 'Interrupt' : 'Voice status'}
        >
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" className={phase === 'speaking' ? 'text-white' : 'text-accent'}>
            <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
            <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
            <line x1="12" y1="18" x2="12" y2="22" />
          </svg>
        </button>
        <p className="text-center text-content-dim text-sm max-w-sm min-h-[2.5em] max-h-[50vh] overflow-y-auto overscroll-contain leading-relaxed px-1 -mx-1">
          {caption}
        </p>
      </div>

      <div className="flex items-center gap-4 pb-safe pb-8">
        <button
          onClick={() => setMuted((m) => !m)}
          className={`w-12 h-12 rounded-full flex items-center justify-center border transition-colors ${
            muted ? 'bg-red-400/10 border-red-400/40 text-red-400' : 'bg-bg-panel border-bg-border text-content-dim hover:text-content'
          }`}
          aria-label={muted ? 'Unmute replies' : 'Mute replies'}
          title={muted ? 'Replies muted — tap to unmute' : 'Mute spoken replies'}
        >
          {muted ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="1" y1="1" x2="23" y2="23" />
              <path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V5a3 3 0 0 0-5.94-.6" />
              <path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23" />
              <line x1="12" y1="19" x2="12" y2="22" />
            </svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
              <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
              <line x1="12" y1="18" x2="12" y2="22" />
            </svg>
          )}
        </button>
        <button
          onClick={onClose}
          className="w-12 h-12 rounded-full flex items-center justify-center bg-red-500 text-white hover:bg-red-600 transition-colors"
          aria-label="End voice mode"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
    </div>
  )
}
