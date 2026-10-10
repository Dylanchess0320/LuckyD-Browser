import { useEffect, useMemo, useState } from 'react';
import {
  useAgents,
  lastWorkingAge,
  toBrowserProviderId,
  type CatalogProvider,
  type ProviderHealth,
} from '../state/agents';
import { useLuckyBase } from '../state/chat';

function formatTtl(sec: number): string {
  if (sec <= 0) return '';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `~${h}h${m > 0 ? ` ${m}m` : ''}`;
  return `~${Math.max(1, m)}m`;
}

function StatusBadge({ p }: { p: ProviderHealth }) {
  if (p.status === 'ready')
    return (
      <span className="rounded-full border border-ld-ok/40 bg-ld-ok/15 px-2 py-0.5 text-[10px] text-ld-ok">
        ✓ Ready
      </span>
    );
  if (p.status === 'exhausted')
    return (
      <span className="rounded-full border border-ld-danger/40 bg-ld-danger/15 px-2 py-0.5 text-[10px] text-ld-danger">
        ✗ Exhausted
      </span>
    );
  return (
    <span className="rounded-full border border-ld-warn/40 bg-ld-warn/15 px-2 py-0.5 text-[10px] text-ld-warn">
      ⚠ Needs key

    </span>
  );
}

export default function Models() {
  const { catalog, current, health, switching, refresh, setModel, switchToBest } =
    useAgents();
  const { checkHealth } = useLuckyBase();
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

  const entries = Object.entries(catalog?.ai_providers ?? {}) as [
    string,
    CatalogProvider,
  ][];

  // Live rotation chain from the bridge health snapshot (core.free_rotation):
  // Cline → Gemini → local Ollama → other free, with live status.
  const rotation = useMemo(
    () =>
      (health?.providers ?? [])
        .filter((p) => p.rotation_order != null)
        .sort((a, b) => (a.rotation_order ?? 99) - (b.rotation_order ?? 99)),
    [health],
  );

  const healthById = useMemo(
    () => new Map((health?.providers ?? []).map((p) => [p.id, p])),
    [health],
  );

  const currentHealth = current ? healthById.get(current.provider) : undefined;
  const currentUnusable =
    !!currentHealth &&
    (currentHealth.status === 'needs_key' || currentHealth.status === 'exhausted');

  const lastWorking = health?.last_working ?? null;

  return (
    <div className="h-full overflow-y-auto px-5 py-6">
      <div className="mx-auto max-w-3xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold">🧠 Free Models</h2>
            <p className="mt-1 text-xs text-ld-muted">
              Zero-cost models from the browser's registry. Selecting one rewrites{' '}
              <code className="font-mono">browser/data/settings.json</code> — the backend
              mirrors it per request, so the next chat run uses it. No restart needed.
            </p>
          </div>
          <button
            onClick={() => void switchToBest()}
            disabled={switching || !health?.best_free}
            className="shrink-0 rounded-lg bg-ld-accent px-3 py-2 text-xs font-semibold text-white transition hover:opacity-90 disabled:opacity-40"
            title={
              health?.best_free
                ? `Switch to ${health.best_free}/${health.best_free_model}`
                : 'No working free provider right now'
            }
          >
            {switching ? 'Switching…' : '⚡ Switch to best working'}
          </button>
        </div>

        {/* Best-working now + last-known-working pair */}
        {(health?.best_free || lastWorking) && (
          <div className="mt-3 rounded-xl border border-ld-border bg-ld-panel px-4 py-3 text-xs">
            {health?.best_free && (
              <div className="text-ld-text">
                <span className="text-ld-muted">Best working now:</span>{' '}
                <span className="font-mono text-ld-ok">
                  {health.best_free}/{health.best_free_model}
                </span>
              </div>
            )}
            {lastWorking && (
              <div className="mt-1 text-ld-text">
                <span className="text-ld-warn">★</span>{' '}
                <span className="text-ld-muted">Last known working:</span>{' '}
                <span className="font-mono">
                  {lastWorking.provider}/{lastWorking.model}
                </span>{' '}
                <span className="text-ld-muted">
                  (worked {lastWorkingAge(lastWorking.timestamp)})
                </span>
              </div>
            )}
          </div>
        )}

        {/* Warning when the selected model can't actually answer */}
        {currentUnusable && currentHealth && (
          <div className="mt-3 rounded-xl border border-ld-danger/50 bg-ld-danger/10 px-4 py-3 text-xs">
            <div className="font-semibold text-ld-danger">
              ⚠ Selected model is unusable:{' '}
              <span className="font-mono">
                {current?.provider}/{current?.model}
              </span>{' '}
              —{' '}
              {currentHealth.status === 'exhausted'
                ? `credits exhausted${
                    currentHealth.credit_ttl_remaining_sec > 0
                      ? ` (retry in ${formatTtl(currentHealth.credit_ttl_remaining_sec)})`
                      : ''
                  }`
                : `needs a key (${currentHealth.env_key ?? 'see provider config'})`}
            </div>
            <button
              onClick={() => void switchToBest()}
              disabled={switching || !health?.best_free}
              className="mt-2 rounded-lg bg-ld-danger px-3 py-1.5 text-xs font-semibold text-white transition hover:opacity-90 disabled:opacity-40"
            >
              {switching ? 'Switching…' : '⚡ Switch to best working'}
            </button>
          </div>
        )}

        {current && (
          <div className="mt-4 rounded-xl border border-ld-accent/40 bg-ld-accent/5 px-4 py-3 text-xs">
            <span className="font-semibold text-ld-accent">Active:</span>{' '}
            <span className="font-mono">
              {current.provider}/{current.model || 'auto'}
            </span>
            {currentHealth && (
              <span className="ml-2">
                <StatusBadge p={currentHealth} />
              </span>
            )}
            {switching && <span className="ml-2 text-ld-muted">switching…</span>}
          </div>
        )}

        {/* Free rotation — live health */}
        {rotation.length > 0 && (
          <div className="mt-5">
            <h3 className="text-sm font-semibold">
              Free rotation — live health
              <span className="ml-2 text-[10px] font-normal text-ld-muted">
                read from the live rotator state
              </span>
            </h3>
            <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
              {rotation.map((p) => {
                const isActive = current?.provider === toBrowserProviderId(p.id);
                const isLastWorking =
                  !!lastWorking &&
                  toBrowserProviderId(lastWorking.provider) ===
                    toBrowserProviderId(p.id);
                return (
                  <div
                    key={p.id}
                    className={`rounded-lg border px-3 py-2.5 ${
                      isActive ? 'border-ld-ok/60 bg-ld-ok/5' : 'border-ld-border'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="w-6 shrink-0 font-mono text-[10px] text-ld-muted">
                        #{p.rotation_order != null ? p.rotation_order + 1 : '–'}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-xs font-semibold">
                        {p.name}
                        {isActive && <span className="ml-1 text-ld-ok">◀</span>}
                        {isLastWorking && (
                          <span className="ml-1 text-ld-warn" title="Last known working">
                            ★
                          </span>
                        )}
                        {p.next_in_rotation && !isActive && (
                          <span className="ml-1 text-[10px] font-normal text-ld-accent">
                            next
                          </span>
                        )}
                      </span>
                      <StatusBadge p={p} />
                    </div>
                    <div className="mt-1 truncate font-mono text-[10px] text-ld-muted">
                      {p.model}
                    </div>
                    {p.status === 'exhausted' && p.credit_ttl_remaining_sec > 0 && (
                      <div className="mt-1 text-[10px] text-ld-danger">
                        Credits exhausted — retry in {formatTtl(p.credit_ttl_remaining_sec)}
                      </div>
                    )}
                    <button
                      disabled={switching || isActive || p.status !== 'ready'}
                      onClick={() => {
                        void pick(p.id, p.model);
                      }}
                      className="mt-2 rounded-lg border border-ld-border px-3 py-1 font-mono text-xs text-ld-muted transition hover:border-ld-accent hover:text-ld-text disabled:opacity-40"
                      title={
                        p.status !== 'ready'
                          ? 'Not usable right now'
                          : `Use ${p.id}/${p.model}`
                      }
                    >
                      {isActive ? 'Active ✓' : 'Use'}
                    </button>
                  </div>
                );
              })}
            </div>

          </div>
        )}

        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter models…"
          className="mt-5 w-full rounded-xl border border-ld-border bg-ld-card px-4 py-2.5 text-sm outline-none placeholder:text-ld-faint focus:border-ld-accent"
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
                  {h && <StatusBadge p={h} />}
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
