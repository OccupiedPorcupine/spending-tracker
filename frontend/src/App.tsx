import { useCallback, useEffect, useState } from 'react';
import { api, Locked, Offline, onLocked } from './lib/api';
import { lockNow } from './lib/auth';
import * as queue from './lib/queue';
import { load, save } from './lib/store';
import type { Meta, Row, Totals } from './lib/types';
import { applyUpdate, onUpdate, updateReady } from './lib/update';
import { Enroll } from './pages/Enroll';
import { Lock } from './pages/Lock';
import { QuickAdd } from './pages/QuickAdd';
import { Settings } from './pages/Settings';

export type Phase = 'checking' | 'open' | 'locked' | 'offline';
export interface Recent { rows: Row[]; totals: Totals; today: string; rows_left: number }

const STRICT_MS = 5 * 60 * 1000;
const FALLBACK_META: Meta = {
  categories: ['Food', 'Things', 'Revenue', 'Loan'], notes: [], last_currency: 'SGD', target: 'dev', version: '', today: '',
};

export function navigate(path: string) {
  history.pushState(null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

function usePath() {
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const f = () => setPath(location.pathname);
    window.addEventListener('popstate', f);
    return () => window.removeEventListener('popstate', f);
  }, []);
  return path;
}

export function useQueue() {
  const [items, setItems] = useState<queue.QItem[]>([]);
  useEffect(() => {
    const f = () => { void queue.items().then(setItems); };
    f();
    return queue.subscribe(f);
  }, []);
  return items;
}

export function App() {
  const path = usePath();
  // With "require Face ID every time" on, a cold start after 5 minutes away begins locked.
  const [phase, setPhase] = useState<Phase>(() =>
    load('strict', false) && Date.now() - load('hiddenAt', Date.now()) > STRICT_MS ? 'locked' : 'checking');
  const [meta, setMeta] = useState<Meta>(() => load('meta', FALLBACK_META));
  const [recent, setRecent] = useState<Recent | null>(null);
  const [update, setUpdate] = useState(updateReady());
  const items = useQueue();

  const refresh = useCallback(async (fresh = false) => {
    try {
      const [m, r] = await Promise.all([
        api<Meta>('/api/meta'),
        api<Recent>(`/api/recent${fresh ? '?fresh=1' : ''}`),
      ]);
      if (m.status === 200) { setMeta(m.data); save('meta', m.data); }
      if (r.status === 200) setRecent(r.data);
    } catch (e) {
      if (e instanceof Offline) setPhase((p) => (p === 'open' ? 'offline' : p));
      if (!(e instanceof Locked) && !(e instanceof Offline)) throw e;
    }
  }, []);

  const check = useCallback(async () => {
    try {
      const s = await api<{ authenticated: boolean; require_unlock: boolean }>('/api/auth/state', { timeout: 5000 });
      if (s.status !== 200) throw new Offline('bad status');
      save('strict', s.data.require_unlock);
      if (s.data.authenticated) {
        setPhase('open');
        void queue.flush().catch(() => undefined);
        void refresh();
      } else {
        setPhase('locked');
      }
    } catch {
      setPhase((p) => (p === 'locked' ? p : 'offline'));
    }
  }, [refresh]);

  useEffect(() => {
    if (phase === 'checking') void check();
  }, [phase, check]);

  useEffect(() => onLocked(() => { setPhase('locked'); setRecent(null); }), []);
  useEffect(() => onUpdate(() => setUpdate(true)), []);
  useEffect(() => queue.onSaved(() => { void refresh(true); }), [refresh]);

  // Back from the background: maybe lock (strict mode), then re-check and sync.
  useEffect(() => {
    const vis = async () => {
      if (document.visibilityState === 'hidden') { save('hiddenAt', Date.now()); return; }
      const away = Date.now() - load('hiddenAt', Date.now());
      if (load('strict', false) && away > STRICT_MS) {
        setPhase('locked');
        setRecent(null);
        void lockNow().catch(() => undefined);
        return;
      }
      setPhase((p) => (p === 'offline' ? 'checking' : p));
      if (phase === 'open') void refresh();
    };
    const online = () => setPhase((p) => (p === 'offline' ? 'checking' : p));
    document.addEventListener('visibilitychange', vis);
    window.addEventListener('online', online);
    return () => { document.removeEventListener('visibilitychange', vis); window.removeEventListener('online', online); };
  }, [phase, refresh]);

  const unlocked = () => { setPhase('open'); void queue.flush().catch(() => undefined); void refresh(true); navigate('/'); };

  let page;
  if (path === '/enroll') page = <Enroll onDone={unlocked} />;
  else if (phase === 'locked') page = <Lock waiting={items.length} onUnlocked={unlocked} />;
  else if (path === '/settings' && phase === 'open') page = <Settings meta={meta} items={items} onLocked={() => setPhase('locked')} />;
  else page = <QuickAdd phase={phase} meta={meta} recent={recent} items={items} onChanged={() => refresh(true)} />;

  return (
    <>
      {update && (
        <button type="button" className="update" onClick={applyUpdate}>Update ready, tap to reload</button>
      )}
      {page}
    </>
  );
}
