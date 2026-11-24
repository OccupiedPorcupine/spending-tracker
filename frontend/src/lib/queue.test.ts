import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Entry } from './types';

const entry = (id: string): Entry => ({
  id, amount: 4.5, direction: 'expense', category: 'Food', required: true, currency: 'SGD', notes: 'x', date: '2026-11-24', source: 'app',
});
const reply = (status: number, body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));

let prev: typeof import('./queue') | null = null;
async function fresh() {
  if (prev) await prev._closeForTests();
  vi.resetModules();
  await new Promise((done) => { const r = indexedDB.deleteDatabase('spend'); r.onsuccess = r.onerror = done; });
  prev = await import('./queue');
  return prev;
}

describe('queue', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('keeps entries until the server says written', async () => {
    const q = await fresh();
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => Promise.reject(new TypeError('offline')))
      .mockImplementationOnce(() => reply(202, { status: 'queued', error: 'slow' }))
      .mockImplementationOnce(() => reply(200, { status: 'written', row: 595 }));
    vi.stubGlobal('fetch', fetchMock);
    await q.enqueue(entry('a'));
    await q.flush();
    expect((await q.items())[0].state).toBe('queued');
    await q.flush();
    expect((await q.items()).length).toBe(1);
    const saved: unknown[] = [];
    q.onSaved((s) => saved.push(s));
    await q.flush();
    expect(await q.items()).toEqual([]);
    expect(saved).toEqual([{ id: 'a', row: 595 }]);
    // the same entry ID was sent every time
    for (const c of fetchMock.mock.calls) expect(JSON.parse(c[1].body).id).toBe('a');
  });

  it('pauses on 401 and keeps the entry', async () => {
    const q = await fresh();
    vi.stubGlobal('fetch', vi.fn(() => reply(401, { error: 'locked' })));
    await q.enqueue(entry('b'));
    await q.flush();
    expect(q.needsUnlock).toBe(true);
    expect((await q.items()).map((i) => i.entry.id)).toEqual(['b']);
  });

  it('parks invalid entries instead of retrying forever', async () => {
    const q = await fresh();
    const f = vi.fn(() => reply(422, { detail: [{ msg: 'bad' }] }));
    vi.stubGlobal('fetch', f);
    await q.enqueue(entry('c'));
    await q.flush();
    await q.flush();
    expect(f).toHaveBeenCalledTimes(1);
    expect((await q.items())[0].state).toBe('failed');
  });

  it('sends a delete for an entry undone before it synced', async () => {
    const q = await fresh();
    const f = vi.fn((url: string) => reply(url.endsWith('/delete') ? 404 : 200, { status: 'written', row: 1 }));
    vi.stubGlobal('fetch', f);
    await q.enqueue(entry('d'));
    await q.discard('d');
    await q.flush();
    expect(f.mock.calls.map((c) => c[0])).toEqual(['/api/entries/d/delete']);
    await q.flush();
    expect(f).toHaveBeenCalledTimes(1); // tombstone cleared after the server answered
  });
});
