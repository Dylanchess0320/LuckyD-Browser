import React, { useCallback, useEffect, useRef, useState } from 'react'
import Sidebar from './components/Sidebar'
import ChatView from './components/ChatView'
import Settings from './components/Settings'
import Toasts from './components/Toasts'
import VoiceMode from './components/VoiceMode'
import { listChats, createChat, updateChat, deleteChat, deleteAllChats, exportAllChats, setAuthUserId, migrateLocalChatsToAccount, supabase } from './lib/db'
import { initAuth, onAuthChange } from './lib/auth'
import { fetchModels, streamChat, quickChat, estimateCost, hasOpenRouter, DEFAULT_MODEL } from './lib/openrouter'
import { streamGemini, quickGemini, hasGemini, GEMINI_MODELS, fetchGeminiModels, isGeminiDirect } from './lib/gemini'
import { generateImageAuto, IMAGE_MODELS } from './lib/imagegen'
import { detectTaskType, needsWebSearch, estimateComplexity, resolveAutoModel, resolveImageModel, isAutoModel, AUTO_FREE_ID, AUTO_PAID_ID } from './lib/auto'
import { textOf } from './components/Message'
import { useSettings } from './lib/settings'
import { uid, download, trimMessagesToBudget, estimateTokens } from './lib/util'
import { toast } from './lib/toast'

function friendlyError(err) {
  const msg = err?.message || 'Unknown error'
  const tag = err?.provider ? `[${err.provider}] ` : ''
  if (err.status === 401) return `${tag}API key rejected (401). Check your keys in Settings.`
  if (err.status === 402) {
    return `${tag}This model needs credits — top up or pick a FREE model.`
  }
  if (err.status === 429) return `${tag}Rate limited — wait a moment or switch models.`
  if (err.status === 404) return `${tag}Model not found — it may have been retired. Pick another model.`
  if (err.status >= 500) return `${tag}Provider server error (${err.status}). Try again shortly.`
  if (/failed to fetch|networkerror|load failed/i.test(msg)) return `${tag}Network error — check your connection.`
  return msg.startsWith('[') ? msg : `${tag}${msg}`
}

