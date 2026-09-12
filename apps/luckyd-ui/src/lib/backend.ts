// LuckyD backend contract — mirrors web_server.py routes 1:1.
// No backend changes needed. Auth: `Authorization: Bearer <hq_token>`
// (.luckyd-code/hq_token) — injected by Electron, or pasted once in Settings.

export interface BackendConfig {
  baseUrl: string; // e.g. http://127.0.0.1:8000
  token: string;
}

declare global {
  interface Window {
    __LUCKYD__?: Partial<BackendConfig>;
  }
}

const LS_KEY = 'luckyd.backend';

export function loadBackendConfig(): BackendConfig {
  const injected = window.__LUCKYD__ ?? {};
  let stored: Partial<BackendConfig> = {};
  try {
    stored = JSON.parse(localStorage.getItem(LS_KEY) ?? '{}');
  } catch {
    stored = {};
  }
  return {
    baseUrl: injected.baseUrl ?? stored.baseUrl ?? 'http://127.0.0.1:8000',
    token: injected.token ?? stored.token ?? '',
  };
}

export function saveBackendConfig(cfg: BackendConfig) {
  localStorage.setItem(LS_KEY, JSON.stringify(cfg));
}

export class BackendError extends Error {
  code: number;
  constructor(code: number, message: string) {
    super(message);
    this.code = code;
  }
}

export interface ApiOptions {
  method?: string;
  json?: unknown;
  headers?: Record<string, string>;
}

export async function api<T>(
  cfg: BackendConfig,
  path: string,
  opts?: ApiOptions,
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts?.headers ?? {}),
  };
  if (cfg.token) headers['Authorization'] = `Bearer ${cfg.token}`;
  const res = await fetch(`${cfg.baseUrl}${path}`, {
    method: opts?.method ?? 'GET',
    headers,
    body: opts?.json !== undefined ? JSON.stringify(opts.json) : undefined,
  });
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const msg =
      (data as { error?: string } | null)?.error ?? `HTTP ${res.status}`;
    throw new BackendError(res.status, msg);
  }
  return data as T;
}

// ── Typed responses (subset the UI needs) ──

export interface HealthResp {
  status: string;
}
export interface ToolsResp {
  tools: { name: string; description: string; aliases: string[] }[];
  count: number;
}
export interface ModelsResp {
  models: string[];
  provider: string;
}
export interface SettingsResp {
  provider: string;
  model: string;
  base_url: string;
  max_turns: number;
}
export interface ChatResp {
  result?: string;
  response?: string;
  error?: string;
}
export interface CostResp {
  input_tokens: number;
  output_tokens: number;
  total_cost: number;
}
export interface BrainStatsResp {
  stats: Record<string, unknown>;
  count: number;
  edges: number;
}
export interface BrainSearchResp {
  results: { content: string; tags: string[]; score: number }[];
  query: string;
}
export interface ScopeInfo {
  id: string;
  title: string;
  desc: string;
  tools: string[];
  policy: string;
}
export interface TrustScopesResp {
  mode: string;
  scopes: ScopeInfo[];
  sites: Record<string, string>;
}
export interface AuditEvent {
  ts: string;
  tool: string;
  scope: string;
  risk: string;
  decision: string;
  summary?: string;
  args?: unknown;
}
export interface AuditResp {
  events: AuditEvent[];
  stats: { total: number; denied: number };
}
export interface PendingApproval {
  call_id: string;
  tool: string;
  args: unknown;
  scope: string;
  summary?: string;
}
export interface Schedule {
  id: string;
  name: string;
  prompt: string;
  cron?: string;
  every_minutes?: number;
  daily_at?: string;
  enabled: boolean;
  allow_scopes: string[];
  next_run_at?: string;
  last_status?: string;
}
export interface SchedulesResp {
  schedules: Schedule[];
}
export interface SchedRun {
  status: string;
  schedule_name: string;
  started_at?: string;
  duration_sec?: number;
  attempt?: number;
  summary?: string;
  error?: string;
}
