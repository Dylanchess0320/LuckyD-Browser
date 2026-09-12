import { useEffect } from 'react';
import { useTrust } from '../state/trust';

const RISK_COLOR: Record<string, string> = {
  low: 'text-ld-ok border-ld-ok',
  medium: 'text-yellow-400 border-yellow-400',
  high: 'text-ld-danger border-ld-danger',
};

export default function Trust() {
  const { scopes, trustMode, audit, pending, refreshTrust, resolveApproval, setScopePolicy, setMode } =
    useTrust();

  useEffect(() => {
    void refreshTrust();
    const t = setInterval(() => void refreshTrust(), 3000);
    return () => clearInterval(t);
  }, [refreshTrust]);

  return (
    <div className="mx-auto h-full max-w-4xl space-y-5 overflow-y-auto px-5 py-6">
      <div>
        <h1 className="text-lg font-semibold">Trust Center</h1>
        <p className="text-xs text-ld-muted">Agentic with receipts — every tool call, logged.</p>
      </div>

      {pending.length > 0 && (
        <section className="rounded-2xl border border-yellow-400/40 bg-ld-card p-4">
          <h2 className="text-sm font-semibold text-yellow-300">
            ⏳ {pending.length} approval{pending.length > 1 ? 's' : ''} waiting
          </h2>
          <div className="mt-3 space-y-3">
            {pending.map((p) => (
              <ApprovalCard key={p.call_id} p={p} onResolve={resolveApproval} />
            ))}
          </div>
        </section>
      )}

      <section className="rounded-2xl border border-ld-border bg-ld-panel p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Policy mode</h2>
          <select
            value={trustMode}
            onChange={(e) => void setMode(e.target.value)}
            className="rounded-lg border border-ld-border bg-ld-window px-3 py-1.5 text-sm"
          >
            {['ask', 'auto', 'step-through'].map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
        <div className="mt-3 space-y-2">
          {scopes.map((s) => (
            <div key={s.id} className="flex items-center justify-between gap-3 rounded-xl border border-ld-border bg-ld-card px-4 py-3">
              <div>
                <div className="text-sm font-medium">{s.title}</div>
                <div className="text-xs text-ld-muted">{s.tools.length} tools</div>
              </div>
              <select
                value={s.policy}
                onChange={(e) => void setScopePolicy(s.id, e.target.value)}
                className="rounded-lg border border-ld-border bg-ld-window px-3 py-1.5 text-sm"
              >
                {['ask', 'allow', 'deny'].map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-2xl border border-ld-border bg-ld-panel p-4">
        <h2 className="text-sm font-semibold">Audit log</h2>
        <div className="mt-3 space-y-1.5">
          {audit.slice(0, 50).map((e, i) => (
            <div key={i} className="flex items-center gap-3 rounded-lg px-2 py-1.5 text-xs hover:bg-ld-card">
              <span className="font-mono text-ld-muted">{e.ts.slice(11, 19)}</span>
              <span className="font-medium">{e.tool}</span>
              <span className={`rounded-full border px-2 py-0.5 ${RISK_COLOR[e.risk] ?? 'border-ld-border text-ld-muted'}`}>
                {e.risk}
              </span>
              <span className="truncate text-ld-muted">{e.summary ?? ''}</span>
            </div>
          ))}
          {audit.length === 0 && <p className="text-xs text-ld-muted">No events yet.</p>}
        </div>
      </section>
    </div>
  );
}

import type { PendingApproval } from '../lib/backend';

function ApprovalCard({
  p,
  onResolve,
}: {
  p: PendingApproval;
  onResolve: (id: string, ok: boolean, remember: string) => Promise<void>;
}) {
  return (
    <div className="rounded-xl border border-ld-border bg-ld-panel p-3">
      <div className="text-sm font-semibold">⚠ {p.tool}</div>
      <pre className="mt-1 max-h-24 overflow-auto rounded-lg bg-ld-window p-2 font-mono text-xs text-ld-muted">
        {JSON.stringify(p.args, null, 2)}
      </pre>
      <div className="mt-2 flex gap-2">
        <button
          onClick={() => void onResolve(p.call_id, true, 'once')}
          className="rounded-lg bg-ld-ok px-3 py-1.5 text-xs font-semibold text-black"
        >
          Approve
        </button>
        <button
          onClick={() => void onResolve(p.call_id, false, 'once')}
          className="rounded-lg border border-ld-danger px-3 py-1.5 text-xs text-ld-danger"
        >
          Deny
        </button>
        <button
          onClick={() => void onResolve(p.call_id, true, 'always')}
          className="rounded-lg border border-ld-border px-3 py-1.5 text-xs text-ld-muted"
        >
          Always allow
        </button>
      </div>
    </div>
  );
}
