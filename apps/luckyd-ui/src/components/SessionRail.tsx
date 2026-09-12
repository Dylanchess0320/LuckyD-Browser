import { useLuckyBase } from '../state/chat';

export default function SessionRail({ onPick }: { onPick: () => void }) {
  const { sessions, activeId, newSession, selectSession, deleteSession } = useLuckyBase();
  return (
    <aside className="flex w-56 flex-col border-r border-ld-border bg-ld-panel">
      <div className="flex items-center justify-between px-3 py-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-ld-muted">Chats</span>
        <button
          onClick={() => { newSession(); onPick(); }}
          title="New chat (Ctrl+Shift+O)"
          className="rounded-lg border border-ld-border px-2 py-1 text-xs text-ld-muted hover:text-ld-text"
        >
          + New
        </button>
      </div>
      <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {sessions.map((s) => (
          <div
            key={s.id}
            onClick={() => { selectSession(s.id); onPick(); }}
            className={`group flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-xs ${
              s.id === activeId ? 'bg-ld-card text-ld-text' : 'text-ld-muted hover:bg-ld-card hover:text-ld-text'
            }`}
          >
            <span className="truncate">{s.title}</span>
            <button
              onClick={(e) => { e.stopPropagation(); deleteSession(s.id); }}
              title="Delete"
              className="hidden text-ld-muted hover:text-ld-danger group-hover:block"
            >
              ✕
            </button>
          </div>
        ))}
        {sessions.length === 0 && (
          <p className="px-3 py-2 text-xs text-ld-faint">No chats yet — say hi.</p>
        )}
      </div>
      <div className="border-t border-ld-border px-3 py-2 text-[11px] text-ld-faint">
        <span className="ld-kbd">Ctrl+K</span> jump · <span className="ld-kbd">Ctrl+Shift+O</span> new
      </div>
    </aside>
  );
}
