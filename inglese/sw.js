/* App inglese senza rete.
 *
 * Il telefono tiene una copia di tutta l'app e di tutti i contenuti del corso: si apre
 * subito anche in macchina dove non prende. Le domande al server (/api/...) non passano
 * di qui: quelle vogliono la rete.
 *
 * VERSIONE la scrive costruisci.py (impronta dei file). Quando cambia, il telefono scarica
 * la copia nuova e avvisa la pagina aperta: compare «C'è una versione nuova», e si
 * ricarica solo se Andrea tocca «Aggiorna adesso». Mai da soli.
 */
const VERSIONE = '5e1c493f90d9';
const CASSETTO = 'inglese-' + VERSIONE;
const FILE = [
 "./",
 "index.html",
 "app.js",
 "stile.css",
 "manifest.webmanifest",
 "icona-180.png",
 "icona-192.png",
 "icona-512.png",
 "contenuti/a1/unita-01.json",
 "contenuti/a1/unita-02.json",
 "contenuti/a1/unita-03.json",
 "contenuti/a1/unita-04.json",
 "contenuti/a1/unita-05.json",
 "contenuti/a1/unita-06.json",
 "contenuti/a1/unita-07.json",
 "contenuti/a1/unita-08.json",
 "contenuti/a1/unita-09.json",
 "contenuti/a1/unita-10.json",
 "contenuti/a1/unita-11.json",
 "contenuti/a1/unita-12.json",
 "contenuti/percorso.json",
 "contenuti/test-livello.json"
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CASSETTO).then(c => c.addAll(FILE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(nomi => {
    const vecchi = nomi.filter(n => n.startsWith('inglese-') && n !== CASSETTO);
    return Promise.all(vecchi.map(n => caches.delete(n))).then(() => self.clients.claim()).then(() => {
      if(!vecchi.length) return;          // prima installazione: niente da dire
      return self.clients.matchAll({type: 'window'})
        .then(f => f.forEach(c => c.postMessage({inglese: 'versione-nuova', versione: VERSIONE})));
    });
  }));
});

self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if(e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.startsWith('/api/')) return;
  const chiave = e.request.mode === 'navigate' ? './' : e.request;
  e.respondWith(caches.open(CASSETTO).then(c => c.match(chiave, {ignoreSearch: true}).then(s => s || fetch(e.request))));
});
