import { useEffect, useState } from 'react';
import { api } from '../lib/backend';
import { useLuckyBase } from '../state/chat';

export default function Memory() {
  const backend = useLuckyBase((s) => s.backend);
  const [q, setQ] = useState('');
  const [results, setResults] = useState<{ content: string; tags: string[]; score: number }[]>([]);
  const [stats, setStats] = useState<{ count?: number; edges?: number } | null>(null);

  useEffect(() => {
    api<{ count: number; edges: number }>(backend, '/api/brain/stats')
      .then((s) => setStats(s))
      .catch(() => setStats(null));
  }, [backend]);

  const search = async () => {
    if (!q.trim()) { setResults([]); return; }
    try {
      const r = await api<{ results: typeof results }>(
        backend, `/api/brain/search?q=${encodeURIComponent(q)}`,
      );
      setResults(r.results);
    } catch { setResults([]); }
  };

  return (
    <div className="mx-auto h-full max-w-3xl space-y-5 overflow-y-auto px-5 py-6">
      <div>
        <h1 className="text-lg font-semibold">Memory</h1>
        <p className="text-xs text-ld-muted">
          {stats ? `${stats.count} memories · ${stats.edges} links` : '…'} — never lost, always yours.
        </p>
      </div>
      <div className="flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void search(); }}
          placeholder="Search memories…"
          className="flex-1 rounded-xl border border-ld-border bg-ld-card px-4 py-2.5 text-sm outline-none focus:border-ld-accent" />
        <button onClick={() => void search()}
          className="rounded-xl bg-ld-accent px-4 py-2 text-sm font-semibold text-white">Search</button>
      </div>
      <div className="space-y-2">
        {results.map((r, i) => (
          <div key={i} className="rounded-xl border border-ld-border bg-ld-card px-4 py-3 text-sm">
            <div>{r.content}</div>
            <div className="mt-1 text-xs text-ld-muted">
              {r.tags.join(' · ')} — score {r.score.toFixed(3)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
