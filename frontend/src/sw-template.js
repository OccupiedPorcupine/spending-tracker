/* Service worker. The version and file list are filled in at build time (see vite.config.ts).
 *
 *  - Only the app shell is cached (index.html, JS, CSS, fonts, icons). /api is never cached.
 *  - Pages are served from the cache first, so the installed app opens instantly and offline,
 *    even when the tailnet is unreachable and a network request would hang.
 *  - A new build installs in the background and waits. The page shows "Update ready, tap to
 *    reload"; the tap sends SKIP_WAITING, the new worker takes over and the page reloads.
 */
const CACHE = 'spend-__VERSION__';
const FILES = __FILES__;

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES)));
});

self.addEventListener('message', (e) => {
  if (e.data === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('spend-') && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  if (req.mode === 'navigate') {
    e.respondWith(caches.open(CACHE).then((c) => c.match('/index.html')).then((hit) => hit || fetch(req)));
    return;
  }
  e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
