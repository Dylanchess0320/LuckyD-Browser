import { create } from 'zustand';
import { Schedule, SchedRun, api } from '../lib/backend';
import { useLuckyBase } from './chat';

interface SchedState {
  schedules: Schedule[];
  runs: SchedRun[];
  refreshSchedules: () => Promise<void>;
  runNow: (id: string) => Promise<void>;
  toggle: (s: Schedule) => Promise<void>;
  remove: (id: string) => Promise<void>;
  create: (body: Record<string, unknown>) => Promise<string | null>;
}

export const useSched = create<SchedState>((set) => ({
  schedules: [],
  runs: [],
  refreshSchedules: async () => {
    const b = useLuckyBase.getState().backend;
    const [ss, rr] = await Promise.all([
      api<{ schedules: Schedule[] }>(b, '/api/schedules'),
      api<{ runs: SchedRun[] }>(b, '/api/schedules/runs?limit=15'),
    ]);
    set({ schedules: ss.schedules, runs: rr.runs });
  },
  runNow: async (id) => {
    const b = useLuckyBase.getState().backend;
    await api(b, `/api/schedules/${id}/run`, { method: 'POST', json: {} });
    await useSched.getState().refreshSchedules();
  },
  toggle: async (s) => {
    const b = useLuckyBase.getState().backend;
    await api(b, `/api/schedules/${s.id}/${s.enabled ? 'disable' : 'enable'}`, {
      method: 'POST', json: {},
    });
    await useSched.getState().refreshSchedules();
  },
  remove: async (id) => {
    const b = useLuckyBase.getState().backend;
    await api(b, `/api/schedules/${id}/delete`, { method: 'POST', json: {} });
    await useSched.getState().refreshSchedules();
  },
  create: async (body) => {
    const b = useLuckyBase.getState().backend;
    try {
      const r = await api<{ schedule?: { id: string }; error?: string }>(
        b, '/api/schedules', { method: 'POST', json: body },
      );
      if (r.error) return r.error;
      await useSched.getState().refreshSchedules();
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : 'create failed';
    }
  },
}));
