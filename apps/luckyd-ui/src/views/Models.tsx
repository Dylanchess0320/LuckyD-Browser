import { useEffect, useMemo, useState } from 'react';
import { useAgents, type CatalogProvider, type ProviderHealth } from '../state/agents';
import { useLuckyBase } from '../state/chat';

function HealthBadge({ h }: { h: ProviderHealth }) {
  if (h.credit_exhausted) {
    return (
      <span
        title="Credits exhausted (HTTP 402) — top up, then clear the marker"
        className="rounded-full bg-ld-danger/15 px-2 py-0.5 text-[10px] font-semibold text-ld-danger"
      >
        Exhausted
      </span>
    );
  }
  if (h.configured) {
    return (
      <span className="rounded-full bg-ld-ok/15 px-2 py-0.5 text-[10px] font-semibold text-ld-ok">
        Ready
      </span>
    );
  }
  return (
    <span className="rounded-full bg-ld-warn/15 px-2 py-0.5 text-[10px] font-semibold text-ld-warn">
      Needs key
    </span>
  );
}

export default function Models() {
  const { catalog, current, switching, refresh, setModel, health, bestFree, switchToBest } =
    useAgents();
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

  const fixToBest = async () => {
    await switchToBest();
    void checkHealth();
  };

  const healthById = useMemo(() => {
    const m = new Map<string, ProviderHealth>();
    for (const h of health ?? []) m.set(h.id, h);
    return m;
  }, [health]);

  // Warning banner when the *selected* model is currently unusable.
  const currentHealth = current ? healthById.get(current.provider) : undefined;
  const currentUnusable =
    currentHealth != null && (!currentHealth.configured || currentHealth.credit_exhausted);
  const unusableReason = currentHealth?.credit_exhausted
    ? 'its credits are exhausted (HTTP 402)'
    : 'it has no API key configured';

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

        {currentUnusable && current && (
          <div className="mt-3 rounded-xl border border-ld-danger/50 bg-ld-danger/10 px-4 py-3 text-xs">
            <div className="font-semibold text-ld-danger">
              ⚠️ {current.provider}/{current.model || 'auto'} is currently unusable —{' '}
              {unusableReason}.
            </div>
            <button
              onClick={() => void fixToBest()}
              disabled={switching || !bestFree}
              title={
                bestFree
                  ? `Switch to ${bestFree.provider}/${bestFree.model}`
                  : 'No working free model found'
              }
              className="mt-2 rounded-lg bg-ld-accent px-3 py-1.5 font-semibold text-white transition disabled:opacity-50"
            >
              {switching
                ? 'Switching…'
                : bestFree
                  ? `Switch to best working (${bestFree.provider}/${bestFree.model})`
                  : 'No working model available'}
            </button>
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
            const h = healthById.get(pid);
            return (
              <section key={pid}>
                <h3 className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                  {p.name}
                  <span className="rounded-full border border-ld-border px-2 py-0.5 text-[10px] font-normal text-ld-muted">
                    {models.length} free
                  </span>
                  {h && <HealthBadge h={h} />}
                  {h?.rotation_order != null && (
                    <span
                      title={
                        h.next_in_rotation
                          ? 'Next in the free-model rotation'
                          : `Rotation order #${h.rotation_order + 1}`
                      }
                      className={`rounded-full border px-2 py-0.5 text-[10px] font-normal ${
                        h.next_in_rotation
                          ? 'border-ld-accent/60 text-ld-accent'
                          : 'border-ld-border text-ld-muted'
                      }`}
                    >
                      #{h.rotation_order + 1}
                      {h.next_in_rotation ? ' · next' : ''}
                    </span>
                  )}
                  {h?.last_working && h.last_working_ago && (
                    <span
                      title={h.last_working_model ?? undefined}
                      className="rounded-full border border-ld-border px-2 py-0.5 text-[10px] font-normal text-ld-muted"
                    >
                      ✓ {h.last_working_ago}
                    </span>
                  )}
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
