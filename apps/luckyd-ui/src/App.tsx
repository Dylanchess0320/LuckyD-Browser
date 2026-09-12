import { useEffect, useState } from 'react';
import Chat from './views/Chat';
import Memory from './views/Memory';
import Schedules from './views/Schedules';
import Settings from './views/Settings';
import Trust from './views/Trust';
import Agents from './views/Agents';
import Models from './views/Models';
import SessionRail from './components/SessionRail';
import { useGlobalKeys } from './hooks/keys';
import { useLuckyBase } from './state/chat';
import { useTrust } from './state/trust';

type Tab = 'chat' | 'agents' | 'models' | 'trust' | 'schedules' | 'memory' | 'settings';

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: 'chat', label: 'Chat', icon: '💬' },
  { id: 'agents', label: 'Agents', icon: '🤖' },
  { id: 'models', label: 'Models', icon: '🧠' },
  { id: 'trust', label: 'Trust', icon: '🛡' },
  { id: 'schedules', label: 'Schedules', icon: '⏰' },
  { id: 'memory', label: 'Memory', icon: '🧠' },
  { id: 'settings', label: 'Settings', icon: '⚙' },
];

export default function App() {
  const [tab, setTab] = useState<Tab>('chat');
  const { connected, checkHealth, lastError } = useLuckyBase();
  const pending = useTrust((s) => s.pending);
  const refreshTrust = useTrust((s) => s.refreshTrust);
  useGlobalKeys(setTab);

  useEffect(() => {
    void checkHealth();
    void refreshTrust().catch(() => undefined);
    const t = setInterval(() => void checkHealth(), 10000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex h-screen bg-ld-window text-ld-text">
      {/* Sidebar */}
      <nav className="flex w-16 flex-col items-center gap-1 border-r border-ld-border bg-ld-panel py-4">
        <div className="mb-4 text-2xl" title="LuckyD">🍀</div>
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            title={t.label}
            className={`relative flex h-11 w-11 items-center justify-center rounded-xl text-xl transition ${
              tab === t.id
                ? 'bg-ld-card text-ld-text shadow-ld-2 ring-1 ring-ld-accent'
                : 'text-ld-muted hover:bg-ld-card hover:text-ld-text'
            }`}
          >
            {t.icon}
            {t.id === 'trust' && pending.length > 0 && (
              <span className="absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-ld-danger px-1 text-[10px] font-bold text-white">
                {pending.length}
              </span>
            )}
          </button>
        ))}
        <div className="mt-auto">
          <span
            title={connected ? 'backend connected' : 'backend offline'}
            className={`block h-2.5 w-2.5 rounded-full ${connected ? 'bg-ld-ok' : 'bg-ld-danger'}`}
          />
        </div>
      </nav>

      {/* Session rail */}
      <SessionRail onPick={() => setTab('chat')} />

      {/* Main */}
      <main className="min-w-0 flex-1">
        {connected === false && (
          <div className="border-b border-ld-danger/40 bg-ld-danger/10 px-5 py-2 text-xs text-ld-danger">
            Backend offline — start it with{' '}
            <code className="font-mono">python web_server.py --web --port 8000</code>, then set the
            token in Settings. {lastError ?? ''}
          </div>
        )}
        {tab === 'chat' && <Chat />}
        {tab === 'agents' && <Agents />}
        {tab === 'models' && <Models />}
        {tab === 'trust' && <Trust />}
        {tab === 'schedules' && <Schedules />}
        {tab === 'memory' && <Memory />}
        {tab === 'settings' && <Settings />}
      </main>
    </div>
  );
}

