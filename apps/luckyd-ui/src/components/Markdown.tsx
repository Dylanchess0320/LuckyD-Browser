import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

function copyText(t: string) {
  try { void navigator.clipboard?.writeText(t); } catch { /* clipboard unavailable */ }
}

/** Code block with header (language + copy). react-markdown v9: <pre> wraps <code>. */
function Pre({ children }: { children?: React.ReactNode }) {
  // Pull the raw text + language out of the nested <code> for the copy button.
  let lang = '';
  let raw = '';
  try {
    const code = (Array.isArray(children) ? children : [children]).find(
      (c: unknown) => (c as { type?: unknown })?.type === 'code',
    ) as { props?: { className?: string; children?: unknown } } | undefined;
    lang = (code?.props?.className ?? '').replace('language-', '');
    const flat = (n: unknown): string =>
      Array.isArray(n) ? n.map(flat).join('') : typeof n === 'string' ? n : '';
    raw = flat(code?.props?.children);
  } catch { /* render plain */ }
  return (
    <div className="overflow-hidden rounded-xl border border-ld-border">
      <div className="flex items-center justify-between bg-ld-panel2 px-3 py-1.5">
        <span className="font-mono text-[11px] text-ld-muted">{lang || 'code'}</span>
        <button
          onClick={() => copyText(raw)}
          className="rounded-md px-2 py-0.5 font-mono text-[11px] text-ld-muted hover:bg-ld-card hover:text-ld-text"
        >
          copy
        </button>
      </div>
      <pre className="!m-0 !rounded-none !border-0">{children}</pre>
    </div>
  );
}

const Md = memo(function Md({ text }: { text: string }) {
  return (
    <div className="ld-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre: Pre,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">{children}</a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

export default Md;
