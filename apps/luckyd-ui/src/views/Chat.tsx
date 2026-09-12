import { useState } from 'react';
import { useLuckyBase } from '../state/chat';
import Md from '../components/Markdown';

const SUGGESTIONS = [
  'Summarize this repo and list the biggest risks',
  'What files changed recently? Review them.',
  'Search my memory for past decisions',
  'Plan a deep-research task for me',
];

export default function Chat() {
  const { messages, busy, workingLabel, send, stop, clearChat, model, provider } = useLuckyBase();
  const [draft, setDraft] = useState('');
  const [copied, setCopied] = useState<string | null>(null);

  const submit = () => {
    if (!draft.trim() || busy) return;
    void send(draft);
    setDraft('');
  };

  const copyMsg = async (id: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(id);
      setTimeout(() => setCopied((c) => (c === id ? null : c)), 1500);
    } catch { /* clipboard blocked */ }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-ld-border px-5 py-3">
        <div>
          <h1 className="text-lg font-semibold text-ld-text">Chat</h1>
          <p className="text-xs text-ld-muted">
            {provider || '…'} · {model || '…'}
            {busy && workingLabel ? ` · ${workingLabel}` : ''}
          </p>
        </div>
        <div className="flex gap-2">
          {busy && (
            <button
              onClick={stop}
              className="rounded-lg border border-ld-danger px-3 py-1.5 text-xs text-ld-danger"
            >
              ■ Stop
            </button>
          )}
          <button
            onClick={() => void clearChat()}
            className="rounded-lg border border-ld-border px-3 py-1.5 text-xs text-ld-muted hover:text-ld-text"
          >
            New chat
          </button>
        </div>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-6">
        {messages.length === 0 && (
          <div className="mx-auto max-w-xl pt-10 text-center">
            <div className="text-4xl">🍀</div>
            <h2 className="mt-3 text-xl font-semibold">What should we build today?</h2>
            <p className="mt-1 text-sm text-ld-muted">
              Same brain, same memory, same tools — non-blocking now.
            </p>
            <div className="mt-6 grid gap-2 text-left">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => void send(s)}
                  className="rounded-xl border border-ld-border bg-ld-card px-4 py-3 text-sm text-ld-text hover:border-ld-accent"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`group flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`relative max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                m.role === 'user'
                  ? 'whitespace-pre-wrap rounded-br-md bg-ld-accent text-white'
                  : 'rounded-bl-md border border-ld-border bg-ld-panel text-ld-text'
              }`}
            >
              {m.role === 'user' ? (
                m.text
              ) : m.done || m.text ? (
                <>
                  <Md text={m.text} />
                  <button
                    onClick={() => void copyMsg(m.id, m.text)}
                    title="Copy answer"
                    className="absolute -right-2 -top-2 hidden rounded-lg border border-ld-border bg-ld-card px-2 py-0.5 text-[11px] text-ld-muted group-hover:block hover:text-ld-text"
                  >
                    {copied === m.id ? '✓' : '⧉'}
                  </button>
                </>
              ) : (
                <span className="ld-working-dot text-ld-muted">
                  {m.phase === 'queued' ? 'Queued…' : `Working…${m.elapsedSec ? ` · ${Math.floor(m.elapsedSec)}s` : ''}`}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="border-t border-ld-border p-4">
        <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-ld-border bg-ld-card p-2 focus-within:border-ld-accent">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={1}
            placeholder="Message LuckyD…  (Enter to send · Shift+Enter for newline)"
            className="max-h-40 flex-1 resize-none bg-transparent px-3 py-2 text-sm text-ld-text outline-none placeholder:text-ld-muted"
          />
          <button
            onClick={busy ? stop : submit}
            disabled={!busy && !draft.trim()}
            title={busy ? 'Stop' : 'Send'}
            className="rounded-xl bg-gradient-to-br from-ld-accent to-ld-accent2 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
          >
            {busy ? '■' : '➤'}
          </button>
        </div>
        <p className="mx-auto mt-2 max-w-3xl text-center text-[11px] text-ld-faint">
          LuckyD runs your models locally when it can — prompts stay on this machine unless you pick a cloud provider.
        </p>
      </div>
    </div>
  );
}


