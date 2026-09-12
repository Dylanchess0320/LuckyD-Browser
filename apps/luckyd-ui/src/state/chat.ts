import { create } from 'zustand';
import { loadBackendConfig, saveBackendConfig, api } from '../lib/backend';
import type { BackendConfig } from '../lib/backend';
import { startBackgroundChat, waitForTask } from '../lib/tasks';
import type { TaskPhase } from '../lib/tasks';

export interface ChatMsg {
  id: string;
  role: 'user' | 'agent';
  text: string;
  phase?: TaskPhase | 'streaming';
  taskId?: string;
  elapsedSec?: number;
  done?: boolean;
}

export interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  messages: ChatMsg[];
}

interface LuckyState {
  backend: BackendConfig;
  setBackend: (cfg: BackendConfig) => void;
  connected: boolean | null;
  provider: string;
  model: string;
  checkHealth: () => Promise<void>;
  sessions: ChatSession[];
  activeId: string | null;
  newSession: () => void;
  selectSession: (id: string) => void;
  deleteSession: (id: string) => void;
  messages: ChatMsg[];
  busy: boolean;
  workingLabel: string;
  send: (text: string) => Promise<void>;
  stop: () => void;
  clearChat: () => Promise<void>;
  lastError: string | null;
}

const SESS_KEY = 'luckyd.sessions.v1';
let msgId = 0;
const nextId = (p: string) => `${p}${++msgId}_${Date.now()}`;
let stopFlag = false;

function loadSessions(): ChatSession[] {
  try {
    const raw = JSON.parse(localStorage.getItem(SESS_KEY) ?? '[]');
    if (Array.isArray(raw)) return raw.filter((s) => s?.id).slice(0, 50);
  } catch { /* fresh start */ }
  return [];
}

function persist(sessions: ChatSession[]) {
  try { localStorage.setItem(SESS_KEY, JSON.stringify(sessions.slice(0, 50))); }
  catch { /* quota — non-fatal */ }
}

function fmtElapsed(sec: number): string {
  if (sec < 60) return `${Math.floor(sec)}s`;
  return `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s`;
}

export const useLuckyBase = create<LuckyState>((set, get) => ({
  backend: loadBackendConfig(),
  setBackend: (cfg) => {
    saveBackendConfig(cfg);
    set({ backend: cfg, connected: null });
    void get().checkHealth();
  },
  connected: null,
  provider: '',
  model: '',
  lastError: null,
  checkHealth: async () => {
    const { backend } = get();
    try {
      const h = await api<{ status: string }>(backend, '/health');
      if (h.status !== 'healthy') throw new Error('unhealthy');
      const s = await api<{ provider: string; model: string }>(backend, '/api/settings');
      set({ connected: true, provider: s.provider, model: s.model });
    } catch (e) {
      set({ connected: false, lastError: e instanceof Error ? e.message : 'down' });
    }
  },
  sessions: loadSessions(),
  activeId: null,
  newSession: () => {
    stopFlag = true;
    const s: ChatSession = { id: nextId('s'), title: 'New chat', createdAt: Date.now(), messages: [] };
    set((st) => {
      const sessions = [s, ...st.sessions].slice(0, 50);
      persist(sessions);
      return { sessions, activeId: s.id, messages: [], busy: false, workingLabel: '' };
    });
  },
  selectSession: (id) => {
    stopFlag = true;
    const s = get().sessions.find((x) => x.id === id);
    if (!s) return;
    set({ activeId: id, messages: s.messages, busy: false, workingLabel: '' });
  },
  deleteSession: (id) => {
    set((st) => {
      const sessions = st.sessions.filter((x) => x.id !== id);
      persist(sessions);
      if (st.activeId !== id) return { sessions };
      const nx = sessions[0];
      return { sessions, activeId: nx?.id ?? null, messages: nx?.messages ?? [], busy: false, workingLabel: '' };
    });
  },
  messages: [],
  busy: false,
  workingLabel: '',

  send: async (text) => {
    const { backend, busy, sessions, activeId, messages } = get();
    if (busy || !text.trim()) return;
    stopFlag = false;
    const clean = text.trim();
    const user: ChatMsg = { id: nextId('m'), role: 'user', text: clean };
    const agent: ChatMsg = { id: nextId('m'), role: 'agent', text: '', phase: 'queued' };
    let sid = activeId;
    let list = sessions;
    if (!sid || !list.some((s) => s.id === sid)) {
      const s: ChatSession = { id: nextId('s'), title: clean.slice(0, 48), createdAt: Date.now(), messages: [] };
      list = [s, ...list].slice(0, 50);
      sid = s.id;
    }
    const base = [...messages, user, agent];
    const sync = (msgs: ChatMsg[]) => {
      const id = get().activeId;
      set((st) => {
        const sessions = st.sessions.map((s) => (s.id === id ? { ...s, messages: msgs } : s));
        persist(sessions);
        return { sessions, messages: msgs };
      });
    };
    persist(list.map((s) => (s.id === sid ? { ...s, messages: base } : s)));
    set({ sessions: list, activeId: sid, messages: base, busy: true, workingLabel: 'Queued…', lastError: null });
    try {
      // Non-blocking: same agent on a worker thread (was: /api/chat froze the tab).
      const taskId = await startBackgroundChat(backend, clean);
      set((st) => ({ messages: st.messages.map((m) => (m.id === agent.id ? { ...m, taskId } : m)) }));
      const fin = await waitForTask(backend, taskId, {
        shouldStop: () => stopFlag,
        onTick: (sec) => set((st) => ({
          workingLabel: `Working… · ${fmtElapsed(sec)}`,
          messages: st.messages.map((m) => (m.id === agent.id ? { ...m, phase: 'running' as const, elapsedSec: sec } : m)),
        })),
      });
      const answer = (fin.result ?? '').trim() || (fin.error ? `Error: ${fin.error}` : '(empty)');
      sync(get().messages.map((m) => (m.id === agent.id ? { ...m, text: answer, phase: 'done' as const, done: true } : m)));
      set({ busy: false, workingLabel: '' });
    } catch (e) {
      const em = e instanceof Error ? e.message : String(e);
      sync(get().messages.map((m) => (m.id === agent.id ? { ...m, text: `Error: ${em}`, phase: 'error' as const, done: true } : m)));
      set({ busy: false, workingLabel: '', lastError: em });
    }
  },
  stop: () => {
    stopFlag = true;
    set({ busy: false, workingLabel: '' });
  },
  clearChat: async () => {
    stopFlag = true;
    const { backend } = get();
    try { await api(backend, '/api/clear', { method: 'POST', json: {} }); }
    catch { /* non-fatal */ }
    get().newSession();
  },
}));