export default function App() {
  const settings = useSettings()
  const [chats, setChats] = useState([])
  const [activeChatId, setActiveChatId] = useState(null)
  const [models, setModels] = useState(() => {
    const base = []
    if (hasGemini()) base.push(...GEMINI_MODELS)
    return base
  })
  const [loading, setLoading] = useState(true)
  const [authUser, setAuthUser] = useState(null)
  const [streamingChatId, setStreamingChatId] = useState(null)
  const [draft, setDraft] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [webSearch, setWebSearch] = useState(false)
  const [imageMode, setImageMode] = useState(false)
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [defaultModel, setDefaultModel] = useState('')

  // ---- ref mirrors so async callbacks always read fresh values ----
  const chatsRef = useRef([])
  const activeChatIdRef = useRef(null)
  const modelsRef = useRef(models)
  const modelsReadyRef = useRef(Promise.resolve()) // resolves once the boot-time catalog fetch settles
  const settingsRef = useRef(settings)
  const webSearchRef = useRef(webSearch)
  const streamingRef = useRef(null)
  const sendingRef = useRef(false) // true from the instant send() is called until it fully settles
  const currentModelIdRef = useRef('')
  const abortRef = useRef(null)
  const lastErrorRef = useRef('')

  useEffect(() => { settingsRef.current = settings }, [settings])
  useEffect(() => { webSearchRef.current = webSearch }, [webSearch])

  const activeChat = chats.find((c) => c.id === activeChatId) || null
  const currentModelId =
    activeChat?.model ||
    defaultModel ||
    settings.recentModels?.[0] ||
    AUTO_FREE_ID
  useEffect(() => { currentModelIdRef.current = currentModelId }, [currentModelId])

  // Re-reads the chat list from whichever backend is currently active
  // (signed-in account vs. local-only) and swaps it in, falling back to the
  // first chat if the active one no longer exists under the new backend.
  const reloadChats = useCallback(async () => {
    try {
      const loaded = await listChats()
      chatsRef.current = loaded
      setChats(loaded)
      const stillValid = loaded.some((c) => c.id === activeChatIdRef.current)
      if (!stillValid) {
        setActiveChatId(loaded[0]?.id || null)
        activeChatIdRef.current = loaded[0]?.id || null
      }
    } catch { /* keep whatever's currently shown */ }
  }, [])

  // ---- auth: resolve sign-in state *before* the boot load below reads the
  // chat list, so a returning signed-in session doesn't briefly show local
  // chats before flipping over. Later sign-in/out events (not at boot)
  // migrate any local chats up and reload the list to match. ----
  const authReadyRef = useRef(null)
  if (!authReadyRef.current) {
    authReadyRef.current = supabase
      ? initAuth().then((user) => {
          setAuthUserId(user?.id)
          setAuthUser(user)
        })
      : Promise.resolve()
  }
  useEffect(() => {
    if (!supabase) return
    let live = true
    let bootDone = false
    authReadyRef.current.then(() => { bootDone = true })
    const unsub = onAuthChange(async (user) => {
      if (!live || !bootDone) return // the initial resolution above already covers boot
      setAuthUserId(user?.id)
      setAuthUser(user)
      if (user) {
        const moved = await migrateLocalChatsToAccount().catch(() => 0)
        if (moved) toast.success(`Synced ${moved} local chat${moved === 1 ? '' : 's'} to your account`)
      }
      await reloadChats()
    })
    return () => { live = false; unsub() }
  }, [reloadChats])

  // ---- boot: wait for auth to settle, load chats, then the model catalog ----
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      await authReadyRef.current
      if (cancelled) return
      try {
        const loaded = await listChats()
        if (cancelled) return
        chatsRef.current = loaded
        setChats(loaded)
        if (loaded.length) {
          setActiveChatId(loaded[0].id)
          activeChatIdRef.current = loaded[0].id
        }
      } catch {
        toast.error('Could not load your chats')
      }
      if (!cancelled) setLoading(false)
      // Stashed so runStream can await "the catalog fetch has settled" before
      // resolving Auto mode on the very first message — otherwise a fast first
      // send could race the fetch and pick from a near-empty catalog (or fail
      // outright with "no models available") purely because of timing.
      modelsReadyRef.current = (async () => {
        const [orResult, gemResult] = await Promise.allSettled([
          hasOpenRouter() ? fetchModels() : Promise.resolve(null),
          hasGemini() ? fetchGeminiModels() : Promise.resolve(null),
        ])
        if (orResult.status === 'rejected' && hasOpenRouter() && !cancelled) {
          toast.error(`Could not load OpenRouter models: ${friendlyError(orResult.reason)}`)
        }
        if (gemResult.status === 'rejected' && hasGemini() && !cancelled) {
          toast.error(`Could not refresh Gemini models: ${friendlyError(gemResult.reason)} (using the built-in list)`)
        }
        if (!cancelled) {
          const orList = orResult.status === 'fulfilled' && orResult.value ? orResult.value : []
          const gemList =
            gemResult.status === 'fulfilled' && gemResult.value
              ? gemResult.value
              : hasGemini()
                ? GEMINI_MODELS
                : []
          const merged = [...orList, ...gemList]
          modelsRef.current = merged
          setModels(merged)
        }
      })()
      await modelsReadyRef.current
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---- mutation helpers (chatsRef is the single source of truth) ----
  const patchChatLocal = useCallback((id, patch) => {
    chatsRef.current = chatsRef.current.map((c) =>
      c.id === id ? { ...c, ...patch, updated_at: Date.now() } : c,
    )
    setChats(chatsRef.current)
  }, [])

  const persist = useCallback((id, patch) => {
    updateChat(id, patch).catch(() => {})
  }, [])

  // ---- chat CRUD ----
  const selectChat = useCallback((id) => {
    setActiveChatId(id)
    activeChatIdRef.current = id
    setDraft('')
  }, [])

  const handleNewChat = useCallback(() => {
    // A chat is only persisted once the first message is sent
    setActiveChatId(null)
    activeChatIdRef.current = null
    setDraft('')
    setSidebarOpen(false)
  }, [])

  const renameChat = useCallback(
    (id, title) => {
      patchChatLocal(id, { title })
      persist(id, { title })
    },
    [patchChatLocal, persist],
  )

  const togglePin = useCallback(
    (id) => {
      const chat = chatsRef.current.find((c) => c.id === id)
      if (!chat) return
      const pinned = !chat.pinned
      patchChatLocal(id, { pinned })
      persist(id, { pinned })
    },
    [patchChatLocal, persist],
  )

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort() // partial reply is persisted via the abort listener
  }, [])

  const handleDeleteChat = useCallback(
    async (id) => {
      const deleted = chatsRef.current.find((c) => c.id === id)
      if (!deleted) return
      if (streamingRef.current === id) stopStreaming()
      const idx = chatsRef.current.findIndex((c) => c.id === id)
      chatsRef.current = chatsRef.current.filter((c) => c.id !== id)
      setChats(chatsRef.current)
      if (activeChatIdRef.current === id) {
        const next = chatsRef.current[0]
        setActiveChatId(next?.id || null)
        activeChatIdRef.current = next?.id || null
      }
      await deleteChat(id)
      toast('Chat deleted', {
        action: {
          label: 'Undo',
          fn: async () => {
            await createChat(deleted)
            const next = [...chatsRef.current]
            next.splice(Math.min(idx, next.length), 0, deleted)
            chatsRef.current = next
            setChats(next)
            setActiveChatId(deleted.id)
            activeChatIdRef.current = deleted.id
          },
        },
      })
    },
    [stopStreaming],
  )

  const handleDeleteAll = useCallback(async () => {
    const backup = chatsRef.current
    if (!backup.length) return
    if (abortRef.current) stopStreaming()
    chatsRef.current = []
    setChats([])
    setActiveChatId(null)
    activeChatIdRef.current = null
    await deleteAllChats()
    toast('All chats deleted', {
      duration: 5000,
      action: {
        label: 'Undo',
        fn: async () => {
          for (const c of backup) await createChat(c)
          chatsRef.current = backup
          setChats(backup)
        },
      },
    })
  }, [stopStreaming])

  const handleExport = useCallback(async () => {
    const data = await exportAllChats()
    download(`nexus-backup-${new Date().toISOString().slice(0, 10)}.json`, data, 'application/json')
    toast.success('Backup downloaded')
  }, [])

  const handleImport = useCallback(async (imported) => {
    let count = 0
    for (const c of imported) {
      if (!c || typeof c !== 'object' || !Array.isArray(c.messages)) continue
      // fresh id so an imported chat never clobbers an existing one
      const chat = await createChat({ ...c, id: uid(), pinned: !!c.pinned })
      chatsRef.current = [chat, ...chatsRef.current]
      count++
    }
    if (count) {
      setChats([...chatsRef.current])
      toast.success(`Imported ${count} chat${count === 1 ? '' : 's'}`)
    } else {
      toast.error('No chats found in that file')
    }
  }, [])

  const setModel = useCallback(
    (modelId) => {
      const id = activeChatIdRef.current
      if (id) {
        patchChatLocal(id, { model: modelId })
        persist(id, { model: modelId })
      } else {
        setDefaultModel(modelId)
      }
    },
    [patchChatLocal, persist],
  )

  // ---- global shortcuts ----
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        if (abortRef.current) stopStreaming()
        else {
          setSettingsOpen(false)
          setSidebarOpen(false)
        }
      } else if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'o') {
        e.preventDefault()
        handleNewChat()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handleNewChat, stopStreaming])

  // ---- core streaming routine shared by send / regenerate / edit-resubmit ----
  // Returns the assistant's final text (null when aborted or failed).
  const runStream = useCallback(
    async (chatId, baseMessages, streamOpts = {}) => {
      const chat = chatsRef.current.find((c) => c.id === chatId)
      const rawModelId = chat?.model
      if (!chat || !rawModelId) return null

      // What is the user actually asking for, right now? Both the auto-model
      // resolution below and the auto-web-search toggle need this.
      const lastUser = [...baseMessages].reverse().find((m) => m.role === 'user')
      const lastUserText = textOf(lastUser)
      const wantsWeb = hasOpenRouter() && needsWebSearch(lastUserText)

      // Auto mode: resolve to a real model based on what the last user
      // message is actually asking for, using whatever's in the live catalog.
      let modelId = rawModelId
      let autoTier = null
      if (isAutoModel(rawModelId)) {
        // Make sure the boot-time catalog fetch has actually settled first —
        // a no-op await once it has (the common case), but on a very fast
        // first message this avoids picking from a near-empty catalog, or
        // erroring out entirely, purely because the fetch hadn't landed yet.
        await modelsReadyRef.current.catch(() => {})
        autoTier = rawModelId === AUTO_PAID_ID ? 'paid' : 'free'
        const task = detectTaskType(lastUserText)
        const complexity = estimateComplexity(lastUserText)
        const resolved = resolveAutoModel({
          task,
          tier: autoTier,
          models: modelsRef.current,
          needsWeb: wantsWeb,
          complexity,
          realtime: !!streamOpts.realtime,
        })
        if (!resolved) {
          toast.error(
            autoTier === 'paid'
              ? 'Auto (Paid) has no models to pick from — add an OpenRouter key with paid access, or use Auto (Free).'
              : 'No free models available — add an API key in Settings.',
          )
          return null
        }
        modelId = resolved
      }

      // Web search + provider routing: only OpenRouter's :online plugin can
      // actually ground answers on live data here — the direct Gemini path
      // has no search tool wired up. When the question needs it, turn the
      // toggle on and steer this call to OpenRouter even if the chat (or a
      // manual pick) landed on a Gemini model.
      const rerouteForWeb = wantsWeb && hasOpenRouter() && isGeminiDirect(modelId)
      let effectiveWebSearch = webSearchRef.current
      if (wantsWeb && !effectiveWebSearch) {
        effectiveWebSearch = true
        webSearchRef.current = true
        setWebSearch(true)
        toast(
          rerouteForWeb
            ? "Turned on web search and switched off Gemini for this — it can't browse the web"
            : 'Turned on web search — this looks like it needs current info',
          { duration: 3500 },
        )
      } else if (rerouteForWeb) {
        toast("Switched off Gemini for this — it can't browse the web", { duration: 3500 })
      }

      const assistantMsg = { id: uid(), role: 'assistant', content: '', reasoning: '', citations: [] }
      patchChatLocal(chatId, { messages: [...baseMessages, assistantMsg] })
      streamingRef.current = chatId
      setStreamingChatId(chatId)

      const s = settingsRef.current
      const sys = s.instructions?.trim() ? [{ role: 'system', content: s.instructions.trim() }] : []

      // Keep the sent history under the model's context window. Reserve a
      // chunk for the reply + the system prompt, and leave headroom since our
      // token estimate is approximate. Falls back to a conservative budget
      // when the model's context length isn't in the catalog (e.g. Gemini's
      // static list, or the catalog fetch hasn't landed yet).
      const modelInfo = modelsRef.current.find((m) => m.id === modelId)
      const contextWindow = modelInfo?.context > 0 ? modelInfo.context : 32000
      const replyReserve = 2000
      const budget = Math.max(2000, contextWindow - replyReserve - estimateTokens(s.instructions))
      const { messages: trimmedBase, trimmedCount } = trimMessagesToBudget(baseMessages, budget)
      if (trimmedCount > 0) {
        toast(`Trimmed ${trimmedCount} older message${trimmedCount === 1 ? '' : 's'} to fit the model's context window`, { duration: 4000 })
      }

      const apiMessages = [...sys, ...trimmedBase.map((m) => ({ role: m.role, content: m.content }))]

      const controller = new AbortController()
      abortRef.current = controller

      const applyToken = (updater) => {
        const cur = chatsRef.current.find((c) => c.id === chatId)
        if (!cur) return
        const msgs = [...cur.messages]
        const last = { ...msgs[msgs.length - 1] }
        updater(last)
        msgs[msgs.length - 1] = last
        chatsRef.current = chatsRef.current.map((c) =>
          c.id === chatId ? { ...c, messages: msgs, updated_at: Date.now() } : c,
        )
        setChats(chatsRef.current)
      }
      const onToken = (_, full) => applyToken((m) => { m.content = full })
      const onReasoning = (_, full) => applyToken((m) => { m.reasoning = full })
      const onCitations = (list) => applyToken((m) => { m.citations = list })

      // (cost/label info for the *used* model is resolved after streaming,
      // since the fallback cascade may have switched providers)

      // If the user hits Stop, persist whatever partial reply exists right now
      controller.signal.addEventListener(
        'abort',
        () => {
          const cur = chatsRef.current.find((c) => c.id === chatId)
          if (cur) persist(chatId, { messages: cur.messages })
        },
        { once: true },
      )

      // Ordered provider candidates: the chat's own provider first, then every
      // other configured provider as a safety net. Hopping providers is only
      // safe before the first token has been emitted.
      const PROVIDER_LABEL = { openrouter: 'OpenRouter', gemini: 'Gemini' }
      const primaryKind =
        rerouteForWeb || (hasOpenRouter() && !isGeminiDirect(modelId)) ? 'openrouter' : 'gemini'
      const kinds = [primaryKind]
      for (const k of ['openrouter', 'gemini']) {
        if (kinds.includes(k)) continue
        if (k === 'openrouter' && hasOpenRouter()) kinds.push(k)
        if (k === 'gemini' && hasGemini()) kinds.push(k)
      }
      const modelFor = (kind) => {
        if (kind === 'gemini') return isGeminiDirect(modelId) ? modelId : GEMINI_MODELS[0].id
        return isGeminiDirect(modelId) ? DEFAULT_MODEL : modelId
      }

      try {
        let result = null
        let usedModelId = null
        let lastErr = null
        for (let ci = 0; ci < kinds.length; ci++) {
          const kind = kinds[ci]
          const useModel = modelFor(kind)
          const emitted = { v: false }
          const guardedToken = (d, full) => {
            emitted.v = true
            onToken(d, full)
          }
          try {
            if (kind === 'gemini') {
              result = await streamGemini({
                model: useModel,
                messages: apiMessages,
                signal: controller.signal,
                onToken: guardedToken,
                temperature: s.temperature,
              })
            } else {
              result = await streamChat({
                model: useModel,
                messages: apiMessages,
                signal: controller.signal,
                onToken: guardedToken,
                onReasoning,
                onCitations,
                temperature: s.temperature,
                webSearch: kind === 'openrouter' ? effectiveWebSearch : undefined,
              })
            }
            usedModelId = useModel
            break
          } catch (err) {
            if (err?.name === 'AbortError') throw err
            lastErr = err
            if (emitted.v) throw err // mid-stream failures can't hop providers
            // Reset the placeholder so the next provider starts on a clean bubble
            applyToken((m) => { m.content = ''; m.reasoning = ''; m.citations = [] })
            if (ci + 1 < kinds.length) {
              toast.error(`${friendlyError(err)} Trying ${PROVIDER_LABEL[kinds[ci + 1]]}…`, { duration: 5000 })
            }
          }
        }
        if (!result) throw lastErr || new Error('All providers failed')
        if (controller.signal.aborted) return null
        const usedInfo = modelsRef.current.find((m) => m.id === usedModelId)
        const done = {
          ...assistantMsg,
          content: result.content,
          reasoning: result.reasoning,
          citations: result.citations,
          usage: result.usage,
          cost: estimateCost(usedInfo, result.usage),
          modelName: usedInfo?.name || usedModelId,
        }
        patchChatLocal(chatId, { messages: [...baseMessages, done] })
        persist(chatId, { messages: [...baseMessages, done] })
        return result.content
      } catch (err) {
        if (err.name !== 'AbortError') {
          const friendly = friendlyError(err)
          lastErrorRef.current = friendly
          const cur = chatsRef.current.find((c) => c.id === chatId)
          const last = cur?.messages?.[cur.messages.length - 1]
          const failed = {
            ...assistantMsg,
            content: last?.role === 'assistant' ? last.content : '',
            reasoning: last?.role === 'assistant' ? last.reasoning || '' : '',
            error: friendly,
          }
          patchChatLocal(chatId, { messages: [...baseMessages, failed] })
          persist(chatId, { messages: [...baseMessages, failed] })
          toast.error(friendly, { duration: 6000 })
        }
        return null
      } finally {
        abortRef.current = null
        streamingRef.current = null
        setStreamingChatId(null)
      }
    },
    [patchChatLocal, persist],
  )

  const generateTitle = useCallback(
    async (chatId, userText, assistantText) => {
      const prompt = [
        {
          role: 'system',
          content:
            'Write a 3-5 word title for this conversation. Reply with the title only — no quotes, no emoji, no trailing punctuation.',
        },
        {
          role: 'user',
          content: `User: ${userText.slice(0, 400)}\nAssistant: ${(assistantText || '').slice(0, 400)}`,
        },
      ]
      let title = ''
      try {
        if (hasOpenRouter()) title = await quickChat({ model: DEFAULT_MODEL, messages: prompt, max_tokens: 20 })
        else if (hasGemini()) title = await quickGemini({ messages: prompt, maxTokens: 20 })
      } catch {
        return
      }
      title = title.replace(/^["'`]+|["'`.!]+$/g, '').split('\n')[0].trim().slice(0, 60)
      if (title) renameChat(chatId, title)
    },
    [renameChat],
  )

  const send = useCallback(
    async (text, imageDataUrls = [], opts = {}) => {
      if (streamingRef.current || sendingRef.current) {
        toast('Still replying — press Stop first')
        return
      }
      if (!hasOpenRouter() && !hasGemini()) {
        setSettingsOpen(true)
        toast.error('Add an API key in Settings to start chatting', { duration: 5000 })
        return
      }
      // Set synchronously, before any awaits below, so a second tap can't
      // slip through the gap before streamingRef gets set further down (e.g.
      // while the model catalog is still finishing its boot-time fetch).
      sendingRef.current = true
      try {
      let chat = chatsRef.current.find((c) => c.id === activeChatIdRef.current)
      if (!chat) {
        const modelId = currentModelIdRef.current
        if (!modelId) {
          toast.error('Pick a model first')
          return
        }
        // Local-first: build the chat and switch to it immediately. Creating a
        // chat used to `await createChat(...)` — a network call (Supabase, when
        // configured) — before touching any state, so the very first message
        // in a session left the screen looking frozen for however long that
        // round-trip took, with nothing shown until it resolved. Every other
        // mutation in this file updates local state first and persists in the
        // background (see patchChatLocal + persist below); this now matches
        // that pattern instead of being the one exception.
        chat = {
          id: uid(),
          title: 'New chat',
          model: modelId,
          messages: [],
          pinned: false,
          created_at: Date.now(),
          updated_at: Date.now(),
        }
        chatsRef.current = [chat, ...chatsRef.current]
        setChats(chatsRef.current)
        setActiveChatId(chat.id)
        activeChatIdRef.current = chat.id
        createChat(chat).catch(() => toast.error('Could not save this chat — it may not persist'))
      }
      setSidebarOpen(false)

      // ---- Auto mode: if this looks like an image request, route to image
      // generation automatically — no need to flip the toggle by hand ----
      const chatModelId = chat.model || currentModelIdRef.current
      const autoImageDetected =
        !imageMode && !opts.forceChat && isAutoModel(chatModelId) && !imageDataUrls.length &&
        detectTaskType(text) === 'image'
      if (autoImageDetected) {
        toast('Detected an image request — generating…', { duration: 3000 })
      }

      // ---- Image generation mode: skip the LLM, call Pollinations directly ----
      // (opts.forceChat lets Voice mode bypass this even while image mode is on)
      if ((imageMode || autoImageDetected) && !opts.forceChat) {
        if (!text.trim()) {
          toast.error('Describe the image you want')
          return
        }
        const baseMessages = [...chat.messages, { id: uid(), role: 'user', content: text }]
        patchChatLocal(chat.id, { messages: baseMessages })
        persist(chat.id, { messages: baseMessages })
        setDraft('')
        streamingRef.current = chat.id
        setStreamingChatId(chat.id)
        // Auto-pick the image backend that best matches what's being asked for
        // (in-image text, portrait realism, illustration style, or a quick
        // draft) instead of always using the same one regardless of the ask.
        const pickedImageModel = resolveImageModel(text)
        if (pickedImageModel.reason) {
          const label = IMAGE_MODELS.find((m) => m.id === pickedImageModel.id)?.name || pickedImageModel.id
          toast(`Using ${label} — ${pickedImageModel.reason}`, { duration: 3000 })
        }
        try {
          const img = await generateImageAuto(text, { model: pickedImageModel.id })
          const usedLabel = IMAGE_MODELS.find((m) => m.id === img.model)?.name || img.model
          const assistantMsg = {
            id: uid(),
            role: 'assistant',
            content: [{ type: 'image_url', image_url: { url: img.dataUrl } }],
            reasoning: '',
            citations: [],
            modelName: usedLabel,
          }
          const done = [...baseMessages, assistantMsg]
          patchChatLocal(chat.id, { messages: done })
          persist(chat.id, { messages: done })
        } catch (err) {
          toast.error(`Image generation failed: ${friendlyError(err)}`)
        } finally {
          streamingRef.current = null
          setStreamingChatId(null)
        }
        return
      }

      const content = imageDataUrls.length
        ? [
            ...imageDataUrls.map((url) => ({ type: 'image_url', image_url: { url } })),
            { type: 'text', text: text || 'What is in this image?' },
          ]
        : text
      const baseMessages = [...chat.messages, { id: uid(), role: 'user', content }]
      patchChatLocal(chat.id, { messages: baseMessages })
      persist(chat.id, { messages: baseMessages })
      setDraft('')
      const reply = await runStream(chat.id, baseMessages, { realtime: !!opts.forceChat })
      if (chat.title === 'New chat' && reply) {
        generateTitle(chat.id, text || 'Image question', reply).catch(() => {})
      }
      // Voice mode has no error banner in the chat list, so it needs the
      // failure re-thrown to know something went wrong and speak it aloud.
      if (opts.forceChat && !reply) {
        throw new Error(lastErrorRef.current || 'Could not get a reply — check Settings for a working API key')
      }
      return reply
      } finally {
        sendingRef.current = false
      }
    },
    [runStream, generateTitle, patchChatLocal, persist, imageMode],
  )

  const regenerate = useCallback(() => {
    if (streamingRef.current) return
    const chat = chatsRef.current.find((c) => c.id === activeChatIdRef.current)
    if (!chat) return
    const msgs = [...chat.messages]
    while (msgs.length && msgs[msgs.length - 1].role !== 'user') msgs.pop()
    if (!msgs.length || msgs.length === chat.messages.length) return
    patchChatLocal(chat.id, { messages: msgs })
    persist(chat.id, { messages: msgs })
    runStream(chat.id, msgs)
  }, [runStream, patchChatLocal, persist])

  const editMessage = useCallback(
    (index, newText) => {
      if (streamingRef.current) return
      const chat = chatsRef.current.find((c) => c.id === activeChatIdRef.current)
      const orig = chat?.messages?.[index]
      if (!chat || !orig || orig.role !== 'user') return
      let content = newText
      if (Array.isArray(orig.content)) {
        const imgs = orig.content.filter((p) => p.type === 'image_url')
        content = [...imgs, { type: 'text', text: newText }]
      }
      const msgs = [...chat.messages.slice(0, index), { ...orig, content }]
      patchChatLocal(chat.id, { messages: msgs })
      persist(chat.id, { messages: msgs })
      runStream(chat.id, msgs)
    },
    [runStream, patchChatLocal, persist],
  )

  // ---- render ----
  if (loading) {
    return (
      <div className="h-dvh flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-2 border-bg-border border-t-accent animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex h-dvh overflow-hidden">
      <Sidebar
        chats={chats}
        activeChatId={activeChatId}
        onSelectChat={selectChat}
        onNewChat={handleNewChat}
        onDeleteChat={handleDeleteChat}
        onRenameChat={renameChat}
        onTogglePin={togglePin}
        onOpenSettings={() => setSettingsOpen(true)}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />
      <main className="flex-1 min-w-0 flex flex-col">
        <ChatView
          chat={activeChat}
          chats={chats}
          models={models}
          currentModelId={currentModelId}
          isStreaming={streamingChatId !== null && streamingChatId === activeChatId}
          draft={draft}
          setDraft={setDraft}
          onSend={send}
          onStop={stopStreaming}
          onRegenerate={regenerate}
          onEditMessage={editMessage}
          onSelectModel={setModel}
          onOpenSidebar={() => setSidebarOpen(true)}
          onOpenSettings={() => setSettingsOpen(true)}
          webSearch={webSearch}
          onToggleWebSearch={hasOpenRouter() ? () => setWebSearch((v) => !v) : undefined}
          imageMode={imageMode}
          onToggleImageMode={() => setImageMode((v) => !v)}
          onOpenVoice={() => setVoiceOpen(true)}
        />
      </main>
      <Settings
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onExport={handleExport}
        onImport={handleImport}
        onDeleteAll={handleDeleteAll}
        authUser={authUser}
      />
      <VoiceMode open={voiceOpen} onClose={() => setVoiceOpen(false)} onSendMessage={send} />
      <Toasts />
    </div>
  )
}
