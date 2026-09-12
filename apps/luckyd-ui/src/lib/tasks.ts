// ── Background (non-blocking) agent runs ─────────────────────────────
// The agent loop can take minutes; /api/chat blocks until it's done —
// the tab looks frozen and the Qt WebView often kills it. /api/background/*
// runs the SAME agent on a worker thread and lets the UI poll. Same brain,
// same tools, same audit trail — just non-blocking.
import { api } from './backend';
import type { BackendConfig } from './backend';

export interface TaskStartResp {
  task_id: string;
  id?: string;
}

export type TaskPhase = 'queued' | 'running' | 'done' | 'error';

export interface TaskStatus {
  id: string;
  task: string;
  status: TaskPhase | string;
  result?: string;
  error?: string;
  created?: number;
}

export async function startBackgroundChat(
  cfg: BackendConfig,
  message: string,
): Promise<string> {
  const r = await api<TaskStartResp>(cfg, '/api/background/start', {
    method: 'POST',
    json: { task: message },
  });
  return r.task_id ?? r.id ?? '';
}

export async function pollTask(cfg: BackendConfig, taskId: string): Promise<TaskStatus> {
  // /status and /result both return the task record; /status never deletes.
  return api<TaskStatus>(cfg, `/api/background/status/${taskId}`);
}

export interface StreamCallbacks {
  onTick?: (elapsedSec: number) => void;
  shouldStop?: () => boolean;
}

/** Poll a background task until done/error. Resolves with the final record. */
export async function waitForTask(
  cfg: BackendConfig,
  taskId: string,
  cb?: StreamCallbacks,
): Promise<TaskStatus> {
  const started = Date.now();
  const pollMs = 1200;
  const maxMs = 30 * 60 * 1000; // 30 min — matches server worker timeout headroom
  for (;;) {
    if (cb?.shouldStop?.()) {
      // Client-side stop: the server keeps running (audit-safe), we just detach.
      const cur = await pollTask(cfg, taskId).catch(() => null);
      return { id: taskId, task: '', status: 'error', error: 'Stopped by user.', ...cur } as TaskStatus;
    }
    const st = await pollTask(cfg, taskId);
    cb?.onTick?.((Date.now() - started) / 1000);
    if (st.status === 'done' || st.status === 'error') return st;
    if (Date.now() - started > maxMs) {
      return { ...st, status: 'error', error: 'Timed out waiting for agent (30 min).' };
    }
    await new Promise((r) => setTimeout(r, pollMs));
  }
}
