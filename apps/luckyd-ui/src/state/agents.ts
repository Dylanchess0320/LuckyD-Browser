import { create } from 'zustand';
import { useCallback } from 'react';

// LuckyD Agents Bridge (scripts/luckyd_agents_bridge.py, spawned by
// electron-main) — serves the browser's own Agent Mesh terminal pages on
// 127.0.0.1:9885 and proxies model switching to browser/data/settings.json.

export interface CatalogProvider {
  name: string;
  base_url: string;
  env_key: string;
  free_models: string[];
}
export interface Catalog {
  ai_providers: Record<string, CatalogProvider>;
}
export interface CurrentModel {
  provider: string;
  model: string;
  overrides: Record<string, string>;
  settings_path: string;
}
export interface MeshPing {
  ok: boolean;
  ws_port: number;
  shells: Record<string, boolean>;
}
// 10.5 health snapshot — mirrors core/providers.list_providers() 1:1.
export interface ProviderHealth {
  id: string;
  name: string;
  model: string;
  configured: boolean;
  key_present: boolean;
  requires_key: boolean;
  env_key?: string | null;
  local: boolean;
  free_tier: boolean;
  current: boolean;
  credit_exhausted: boolean;
  /** Position in the free rotation (0-based), null when not in the chain. */
  rotation_order: number | null;
  /** True for the rotation member the switcher would try next. */
  next_in_rotation: boolean;
  /** Seconds until the Cline 402 exhaustion marker expires (0 = n/a). */
  credit_ttl_remaining_sec: number;
  status: 'ready' | 'needs_key' | 'exhausted';
  last_working: boolean;
  last_working_ago: string | null;
  last_working_model: string | null;
}

/** GET /api/providers — live "what would work right now" snapshot. */
export interface HealthSnapshot {
  providers: ProviderHealth[];
  /** Best usable free provider id (core.free_rotation.best_free_provider). */
  best_free: string | null;
  /** Default model for best_free. */
  best_free_model: string | null;
  /** Currently answering provider/model (core.free_rotation.get_active_pair). */
  active: { provider: string; model: string } | null;
  /** Last pair that demonstrably answered (core.last_working), if fresh. */
  last_working: { provider: string; model: string; timestamp: number } | null;
}

export const BRIDGE_URL = 'http://127.0.0.1:9885';

// All allowlisted shells from browser_core/terminal_server.py
// Dylan 2026-09-14: DeepSeek + Muse Spark removed — Muse Code stays.
// OpenCode (mesh-opencode) restored alongside MiniMax Code (mcode) + media (mmx).
// 10.1: Google Jules (mesh-jules) joins the mesh — async cloud coder.
// 2026-09-24: Aider (mesh-aider) joins — open-source git-native pair programmer.
export const MESH_AGENTS: { id: string; label: string; emoji: string }[] = [
  { id: 'mesh-agy', label: 'Antigravity', emoji: '🛸' },
  { id: 'mesh-claude', label: 'Claude', emoji: '🟠' },
  { id: 'mesh-codex', label: 'Codex', emoji: '🟢' },
  { id: 'mesh-copilot', label: 'Copilot', emoji: '⚫' },
  { id: 'mesh-qwen', label: 'Qwen', emoji: '🟣' },
  { id: 'mesh-opencode', label: 'OpenCode', emoji: '🔵' },
  { id: 'mesh-mcode', label: 'MiniMax Code', emoji: '🟥' },
  { id: 'mesh-mmx', label: 'MiniMax Media', emoji: '🎬' },
  { id: 'mesh-cline', label: 'Cline', emoji: '🟡' },
  { id: 'mesh-openclaw', label: 'OpenClaw', emoji: '🦞' },
  { id: 'mesh-hermes', label: 'Hermes', emoji: '⚕' },
  { id: 'mesh-pi', label: 'Pi', emoji: '⚪' },
  { id: 'mesh-grok', label: 'Grok', emoji: '𝕏' },
  { id: 'mesh-muse', label: 'Muse Code', emoji: 'Ⓜ️' },
  { id: 'mesh-jules', label: 'Jules', emoji: '☁️' },
  { id: 'mesh-aider', label: 'Aider', emoji: '⌨️' },
];

export function terminalUrl(shell: string): string {
  return `${BRIDGE_URL}/terminal?shell=${encodeURIComponent(shell)}`;
}

/**
 * The bridge writes browser/data/settings.json, which uses the browser's
 * provider namespace ("google"); core/free_rotation speaks "gemini".
 * The bridge normalizes on write, so compare UI state through this helper.
 */
export function toBrowserProviderId(id: string): string {
  return id === 'gemini' ? 'google' : id;
}

interface AgentsState {
  ping: MeshPing | null;
  catalog: Catalog | null;
  current: CurrentModel | null;
  health: HealthSnapshot | null;
  switching: boolean;
  refresh: () => Promise<void>;
  setModel: (provider: string, model: string) => Promise<void>;
  /** One click: switch to the best provider that works right now. */
  switchToBest: () => Promise<boolean>;
}

async function fetchJson<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

export const useAgents = create<AgentsState>((set, get) => ({
  ping: null,
  catalog: null,
  current: null,
  health: null,
  switching: false,
  refresh: async () => {
    try {
      const [ping, catalog, current, health] = await Promise.all([
        fetchJson<MeshPing>(`${BRIDGE_URL}/api/ping`),
        fetchJson<Catalog>(`${BRIDGE_URL}/api/catalog`),
        fetchJson<CurrentModel>(`${BRIDGE_URL}/api/model`),
        // 10.5 health snapshot + one-click fix target (null on old bridges).
        fetchJson<HealthSnapshot>(`${BRIDGE_URL}/api/providers`),
      ]);
      if (!ping && !catalog && !current && !health) {
        set({ ping: null });
        return;
      }
      set({ ping, catalog, current });
      // Older bridges may not serve /api/providers yet — keep going.
      if (health && Array.isArray(health.providers)) set({ health });
    } catch {
      set({ ping: null });
    }
  },
  setModel: async (provider, model) => {
    set({ switching: true });
    try {
      const r = await fetch(`${BRIDGE_URL}/api/model`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, model }),
      });
      if (r.ok) {
        const next = (await r.json()) as CurrentModel & {
          health?: HealthSnapshot | null;
        };
        set({ current: next });
        // The bridge re-reads settings.json and returns a fresh health
        // snapshot with every switch — the UI updates instantly, no
        // restart of the bridge or the terminal agent required.
        if (next.health && Array.isArray(next.health.providers)) {
          set({ health: next.health });
        }
      }
    } finally {
      set({ switching: false });
    }
  },
  // 10.5 one-click fix: switch to best_free_provider(). Refreshes first so
  // the pick is never stale. Returns true when a switch was performed.
  switchToBest: async () => {
    await get().refresh();
    const h = get().health;
    if (!h?.best_free || !h?.best_free_model) return false;
    await get().setModel(h.best_free, h.best_free_model);
    await get().refresh();
    return true;
  },
}));

export function useAgentsRefresh() {
  return useCallback(() => useAgents.getState().refresh(), []);
}

/** "worked 2h ago" style label for the last-known-working pair. */
export function lastWorkingAge(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}
