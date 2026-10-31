/* Centralino: riceve le notifiche e, al tocco, apre il Centralino sulla cosa giusta.
   Non salva pagine in memoria: il Centralino deve sempre mostrare lo stato vero. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = { titolo: 'Centralino', testo: e.data ? e.data.text() : '' }; }
  const titolo = d.titolo || 'Centralino';
  e.waitUntil(self.registration.showNotification(titolo, {
    body: d.testo || '',
    tag: d.tag || undefined,
    renotify: !!d.tag,
    icon: '/centralino/icona-180.png',
    badge: '/centralino/icona-180.png',
    data: { url: d.url || '/centralino' }
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/centralino';
  e.waitUntil((async () => {
    const tutti = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of tutti) {
      if (new URL(c.url).pathname.startsWith('/centralino')) {
        c.postMessage({ apri: url });
        try { await c.focus(); } catch (x) {}
        return;
      }
    }
    await self.clients.openWindow(url);
  })());
});
