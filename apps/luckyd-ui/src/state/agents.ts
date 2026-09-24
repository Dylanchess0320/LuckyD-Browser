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
  local: boolean;
  free_tier: boolean;
  current: boolean;
  credit_exhausted: boolean;
  rotation_order: number | null;
  next_in_rotation: boolean;
  credit_ttl_remaining_sec: number;
  last_working: boolean;
  last_working_ago: string | null;
  last_working_model: string | null;
}
export interface BestFree {
  provider: string;
  model: string;
  available: boolean;
  last_working_ago?: string | null;
}

export const BRIDGE_URL = 'http://127.0.0.1:9885';

// All allowlisted shells from browser_core/terminal_server.py
// Dylan 2026-09-14: DeepSeek + Muse Spark removed — Muse Code stays.
// OpenCode (mesh-opencode) restored alongside MiniMax Code (mcode) + media (mmx).
// 10.1: Google Jules (mesh-jules) joins the mesh — async cloud coder.
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
];

export function terminalUrl(shell: string): string {
  return `${BRIDGE_URL}/terminal?shell=${encodeURIComponent(shell)}`;
}

interface AgentsState {
  ping: MeshPing | null;
  catalog: Catalog | null;
  current: CurrentModel | null;
  switching: boolean;
  health: ProviderHealth[] | null;
  bestFree: BestFree | null;
  refresh: () => Promise<void>;
  setModel: (provider: string, model: string) => Promise<void>;
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
  switching: false,
  health: null,
  bestFree: null,
  refresh: async () => {
    try {
      const [ping, catalog, current, providers, bestFree] = await Promise.all([
        fetchJson<MeshPing>(`${BRIDGE_URL}/api/ping`),
        fetchJson<Catalog>(`${BRIDGE_URL}/api/catalog`),
        fetchJson<CurrentModel>(`${BRIDGE_URL}/api/model`),
        // 10.5 health snapshot + one-click fix target (null on old bridges).
        fetchJson<{ providers: ProviderHealth[] }>(`${BRIDGE_URL}/api/providers`),
        fetchJson<BestFree>(`${BRIDGE_URL}/api/best-free`),
      ]);
      if (!ping && !catalog && !current && !providers && !bestFree) {
        set({ ping: null });
        return;
      }
      set({
        ping,
        catalog,
        current,
        health: providers?.providers ?? null,
        bestFree: bestFree && bestFree.available ? bestFree : null,
      });
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
        const next: CurrentModel = await r.json();
        set({ current: next });
      }
    } finally {
      set({ switching: false });
    }
  },
  // 10.5 one-click fix: switch to best_free_provider(). Refreshes first so
  // the pick is never stale. Returns true when a switch was performed.
  switchToBest: async () => {
    await get().refresh();
    const best = get().bestFree;
    if (!best?.provider || !best.model) return false;
    await get().setModel(best.provider, best.model);
    await get().refresh();
    return true;
  },
}));

export function useAgentsRefresh() {
  return useCallback(() => useAgents.getState().refresh(), []);
}
