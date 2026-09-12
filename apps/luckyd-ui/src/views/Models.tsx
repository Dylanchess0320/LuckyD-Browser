import { useEffect, useState } from 'react';
import { useAgents, type CatalogProvider } from '../state/agents';
import { useLuckyBase } from '../state/chat';

export default function Models() {
  const { catalog, current, switching, refresh, setModel } = useAgents();
  const { connected, checkHealth } = useLuckyBase();
  const [filter, setFilter] = useState('');

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const pick = async (provider: string, model: string) => {
    await setModel(provider, model);
    // The backend mirrors settings.json per request — nudge the health dot.
    void checkHealth();
  };

  const entries = Object.entries(catalog?.ai_providers ?? {}) as [
    string,
    CatalogProvider,
  ][];

  return (
    <div className="h-full overflow-y-auto px-5 py-6">
      <div className="mx-auto max-w-3xl">
        <h2 className="text-lg font-bold">🧠 Free Models</h2>
        <p className="mt-1 text-xs text-ld-muted">
          Zero-cost models from the browser's registry. Selecting one rewrites{' '}
          <code className="font-mono">browser/data/settings.json</code> — the backend mirrors
          it per request, so the next chat run uses it. No restart needed.
        </p>

        {current && (
          <div className="mt-4 rounded-xl border border-ld-accent/40 bg-ld-accent/5 px-4 py-3 text-xs">
            <span className="font-semibold text-ld-accent">Active:</span>{' '}
            <span className="font-mono">
              {current.provider}/{current.model || 'auto'}
            </span>
            {switching && <span className="ml-2 text-ld-muted">switching…</span>}
          </div>
        )}

        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter models…"
          className="mt-4 w-full rounded-xl border border-ld-border bg-ld-card px-4 py-2.5 text-sm outline-none placeholder:text-ld-faint focus:border-ld-accent"
        />

        <div className="mt-4 space-y-5">
          {entries.map(([pid, p]) => {
            const models = p.free_models.filter((m) =>
              m.toLowerCase().includes(filter.toLowerCase()),
            );
            if (models.length === 0) return null;
            const active = current?.provider === pid;
            return (
              <section key={pid}>
                <h3 className="flex items-center gap-2 text-sm font-semibold">
                  {p.name}
                  <span className="rounded-full border border-ld-border px-2 py-0.5 text-[10px] font-normal text-ld-muted">
                    {models.length} free
                  </span>
                  {active && (
                    <span className="rounded-full bg-ld-ok/15 px-2 py-0.5 text-[10px] text-ld-ok">
                      active
                    </span>
                  )}
                </h3>
                <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                  {models.map((m) => {
                    const selected = active && current?.model === m;
                    return (
                      <button
                        key={m}
                        disabled={switching}
                        onClick={() => {
                          void pick(pid, m);
                        }}
                        className={`flex items-center justify-between rounded-lg border px-3 py-2 font-mono text-xs transition disabled:opacity-50 ${
                          selected
                            ? 'border-ld-ok bg-ld-ok/10 text-ld-text'
                            : 'border-ld-border text-ld-muted hover:border-ld-accent hover:text-ld-text'
                        }`}
                      >
                        <span className="truncate">{m}</span>
                        {selected && <span className="ml-2 text-ld-ok">✓</span>}
                      </button>
                    );
                  })}
                </div>
              </section>
            );
          })}
          {entries.length === 0 && (
            <div className="rounded-xl border border-ld-border bg-ld-panel px-4 py-6 text-center text-xs text-ld-muted">
              Catalog unavailable — is the agents bridge running?
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
