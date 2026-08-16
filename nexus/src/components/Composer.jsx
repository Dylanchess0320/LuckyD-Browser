import React, { useEffect, useRef, useState } from 'react'
import { fileToDataUrl } from '../lib/util'
import { toast } from '../lib/toast'
import { useSettings } from '../lib/settings'
import { isNative, sttAvailable, ensureSttPermission, listenOnce, stopStt } from '../lib/speech'

export default function Composer({
  value,
  onChange,
  onSend,
  onStop,
  isStreaming,
  vision,
  webSearch,
  onToggleWebSearch,
  imageMode,
  onToggleImageMode,
  onOpenVoice,
}) {
  const taRef = useRef(null)
  const fileRef = useRef(null)
  const dictRef = useRef(null) // active dictation session { base, final, interim, active }
  const [images, setImages] = useState([]) // [{ dataUrl, name }]
  const [listening, setListening] = useState(false)
  const [dragging, setDragging] = useState(false)
  const imagesRef = useRef([]) // mirror of `images` for async-safe length checks
  const { enterToSend } = useSettings()

  // Stop dictation when the component unmounts
  useEffect(() => () => {
    if (dictRef.current) dictRef.current.active = false
    stopStt()
  }, [])

  // Show voice buttons only where speech recognition actually exists
  const [voiceOk, setVoiceOk] = useState(true)
  useEffect(() => {
    let on = true
    sttAvailable().then((ok) => { if (on) setVoiceOk(ok) })
    return () => { on = false }
  }, [])

  // Auto-resize the textarea (max ~10 lines). Depends on the placeholder-
  // driving states too — switching Image mode swaps in a longer placeholder,
  // and on an empty textarea the browser measures scrollHeight off whatever
  // text is actually rendered (placeholder included), so this has to re-run
  // then or the box grows once and never shrinks back on toggle-off.
  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
  }, [value, imageMode, listening])

  // Stop dictation if a stream starts
  useEffect(() => {
    if (isStreaming) stopListening()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStreaming])

  const addFiles = async (files) => {
    if (!files?.length) return
    if (!vision) {
      toast.error('This model cannot see images — pick one with the 👁 badge.')
      return
    }
    for (const f of files) {
      if (!f.type.startsWith('image/')) {
        toast.error(`${f.name || 'File'} is not an image`)
        continue
      }
      if (imagesRef.current.length >= 4) {
        toast.error('Up to 4 images per message')
        break
      }
      try {
        const img = await fileToDataUrl(f)
        imagesRef.current = [...imagesRef.current, { dataUrl: img.dataUrl, name: img.name || f.name }]
        setImages(imagesRef.current)
      } catch {
        toast.error(`Could not read ${f.name || 'file'}`)
      }
    }
  }

  const removeImage = (i) => {
    imagesRef.current = imagesRef.current.filter((_, j) => j !== i)
    setImages(imagesRef.current)
  }

  const clearImages = () => {
    imagesRef.current = []
    setImages([])
  }

  const stopListening = () => {
    if (dictRef.current) dictRef.current.active = false
    stopStt()
    setListening(false)
  }

  // Dictation: continuous append-style sessions built from listen-until-silence
  // turns (native plugin on Android, Web Speech API in the browser).
  const toggleMic = async () => {
    if (listening) return stopListening()
    if (!(await sttAvailable())) {
      return toast.error(
        isNative
          ? 'Speech recognition is not available on this device'
          : 'Voice input needs Chrome or Edge on desktop/Android web',
      )
    }
    if (isNative && !(await ensureSttPermission())) {
      return toast.error('Microphone permission denied')
    }
    const session = { base: value, final: '', interim: '', active: true }
    dictRef.current = session
    setListening(true)
    const apply = () => {
      const said = [session.final, session.interim].filter(Boolean).join(' ')
      onChange([session.base, said].filter(Boolean).join(' ').replace(/\s+/g, ' ').trimStart())
    }
    while (session.active) {
      let heard = ''
      try {
        heard = await listenOnce({
          onPartial: (t) => {
            if (!session.active) return
            session.interim = t
            apply()
          },
        })
      } catch (err) {
        if (session.active) toast.error(err?.message || 'Microphone error')
        break
      }
      if (!session.active) break
      if (heard?.trim()) {
        session.final = [session.final, heard.trim()].filter(Boolean).join(' ')
        session.interim = ''
        apply()
      }
      // silence → immediately reopen the mic for the next utterance
    }
    setListening(false)
    if (dictRef.current === session) dictRef.current = null
  }

  const send = () => {
    const text = value.trim()
    if ((!text && !imagesRef.current.length) || isStreaming) return
    onSend(text, imagesRef.current.map((i) => i.dataUrl))
    clearImages()
    stopListening()
  }

  const onKeyDown = (e) => {
    if (e.key !== 'Enter' || e.nativeEvent?.isComposing) return
    const plainEnter = !e.shiftKey && !e.ctrlKey && !e.metaKey
    const modEnter = (e.ctrlKey || e.metaKey) && !e.shiftKey
    if ((enterToSend && plainEnter) || (!enterToSend && modEnter)) {
      e.preventDefault()
      send()
    }
  }

  const onPaste = (e) => {
    const items = Array.from(e.clipboardData?.items || [])
    const imgs = items
      .filter((i) => i.type.startsWith('image/'))
      .map((i) => i.getAsFile())
      .filter(Boolean)
    if (imgs.length) {
      e.preventDefault()
      addFiles(imgs)
    }
  }

  const canSend = (value.trim() || images.length > 0) && !isStreaming
  const iconBtn = 'p-2 rounded-xl transition-colors text-content-dim hover:bg-bg-hover hover:text-content'

  return (
    <div
      className="relative border-t border-bg-border bg-bg shrink-0"
      onDragOver={(e) => {
        e.preventDefault()
        if (vision) setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        addFiles(Array.from(e.dataTransfer?.files || []))
      }}
    >
      {dragging && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-bg/85 backdrop-blur-sm border-2 border-dashed border-accent text-sm text-accent font-medium pointer-events-none">
          Drop images to attach
        </div>
      )}
      <div className="max-w-chat mx-auto px-3 sm:px-4 pt-2.5 pb-2">
        {images.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap">
            {images.map((img, i) => (
              <div key={i} className="relative">
                <img
                  src={img.dataUrl}
                  alt={img.name || 'attachment'}
                  className="w-16 h-16 object-cover rounded-lg border border-bg-border"
                />
                <button
                  onClick={() => removeImage(i)}
                  aria-label="Remove image"
                  className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-bg-panel border border-bg-border text-content-faint hover:text-content text-xs leading-none"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="flex items-end gap-0.5 bg-bg-panel border border-bg-border rounded-2xl px-1.5 py-1.5 focus-within:border-accent/60 transition-colors">
          <button
            onClick={() => fileRef.current?.click()}
            className={iconBtn}
            title={vision ? 'Attach image' : 'Current model cannot see images'}
            aria-label="Attach image"
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          </button>
          {onToggleWebSearch && (
            <button
              onClick={onToggleWebSearch}
              className={`p-2 rounded-xl transition-colors ${
                webSearch ? 'text-accent bg-accent-muted' : iconBtn
              }`}
              title={webSearch ? 'Web search: on' : 'Search the web'}
              aria-label="Toggle web search"
            >
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <line x1="2" y1="12" x2="22" y2="12" />
                <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
              </svg>
            </button>
          )}
          {onToggleImageMode && (
            <button
              onClick={onToggleImageMode}
              className={`p-2 rounded-xl transition-colors ${
                imageMode ? 'text-accent bg-accent-muted' : iconBtn
              }`}
              title={imageMode ? 'Image generation: on' : 'Generate an image instead of chatting'}
              aria-label="Toggle image generation"
            >
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <circle cx="8.5" cy="8.5" r="1.5" />
                <polyline points="21 15 16 10 5 21" />
              </svg>
            </button>
          )}
          <textarea
            ref={taRef}
            rows={1}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            enterKeyHint="send"
            placeholder={listening ? 'Listening…' : imageMode ? 'Describe an image…' : 'Message Nexus'}
            aria-label="Message"
            className="flex-1 min-w-0 bg-transparent resize-none outline-none px-2 py-1.5 text-[0.95rem] max-h-[200px] transition-[height] duration-150 ease-out placeholder:text-content-faint text-content"
          />
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => {
              addFiles(Array.from(e.target.files || []))
              e.target.value = ''
            }}
          />
          {voiceOk && (
            <button
              onClick={toggleMic}
              className={`p-2 rounded-xl transition-colors ${
                listening ? 'text-red-400 bg-red-400/10 mic-live' : iconBtn
              }`}
              title={listening ? 'Stop dictation' : 'Dictate'}
              aria-label="Dictate"
            >
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="9" y="2" width="6" height="12" rx="3" />
                <path d="M5 10a7 7 0 0 0 14 0" />
                <line x1="12" y1="17" x2="12" y2="22" />
              </svg>
            </button>
          )}
          {voiceOk && onOpenVoice && (
            <button
              onClick={onOpenVoice}
              className={iconBtn}
              title="Voice mode"
              aria-label="Open voice mode"
            >
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 12v-1a9 9 0 0 1 18 0v1" />
                <path d="M21 12v4a2 2 0 0 1-2 2h-1v-7h3z" />
                <path d="M3 12v4a2 2 0 0 0 2 2h1v-7H3z" />
              </svg>
            </button>
          )}
          {isStreaming ? (
            <button
              onClick={onStop}
              className="p-2 rounded-xl bg-accent text-white hover:bg-accent-hover transition-colors"
              title="Stop generating"
              aria-label="Stop generating"
            >
              <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              onClick={send}
              disabled={!canSend}
              className={`p-2 rounded-xl transition-colors ${
                canSend ? 'bg-accent text-white hover:bg-accent-hover' : 'text-content-faint cursor-not-allowed'
              }`}
              title="Send"
              aria-label="Send message"
            >
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="12" y1="19" x2="12" y2="5" />
                <polyline points="5 12 12 5 19 12" />
              </svg>
            </button>
          )}
        </div>
      </div>
      <div className="pb-safe" />
    </div>
  )
}
