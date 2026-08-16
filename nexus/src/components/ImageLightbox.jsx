import React, { useEffect, useState } from 'react'
import { saveImage } from '../lib/save'
import { toast } from '../lib/toast'

// Full-screen image preview (tap an image in a message to open).
// Save button: native → Documents/Nexus via Filesystem, web → download.
export default function ImageLightbox({ src, alt = 'image', onClose }) {
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!src) return null

  const save = async () => {
    if (saving) return
    setSaving(true)
    try {
      const where = await saveImage(src, 'nexus')
      toast.success(where)
    } catch (e) {
      toast.error(e?.message || 'Could not save the image')
    } finally {
      setSaving(false)
    }
  }

  const btn =
    'flex items-center gap-2 px-3 py-2 rounded-xl bg-white/10 hover:bg-white/20 text-white text-sm transition-colors'

  return (
    <div
      className="fixed inset-0 z-[90] flex flex-col bg-black/90 backdrop-blur-sm fade-in"
      role="dialog"
      aria-modal="true"
      aria-label="Image preview"
      onClick={onClose}
    >
      <div className="flex items-center justify-end gap-2 px-4 pt-safe pt-4 shrink-0">
        <button onClick={(e) => { e.stopPropagation(); save() }} disabled={saving} className={btn}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button onClick={onClose} aria-label="Close preview" className={btn}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
          Close
        </button>
      </div>
      <div className="flex-1 min-h-0 flex items-center justify-center p-4 pb-safe">
        <img
          src={src}
          alt={alt}
          onClick={(e) => e.stopPropagation()}
          className="max-w-full max-h-full object-contain rounded-lg select-none"
        />
      </div>
    </div>
  )
}
