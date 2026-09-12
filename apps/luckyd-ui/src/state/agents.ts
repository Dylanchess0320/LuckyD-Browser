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

export const BRIDGE_URL = 'http://127.0.0.1:9885';

// All allowlisted shells from browser_core/terminal_server.py
export const MESH_AGENTS: { id: string; label: string; emoji: string }[] = [
  { id: 'mesh-agy', label: 'Antigravity', emoji: '🛸' },
  { id: 'mesh-claude', label: 'Claude', emoji: '🟠' },
  { id: 'mesh-codex', label: 'Codex', emoji: '🟢' },
  { id: 'mesh-copilot', label: 'Copilot', emoji: '⚫' },
  { id: 'mesh-qwen', label: 'Qwen', emoji: '🟣' },
  { id: 'mesh-opencode', label: 'OpenCode', emoji: '🔵' },
  { id: 'mesh-cline', label: 'Cline', emoji: '🟡' },
  { id: 'mesh-openclaw', label: 'OpenClaw', emoji: '🦞' },
  { id: 'mesh-dsh', label: 'DeepSeek', emoji: '🐋' },
  { id: 'mesh-hermes', label: 'Hermes', emoji: '⚕' },
  { id: 'mesh-pi', label: 'Pi', emoji: '⚪' },
  { id: 'mesh-grok', label: 'Grok', emoji: '𝕏' },
  { id: 'mesh-muse', label: 'Muse Code', emoji: 'Ⓜ️' },
  { id: 'mesh-muse-spark', label: 'Muse Spark', emoji: '✨' },
];

export function terminalUrl(shell: string): string {
  return `${BRIDGE_URL}/terminal?shell=${encodeURIComponent(shell)}`;
}

interface AgentsState {
  ping: MeshPing | null;
  catalog: Catalog | null;
  current: CurrentModel | null;
  switching: boolean;
  refresh: () => Promise<void>;
  setModel: (provider: string, model: string) => Promise<void>;
}

export const useAgents = create<AgentsState>((set, get) => ({
  ping: null,
  catalog: null,
  current: null,
  switching: false,
  refresh: async () => {
    try {
      const [ping, catalog, current] = await Promise.all([
        fetch(`${BRIDGE_URL}/api/ping`).then((r) => r.json()),
        fetch(`${BRIDGE_URL}/api/catalog`).then((r) => r.json()),
        fetch(`${BRIDGE_URL}/api/model`).then((r) => r.json()),
      ]);
      set({ ping, catalog, current });
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
}));

export function useAgentsRefresh() {
  return useCallback(() => useAgents.getState().refresh(), []);
}
