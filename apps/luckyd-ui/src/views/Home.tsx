import { useEffect } from 'react';
import { useDash } from '../state/dash';
import { useLuckyBase } from '../state/chat';

export default function Home({ go }: { go: (t: 'chat' | 'agents' | 'models' | 'trust' | 'schedules' | 'memory') => void }) {
  const d = useDash();
  const { send } = useLuckyBase();

  useEffect(() => { void d.refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const ask = (q: string) => { void send(q); go('chat'); };

  const cards = [
    { icon: '🛡', title: 'Trust Center', sub: d.approvalCount > 0 ? `${d.approvalCount} waiting` : 'Agentic with receipts', onClick: () => go('trust') },
    { icon: '⏰', title: 'Scheduled Agents', sub: d.schedCount > 0 ? `${d.schedCount} active` : 'Works while you rest', onClick: () => go('schedules') },
    { icon: '🧠', title: 'Memory', sub: `${d.brainCount} memories · ${d.brainEdges} links`, onClick: () => go('memory') },
  ];

  return (
    <div className="mx-auto h-full max-w-3xl overflow-y-auto px-5 py-8">
      <div className="text-center">
        <div className="text-5xl">🍀</div>
        <h1 className="mt-3 text-2xl font-bold tracking-tight">Good evening, Dylan</h1>
        <p className="mt-1 text-sm text-ld-muted">
          {d.toolCount > 0 ? `${d.toolCount} tools armed` : 'Warming up…'}
          {d.cost ? ` · ${(d.cost.input + d.cost.output).toLocaleString()} tokens` : ''}
        </p>
      </div>

      <div className="mt-6 grid grid-cols-3 gap-3">
        {cards.map((c) => (
          <button
            key={c.title}
            onClick={c.onClick}
            className="group rounded-2xl border border-ld-border bg-ld-panel p-4 text-left transition hover:border-ld-accent hover:shadow-ld-2"
          >
            <div className="text-2xl">{c.icon}</div>
            <div className="mt-2 text-sm font-semibold">{c.title}</div>
            <div className="text-xs text-ld-muted">{c.sub}</div>
          </button>
        ))}
      </div>

      <div className="mt-4 rounded-2xl border border-ld-border bg-ld-panel p-4">
        <div className="text-xs font-semibold uppercase tracking-wider text-ld-muted">Try asking</div>
        <div className="mt-2 grid gap-2">
          {[
            'Summarize this repo and list the biggest risks',
            'What did my scheduled agents do overnight?',
            'Search my memory for past decisions',
          ].map((q) => (
            <button
              key={q}
              onClick={() => ask(q)}
              className="rounded-xl border border-ld-border bg-ld-card px-4 py-2.5 text-left text-sm hover:border-ld-accent"
            >
              {q} <span className="float-right text-ld-faint">➤</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
