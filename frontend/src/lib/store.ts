/** Small per-device preferences in localStorage. Every access is guarded: storage can be
 * unavailable (private mode, cleared site data) and the app must still work. */
export function load<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem('spend-' + key);
    return v === null ? fallback : (JSON.parse(v) as T);
  } catch {
    return fallback;
  }
}

export function save(key: string, value: unknown): void {
  try { localStorage.setItem('spend-' + key, JSON.stringify(value)); } catch { /* ignore */ }
}
