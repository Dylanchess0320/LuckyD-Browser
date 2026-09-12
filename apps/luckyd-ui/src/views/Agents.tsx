import { useEffect, useState } from 'react';
import { useAgents, MESH_AGENTS, terminalUrl } from '../state/agents';
import { useSched } from '../state/schedules';
import { useLuckyBase } from '../state/chat';
import type { Schedule } from '../lib/backend';

type Page = { kind: 'mesh' } | { kind: 'term'; shell: string; label: string; emoji: string };

const CORE: { shell: string; label: string; emoji: string }[] = [
  { shell: 'agent', label: 'Agent 1 (v3.6)', emoji: 'ðŸ€' },
  { shell: 'agent2', label: 'Agent 2 (v2.2)', emoji: 'ðŸ”§' },
  { shell: 'powershell', label: 'PowerShell', emoji: 'âŒ¨' },
  { shell: 'cmd', label: 'CMD', emoji: 'ðŸªŸ' },
];


function MuseCard() {
  const { schedules, refreshSchedules, create, remove } = useSched();
  const { backend, connected } = useLuckyBase();
  const [busy, setBusy] = useState(false);
  const muse = schedules.find((s: Schedule) => s.name === 'Overnight Muse');

  const schedule = async () => {
    setBusy(true);
    try {
      await create({
        name: 'Overnight Muse',
        prompt: 'Check the overnight queue and summarize what you built while I slept.',
        daily_at: '02:00',
        allow_scopes: ['read', 'network', 'memory'],
      });
      void refreshSchedules();
    } finally {
      setBusy(false);
    }
  };

  if (connected === false) return null;

  return (
    <div className="flex items-center gap-3 border-t border-ld-border px-5 py-2.5">
      <span className="text-sm">â“‚ï¸</span>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-semibold">Overnight Muse</div>
        <div className="truncate text-[11px] text-ld-muted">
          Daily 02:00 run on the selected free model â€”{' '}
          {muse?.enabled ? `next ${muse.next_run_at ?? 'scheduled'}` : 'not scheduled'}
        </div>
      </div>
      {muse ? (
        <button
          onClick={() => {
            void remove(muse.id).catch(() => undefined);
          }}
          className="rounded-lg border border-ld-border px-3 py-1.5 text-xs text-ld-muted hover:border-ld-danger hover:text-ld-danger"
        >
          Unscheduled
        </button>
      ) : (
        <button
          disabled={busy}
          onClick={() => {
            void schedule().catch(() => undefined);
          }}
          className="rounded-lg border border-ld-accent/50 px-3 py-1.5 text-xs text-ld-accent transition hover:bg-ld-accent/10 disabled:opacity-50"
        >
          {busy ? 'Schedulingâ€¦' : 'Schedule'}
        </button>
      )}
    </div>
  );
}

export default function Agents() {
  const { ping, refresh } = useAgents();
  const [page, setPage] = useState<Page>({ kind: 'mesh' });

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const src =
    page.kind === 'mesh'
      ? 'http://127.0.0.1:9885/'
      : terminalUrl(page.shell);

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-ld-border px-5 py-3">
        <span className="text-sm font-semibold">ðŸ¤– Agent Mesh</span>
        <span
          className={`h-2 w-2 rounded-full ${
            ping?.ok ? 'bg-ld-ok' : 'bg-ld-danger'
          }`}
          title={ping?.ok ? 'bridge running' : 'bridge offline'}
        />
        <span className="text-xs text-ld-muted">
          {ping?.ok
            ? `${Object.values(ping.shells).filter(Boolean).length} agent CLIs ready`
            : 'starting the terminal bridgeâ€¦'}
        </span>
      </div>

      {/* Launcher chips */}
      <div className="flex flex-wrap gap-1.5 border-b border-ld-border px-5 py-2.5">
        <Chip
          on={page.kind === 'mesh'}
          onClick={() => setPage({ kind: 'mesh' })}
          emoji="ðŸ•¸"
          label="4-up Workspace"
        />
        {CORE.map((c) => (
          <Chip
            key={c.shell}
            on={page.kind === 'term' && page.shell === c.shell}
            onClick={() => setPage({ kind: 'term', shell: c.shell, label: c.label, emoji: c.emoji })}
            emoji={c.emoji}
            label={c.label}
          />
        ))}
        {MESH_AGENTS.map((a) => (
          <Chip
            key={a.id}
            on={page.kind === 'term' && page.shell === a.id}
            dim={ping ? ping.shells[a.id] === false : undefined}
            onClick={() => setPage({ kind: 'term', shell: a.id, label: a.label, emoji: a.emoji })}
            emoji={a.emoji}
            label={a.label}
          />
        ))}
      </div>

      {/* Page */}
      <div className="min-h-0 flex-1">
        <iframe
          key={src}
          src={src}
          title={page.kind === 'mesh' ? 'Agent Mesh workspace' : `${page.label} terminal`}
          className="h-full w-full border-0 bg-black"
          allow="clipboard-read; clipboard-write"
        />
      </div>

      <MuseCard />
    </div>
  );
}

function Chip({
  on,
  dim,
  onClick,
  emoji,
  label,
}: {
  on: boolean;
  dim?: boolean;
  onClick: () => void;
  emoji: string;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      title={dim === false ? `${label} â€” CLI not installed` : label}
      className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition ${
        on
          ? 'border-ld-accent bg-ld-accent/10 text-ld-text'
          : 'border-ld-border text-ld-muted hover:border-ld-accent/50 hover:text-ld-text'
      } ${dim === false ? 'opacity-40' : ''}`}
    >
      <span>{emoji}</span>
      <span className="font-medium">{label}</span>
    </button>
  );
}
