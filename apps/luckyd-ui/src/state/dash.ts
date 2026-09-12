import { create } from 'zustand';
import { api } from '../lib/backend';
import { useLuckyBase } from './chat';

export interface CostState {
  input: number;
  output: number;
  usd: number;
}

interface DashState {
  cost: CostState | null;
  brainCount: number;
  brainEdges: number;
  toolCount: number;
  schedCount: number;
  runCount: number;
  approvalCount: number;
  refresh: () => Promise<void>;
}

export const useDash = create<DashState>((set) => ({
  cost: null,
  brainCount: 0,
  brainEdges: 0,
  toolCount: 0,
  schedCount: 0,
  runCount: 0,
  approvalCount: 0,
  refresh: async () => {
    const b = useLuckyBase.getState().backend;
    try {
      const [cost, brain, tools, sched, appr] = await Promise.all([
        api<{ input_tokens: number; output_tokens: number; total_cost: number }>(b, '/api/cost').catch(() => null),
        api<{ count: number; edges: number }>(b, '/api/brain/stats').catch(() => null),
        api<{ count: number }>(b, '/api/tools').catch(() => null),
        api<{ schedules: unknown[] }>(b, '/api/schedules').catch(() => null),
        api<{ pending: unknown[] }>(b, '/api/approvals/pending').catch(() => null),
      ]);
      set({
        cost: cost ? { input: cost.input_tokens, output: cost.output_tokens, usd: cost.total_cost } : null,
        brainCount: brain?.count ?? 0,
        brainEdges: brain?.edges ?? 0,
        toolCount: tools?.count ?? 0,
        schedCount: sched?.schedules.length ?? 0,
        approvalCount: appr?.pending.length ?? 0,
      });
    } catch { /* dashboard is best-effort */ }
  },
}));
