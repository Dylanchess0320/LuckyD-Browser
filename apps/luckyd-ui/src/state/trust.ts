import { create } from 'zustand';
import {
  ScopeInfo,
  api,
  type AuditEvent,
  type BackendConfig,
  type PendingApproval,
} from '../lib/backend';
import { useLuckyBase } from './chat';

interface TrustState {
  scopes: ScopeInfo[];
  trustMode: string;
  audit: AuditEvent[];
  pending: PendingApproval[];
  refreshTrust: () => Promise<void>;
  resolveApproval: (callId: string, ok: boolean, remember: string) => Promise<void>;
  setScopePolicy: (scope: string, policy: string) => Promise<void>;
  setMode: (mode: string) => Promise<void>;
}

function backend(): BackendConfig {
  return useLuckyBase.getState().backend;
}

export const useTrust = create<TrustState>((set) => ({
  scopes: [],
  trustMode: '',
  audit: [],
  pending: [],
  refreshTrust: async () => {
    const b = backend();
    const [sc, au, pe] = await Promise.all([
      api<{ mode: string; scopes: ScopeInfo[] }>(b, '/api/trust/scopes'),
      api<{ events: AuditEvent[] }>(b, '/api/audit?limit=100'),
      api<{ pending: PendingApproval[] }>(b, '/api/approvals/pending'),
    ]);
    set({ scopes: sc.scopes, trustMode: sc.mode, audit: au.events, pending: pe.pending });
  },
  resolveApproval: async (callId, approved, remember) => {
    const b = backend();
    await api(b, '/api/approvals/resolve', {
      method: 'POST', json: { call_id: callId, approved, remember },
    });
    await useTrust.getState().refreshTrust();
  },
  setScopePolicy: async (scope, policy) => {
    const b = backend();
    await api(b, '/api/trust/policy', {
      method: 'POST', json: { action: 'set_scope', scope, policy },
    });
    await useTrust.getState().refreshTrust();
  },
  setMode: async (mode) => {
    const b = backend();
    await api(b, '/api/trust/policy', {
      method: 'POST', json: { action: 'set_mode', mode },
    });
    await useTrust.getState().refreshTrust();
  },
}));
