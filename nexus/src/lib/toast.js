// Tiny global toast bus — toast.success('Saved!') from anywhere
const listeners = new Set()
let seq = 0

export function subscribeToasts(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function toast(message, { type = 'info', duration = 3200, action } = {}) {
  const t = { id: ++seq, message, type, duration, action }
  listeners.forEach((fn) => fn(t))
  return t.id
}

toast.success = (m, o) => toast(m, { ...o, type: 'success' })
toast.error = (m, o) => toast(m, { ...o, type: 'error' })
