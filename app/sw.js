/* Rilievo senza rete.
 *
 * L'app e' un file solo (index.html, con dentro caratteri, dati e programma): basta
 * tenerne una copia nel telefono. Si apre subito dalla copia e intanto si chiede al
 * server quella nuova, che vale dalla volta dopo. Cosi' si apre anche in giardino
 * dove non prende, e anche quando il server gratuito dorme e ci mette un minuto a
 * svegliarsi.
 *
 * Le domande al rilievo (/rilievo, /particelle) e le foto dall'alto non passano di qui:
 * quelle vogliono la rete, e l'app lo dice con parole sue.
 *
 * VERSIONE la scrive costruisci.py: cambia quando cambia l'app, e il telefono si
 * accorge da solo che c'e' una copia nuova da tenere.
 */
const VERSIONE = 'b5d79db81e68';
const CASSETTO = 'rilievo-app';
const FILE = ['./', 'manifest.webmanifest', 'icona-192.png', 'icona-512.png', 'icona-180.png'];

/* se il cassetto c'e' gia', questa non e' la prima installazione: c'era una copia
   vecchia aperta, e chi la sta guardando va avvisato che ne e' arrivata una nuova */
let eraGiaQui = false;

self.addEventListener('install', e => {
  e.waitUntil(caches.has(CASSETTO)
    .then(c => { eraGiaQui = c; return caches.open(CASSETTO); })
    .then(c => c.addAll(FILE))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(self.clients.claim().then(() => {
    if(!eraGiaQui) return;
    /* la pagina aperta e' quella di prima: glielo diciamo, e ricarica solo se
       la persona tocca "Aggiorna". Mai da soli: potrebbe star dettando. */
    return self.clients.matchAll({type:'window'})
      .then(f => f.forEach(c => c.postMessage({rilievo:'versione-nuova', versione:VERSIONE})));
  }));
});

self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin) return;
  const pagina = e.request.mode === 'navigate' || u.pathname === '/' || u.pathname === '/index.html';
  if (!pagina && !FILE.some(f => new URL(f, location.href).pathname === u.pathname)) return;
  const chiave = pagina ? './' : e.request;

  let salvataggio = Promise.resolve();
  const nuova = fetch(e.request).then(r => {
    if (r.ok) {
      const copia = r.clone();
      salvataggio = caches.open(CASSETTO).then(c => c.put(chiave, copia));
    }
    return r;
  });
  e.waitUntil(nuova.then(() => salvataggio, () => {}));
  e.respondWith(caches.open(CASSETTO).then(c => c.match(chiave)).then(s => s || nuova));
});
