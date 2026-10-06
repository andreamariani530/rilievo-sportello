/* App inglese di Andrea.
 *
 * Tutto lo stato sta nel telefono (localStorage) e, con l'accesso, anche sul server.
 * I contenuti del corso sono file JSON in contenuti/: scritti e controllati, non inventati
 * al volo. L'insegnante (Claude Haiku, dal server) aggiunge spiegazioni e conversazione.
 *
 * Le variabili che i collaudi toccano sono `var` (S, ACCESSO, VOCE, Q, MACCHINA, C):
 * cosi' si possono leggere e sostituire da fuori.
 */
'use strict';

var VERSIONE_APP = '2026-10-06 04:00';
var CHIAVE_STATO = 'inglese-stato-v1';
var CHIAVE_ACCESSO = 'inglese-accesso-v1';
var GIORNO = 86400000;
var INTERVALLI = [0, 1, 2, 4, 8, 15, 30, 60];      // giorni di attesa per ogni "scatola"
var LIVELLI = ['A1', 'A2', 'B1', 'B2'];
var PASSI = [
  {id: 'parole', nome: 'Parole nuove'},
  {id: 'grammatica', nome: 'La regola'},
  {id: 'ascolto', nome: 'Ascolto'},
  {id: 'esercizi', nome: 'Esercizi'},
  {id: 'esame', nome: 'Esame finale'}
];
var UNITA_A1 = Array.from({length: 12}, (_, i) => 'a1-' + String(i + 1).padStart(2, '0'));

var S = null;            // lo stato (progressi)
var ACCESSO = null;      // {mail, gettone} oppure {locale:true}
var C = {percorso: null, test: null, unita: {}};
var Q = null;            // l'esercizio in corso (quiz)
var MACCHINA = {attiva: false};
var PARLA = {attiva: false};

/* ---------- tempo, così i collaudi possono spostare i giorni ---------- */
var spostamento = 0;
function adesso(){ return Date.now() + spostamento; }
function inizioGiorno(t){ const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); }
function chiaveGiorno(t){ const d = new Date(t); return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate(); }

