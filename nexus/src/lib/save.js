// Save a generated/attached image to the device.
// Native: writes to the public Documents/Nexus folder via the Filesystem plugin.
// Web: classic anchor download.
import { Capacitor } from '@capacitor/core'

const isNative = (() => {
  try {
    return !!Capacitor?.isNativePlatform?.()
  } catch {
    return false
  }
})()

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1] || '')
    r.onerror = reject
    r.readAsDataURL(blob)
  })
}

const EXT_BY_MIME = {
  'image/jpeg': 'jpg',
  'image/jpg': 'jpg',
  'image/png': 'png',
  'image/webp': 'webp',
  'image/gif': 'gif',
}

/**
 * Persist an image (data URL or remote URL).
 * Returns a human-readable description of where it went.
 */
export async function saveImage(src, nameHint = 'image') {
  if (!src) throw new Error('Nothing to save')
  const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')

  if (isNative) {
    const { Filesystem, Directory } = await import('@capacitor/filesystem')
    let base64
    let ext = 'png'
    if (src.startsWith('data:')) {
      const [meta, data] = src.split(',', 2)
      const mime = /^data:([^;,]+)/.exec(meta)?.[1] || 'image/png'
      ext = EXT_BY_MIME[mime] || 'png'
      base64 = data
    } else {
      const res = await fetch(src)
      if (!res.ok) throw new Error(`Could not fetch image (HTTP ${res.status})`)
      const blob = await res.blob()
      ext = EXT_BY_MIME[blob.type] || 'png'
      base64 = await blobToBase64(blob)
    }
    const path = `Nexus/nexus-${nameHint}-${ts}.${ext}`
    await Filesystem.writeFile({
      path,
      data: base64,
      directory: Directory.Documents,
      recursive: true,
    })
    return `Saved to Documents/${path}`
  }

  const a = document.createElement('a')
  a.href = src
  a.download = `nexus-${nameHint}-${ts}.png`
  document.body.appendChild(a)
  a.click()
  a.remove()
  return 'Download started'
}
