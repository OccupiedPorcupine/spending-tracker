/** Service worker registration and the "Update ready" prompt. iOS Home Screen apps otherwise keep
 * running an old build, so the page also asks for an update check whenever it becomes visible. */
let waiting: ServiceWorker | null = null;
const listeners = new Set<() => void>();
export const updateReady = () => waiting !== null;
export const onUpdate = (f: () => void) => { listeners.add(f); return () => { listeners.delete(f); }; };

export function registerSW(): void {
  if (!('serviceWorker' in navigator) || !import.meta.env.PROD) return;
  let reloading = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (reloading) return;
    reloading = true;
    location.reload();
  });
  window.addEventListener('load', async () => {
    const reg = await navigator.serviceWorker.register('/sw.js').catch(() => null);
    if (!reg) return;
    const found = (w: ServiceWorker | null) => {
      if (!w || !navigator.serviceWorker.controller) return; // first install: nothing to replace
      waiting = w;
      listeners.forEach((f) => f());
    };
    if (reg.waiting) found(reg.waiting);
    reg.addEventListener('updatefound', () => {
      const w = reg.installing;
      w?.addEventListener('statechange', () => { if (w.state === 'installed') found(w); });
    });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') void reg.update().catch(() => undefined);
    });
  });
}

export function applyUpdate(): void {
  if (waiting) waiting.postMessage('SKIP_WAITING');
  else location.reload();
}
