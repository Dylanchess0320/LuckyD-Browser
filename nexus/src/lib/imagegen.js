// Free image generation via Pollinations.ai (no API key needed)
const POLL_BASE = 'https://image.pollinations.ai'

export const IMAGE_SIZES = [
  { label: 'Square (1:1)', width: 1024, height: 1024 },
  { label: 'Portrait (3:4)', width: 768, height: 1024 },
  { label: 'Landscape (4:3)', width: 1024, height: 768 },
  { label: 'Wide (16:9)', width: 1024, height: 576 },
]

// Underlying image backends this app can pick between (all served through
// Pollinations' free, no-auth /prompt endpoint via the `model=` param).
// auto.js's resolveImageModel() picks one of these per-prompt; keep 'flux'
// first — it's treated as the universal safe fallback if another model errors.
export const IMAGE_MODELS = [
  { id: 'flux', name: 'Flux', description: 'Balanced default — best all-purpose quality.' },
  { id: 'turbo', name: 'Turbo', description: 'Fastest generation, lower fidelity — good for quick drafts.' },
  { id: 'gptimage', name: 'GPT Image', description: 'Best at rendering legible text/typography inside an image.' },
  { id: 'nanobanana', name: 'Nanobanana', description: "Google's image model — strong photorealistic portraits." },
  { id: 'seedream', name: 'Seedream', description: 'Stronger stylized, illustration, and anime output.' },
]
export const DEFAULT_IMAGE_MODEL = 'flux'

// Generate an image, return { dataUrl, width, height, model }
export async function generateImage(
  prompt,
  { width = 1024, height = 1024, seed = null, model = DEFAULT_IMAGE_MODEL } = {},
) {
  const encoded = encodeURIComponent(prompt.trim())
  const seedParam = seed != null ? `&seed=${seed}` : `&seed=${Math.floor(Math.random() * 1000000)}`
  const url = `${POLL_BASE}/prompt/${encoded}?width=${width}&height=${height}&nologo=true&model=${encodeURIComponent(model)}${seedParam}`
  const res = await fetch(url)
  if (!res.ok) {
    const err = new Error(`Image generation failed (HTTP ${res.status})`)
    err.status = res.status
    err.model = model
    throw err
  }
  const blob = await res.blob()
  const dataUrl = await new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result)
    r.onerror = reject
    r.readAsDataURL(blob)
  })
  return { dataUrl, width, height, url, model }
}

// Generate with an automatic one-shot fallback: if the style-matched model
// errors (unavailable/rate-limited/etc.), retry once on the universal
// default ('flux') before giving up — the same safety-net shape as the chat
// provider cascade in App.jsx's runStream, sized down for a single backend.
export async function generateImageAuto(prompt, opts = {}) {
  const wanted = opts.model || DEFAULT_IMAGE_MODEL
  try {
    return await generateImage(prompt, { ...opts, model: wanted })
  } catch (err) {
    if (wanted === DEFAULT_IMAGE_MODEL) throw err
    return await generateImage(prompt, { ...opts, model: DEFAULT_IMAGE_MODEL })
  }
}

// Generate a variation (reuse seed with modifications)
export function getSeed() {
  return Math.floor(Math.random() * 1000000)
}