/* ---------- utilità ---------- */
function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])); }
function $(sel){ return document.querySelector(sel); }
function mescola(a){ a = a.slice(); for(let i = a.length - 1; i > 0; i--){ const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; }
function nuovoId(){ return 'c' + adesso().toString(36) + Math.random().toString(36).slice(2, 7); }
function schermo(html){ $('#schermo').innerHTML = html; if(typeof window.scrollTo === 'function') try{ window.scrollTo(0, 0); }catch(e){} }

/* ---------- confronto delle risposte ---------- */
var NUMERI = {zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18,
  nineteen: 19, twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90, hundred: 100};
var CONTRATTE = [
  [/\bi'm\b/g, 'i am'], [/\b(you|we|they|who|what)'re\b/g, '$1 are'],
  [/\b(he|she|it|that|what|there|where|who|here|how)'s\b/g, '$1 is'],
  [/\bcan't\b/g, 'can not'], [/\bcannot\b/g, 'can not'], [/\bwon't\b/g, 'will not'], [/\bain't\b/g, 'is not'],
  [/\b([a-z]+)n't\b/g, '$1 not'], [/\b(i|you|we|they|he|she|it)'d\b/g, '$1 would'],
  [/\b(i|you|we|they|he|she|it|that)'ll\b/g, '$1 will'], [/\b(i|you|we|they)'ve\b/g, '$1 have'],
  [/\blet's\b/g, 'let us'], [/\bok\b/g, 'okay']
];
function normalizza(s){
  s = String(s || '').toLowerCase().replace(/[’‘`´]/g, "'");
  for(const [re, r] of CONTRATTE) s = s.replace(re, r);
  s = s.replace(/[^a-z0-9'\s]/g, ' ').replace(/'/g, '').replace(/\s+/g, ' ').trim();
  return s.split(' ').map(w => (w in NUMERI ? String(NUMERI[w]) : w)).join(' ');
}
function distanza(a, b){
  if(Math.abs(a.length - b.length) > 2) return 3;
  const d = Array.from({length: a.length + 1}, (_, i) => [i]);
  for(let j = 1; j <= b.length; j++) d[0][j] = j;
  for(let i = 1; i <= a.length; i++) for(let j = 1; j <= b.length; j++)
    d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return d[a.length][b.length];
}
/* 'giusta', 'quasi' (una lettera sbagliata in una frase lunga: vale, ma lo diciamo) o 'sbagliata' */
function confronta(risposta, accettate){
  const n = normalizza(risposta);
  if(!n) return 'sbagliata';
  const buone = (accettate || []).map(normalizza);
  if(buone.includes(n)) return 'giusta';
  if(n.length > 7 && buone.some(b => distanza(b, n) <= 1)) return 'quasi';
  return 'sbagliata';
}
/* la voce: il telefono capisce a modo suo, quindi basta che ci siano quasi tutte le parole */
function simileVoce(sentito, attese){
  const alternative = String(sentito || '').split('|').map(normalizza).filter(Boolean);
  if(!alternative.length) return false;
  return (attese || []).some(a => {
    const parole = normalizza(a).split(' ').filter(Boolean);
    if(!parole.length) return false;
    return alternative.some(alt => {
      const dette = alt.split(' ');
      const trovate = parole.filter(p => dette.includes(p)).length;
      return trovate / parole.length >= 0.75;
    });
  });
}

/* ---------- stato ---------- */
function statoVuoto(){
  return {versione: 1, aggiornato: 0, livello: null, testFatto: false, risultatoTest: null,
    unita: {}, ripasso: [], frasi: [], giorni: {ultimo: '', serie: 0, fatti: 0}, chiedi: []};
}
function caricaStato(){
  try{ S = Object.assign(statoVuoto(), JSON.parse(localStorage.getItem(CHIAVE_STATO) || 'null') || {}); }
  catch(e){ S = statoVuoto(); }
  try{ ACCESSO = JSON.parse(localStorage.getItem(CHIAVE_ACCESSO) || 'null'); }catch(e){ ACCESSO = null; }
}
function salva(){
  S.aggiornato = adesso();
  localStorage.setItem(CHIAVE_STATO, JSON.stringify(S));
  programmaSync();
}
function salvaAccesso(){ localStorage.setItem(CHIAVE_ACCESSO, JSON.stringify(ACCESSO)); }
function adotta(p){
  S = Object.assign(statoVuoto(), p);
  localStorage.setItem(CHIAVE_STATO, JSON.stringify(S));
}

/* ---------- server ---------- */
function api(via, corpo, metodo){
  const h = {'content-type': 'application/json'};
  if(ACCESSO && ACCESSO.mail){ h['x-mail'] = ACCESSO.mail; h['x-gettone'] = ACCESSO.gettone || ''; }
  return fetch(via, {method: metodo || (corpo ? 'POST' : 'GET'), headers: h, body: corpo ? JSON.stringify(corpo) : undefined})
    .then(r => r.json().catch(() => ({})).then(d => ({codice: r.status, dati: d || {}})));
}
var timerSync = null;
function programmaSync(){
  if(!ACCESSO || !ACCESSO.gettone) return;
  clearTimeout(timerSync);
  timerSync = setTimeout(sincronizza, 1500);
}
function sincronizza(forza){
  if(!ACCESSO || !ACCESSO.gettone) return Promise.resolve('fuori');
  return api('/api/progressi', {progressi: S, forza: !!forza}).then(({codice, dati}) => {
    if(codice === 200){ ACCESSO.salvato = adesso(); salvaAccesso(); return 'ok'; }
    if(codice === 409 && dati.progressi){ adotta(dati.progressi); vai(); return 'adottata'; }
    if(codice === 401){ ACCESSO.gettone = ''; salvaAccesso(); return 'rientra'; }
    return 'errore';
  }).catch(() => 'senza-rete');
}
function entra(mail, password, nuovo){
  return api(nuovo ? '/api/registra' : '/api/entra', {mail, password}).then(r => {
    // il server gratuito perde il disco ai riavvii: si ricrea l'accesso con le stesse parole
    if(!nuovo && r.codice === 401 && r.dati.nessun_account) return api('/api/registra', {mail, password});
    return r;
  }).then(r => {
    if(r.codice !== 200 || !r.dati.gettone) return r.dati.errore || 'Non riesco a entrare. Riprova.';
    ACCESSO = {mail: r.dati.mail, gettone: r.dati.gettone};
    salvaAccesso();
    const remoto = r.dati.progressi;
    if(remoto && (remoto.aggiornato || 0) > (S.aggiornato || 0)){ adotta(remoto); return ''; }
    return sincronizza(true).then(() => '');
  }).catch(() => 'Il server non risponde. Se è appena partito ci mette un minuto: riprova.');
}
function esci(){
  const fatto = ACCESSO && ACCESSO.gettone ? api('/api/esci', {}).catch(() => {}) : Promise.resolve();
  return fatto.then(() => { ACCESSO = null; localStorage.removeItem(CHIAVE_ACCESSO); location.hash = '#accesso'; vai(); });
}

/* ---------- contenuti ---------- */
function leggiContenuto(via){
  return fetch('contenuti/' + via).then(r => { if(!r.ok) throw new Error(via); return r.json(); });
}
function percorso(){ return C.percorso ? Promise.resolve(C.percorso) : leggiContenuto('percorso.json').then(p => (C.percorso = p)); }
function testLivello(){ return C.test ? Promise.resolve(C.test) : leggiContenuto('test-livello.json').then(p => (C.test = p)); }
function unita(id){
  if(C.unita[id]) return Promise.resolve(C.unita[id]);
  const [l, n] = id.split('-');
  return leggiContenuto(l + '/unita-' + n + '.json').then(u => (C.unita[id] = u));
}

/* ---------- avanzamento nel corso ---------- */
function progresso(id){ return S.unita[id] || (S.unita[id] = {passo: 0, superata: false, voto: null, tentativi: 0}); }
function superata(id){ return !!(S.unita[id] && S.unita[id].superata); }
function aperta(id){
  const i = UNITA_A1.indexOf(id);
  if(i < 0) return false;
  if(i === 0 || superata(UNITA_A1[i - 1])) return true;
  return LIVELLI.indexOf(S.livello || 'A1') >= 1;     // il test dice che A1 lo sai: tutto aperto
}
function unitaCorrente(){ return UNITA_A1.find(id => aperta(id) && !superata(id)) || null; }
function livelloAttuale(){
  if(S.livello && LIVELLI.indexOf(S.livello) > 0) return S.livello;
  return UNITA_A1.every(superata) ? 'A2' : 'A1';
}
function segnaGiorno(){
  const oggi = chiaveGiorno(adesso()), ieri = chiaveGiorno(adesso() - GIORNO);
  if(S.giorni.ultimo !== oggi){
    S.giorni.serie = S.giorni.ultimo === ieri ? S.giorni.serie + 1 : 1;
    S.giorni.ultimo = oggi;
  }
  S.giorni.fatti = (S.giorni.fatti || 0) + 1;
}

/* ---------- ripasso (ripetizione distanziata, a scatole) ---------- */
function testoCarta(es){
  if(es.tipo === 'completa') return {it: es.it || es.frase, en: (es.frase || '').replace('___', (es.risposte || [''])[0])};
  if(es.tipo === 'scelta' || es.tipo === 'ascolto') return {it: es.domanda, en: (es.scelte || [])[es.giusta]};
  if(es.tipo === 'voce') return {it: es.it, en: es.en};
  return {it: es.it || '', en: (es.risposte || [''])[0]};
}
function aggiungiCarta(es, origine, scatola){
  const t = testoCarta(es);
  const chiave = normalizza(t.en) + '|' + String(t.it || '').toLowerCase();
  let c = S.ripasso.find(x => normalizza(x.en) + '|' + String(x.it || '').toLowerCase() === chiave);
  const domani = inizioGiorno(adesso()) + GIORNO;
  if(c){
    if(!scatola){ c.scatola = 0; c.prossima = domani; }
    return c;
  }
  c = {id: nuovoId(), es: es, en: t.en, it: t.it, origine: origine || '', scatola: scatola || 0,
    prossima: scatola ? inizioGiorno(adesso()) + INTERVALLI[scatola] * GIORNO : domani, sbagli: 0, creata: adesso()};
  S.ripasso.push(c);
  return c;
}
function cartaParola(p){ return {tipo: 'traduci', it: p.it, risposte: [p.en], parola: true, esempio: p.esempio}; }
function aggiornaCarta(c, ok){
  if(ok){ c.scatola = Math.min((c.scatola || 0) + 1, INTERVALLI.length - 1); c.prossima = inizioGiorno(adesso()) + INTERVALLI[c.scatola] * GIORNO; }
  else{ c.scatola = 0; c.sbagli = (c.sbagli || 0) + 1; c.prossima = inizioGiorno(adesso()) + GIORNO; }
}
function daRipassare(max){
  return S.ripasso.filter(c => c.prossima <= adesso()).sort((a, b) => a.prossima - b.prossima).slice(0, max || 12);
}
function modificaCarta(id, en, it){
  const c = S.ripasso.find(x => x.id === id);
  if(!c || !String(en).trim() || !String(it).trim()) return false;
  c.en = String(en).trim(); c.it = String(it).trim();
  c.es = {tipo: 'traduci', it: c.it, risposte: [c.en]};
  salva(); return true;
}
function cancellaCarta(id){ S.ripasso = S.ripasso.filter(x => x.id !== id); salva(); }
function nuovaCarta(en, it){
  if(!String(en).trim() || !String(it).trim()) return null;
  const c = aggiungiCarta({tipo: 'traduci', it: String(it).trim(), risposte: [String(en).trim()]}, 'aggiunta da te');
  c.prossima = adesso(); salva(); return c;
}

/* ---------- voce del telefono ---------- */
var VOCE = {
  puoParlare(){ return typeof speechSynthesis !== 'undefined' && typeof SpeechSynthesisUtterance !== 'undefined'; },
  riconoscitore(){ return typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition) || null; },
  puoAscoltare(){ return !!this.riconoscitore(); },
  parla(testo, lingua, chi){
    return new Promise(ok => {
      if(!this.puoParlare() || !testo) return ok();
      let fatto = false; const fine = () => { if(!fatto){ fatto = true; ok(); } };
      const u = new SpeechSynthesisUtterance(testo);
      u.lang = lingua === 'it' ? 'it-IT' : 'en-US';
      u.rate = lingua === 'it' ? 1 : 0.92;
      if(chi === 'donna') u.pitch = 1.15; else if(chi === 'uomo') u.pitch = 0.85;
      const voci = speechSynthesis.getVoices ? speechSynthesis.getVoices() : [];
      const v = voci.find(x => x.lang && x.lang.replace('_', '-').startsWith(u.lang));
      if(v) u.voice = v;
      u.onend = fine; u.onerror = fine;
      speechSynthesis.speak(u);
      setTimeout(fine, 2500 + testo.length * 110);
    });
  },
  ascolta(lingua, ms){
    return new Promise(ok => {
      const R = this.riconoscitore();
      if(!R) return ok('');
      const r = new R();
      r.lang = lingua === 'it' ? 'it-IT' : 'en-US';
      r.interimResults = false; r.maxAlternatives = 3; r.continuous = false;
      let fatto = false;
      const fine = t => { if(fatto) return; fatto = true; try{ r.abort(); }catch(e){} ok(t || ''); };
      r.onresult = e => { const alt = Array.from(e.results[0] || []).map(a => a.transcript); fine(alt.join(' | ')); };
      r.onerror = () => fine(''); r.onend = () => fine('');
      try{ r.start(); }catch(e){ fine(''); }
      setTimeout(() => fine(''), ms || 8000);
    });
  },
  ferma(){ try{ if(this.puoParlare()) speechSynthesis.cancel(); }catch(e){} }
};
function ascolta(testo){ return VOCE.parla(testo, 'en'); }

/* ---------- il quiz: esercizi, esame, ripasso e test usano lo stesso motore ---------- */
function avviaQuiz(o){
  Q = Object.assign({i: 0, giuste: 0, errori: [], risposto: false, scelti: [], titolo: '', modo: 'esercizi'}, o);
  disegnaQuiz();
}
function consegnaDi(e){
  if(e.tipo === 'scelta') return esc(e.domanda);
  if(e.tipo === 'ascolto') return esc(e.domanda || 'Che cosa hai sentito?');
  if(e.tipo === 'completa') return esc(e.frase).replace('___', '<span class="accento">_____</span>');
  if(e.tipo === 'riordina') return 'Metti in ordine: <i>' + esc(e.it || '') + '</i>';
  if(e.tipo === 'traduci') return (e.parola ? 'Come si dice: ' : 'In inglese: ') + '<i>' + esc(e.it) + '</i>';
  if(e.tipo === 'voce') return 'Ascolta e ripeti ad alta voce:<br><span class="parola-en">' + esc(e.en) + '</span>';
  return '';
}
function disegnaQuiz(){
  const e = Q.voci[Q.i];
  const perc = Math.round(Q.i / Q.voci.length * 100);
  let corpo = '';
  if(e.tipo === 'scelta' || e.tipo === 'ascolto'){
    corpo = (e.tipo === 'ascolto' ? '<p><button class="vuoto" id="riascolta">Ascolta</button></p>' : '') +
      '<div class="scelte">' + e.scelte.map((s, k) => `<button data-scelta="${k}">${esc(s)}</button>`).join('') + '</div>';
  } else if(e.tipo === 'riordina'){
    Q.scelti = [];
    Q.pezzi = mescola(e.parole.map((p, k) => ({p, k})));
    corpo = '<div class="costruita" id="costruita"></div><div class="pezzi" id="pezzi">' +
      Q.pezzi.map((x, k) => `<button data-pezzo="${k}">${esc(x.p)}</button>`).join('') +
      '</div><div class="fila"><button id="controlla">Controlla</button><button class="vuoto" id="ricomincia">Ricomincia</button></div>';
  } else if(e.tipo === 'voce'){
    corpo = '<div class="fila"><button class="vuoto" id="riascolta">Ascolta</button>' +
      (VOCE.puoAscoltare() ? '<button id="parlo">Ora parlo io</button>' : '') +
      '<button class="testo" id="salta-voce">Adesso non posso parlare</button></div><p class="piccolo" id="sentito"></p>' +
      (e.it ? '<p class="piccolo">' + esc(e.it) + '</p>' : '');
  } else {
    corpo = '<input id="risposta" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Scrivi qui">' +
      (e.tipo === 'completa' && e.it ? '<p class="piccolo">' + esc(e.it) + '</p>' : '') +
      '<p class="fila"><button id="controlla">Controlla</button>' +
      (VOCE.puoAscoltare() && e.tipo === 'traduci' ? '<button class="vuoto" id="detta">Dillo a voce</button>' : '') + '</p>';
  }
  schermo(`<p class="occhiello">${esc(Q.titolo)} · ${Q.i + 1} di ${Q.voci.length}</p>
    <div class="barra"><i style="width:${perc}%"></i></div>
    <p class="consegna">${consegnaDi(e)}</p>${corpo}<div id="esito"></div>
    <p><button class="testo" id="esci-quiz">Esci</button></p>`);
  Q.risposto = false;
  document.querySelectorAll('[data-scelta]').forEach(b => b.onclick = () => rispondiQuiz(+b.dataset.scelta));
  document.querySelectorAll('[data-pezzo]').forEach(b => b.onclick = () => {
    if(Q.risposto) return;
    Q.scelti.push(Q.pezzi[+b.dataset.pezzo].p); b.disabled = true;
    $('#costruita').textContent = Q.scelti.join(' ');
  });
  const on = (id, f) => { const x = $(id); if(x) x.onclick = f; };
  on('#ricomincia', () => { Q.scelti = []; $('#costruita').textContent = ''; document.querySelectorAll('[data-pezzo]').forEach(b => b.disabled = false); });
  on('#controlla', () => rispondiQuiz(e.tipo === 'riordina' ? Q.scelti.join(' ') : ($('#risposta') || {}).value));
  on('#riascolta', () => VOCE.parla(e.en, 'en'));
  on('#salta-voce', () => rispondiQuiz({saltato: true}));
  on('#parlo', () => { $('#sentito').textContent = 'Ti ascolto…'; VOCE.ascolta('en', 7000).then(t => rispondiQuiz({sentito: t})); });
  on('#detta', () => VOCE.ascolta('en', 8000).then(t => { if(t && $('#risposta')) $('#risposta').value = t.split('|')[0].trim(); }));
  on('#esci-quiz', () => { Q = null; vai(); });
  const r = $('#risposta');
  if(r) r.onkeydown = ev => { if(ev.key === 'Enter') rispondiQuiz(r.value); };
  if(e.tipo === 'ascolto' || e.tipo === 'voce') VOCE.parla(e.en, 'en');
}
function giustaDi(e){
  if(e.tipo === 'scelta' || e.tipo === 'ascolto') return e.scelte[e.giusta];
  if(e.tipo === 'completa') return e.frase.replace('___', e.risposte[0]);
  if(e.tipo === 'voce') return e.en;
  return e.risposte[0];
}
function valuta(e, v){
  if(e.tipo === 'scelta' || e.tipo === 'ascolto') return v === e.giusta ? 'giusta' : 'sbagliata';
  if(e.tipo === 'voce'){
    if(v && v.saltato) return 'saltata';
    return simileVoce(v && v.sentito, [e.en]) ? 'giusta' : 'sbagliata';
  }
  return confronta(v, e.risposte);
}
function rispondiQuiz(v){
  if(!Q || Q.risposto) return;
  const e = Q.voci[Q.i];
  const esito = valuta(e, v);
  const ok = esito === 'giusta' || esito === 'quasi' || esito === 'saltata';
  Q.risposto = true;
  if(esito !== 'saltata'){ if(ok) Q.giuste++; else Q.errori.push(e); }
  else Q.saltate = (Q.saltate || 0) + 1;
  if(Q.dopoRisposta) Q.dopoRisposta(e, ok && esito !== 'saltata');
  if(e.tipo === 'scelta' || e.tipo === 'ascolto') document.querySelectorAll('[data-scelta]').forEach(b => {
    b.disabled = true;
    if(+b.dataset.scelta === e.giusta) b.classList.add('giusta');
    else if(+b.dataset.scelta === v) b.classList.add('sbagliata');
  });
  let msg;
  if(esito === 'giusta') msg = 'Giusto.';
  else if(esito === 'quasi') msg = 'Giusto, ma attento a come si scrive: <i>' + esc(giustaDi(e)) + '</i>';
  else if(esito === 'saltata') msg = 'Va bene, la ripetiamo un\'altra volta.';
  else msg = (e.tipo === 'voce' && v && v.sentito ? 'Ho capito: «' + esc(v.sentito.split('|')[0]) + '». ' : '') +
    'La risposta giusta: <i>' + esc(giustaDi(e)) + '</i>';
  if(e.spiega && esito === 'sbagliata') msg += '<br><span class="piccolo">' + esc(e.spiega) + '</span>';
  const ultima = Q.i + 1 >= Q.voci.length;
  $('#esito').innerHTML = `<div class="esito${ok ? '' : ' no'}">${msg}</div><button id="avanti">${ultima ? 'Fine' : 'Avanti'}</button>`;
  $('#avanti').onclick = avantiQuiz;
}
function avantiQuiz(){
  if(!Q) return;
  Q.i++;
  if(Q.i >= Q.voci.length){
    const q = Q; Q = null;
    const contate = q.voci.length - (q.saltate || 0);
    q.fine({giuste: q.giuste, totale: contate, errori: q.errori, quota: contate ? q.giuste / contate : 1});
    return;
  }
  disegnaQuiz();
}

/* ---------- schermate ---------- */
var MENU = [['oggi', 'Oggi', '◐'], ['percorso', 'Percorso', '≡'], ['macchina', 'Macchina', '◉'], ['serie', 'Serie', '♪'], ['chiedi', 'Chiedi', '?'], ['io', 'Io', '·']];
function menu(qui){
  const n = $('#menu');
  if(!n) return;
  n.hidden = !qui;
  n.innerHTML = MENU.map(([id, nome, segno]) => `<a href="#${id}" class="${id === qui ? 'qui' : ''}"><span>${segno}</span>${nome}</a>`).join('');
}
function vai(){
  if(!S) caricaStato();
  if(MACCHINA.attiva && location.hash !== '#macchina') fermaMacchina();
  if(PARLA.attiva && location.hash !== '#parla') fermaParla();
  const h = (location.hash || '#oggi').slice(1).split('/');
  if(!ACCESSO) return schermataAccesso();
  if(!S.testFatto && h[0] !== 'test') return schermataInizioTest();
  const pagine = {oggi: schermataOggi, percorso: schermataPercorso, macchina: schermataMacchina, serie: schermataSerie,
    chiedi: schermataChiedi, io: schermataIo, test: schermataInizioTest, lezione: () => schermataLezione(h[1]),
    passo: () => apriPasso(h[1], +h[2]), ripasso: avviaRipasso, parla: schermataParla, accesso: schermataAccesso};
  (pagine[h[0]] || schermataOggi)();
}

/* accesso */
function schermataAccesso(){
  menu(null);
  schermo(`<p class="occhiello">Inglese</p><h1>Il tuo inglese,<br>dieci minuti al giorno.</h1>
    <p class="piccolo">Entra con la tua mail: i progressi restano salvati anche se cambi telefono.</p>
    <label for="mail">Mail</label><input id="mail" type="email" autocomplete="username">
    <label for="password">Password (almeno 8 caratteri)</label><input id="password" type="password" autocomplete="current-password">
    <p class="errore" id="errore-accesso"></p>
    <button class="largo" id="entra">Entra</button>
    <button class="largo vuoto" id="registra">Prima volta: crea l'accesso</button>
    <hr class="filetto"><button class="testo" id="locale">Usa senza accesso, solo su questo telefono</button>`);
  const prova = nuovo => {
    $('#errore-accesso').textContent = '';
    entra($('#mail').value, $('#password').value, nuovo).then(no => {
      if(no){ $('#errore-accesso').textContent = no; return; }
      location.hash = S.testFatto ? '#oggi' : '#test'; vai();
    });
  };
  $('#entra').onclick = () => prova(false);
  $('#registra').onclick = () => prova(true);
  $('#locale').onclick = () => { ACCESSO = {locale: true}; salvaAccesso(); location.hash = '#test'; vai(); };
}

/* test di livello: parte da A2, sale se va bene, scende se va male. 4 domande per livello. */
function schermataInizioTest(){
  menu(null);
  schermo(`<p class="occhiello">Prima di cominciare</p><h1>Vediamo da dove parti.</h1>
    <p>Circa cinque minuti. Le domande si adattano: se vanno bene salgono, se no scendono.
    Alcune sono da ascoltare: alza il volume.</p><p class="piccolo">Se non sai, tira a indovinare o scegli quella che ti sembra: non è un esame.</p>
    <button class="largo" id="via-test">Comincia il test</button>
    <button class="largo vuoto" id="da-zero">Parto da zero, salta il test</button>`);
  $('#via-test').onclick = avviaTest;
  $('#da-zero').onclick = () => chiudiTest('A1', ['Partito da zero']);
}
function avviaTest(){
  return testLivello().then(t => {
    const stato = {i: 1, passati: {}, provati: {}, storia: []};
    const giro = () => {
      const liv = LIVELLI[stato.i];
      stato.provati[liv] = true;
      const voci = mescola(t.domande.filter(d => d.livello === liv)).slice(0, 4);
      avviaQuiz({voci, titolo: 'Test di livello', modo: 'test', fine: r => {
        const passato = r.giuste >= 3;
        stato.storia.push(liv + ': ' + r.giuste + ' su 4');
        if(passato){
          stato.passati[liv] = true;
          if(stato.i === LIVELLI.length - 1) return chiudiTest('B2', stato.storia);
          if(stato.provati[LIVELLI[stato.i + 1]]) return chiudiTest(LIVELLI[stato.i + 1], stato.storia);
          stato.i++; return giro();
        }
        if(stato.i === 0 || stato.passati[LIVELLI[stato.i - 1]]) return chiudiTest(liv, stato.storia);
        stato.i--; return giro();
      }});
    };
    giro();
  });
}
function chiudiTest(livello, storia){
  S.livello = livello; S.testFatto = true; S.risultatoTest = {livello, storia, quando: adesso()};
  salva();
  /* l'indirizzo passa a Oggi senza cambiare schermata: cosi' «Rifai il test» riparte davvero */
  try{ history.replaceState(null, '', '#oggi'); }catch(e){}
  menu(null);
  const pronto = livello === 'A1';
  schermo(`<p class="occhiello">Il tuo livello</p><h1 class="accento">${esc(livello)}</h1>
    <p>${pronto ? 'Si parte dalle basi, con calma. In dodici unità arrivi a presentarti, chiedere e capire risposte semplici.'
      : 'Le basi le hai. Le unità A1 sono tutte aperte: puoi fare solo gli esami per ripassare. Le unità ' + esc(livello) + ' sono in preparazione.'}</p>
    <p class="piccolo">${(storia || []).map(esc).join(' · ')}</p>
    <button class="largo" id="vai-oggi">Vai alla lezione di oggi</button>`);
  $('#vai-oggi').onclick = () => { location.hash = '#oggi'; vai(); };
}

/* Oggi */
function schermataOggi(){
  menu('oggi');
  const cur = unitaCorrente();
  const ripasso = daRipassare().length;
  const p = cur ? progresso(cur) : null;
  return (cur ? unita(cur) : Promise.resolve(null)).then(u => {
    const nuovo = u ? `<b>${esc(PASSI[p.passo].nome)}</b> · Unità ${u.numero}, ${esc(u.titolo)}` : 'Hai finito il corso A1. Il livello A2 è in preparazione.';
    const ora = new Date(adesso()).getHours();
    schermo(`<p class="occhiello">${ora < 12 ? 'Buongiorno' : ora < 18 ? 'Buon pomeriggio' : 'Buonasera'}${S.giorni.serie > 1 ? ' · ' + S.giorni.serie + ' giorni di fila' : ''}</p>
      <h1>La lezione di oggi</h1><p class="piccolo">Dieci, quindici minuti. Fai le tre parti in ordine.</p>
      <div class="passo-oggi"><span class="n">1</span><div><b>Ripasso</b><br>
        <span class="piccolo">${ripasso ? ripasso + (ripasso === 1 ? ' cosa da ripassare' : ' cose da ripassare') : 'Niente da ripassare oggi.'}</span><br>
        ${ripasso ? '<button id="fai-ripasso">Ripassa</button>' : ''}</div></div>
      <div class="passo-oggi"><span class="n">2</span><div><b>Pezzo nuovo</b><br><span class="piccolo">${nuovo}</span><br>
        ${u ? '<button id="fai-nuovo">Comincia</button>' : ''}</div></div>
      <div class="passo-oggi"><span class="n">3</span><div><b>Due minuti a voce</b><br>
        <span class="piccolo">Rispondi a voce alle domande. ${VOCE.puoAscoltare() ? '' : 'Questo telefono non ascolta: scriverai le risposte.'}</span><br>
        <button id="fai-voce" class="vuoto">Parla</button></div></div>`);
    const on = (id, f) => { const x = $(id); if(x) x.onclick = f; };
    on('#fai-ripasso', () => { location.hash = '#ripasso'; });
    on('#fai-nuovo', () => { location.hash = '#passo/' + cur + '/' + p.passo; });
    on('#fai-voce', () => { location.hash = '#parla'; });
  });
}

function avviaRipasso(){
  const carte = daRipassare();
  if(!carte.length){ location.hash = '#oggi'; return schermataOggi(); }
  const voci = carte.map(c => Object.assign({}, c.es, {_carta: c.id}));
  avviaQuiz({voci, titolo: 'Ripasso', modo: 'ripasso',
    dopoRisposta: (e, ok) => { const c = S.ripasso.find(x => x.id === e._carta); if(c) aggiornaCarta(c, ok); salva(); },
    fine: r => {
      segnaGiorno(); salva();
      schermo(`<p class="occhiello">Ripasso fatto</p><h1>${r.giuste} su ${r.totale}</h1>
        <p>${r.errori.length ? 'Quelle sbagliate tornano domani.' : 'Tutto giusto. Le rivedi tra qualche giorno.'}</p>
        <button class="largo" id="torna">Torna a Oggi</button>`);
      $('#torna').onclick = () => { location.hash = '#oggi'; vai(); };
    }});
}

/* Percorso */
function schermataPercorso(){
  menu('percorso');
  return percorso().then(p => {
    const html = p.livelli.map(l => {
      const a1 = l.livello === 'A1';
      const righe = l.unita.map(u => {
        const id = 'a1-' + String(u.numero).padStart(2, '0');
        if(!a1) return `<li class="chiusa"><div class="riga"><span>${u.numero}. ${esc(u.titolo)}</span><span class="stato">in preparazione</span></div>
          <div class="piccolo">${esc(u.grammatica)} · ${esc(u.obiettivo)}</div></li>`;
        const st = superata(id) ? `<span class="stato fatto">superata${S.unita[id].voto != null ? ' · ' + S.unita[id].voto + '%' : ''}</span>`
          : aperta(id) ? `<span class="stato">${progresso(id).passo}/5</span>` : '<span class="stato">chiusa</span>';
        const titolo = aperta(id) ? `<a href="#lezione/${id}">${u.numero}. ${esc(u.titolo)}</a>` : `<span class="chiusa">${u.numero}. ${esc(u.titolo)}</span>`;
        return `<li><div class="riga">${titolo}${st}</div><div class="piccolo">${esc(u.grammatica)}</div></li>`;
      }).join('');
      return `<h2>${esc(l.livello)} <span class="piccolo">${esc(l.titolo)}</span></h2><p class="piccolo">${esc(l.alla_fine)}</p><ul class="elenco">${righe}</ul>`;
    }).join('');
    schermo(`<p class="occhiello">Il percorso</p><h1>Dalle basi alle serie senza sottotitoli.</h1>
      <p class="piccolo">Ogni unità si apre superando l'esame di quella prima (70%).</p>${html}`);
  });
}

/* Lezione: le cinque parti di un'unità */
function schermataLezione(id){
  menu('percorso');
  if(!aperta(id)){ location.hash = '#percorso'; return schermataPercorso(); }
  return unita(id).then(u => {
    const p = progresso(id);
    schermo(`<p class="occhiello">Unità ${u.numero} · ${esc(u.livello)}</p><h1>${esc(u.titolo)}</h1>
      <p>${esc(u.obiettivo)}</p><ul class="elenco">${PASSI.map((x, k) => `<li><div class="riga">
        ${k <= p.passo || p.superata ? `<a href="#passo/${id}/${k}">${k + 1}. ${x.nome}</a>` : `<span class="chiusa">${k + 1}. ${x.nome}</span>`}
        <span class="stato${k < p.passo || p.superata ? ' fatto' : ''}">${k < p.passo || (p.superata && k === 4) ? 'fatto' : k === p.passo ? 'adesso' : ''}</span></div></li>`).join('')}</ul>
      ${p.superata ? `<p class="accento">Esame superato con ${p.voto}%.</p>` : ''}`);
  });
}
function passoFatto(id, k){
  const p = progresso(id);
  if(p.passo === k && k < 4) p.passo = k + 1;
  segnaGiorno(); salva();
}
function apriPasso(id, k){
  menu(null);
  if(!aperta(id) || k > progresso(id).passo && !superata(id)){ location.hash = '#lezione/' + id; return; }
  return unita(id).then(u => {
    const fine = () => { passoFatto(id, k); location.hash = '#lezione/' + id; vai(); };
    const titolo = `Unità ${u.numero} · ${PASSI[k].nome}`;
    if(PASSI[k].id === 'parole'){
      schermo(`<p class="occhiello">${titolo}</p><h1>Le parole di oggi</h1><p class="piccolo">Tocca una parola per sentirla.</p>
        <ul class="elenco">${u.parole.map((w, j) => `<li data-parola="${j}"><div class="parola-en">${esc(w.en)}</div><div class="parola-it">${esc(w.it)}</div>
        <div class="esempio">${esc(w.esempio.en)}</div><div class="piccolo">${esc(w.esempio.it)}</div></li>`).join('')}</ul>
        <button class="largo" id="fatto">Le ho viste: mettile nel ripasso</button>`);
      document.querySelectorAll('[data-parola]').forEach(li => li.onclick = () => { const w = u.parole[+li.dataset.parola]; VOCE.parla(w.en + '. ' + w.esempio.en, 'en'); });
      $('#fatto').onclick = () => { u.parole.forEach(w => aggiungiCarta(cartaParola(w), 'Unità ' + u.numero, 1)); fine(); };
    } else if(PASSI[k].id === 'grammatica'){
      const g = u.grammatica;
      schermo(`<p class="occhiello">${titolo}</p><h1>${esc(g.titolo)}</h1>${g.spiegazione.map(x => `<p>${esc(x)}</p>`).join('')}
        ${g.tabella ? '<table>' + g.tabella.map(r => '<tr>' + r.map(c => `<td>${esc(c)}</td>`).join('') + '</tr>').join('') + '</table>' : ''}
        <h3>Esempi</h3><ul class="elenco">${g.esempi.map((x, j) => `<li data-esempio="${j}"><div>${esc(x.en)}</div><div class="piccolo">${esc(x.it)}</div></li>`).join('')}</ul>
        ${g.attenzione ? `<p class="esito no">${esc(g.attenzione)}</p>` : ''}
        <button class="largo" id="fatto">Ho capito</button>`);
      document.querySelectorAll('[data-esempio]').forEach(li => li.onclick = () => VOCE.parla(g.esempi[+li.dataset.esempio].en, 'en'));
      $('#fatto').onclick = fine;
    } else if(PASSI[k].id === 'ascolto'){
      const d = u.dialogo;
      schermo(`<p class="occhiello">${titolo}</p><h1>${esc(d.titolo)}</h1><p class="piccolo">${esc(d.situazione)}</p>
        <p>Ascolta il dialogo una o due volte, poi rispondi alle domande. Il testo c'è, ma prova prima senza.</p>
        <div class="fila"><button id="ascolta-dialogo">Ascolta</button><button class="vuoto" id="mostra">Mostra il testo</button></div>
        <div id="testo" hidden>${d.battute.map(b => `<p class="battuta"><b>${esc(b.nome)}</b> ${esc(b.en)}<br><span class="piccolo">${esc(b.it)}</span></p>`).join('')}</div>
        <button class="largo" id="domande">Alle domande</button>`);
      $('#ascolta-dialogo').onclick = () => d.battute.reduce((pr, b) => pr.then(() => VOCE.parla(b.en, 'en', b.voce)), Promise.resolve());
      $('#mostra').onclick = () => { $('#testo').hidden = !$('#testo').hidden; };
      $('#domande').onclick = () => { VOCE.ferma(); avviaQuiz({voci: d.domande.map(x => Object.assign({tipo: 'scelta'}, x)), titolo, fine}); };
    } else if(PASSI[k].id === 'esercizi'){
      avviaQuiz({voci: u.esercizi, titolo, fine: r => {
        r.errori.forEach(e => aggiungiCarta(e, 'Unità ' + u.numero));
        passoFatto(id, k);
        schermo(`<p class="occhiello">${titolo}</p><h1>${r.giuste} su ${r.totale}</h1>
          <p>${r.errori.length ? 'Gli errori sono finiti nel ripasso: li rivedi nei prossimi giorni.' : 'Nessun errore.'} Adesso tocca all'esame.</p>
          <button class="largo" id="torna">Avanti</button>`);
        $('#torna').onclick = () => { location.hash = '#lezione/' + id; vai(); };
      }});
    } else {
      schermo(`<p class="occhiello">${titolo}</p><h1>L'esame</h1><p>${u.esame.domande.length} domande. Per passare serve il ${Math.round(u.esame.soglia * 100)}%.
        Se lo passi si apre l'unità dopo.</p><button class="largo" id="via">Comincia</button>`);
      $('#via').onclick = () => avviaQuiz({voci: u.esame.domande, titolo, modo: 'esame', fine: r => chiudiEsame(id, u, r)});
    }
  });
}
function chiudiEsame(id, u, r){
  const p = progresso(id);
  const voto = Math.round(r.quota * 100);
  p.tentativi = (p.tentativi || 0) + 1;
  const passato = r.quota >= (u.esame.soglia || 0.7);
  if(passato){ p.superata = true; p.voto = Math.max(voto, p.voto || 0); }
  r.errori.forEach(e => aggiungiCarta(e, 'Esame unità ' + u.numero));
  segnaGiorno(); salva();
  const dopo = unitaCorrente();
  schermo(`<p class="occhiello">Esame unità ${u.numero}</p><h1 class="${passato ? 'accento' : ''}">${voto}%</h1>
    <p>${passato ? 'Superato. ' + (dopo ? 'Si apre l\'unità dopo.' : 'Hai finito tutto il livello A1.')
      : 'Serve il ' + Math.round((u.esame.soglia || 0.7) * 100) + '%. Gli errori sono nel ripasso: ripassali e riprova quando vuoi.'}</p>
    <button class="largo" id="torna">${passato ? 'Torna a Oggi' : 'Torna all\'unità'}</button>`);
  $('#torna').onclick = () => { location.hash = passato ? '#oggi' : '#lezione/' + id; vai(); };
  return passato;
}

/* ---------- l'insegnante ---------- */
function chiamaInsegnante(modo, testo, storia){
  if(!ACCESSO || !ACCESSO.gettone) return Promise.resolve({codice: 401, dati: {errore: "Per l'insegnante serve l'accesso con mail e password (in «Io»)."}});
  return api('/api/insegnante', {modo, testo, livello: livelloAttuale(), storia: storia || []})
    .catch(() => ({codice: 0, dati: {errore: 'Senza rete l\'insegnante non risponde. Il resto dell\'app funziona.'}}));
}

/* due minuti a voce: con l'insegnante se risponde, se no con le domande dell'unità */
function schermataParla(){
  menu(null);
  schermo(`<p class="occhiello">Due minuti a voce</p><h1>Parliamo.</h1>
    <p>Ti faccio una domanda in inglese, tu rispondi a voce. Dopo due minuti ci fermiamo.</p>
    <div class="ora" id="parla-ora"></div>
    ${VOCE.puoAscoltare() ? '' : '<input id="parla-scritto" placeholder="Scrivi la risposta e premi invio">'}
    <button class="largo" id="parla-via">Comincia</button><button class="largo vuoto" id="parla-stop">Ferma</button>`);
  $('#parla-via').onclick = () => dueMinuti();
  $('#parla-stop').onclick = () => { fermaParla(); location.hash = '#oggi'; };
}
function rispostaUtente(ms){
  if(VOCE.puoAscoltare()) return VOCE.ascolta('en', ms || 10000);
  return new Promise(ok => {
    const i = $('#parla-scritto');
    if(!i) return ok('');
    i.value = ''; i.focus && i.focus();
    i.onkeydown = ev => { if(ev.key === 'Enter'){ i.onkeydown = null; ok(i.value); } };
    setTimeout(() => ok(i.value || ''), ms || 60000);
  });
}
function dì(testo, lingua){
  const o = $('#parla-ora') || $('#macchina-ora');
  if(o) o.textContent = testo;
  return VOCE.parla(testo, lingua);
}
function fermaParla(){ PARLA.attiva = false; VOCE.ferma(); }
function dueMinuti(durata){
  const cur = unitaCorrente() || UNITA_A1[UNITA_A1.length - 1];
  PARLA = {attiva: true, fine: adesso() + (durata || 120000), scambi: 0, conInsegnante: !!(ACCESSO && ACCESSO.gettone)};
  return unita(cur).then(u => {
    const domande = u.parlato || [];
    const storia = [];
    let k = 0;
    const offline = () => {
      if(!PARLA.attiva || adesso() > PARLA.fine || k >= domande.length) return chiudi();
      const d = domande[k++];
      return dì(d.domanda, 'en').then(() => rispostaUtente()).then(t => {
        PARLA.scambi++;
        return dì('For example: ' + d.modello, 'en');
      }).then(offline);
    };
    const conInsegnante = (domanda) => {
      if(!PARLA.attiva || adesso() > PARLA.fine) return chiudi();
      return dì(domanda, 'en').then(() => rispostaUtente()).then(t => {
        t = String(t || '').split('|')[0].trim();
        storia.push({ruolo: 'insegnante', testo: domanda});
        if(!t){
          // silenzio: si ripete una volta, al secondo silenzio ci si ferma
          PARLA.silenzi = (PARLA.silenzi || 0) + 1;
          return PARLA.silenzi >= 2 ? chiudi() : conInsegnante(domanda);
        }
        PARLA.silenzi = 0;
        PARLA.scambi++;
        return chiamaInsegnante('conversa', t, storia).then(r => {
          storia.push({ruolo: 'io', testo: t});
          if(r.codice !== 200){ PARLA.conInsegnante = false; return offline(); }
          return conInsegnante(r.dati.risposta);
        });
      });
    };
    const chiudi = () => {
      const c = PARLA.scambi; PARLA.attiva = false;
      segnaGiorno(); salva();
      return dì(c ? 'Well done. That\'s all for today.' : 'Okay, next time.', 'en').then(() => c);
    };
    const prima = domande[0] ? domande[0].domanda : 'How are you today?';
    if(PARLA.conInsegnante){ k = 1; return conInsegnante(prima); }
    return offline();
  });
}

/* ---------- modalità macchina: un tasto, poi solo voce ---------- */
function schermataMacchina(){
  menu('macchina');
  schermo(`<div class="macchina"><p class="occhiello">Modalità macchina</p>
    <p class="piccolo">Un tocco e poi solo voce. Ti dico una frase in italiano, tu la dici in inglese.
    Per fermarti di' «basta». Per sentirla ancora di' «ripeti».</p>
    <button class="grande" id="macchina-via">${MACCHINA.attiva ? 'Ferma' : 'Parti'}</button>
    <div class="ora" id="macchina-ora"></div></div>`);
  $('#macchina-via').onclick = () => { if(MACCHINA.attiva){ fermaMacchina(); schermataMacchina(); } else avviaMacchina(); };
}
function comandoVoce(s){
  const t = normalizza(String(s || '').split('|')[0]);
  if(/^(basta|busta|stop|enough|finish|the end|fine)$/.test(t)) return 'basta';
  if(/^(ripeti|repeat|again|repeat please|ripeti per favore)$/.test(t)) return 'ripeti';
  if(/^(non lo so|passo|i do not know|pass|next)$/.test(t)) return 'passo';
  return '';
}
function carteMacchina(){
  const fuori = daRipassare(15).map(c => ({it: c.it, en: c.en, accettate: c.es && c.es.risposte && c.es.tipo !== 'completa' ? c.es.risposte : [c.en], carta: c}))
    .filter(x => x.it && x.en && !/___/.test(x.it));
  return fuori;
}
var blocco = null;
function avviaMacchina(){
  MACCHINA = {attiva: true, giuste: 0, totale: 0, registro: []};
  schermataMacchina();
  try{ if(navigator.wakeLock) navigator.wakeLock.request('screen').then(b => { blocco = b; }).catch(() => {}); }catch(e){}
  const cur = unitaCorrente();
  const inizio = () => {
    let carte = carteMacchina();
    if(carte.length < 6 && cur) return unita(cur).then(u => carte.concat(
      u.parole.filter(w => !S.ripasso.some(c => normalizza(c.en) === normalizza(w.en))).slice(0, 8 - carte.length)
        .map(w => ({it: w.it, en: w.en, accettate: [w.en], parola: w})))).catch(() => carte);
    return Promise.resolve(carte);
  };
  return dì('Modalità macchina. Ti dico una frase in italiano, tu dimmela in inglese.', 'it')
    .then(inizio).then(carte => {
      if(!carte.length) return dì('Oggi non c\'è niente da ripassare. Ci sentiamo domani.', 'it');
      return carte.reduce((pr, c) => pr.then(() => (MACCHINA.attiva ? domandaVoce(c) : null)), Promise.resolve())
        .then(() => dì(`Fatto. ${MACCHINA.giuste} giuste su ${MACCHINA.totale}.`, 'it'));
    }).then(() => { segnaGiorno(); salva(); fermaMacchina(true); return MACCHINA; });
}
function domandaVoce(c){
  MACCHINA.totale++;
  let ok = false;
  const chiedi = () => dì(c.it, 'it').then(() => VOCE.ascolta('en', 9000));
  return chiedi().then(s => (comandoVoce(s) === 'ripeti' ? chiedi() : s)).then(s => {
    const cmd = comandoVoce(s);
    if(cmd === 'basta'){ MACCHINA.attiva = false; MACCHINA.totale--; return null; }
    if(cmd !== 'passo' && simileVoce(s, c.accettate)){
      ok = true; MACCHINA.giuste++;
      return dì('Giusto.', 'it').then(() => dì(c.en, 'en'));
    }
    return dì(s && cmd !== 'passo' ? 'Quasi. Si dice:' : 'Si dice:', 'it').then(() => dì(c.en, 'en'))
      .then(() => dì('Ripeti.', 'it')).then(() => VOCE.ascolta('en', 9000))
      .then(s2 => {
        if(comandoVoce(s2) === 'basta'){ MACCHINA.attiva = false; return null; }
        return dì(simileVoce(s2, c.accettate) ? 'Bene.' : 'Ci torniamo domani.', 'it');
      });
  }).then(() => {
    MACCHINA.registro.push({it: c.it, ok});
    if(c.carta) aggiornaCarta(c.carta, ok);
    else if(c.parola && !ok) aggiungiCarta(cartaParola(c.parola), 'Modalità macchina');
    salva();
  });
}
function fermaMacchina(finita){
  MACCHINA.attiva = false;
  if(!finita) VOCE.ferma();
  try{ if(blocco) blocco.release(); }catch(e){}
  blocco = null;
  const b = $('#macchina-via'); if(b) b.textContent = 'Parti';
}

/* ---------- serie e canzoni ---------- */
function schermataSerie(){
  menu('serie');
  schermo(`<p class="occhiello">Serie e canzoni</p><h1>Una frase che non capisci?</h1>
    <p class="piccolo">Incolla solo il pezzo che ti interessa: una battuta, una strofa. L'insegnante te lo spiega parola per parola,
    con lo slang. I testi delle canzoni non sono dentro l'app.</p>
    <label for="frase">La frase</label><textarea id="frase" placeholder="I ain't gonna let you down"></textarea>
    <label for="fonte">Da dove viene (facoltativo)</label><input id="fonte" placeholder="Breaking Bad, stagione 1">
    <button class="largo" id="spiega">Spiegamela</button><div id="spiegazione"></div>
    <h2>Le frasi salvate</h2><ul class="elenco" id="frasi"></ul>`);
  $('#spiega').onclick = () => spiegaFrase($('#frase').value, $('#fonte').value);
  disegnaFrasi();
}
function disegnaFrasi(){
  const ul = $('#frasi');
  if(!ul) return;
  ul.innerHTML = S.frasi.length ? S.frasi.slice().reverse().map(f => `<li data-frase="${f.id}"><div class="parola-en">${esc(f.testo)}</div>
    ${f.fonte ? `<div class="piccolo">${esc(f.fonte)}</div>` : ''}<div class="piccolo" style="white-space:pre-wrap">${esc(f.spiegazione)}</div>
    <div class="fila"><button class="testo" data-modifica="${f.id}">Modifica</button><button class="testo" data-cancella="${f.id}">Cancella</button></div></li>`).join('')
    : '<li class="piccolo">Ancora nessuna.</li>';
  ul.querySelectorAll('[data-cancella]').forEach(b => b.onclick = () => { cancellaFrase(b.dataset.cancella); disegnaFrasi(); });
  ul.querySelectorAll('[data-modifica]').forEach(b => b.onclick = () => {
    const f = S.frasi.find(x => x.id === b.dataset.modifica);
    const li = b.closest('li');
    li.innerHTML = `<textarea data-testo>${esc(f.testo)}</textarea><textarea data-nota>${esc(f.spiegazione)}</textarea>
      <div class="fila"><button data-salva>Salva</button><button class="vuoto" data-annulla>Annulla</button></div>`;
    li.querySelector('[data-salva]').onclick = () => { modificaFrase(f.id, li.querySelector('[data-testo]').value, li.querySelector('[data-nota]').value); disegnaFrasi(); };
    li.querySelector('[data-annulla]').onclick = disegnaFrasi;
  });
}
function spiegaFrase(testo, fonte){
  testo = String(testo || '').trim();
  const box = $('#spiegazione');
  if(!testo){ if(box) box.innerHTML = '<p class="errore">Incolla prima una frase.</p>'; return Promise.resolve(null); }
  if(box) box.innerHTML = '<p class="piccolo">L\'insegnante sta leggendo…</p>';
  return chiamaInsegnante('spiega', testo).then(({codice, dati}) => {
    if(codice !== 200){ if(box) box.innerHTML = `<p class="errore">${esc(dati.errore || 'Non ha risposto.')}</p>`; return null; }
    const f = {id: nuovoId(), testo, fonte: String(fonte || '').trim(), spiegazione: dati.spiegazione || '', parole: dati.parole || [], quando: adesso()};
    if(box){
      box.innerHTML = `<div class="esito" style="white-space:pre-wrap">${esc(f.spiegazione)}</div>
        ${f.parole.length ? '<h3>Parole da ripassare</h3><ul class="elenco">' + f.parole.map((p, j) => `<li><label style="margin:0;color:inherit"><input type="checkbox" data-p="${j}" checked style="width:auto">
          <b style="font-weight:400">${esc(p.en)}</b>, ${esc(p.it)}${p.nota ? ` <span class="piccolo">(${esc(p.nota)})</span>` : ''}</label></li>`).join('') + '</ul>' : ''}
        <button class="largo" id="salva-frase">Salva la frase${f.parole.length ? ' e metti le parole nel ripasso' : ''}</button>`;
      $('#salva-frase').onclick = () => {
        const scelte = Array.from(document.querySelectorAll('[data-p]')).filter(x => x.checked).map(x => f.parole[+x.dataset.p]);
        salvaFrase(f, scelte);
        box.innerHTML = '<p class="accento">Salvata.' + (scelte.length ? ' ' + scelte.length + ' parole nel ripasso.' : '') + '</p>';
        disegnaFrasi();
      };
    }
    return f;
  });
}
function salvaFrase(f, parole){
  S.frasi.push(f);
  (parole || []).forEach(p => {
    const c = aggiungiCarta({tipo: 'traduci', it: p.it, risposte: [p.en], parola: true}, 'Serie e canzoni');
    c.prossima = inizioGiorno(adesso()) + GIORNO;
  });
  salva();
}
function modificaFrase(id, testo, spiegazione){
  const f = S.frasi.find(x => x.id === id);
  if(!f || !String(testo).trim()) return false;
  f.testo = String(testo).trim(); f.spiegazione = String(spiegazione || '');
  salva(); return true;
}
function cancellaFrase(id){ S.frasi = S.frasi.filter(x => x.id !== id); salva(); }

/* ---------- chiedi all'insegnante ---------- */
function schermataChiedi(){
  menu('chiedi');
  schermo(`<p class="occhiello">Chiedi all'insegnante</p><h1>Un dubbio?</h1>
    <p class="piccolo">Una parola, una regola, come si dice una cosa. Risponde a livello ${esc(livelloAttuale())}: più vai avanti, più parla inglese.</p>
    <div id="chat">${S.chiedi.map(m => `<div class="messaggio ${m.ruolo === 'io' ? 'io' : ''}">${esc(m.testo)}</div>`).join('')}</div>
    <textarea id="domanda" placeholder="Che differenza c'è tra make e do?"></textarea>
    <button class="largo" id="manda">Chiedi</button>
    ${S.chiedi.length ? '<button class="testo" id="pulisci">Cancella la conversazione</button>' : ''}<p class="piccolo" id="budget"></p>`);
  $('#manda').onclick = () => chiedi($('#domanda').value);
  const p = $('#pulisci'); if(p) p.onclick = () => { S.chiedi = []; salva(); schermataChiedi(); };
}
function chiedi(testo){
  testo = String(testo || '').trim();
  if(!testo) return Promise.resolve(null);
  const storia = S.chiedi.slice(-12);
  return chiamaInsegnante('chiedi', testo, storia).then(({codice, dati}) => {
    if(codice !== 200){ const b = $('#budget'); if(b) b.textContent = dati.errore || 'Non ha risposto.'; return null; }
    S.chiedi.push({ruolo: 'io', testo}, {ruolo: 'insegnante', testo: dati.risposta});
    S.chiedi = S.chiedi.slice(-40);
    salva();
    if(location.hash === '#chiedi') schermataChiedi();
    return dati.risposta;
  });
}

/* ---------- Io: il mio ripasso, esporta/importa, accesso ---------- */
function schermataIo(){
  menu('io');
  const chi = ACCESSO && ACCESSO.gettone ? 'Entrato come ' + esc(ACCESSO.mail) + (ACCESSO.salvato ? ' · salvato sul server' : '')
    : ACCESSO && ACCESSO.mail ? 'Devi rientrare per salvare sul server.' : 'Solo su questo telefono.';
  schermo(`<p class="occhiello">Io</p><h1>Le tue cose</h1><p class="piccolo">${chi}</p>
    <p>Livello: <b>${esc(livelloAttuale())}</b> · unità superate: ${UNITA_A1.filter(superata).length} di 12 · ${S.ripasso.length} cose nel ripasso</p>
    <h2>Il mio ripasso</h2>
    <label for="nuova-en">Aggiungi: in inglese</label><input id="nuova-en"><label for="nuova-it">in italiano</label><input id="nuova-it">
    <p><button class="vuoto" id="aggiungi">Aggiungi al ripasso</button></p>
    <input id="cerca" placeholder="Cerca nel ripasso"><ul class="elenco" id="carte"></ul>
    <h2>I tuoi progressi</h2><p class="piccolo">Una copia su file, da tenere o da portare su un altro telefono.</p>
    <div class="fila"><button class="vuoto" id="esporta">Scarica i progressi</button>
    <label class="bottone vuoto" style="margin:0;background:transparent;color:var(--salvia);border:1px solid var(--salvia)">Carica da file<input type="file" id="importa" accept="application/json,.json" hidden></label></div>
    <p class="piccolo" id="esito-file"></p>
    <h2>Altro</h2><div class="fila"><button class="vuoto" id="rifai-test">Rifai il test di livello</button>
    ${ACCESSO && ACCESSO.mail ? '<button class="vuoto" id="esci">Esci</button>' : '<button class="vuoto" id="accedi">Entra con la mail</button>'}</div>
    <p class="piccolo">Versione ${VERSIONE_APP}</p>`);
  const disegna = () => {
    const f = normalizza($('#cerca').value);
    const carte = S.ripasso.filter(c => !f || normalizza(c.en + ' ' + c.it).includes(f));
    $('#carte').innerHTML = carte.slice(0, 200).map(c => `<li data-carta="${c.id}"><div class="riga"><span>${esc(c.en)}</span>
      <span class="stato">${c.prossima <= adesso() ? 'da ripassare' : 'tra ' + Math.max(1, Math.ceil((c.prossima - adesso()) / GIORNO)) + ' g'}</span></div>
      <div class="piccolo">${esc(c.it)}${c.origine ? ' · ' + esc(c.origine) : ''}</div>
      <div class="fila"><button class="testo" data-modifica="${c.id}">Modifica</button><button class="testo" data-cancella="${c.id}">Cancella</button></div></li>`).join('')
      || '<li class="piccolo">Niente.</li>';
    document.querySelectorAll('#carte [data-cancella]').forEach(b => b.onclick = () => { cancellaCarta(b.dataset.cancella); disegna(); });
    document.querySelectorAll('#carte [data-modifica]').forEach(b => b.onclick = () => {
      const c = S.ripasso.find(x => x.id === b.dataset.modifica), li = b.closest('li');
      li.innerHTML = `<input data-en value="${esc(c.en)}"><input data-it value="${esc(c.it)}">
        <div class="fila"><button data-salva>Salva</button><button class="vuoto" data-annulla>Annulla</button></div>`;
      li.querySelector('[data-salva]').onclick = () => { modificaCarta(c.id, li.querySelector('[data-en]').value, li.querySelector('[data-it]').value); disegna(); };
      li.querySelector('[data-annulla]').onclick = disegna;
    });
  };
  $('#cerca').oninput = disegna;
  disegna();
  $('#aggiungi').onclick = () => { if(nuovaCarta($('#nuova-en').value, $('#nuova-it').value)){ $('#nuova-en').value = ''; $('#nuova-it').value = ''; disegna(); } };
  $('#esporta').onclick = esporta;
  $('#importa').onchange = ev => {
    const file = ev.target.files && ev.target.files[0];
    if(!file) return;
    const r = new FileReader();
    r.onload = () => { const no = importaTesto(String(r.result)); $('#esito-file').textContent = no || 'Progressi caricati.'; if(!no) schermataIo(); };
    r.readAsText(file);
  };
  $('#rifai-test').onclick = () => { location.hash = '#test'; };
  const e = $('#esci'); if(e) e.onclick = esci;
  const a = $('#accedi'); if(a) a.onclick = () => { ACCESSO = null; schermataAccesso(); };
}
function testoEsportazione(){
  return JSON.stringify({app: 'inglese-andrea', formato: 1, esportato: new Date(adesso()).toISOString(), progressi: S}, null, 1);
}
function esporta(){
  const blob = new Blob([testoEsportazione()], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'inglese-progressi-' + chiaveGiorno(adesso()) + '.json';
  document.body.appendChild(a); a.click(); a.remove();
}
function importaTesto(t){
  let d;
  try{ d = JSON.parse(t); }catch(e){ return 'Questo file non si legge.'; }
  if(!d || d.app !== 'inglese-andrea' || !d.progressi || typeof d.progressi !== 'object' || !Array.isArray(d.progressi.ripasso))
    return 'Questo non è un file dei progressi di questa app.';
  adotta(d.progressi);
  salva();
  if(ACCESSO && ACCESSO.gettone) sincronizza(true);
  return '';
}

/* ---------- app installabile e versione nuova ---------- */
function preparaInstallazione(){
  if(typeof navigator === 'undefined' || !('serviceWorker' in navigator) || !/^https?:$/.test(location.protocol)) return;
  navigator.serviceWorker.register('sw.js').catch(() => {});
  navigator.serviceWorker.addEventListener('message', e => { if(e.data && e.data.inglese === 'versione-nuova') proponiAggiornamento(); });
}
/* mai ricaricare da soli: uno può essere in macchina a metà ripasso */
function proponiAggiornamento(){
  if($('#barra-aggiorna')) return;
  const b = document.createElement('div');
  b.className = 'aggiorna'; b.id = 'barra-aggiorna';
  b.innerHTML = '<p style="margin:0 0 8px">C\'è una versione nuova dell\'app.</p><div class="fila"><button id="fai-aggiornamento">Aggiorna adesso</button>' +
    '<button class="vuoto" id="dopo-aggiornamento">Più tardi</button></div>';
  document.body.appendChild(b);
  $('#fai-aggiornamento').onclick = () => location.reload();
  $('#dopo-aggiornamento').onclick = () => b.remove();
}

function avvia(){
  caricaStato();
  window.addEventListener('hashchange', vai);
  preparaInstallazione();
  if(ACCESSO && ACCESSO.gettone) sincronizza();
  vai();
}
