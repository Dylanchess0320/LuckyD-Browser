import { useEffect } from 'react';
import { useLuckyBase } from '../state/chat';

export function useGlobalKeys(go: (t: 'chat' | 'agents' | 'models' | 'trust' | 'schedules' | 'memory' | 'settings') => void) {
  const newSession = useLuckyBase((s) => s.newSession);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        const pick = prompt('Go to…\n1. Chat\n2. Agents\n3. Models\n4. Trust\n5. Schedules\n6. Memory\n7. Settings\nN. New chat');
        if (!pick) return;
        if (pick.toLowerCase() === 'n') { newSession(); go('chat'); return; }
        const map = ['chat', 'agents', 'models', 'trust', 'schedules', 'memory', 'settings'] as const;
        const t = map[Number(pick) - 1];
        if (t) go(t);
      }
      if (mod && e.shiftKey && e.key.toLowerCase() === 'o') {
        e.preventDefault();
        newSession();
        go('chat');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go, newSession]);
}
