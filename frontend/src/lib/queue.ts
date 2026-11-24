/**
 * Offline queue. Every entry goes into IndexedDB first, then is sent. It leaves the queue only
 * when the server says the row is in the sheet. Entries are keyed by their client UUID and the
 * server writes each ID once, so resending is always safe.
 *
 * Safari has no Background Sync, so the queue is flushed on load, on `online`, when the page
 * becomes visible, after each save, after unlocking, and every 30 s while anything is waiting.
 * Logging out or a session expiring never clears it: a 401 just pauses sending ("Unlock to sync").
 */
import { openDB, type IDBPDatabase } from 'idb';
import { api, errorText, Locked, Offline } from './api';
import type { Entry } from './types';

export type QState = 'saving' | 'queued' | 'failed';
export interface QItem { entry: Entry; state: QState; error?: string; createdAt: number; tries: number; stuck?: boolean }
export interface Saved { id: string; row: number }

let dbp: Promise<IDBPDatabase> | null = null;
const db = () => (dbp ??= openDB('spend', 1, {
  upgrade(d) {
    d.createObjectStore('queue', { keyPath: 'entry.id' });
    d.createObjectStore('undo'); // IDs undone before they synced: the server may still hold them
  },
}));

const listeners = new Set<() => void>();
const savedListeners = new Set<(s: Saved) => void>();
export const subscribe = (f: () => void) => { listeners.add(f); return () => { listeners.delete(f); }; };
export const onSaved = (f: (s: Saved) => void) => { savedListeners.add(f); return () => { savedListeners.delete(f); }; };
const changed = () => listeners.forEach((f) => f());

export let needsUnlock = false;
export let lastSync: number | null = null;

export async function items(): Promise<QItem[]> {
  const all = (await (await db()).getAll('queue')) as QItem[];
  return all.sort((a, b) => a.createdAt - b.createdAt);
}

async function put(item: QItem) { await (await db()).put('queue', item); }
async function remove(id: string) { await (await db()).delete('queue', id); }

export async function enqueue(entry: Entry): Promise<void> {
  await put({ entry, state: 'saving', createdAt: Date.now(), tries: 0 });
  changed();
}

/** Undo an entry that has not synced. It may already be in the server's retry ledger, so a
 * delete for its ID is kept and sent on every flush until the server confirms. */
export async function discard(id: string): Promise<void> {
  const d = await db();
  await d.put('undo', Date.now(), id);
  await d.delete('queue', id);
  changed();
}

let running: Promise<void> | null = null;

export function flush(): Promise<void> {
  running ??= run().finally(() => { running = null; });
  return running;
}

async function run(): Promise<void> {
  const d = await db();
  for (const id of (await d.getAllKeys('undo')) as string[]) {
    try {
      const r = await api(`/api/entries/${id}/delete`, { body: {} });
      if (r.status === 200 || r.status === 404) await d.delete('undo', id);
    } catch (e) {
      if (e instanceof Locked) { needsUnlock = true; changed(); return; }
      if (e instanceof Offline) return;
      throw e;
    }
  }
  for (const item of await items()) {
    if (item.stuck) continue; // the server rejected it as invalid; only the user can fix or discard it
    let res;
    try {
      res = await api<{ status: string; row?: number; error?: string; retry?: boolean; detail?: unknown }>(
        '/api/entries', { body: item.entry, timeout: 15000 });
    } catch (e) {
      if (e instanceof Locked) { needsUnlock = true; changed(); return; }
      if (e instanceof Offline) { await mark(item, 'queued', 'offline'); return; }
      throw e;
    }
    needsUnlock = false;
    lastSync = Date.now();
    const s = res.data?.status;
    if (res.status === 200 && (s === 'written' || s === 'deleted')) {
      await remove(item.entry.id);
      changed();
      if (s === 'written') savedListeners.forEach((f) => f({ id: item.entry.id, row: res.data.row! }));
    } else if (res.status === 422) {
      await mark(item, 'failed', errorText(res.data), true);
    } else if (res.status === 202 && res.data?.retry === false) {
      await mark(item, 'failed', res.data.error ?? 'sheet refused the row');
    } else {
      // 202 queued (the server keeps retrying too), 5xx, 429: try again later
      await mark(item, 'queued', res.data?.error ?? `server ${res.status}`);
    }
  }
}

async function mark(item: QItem, state: QState, error: string, stuck = false) {
  await put({ ...item, state, error, stuck, tries: item.tries + 1 });
  changed();
}

let installed = false;
export function installSync(): void {
  if (installed) return;
  installed = true;
  const go = () => { void flush().catch(() => undefined); };
  window.addEventListener('online', go);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') go(); });
  setInterval(async () => { if ((await items()).length) go(); }, 30000);
  go();
}

/** Tests only: close the database so it can be deleted between tests. */
export async function _closeForTests(): Promise<void> {
  if (dbp) (await dbp).close();
  dbp = null;
}
