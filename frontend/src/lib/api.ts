/** Fetch wrapper. Every request has a timeout: with Tailscale off the server's address is
 * unroutable and a plain fetch can hang for over a minute. */
export class Offline extends Error {}
export class Locked extends Error {}

export interface ApiResult<T> { status: number; data: T }

const lockListeners = new Set<() => void>();
export const onLocked = (f: () => void) => { lockListeners.add(f); return () => { lockListeners.delete(f); }; };

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown; timeout?: number } = {}): Promise<ApiResult<T>> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), opts.timeout ?? 8000);
  let res: Response;
  try {
    res = await fetch(path, {
      method: opts.method ?? (opts.body === undefined ? 'GET' : 'POST'),
      headers: opts.body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      credentials: 'same-origin',
      cache: 'no-store',
      signal: ctl.signal,
    });
  } catch {
    throw new Offline('offline');
  } finally {
    clearTimeout(timer);
  }
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    lockListeners.forEach((f) => f());
    throw new Locked('locked');
  }
  let data: unknown = null;
  try { data = await res.json(); } catch { /* empty body */ }
  return { status: res.status, data: data as T };
}

export const errorText = (data: unknown): string => {
  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>;
    if (typeof d.detail === 'string') return d.detail;
    if (typeof d.error === 'string') return d.error;
    if (Array.isArray(d.detail)) return 'invalid entry';
  }
  return 'something went wrong';
};
