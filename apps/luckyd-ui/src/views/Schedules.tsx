import { useEffect, useState } from 'react';
import { useSched } from '../state/schedules';

export default function Schedules() {
  const { schedules, runs, refreshSchedules, runNow, toggle, remove, create } = useSched();
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [when, setWhen] = useState('');
  const [kind, setKind] = useState<'cron' | 'every' | 'daily'>('daily');
  const [msg, setMsg] = useState('');

  useEffect(() => {
    void refreshSchedules();
    const t = setInterval(() => void refreshSchedules(), 15000);
    return () => clearInterval(t);
  }, [refreshSchedules]);

  const submit = async () => {
    const body: Record<string, unknown> = {
      name, prompt, allow_scopes: ['read', 'network', 'memory'],
      max_turns: 25, max_runtime_minutes: 10, max_retries: 1,
    };
    if (kind === 'cron') body.cron = when;
    else if (kind === 'every') body.every_minutes = Number(when);
    else body.daily_at = when;
    const err = await create(body);
    setMsg(err ?? 'Created!');
    if (!err) { setName(''); setPrompt(''); setWhen(''); }
  };

  return (
    <div className="mx-auto h-full max-w-4xl space-y-5 overflow-y-auto px-5 py-6">
      <div>
        <h1 className="text-lg font-semibold">Scheduled Agents</h1>
        <p className="text-xs text-ld-muted">Works while you rest — morning digest included.</p>
      </div>

      <section className="rounded-2xl border border-ld-border bg-ld-panel p-4">
        <h2 className="text-sm font-semibold">New schedule</h2>
        <div className="mt-3 grid gap-2">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name"
            className="rounded-lg border border-ld-border bg-ld-window px-3 py-2 text-sm outline-none" />
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Task prompt…"
            rows={2} className="rounded-lg border border-ld-border bg-ld-window px-3 py-2 text-sm outline-none" />
          <div className="flex gap-2">
            <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}
              className="rounded-lg border border-ld-border bg-ld-window px-3 py-2 text-sm">
              <option value="daily">daily_at (07:30)</option>
              <option value="every">every_minutes</option>
              <option value="cron">cron</option>
            </select>
            <input value={when} onChange={(e) => setWhen(e.target.value)}
              placeholder={kind === 'cron' ? '0 7 * * 1-5' : kind === 'every' ? '60' : '07:30'}
              className="flex-1 rounded-lg border border-ld-border bg-ld-window px-3 py-2 text-sm outline-none" />
            <button onClick={() => void submit()}
              className="rounded-lg bg-ld-accent px-4 py-2 text-sm font-semibold text-white">
              Create
            </button>
          </div>
          {msg && <p className="text-xs text-ld-muted">{msg}</p>}
        </div>
      </section>

      <section className="rounded-2xl border border-ld-border bg-ld-panel p-4">
        <h2 className="text-sm font-semibold">Schedules ({schedules.length})</h2>
        <div className="mt-3 space-y-2">
          {schedules.map((s) => (
            <div key={s.id} className="flex items-center justify-between gap-3 rounded-xl border border-ld-border bg-ld-card px-4 py-3">
              <div>
                <div className="text-sm font-medium">{s.name || s.id}</div>
                <div className="text-xs text-ld-muted">
                  {s.cron ?? s.daily_at ?? `every ${s.every_minutes}m`} · next: {s.next_run_at ?? '—'}
                </div>
              </div>
              <div className="flex gap-2">
                <button onClick={() => void toggle(s)}
                  className="rounded-lg border border-ld-border px-3 py-1 text-xs">
                  {s.enabled ? 'Pause' : 'Enable'}
                </button>
                <button onClick={() => void runNow(s.id)}
                  className="rounded-lg border border-ld-border px-3 py-1 text-xs">Run now</button>
                <button onClick={() => { if (confirm('Delete?')) void remove(s.id); }}
                  className="rounded-lg border border-ld-danger px-3 py-1 text-xs text-ld-danger">
                  Delete
                </button>
              </div>
            </div>
          ))}
          {schedules.length === 0 && <p className="text-xs text-ld-muted">No schedules yet.</p>}
        </div>
      </section>

      <section className="rounded-2xl border border-ld-border bg-ld-panel p-4">
        <h2 className="text-sm font-semibold">Recent runs</h2>
        <div className="mt-3 space-y-2">
          {runs.map((r, i) => (
            <div key={i} className="rounded-xl border border-ld-border bg-ld-card px-4 py-2 text-xs">
              <span className={r.status === 'ok' ? 'text-ld-ok' : 'text-ld-danger'}>●</span>{' '}
              <b>{r.schedule_name}</b>{' '}
              <span className="text-ld-muted">{r.started_at ?? ''} · {Math.round(r.duration_sec ?? 0)}s</span>
              <div className="mt-1 text-ld-muted">{(r.summary ?? r.error ?? '').slice(0, 220)}</div>
            </div>
          ))}
          {runs.length === 0 && <p className="text-xs text-ld-muted">No runs yet.</p>}
        </div>
      </section>
    </div>
  );
}
