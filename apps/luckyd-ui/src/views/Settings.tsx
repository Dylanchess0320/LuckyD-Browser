import { useEffect, useState } from 'react';
import { api } from '../lib/backend';
import { useLuckyBase } from '../state/chat';

export default function Settings() {
  const { backend, setBackend, provider, model, connected, checkHealth } = useLuckyBase();
  const [baseUrl, setBaseUrl] = useState(backend.baseUrl);
  const [token, setToken] = useState(backend.token);
  const [tools, setTools] = useState<{ name: string; description: string }[]>([]);

  useEffect(() => { void checkHealth(); }, [checkHealth]);
  useEffect(() => {
    if (!connected) return;
    api<{ tools: { name: string; description: string }[] }>(backend, '/api/tools')
      .then((t) => setTools(t.tools))
      .catch(() => setTools([]));
  }, [backend, connected]);

  return (
    <div className="mx-auto h-full max-w-3xl space-y-5 overflow-y-auto px-5 py-6">
      <h1 className="text-lg font-semibold">Settings</h1>

      <section className="rounded-2xl border border-ld-border bg-ld-panel p-4">
        <h2 className="text-sm font-semibold">Backend connection</h2>
        <p className="mt-1 text-xs text-ld-muted">
          Same Python brain — no data migration, ever. Status:{' '}
          {connected === null ? '…' : connected ? '✅ connected' : '❌ offline'}
          {connected && ` · ${provider} · ${model}`}
        </p>
        <div className="mt-3 grid gap-2">
          <label className="text-xs text-ld-muted">Base URL
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
              className="mt-1 w-full rounded-lg border border-ld-border bg-ld-window px-3 py-2 font-mono text-sm outline-none" />
          </label>
          <label className="text-xs text-ld-muted">Token (from .luckyd-code/hq_token)
            <input value={token} onChange={(e) => setToken(e.target.value)} type="password"
              className="mt-1 w-full rounded-lg border border-ld-border bg-ld-window px-3 py-2 font-mono text-sm outline-none" />
          </label>
          <button
            onClick={() => setBackend({ baseUrl: baseUrl.trim(), token: token.trim() })}
            className="rounded-lg bg-ld-accent px-4 py-2 text-sm font-semibold text-white">
            Save & reconnect
          </button>
        </div>
      </section>

      <section className="rounded-2xl border border-ld-border bg-ld-panel p-4">
        <h2 className="text-sm font-semibold">Tools ({tools.length})</h2>
        <div className="mt-2 max-h-96 space-y-1 overflow-y-auto">
          {tools.map((t) => (
            <div key={t.name} className="rounded-lg px-2 py-1.5 text-xs hover:bg-ld-card">
              <span className="font-mono font-semibold text-ld-accent">{t.name}</span>
              <span className="ml-2 text-ld-muted">{t.description}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
