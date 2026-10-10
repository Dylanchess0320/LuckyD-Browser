import { useAgents } from '../state/agents';

export type PairStatus = 'ready' | 'needs_key' | 'exhausted' | null;

/**
 * The currently answering provider/model, read live from the agents bridge:
 * /api/model re-reads browser/data/settings.json on every fetch and
 * setModel() pushes the fresh pair + health snapshot into the store, so this
 * stays correct across switches with no restart.
 */
export function useLivePair(): {
  provider: string;
  model: string;
  status: PairStatus;
  switching: boolean;
} {
  const current = useAgents((s) => s.current);
  const health = useAgents((s) => s.health);
  const switching = useAgents((s) => s.switching);
  const provider = current?.provider ?? '';
  const model = current?.model ?? '';
  const h = (health?.providers ?? []).find((p) => p.id === provider);
  return { provider, model, status: h?.status ?? null, switching };
}

/** Tiny health dot for the active pair. */
export function PairDot({ status }: { status: PairStatus }) {
  const cls =
    status === 'ready'
      ? 'bg-ld-ok'
      : status === 'exhausted'
        ? 'bg-ld-danger'
        : status === 'needs_key'
          ? 'bg-ld-warn'
          : 'bg-ld-faint';
  const title =
    status === 'ready'
      ? 'provider ready'
      : status === 'exhausted'
        ? 'provider credits exhausted'
        : status === 'needs_key'
          ? 'provider needs a key'
          : 'provider status unknown';
  return (
    <span
      title={title}
      className={`inline-block h-1.5 w-1.5 rounded-full ${cls}`}
    />
  );
}
