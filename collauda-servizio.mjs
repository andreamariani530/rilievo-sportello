// Collaudo del servizio con un WhatsApp finto e un Centralino finto: nessuna rete, nessun messaggio vero.
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomBytes } from 'node:crypto'
import { BufferJSON, nuoveCreds } from './motore.js'
import { creaServizio, cifra, decifra } from './servizio.js'

let ok = 0, ko = 0
const prova = (nome, vero) => { if (vero) ok++; else { ko++; console.log('  NO   ', nome) } }
const dorme = (ms) => new Promise((r) => setTimeout(r, ms))
const finche = async (f, ms = 2000) => { const t = Date.now(); while (Date.now() - t < ms) { if (f()) return true; await dorme(10) } return false }
const CHIAVE = 'x'.repeat(40), CIFRA = randomBytes(32)

function ambiente(opz = {}) {
	const c = { eventi: [], stati: [], file: [], copie: [], rotto: false, gen: 0, dati: null, ...opz.centralino }
	const centralino = {
		apriSessione: async () => { if (c.rotto) throw new Error('giu'); c.gen += 1; return { gen: c.gen, dati: c.dati } },
		scriviSessione: async (g, d) => { if (c.rotto) return 'errore'; if (g < c.gen) return 'superata'; c.dati = d; c.copie.push(d); return 'ok' },
		mandaStato: async (s) => { c.stati.push(s) },
		mandaEventi: async (e) => { if (c.rotto) throw new Error('giu'); c.eventi.push(...e) },
		mandaFile: async (f) => { if (c.rotto) throw new Error('giu'); c.file.push(f) }
	}
	const m = { aperti: 0, mandati: [], avvisa: null, sess: null, fallisci: false, chiesti: 0, usciti: 0 }
	const creaMotore = ({ sessione, avvisa }) => {
		m.avvisa = avvisa; m.sess = sessione
		return {
			apri: async ({ qr } = {}) => { m.aperti += 1; if (qr) setTimeout(() => qr('QRDATI'), 1); else if (opz.apreSubito !== false) setTimeout(() => avvisa('aperto', {}), 1) },
			chiedi_codice: async () => { m.chiesti += 1; return 'ABCD1234' },
			manda: async (j, t) => { if (m.fallisci) throw new Error('no'); m.mandati.push({ j, t, q: Date.now() }); return 'ID' + m.mandati.length },
			esci: async () => { m.usciti += 1 },
			chiudi: () => {}
		}
	}
	const cartella = mkdtempSync(join(tmpdir(), 'wa-prova-'))
	const s = creaServizio({ chiave: CHIAVE, chiaveCifra: CIFRA, cartella, numero: '390000000', ...opz.servizio, centralino, creaMotore, nuoveCreds, BufferJSON, qrSvg: () => '<svg/>', ritardoCopia: 5, pausaInvio: opz.pausa ?? 0, tetto: opz.tetto ?? 3, tentativiOra: opz.tentativi ?? 5, attesa: opz.attesa ?? (() => 30), attesaCoda: () => 10, attesaAvvio: () => 10 })
	const chiama = (metodo, percorso, corpo, k = CHIAVE) => s.gestisci(metodo, percorso, async () => corpo || {}, k)
	return { c, m, s, chiama, cartella }
}
const collegato = async (a) => {
	await a.s.avvia()
	await a.chiama('POST', '/collega', {})
	a.m.sess.creds.registered = true
	a.m.sess.creds.account = {}
	a.m.avvisa('aperto', {})
}

console.log('Porte e chiave')
{
	const a = ambiente(); await a.s.avvia()
	const [c, d] = await a.chiama('GET', '/salute', null, '')
	prova('salute senza chiave e senza dati', c === 200 && JSON.stringify(d) === '{"vivo":true}')
	for (const [m, p] of [['GET', '/stato'], ['POST', '/invia'], ['POST', '/collega'], ['POST', '/scollega'], ['POST', '/prova-via']]) {
		prova('senza chiave rifiuta ' + p, (await a.chiama(m, p, {}, ''))[0] === 401)
		prova('chiave sbagliata rifiuta ' + p, (await a.chiama(m, p, {}, 'y'.repeat(40)))[0] === 401)
	}
	prova('da scollegato non si connette da solo', a.m.aperti === 0 && a.s.pubblico().stato === 'da_collegare')
}

console.log('Collegamento col codice')
{
	const a = ambiente(); await a.s.avvia()
	const [c, d] = await a.chiama('POST', '/collega', {})
	prova('il tocco da il codice di 8 caratteri', c === 200 && d.codice === 'ABCD1234' && d.scade > Date.now())
	prova('lo stato pubblico non contiene il codice', !JSON.stringify(a.s.pubblico()).includes('ABCD1234'))
	const [, d2] = await a.chiama('POST', '/collega', {})
	prova('secondo tocco, stesso codice ancora valido', d2.codice === 'ABCD1234' && a.m.chiesti === 1)
	a.m.sess.creds.registered = true
	a.m.sess.creds.account = {}
	a.m.avvisa('chiuso', { codice: 515 })
	await finche(() => a.s.pubblico().stato === 'collegato')
	prova('dopo l\'abbinamento si riconnette una volta e risulta collegato', a.s.pubblico().stato === 'collegato' && a.s.pubblico().collegato_dal)
	const [, q] = await a.chiama('POST', '/collega', { modo: 'qr' })
	prova('il QR non riapre niente se e\' gia\' collegato', q.gia === true)
	const r = ambiente(); await r.s.avvia()
	const [, dq] = await r.chiama('POST', '/collega', { modo: 'qr' })
	prova('riserva col QR', dq.qr_svg === '<svg/>' && r.m.chiesti === 0)
	const l = ambiente({ tentativi: 2 }); await l.s.avvia()
	for (let i = 0; i < 2; i++) { await l.chiama('POST', '/collega', {}); l.m.avvisa('chiuso', { codice: 408 }); await dorme(5) }
	prova('dopo il tetto di prove in un\'ora si ferma', (await l.chiama('POST', '/collega', {}))[0] === 429)
	const f = ambiente(); await f.s.avvia(); await f.chiama('POST', '/collega', {})
	const aperti = f.m.aperti
	f.m.avvisa('chiuso', { codice: 401 })
	await dorme(50)
	prova('collegamento rifiutato: dice perche\' e non riprova', f.s.pubblico().stato === 'da_collegare' && /rifiutato/.test(f.s.pubblico().motivo) && f.m.aperti === aperti)
}

console.log('Invio')
{
	const a = ambiente({ tetto: 3 }); await a.s.avvia()
	prova('scollegato: niente invio', (await a.chiama('POST', '/invia', { a: '39333', testo: 'x', cid: 'z', origine: 'app' }))[0] === 409)
	await collegato(a)
	prova('senza tocco rifiutato', (await a.chiama('POST', '/invia', { a: '39333', testo: 'x', cid: 'a' }))[0] === 403 && a.m.mandati.length === 0)
	prova('origine inventata rifiutata', (await a.chiama('POST', '/invia', { a: '39333', testo: 'x', cid: 'a', origine: 'braccio' }))[0] === 403)
	prova('senza riferimento rifiutato', (await a.chiama('POST', '/invia', { a: '39333', testo: 'x', origine: 'app' }))[0] === 400)
	const [c1, d1] = await a.chiama('POST', '/invia', { a: '39333', testo: 'ciao', cid: 'c1', origine: 'app' })
	prova('col tocco parte', c1 === 200 && d1.id === 'ID1' && a.m.mandati[0].j === '39333@s.whatsapp.net' && a.m.mandati[0].t === 'ciao')
	const [c2, d2] = await a.chiama('POST', '/invia', { a: '39333', testo: 'ciao', cid: 'c1', origine: 'app' })
	prova('stesso riferimento non parte due volte', c2 === 200 && d2.gia && a.m.mandati.length === 1)
	a.m.fallisci = true
	prova('se WhatsApp rifiuta: errore e non conta', (await a.chiama('POST', '/invia', { a: '39333', testo: 'y', cid: 'c2', origine: 'app' }))[0] === 502 && a.s.pubblico().oggi === 1)
	a.m.fallisci = false
	prova('poi si puo\' riprovare', (await a.chiama('POST', '/invia', { a: '39333', testo: 'y', cid: 'c2', origine: 'app' }))[0] === 200)
	await a.chiama('POST', '/invia', { a: '39334', testo: 'z', cid: 'c3', origine: 'voce' })
	const [c4, d4] = await a.chiama('POST', '/invia', { a: '39335', testo: 'w', cid: 'c4', origine: 'decisione' })
	prova('tetto del giorno', c4 === 429 && /tetto/.test(d4.errore) && a.m.mandati.length === 3)
	const p = ambiente({ pausa: 120, tetto: 9 }); await collegato(p)
	await Promise.all([p.chiama('POST', '/invia', { a: '1', testo: 'a', cid: '1', origine: 'app' }), p.chiama('POST', '/invia', { a: '2', testo: 'b', cid: '2', origine: 'app' })])
	prova('pausa fra un invio e l\'altro', p.m.mandati[1].q - p.m.mandati[0].q >= 110)
	prova('la risposta automatica ha la sua origine', (await p.chiama('POST', '/invia', { a: '39333', testo: 'auto', cid: 'auto1', origine: 'jarvis' }))[0] === 200)
	const q = ambiente({ pausa: 80, tetto: 9 }); await collegato(q)
	const tre = await Promise.all(['1', '2', '3'].map((x) => q.chiama('POST', '/invia', { a: x, testo: x, cid: x, origine: 'app' })))
	prova('mai a raffica: il terzo insieme viene rifiutato', tre[2][0] === 429 && q.m.mandati.length === 2)
}

console.log('Arrivi')
{
	const a = ambiente(); await collegato(a)
	const voce = { id: 'M1', dir: 'in', from: '39333', type: 'text', text: { body: 'SEGRETO' }, timestamp: 1 }
	a.m.avvisa('messaggio', { voce }); a.m.avvisa('messaggio', { voce })
	await finche(() => a.c.eventi.length >= 1); await dorme(30)
	prova('arrivo inoltrato una volta sola', a.c.eventi.length === 1 && a.c.eventi[0].voce.id === 'M1')
	a.c.rotto = true
	a.m.avvisa('messaggio', { voce: { ...voce, id: 'M2' } })
	await dorme(50)
	prova('Rilievo giu\': resta in coda', a.s.pubblico().in_coda === 1)
	a.c.rotto = false
	await finche(() => a.c.eventi.length === 2)
	prova('Rilievo su: riparte da solo', a.c.eventi.length === 2 && a.s.pubblico().in_coda === 0)
	a.m.avvisa('messaggio', { voce: { ...voce, id: 'M3', dir: 'out' } })
	await finche(() => a.c.eventi.length === 3)
	prova('quello scritto dal telefono arriva come uscita', a.c.eventi[2].voce.dir === 'out' && a.s.pubblico().ultimo_telefono)
	a.m.avvisa('file', { voce: { id: 'F1', from: '39333', type: 'image', image: { mime_type: 'image/jpeg' } }, dati: Buffer.from('xx') })
	await finche(() => a.c.file.length === 1)
	prova('il file arriva', a.c.file[0]?.id === 'F1')
	a.m.avvisa('file', { voce: { id: 'F2', from: '39333', type: 'video', video: {} }, troppo: true })
	await finche(() => a.c.eventi.length === 4)
	prova('file troppo grosso: lo dice', a.c.eventi[3].genere === 'file-assente' && a.c.eventi[3].stato === 'troppo grande')
	a.m.avvisa('stato', { id: 'ID1', status: 'read', recipient_id: '39333' })
	await finche(() => a.c.eventi.length === 5)
	prova('la spunta dell\'altra persona arriva', a.c.eventi[4].status === 'read')
}

console.log('Cadute')
{
	const a = ambiente({ attesa: () => 20 }); await collegato(a)
	const aperti = a.m.aperti
	a.m.avvisa('chiuso', { codice: 428 })
	prova('caduta: dice riprovo', a.s.pubblico().stato === 'riprovo')
	await finche(() => a.s.pubblico().stato === 'collegato')
	prova('e si riconnette', a.m.aperti === aperti + 1)
	const tempi = []
	const b = ambiente({ apreSubito: false, attesa: (n) => { tempi.push(n); return 5 } }); await collegato(b)
	for (let i = 0; i < 3; i++) { b.m.avvisa('chiuso', { codice: 408 }); await dorme(15) }
	prova('attesa crescente', tempi.join() === '0,1,2')
	const l = ambiente(); await collegato(l); const ap = l.m.aperti
	l.m.avvisa('chiuso', { codice: 401 }); await dorme(40)
	prova('scollegato da WhatsApp: smette e lo dice', l.s.pubblico().stato === 'scollegato' && l.m.aperti === ap && l.c.stati.at(-1).stato === 'scollegato')
	prova('e la sessione vecchia e\' tolta', !l.s._v.creds)
	const f = ambiente(); await collegato(f); const af = f.m.aperti
	f.m.avvisa('chiuso', { codice: 403 }); await dorme(40)
	prova('bloccato: smette e lo dice', f.s.pubblico().stato === 'bloccato' && f.m.aperti === af && f.c.stati.at(-1).stato === 'bloccato')
	prova('bloccato: niente invio', (await f.chiama('POST', '/invia', { a: '1', testo: 'x', cid: 'x', origine: 'app' }))[0] === 409)
	const r = ambiente(); await collegato(r); const ar = r.m.aperti
	r.m.avvisa('chiuso', { codice: 440 }); await dorme(40)
	prova('sostituito: non si riprende il posto', r.s.pubblico().stato === 'sostituito' && r.m.aperti === ar)
	const u = ambiente(); await collegato(u)
	const [cu] = await u.chiama('POST', '/scollega', {})
	prova('scollega col tocco', cu === 200 && u.m.usciti === 1 && u.s.pubblico().stato === 'da_collegare')
}

console.log('La sessione sopravvive al riavvio')
{
	const a = ambiente(); await collegato(a)
	a.m.sess.chiavi['session-1'] = { numero: Buffer.from([1, 2, 3]) }
	a.m.sess.cambiata()
	await a.s._salva()
	await a.chiama('POST', '/invia', { a: '39333', testo: 'x', cid: 'r', origine: 'app' })
	await finche(() => a.c.dati !== null); await dorme(20); await a.s.fine()
	prova('in chiaro sul disco di Rilievo non c\'e\' niente', !a.c.dati.toString('latin1').includes('session-1') && !a.c.dati.toString('latin1').includes('noiseKey'))
	const dentro = JSON.parse(decifra(CIFRA, a.c.dati).toString(), BufferJSON.reviver)
	prova('si apre con la chiave giusta', dentro.creds.registered === true)
	let rotta = false
	try { decifra(randomBytes(32), a.c.dati) } catch { rotta = true }
	prova('con un\'altra chiave non si apre', rotta)
	const b = ambiente({ centralino: { dati: a.c.dati, gen: a.c.gen } }); await b.s.avvia()
	await finche(() => b.s.pubblico().stato === 'collegato')
	prova('riavvio: non chiede di ricollegare', b.s.pubblico().stato === 'collegato' && b.m.chiesti === 0 && b.s._v.creds.registered)
	prova('riavvio: le chiavi sono integre', Buffer.isBuffer(b.s._v.chiavi['session-1'].numero) && b.s._v.chiavi['session-1'].numero[2] === 3)
	prova('riavvio: il conto del giorno resta', b.s.pubblico().oggi === 1)
	b.c.gen += 1
	b.m.sess.cambiata()
	await dorme(40)
	prova('copia vecchia dopo un nuovo avvio: si ferma da sola', b.s.pubblico().stato === 'sostituito')
	const v = ambiente({ centralino: { rotto: true } })
	const av = v.s.avvia(); await dorme(30)
	prova('Centralino giu\' all\'avvio: aspetta, non parte vuoto', v.s.pubblico().stato === 'attendo_centralino')
	v.c.rotto = false; await av
	prova('poi riparte', v.s.pubblico().stato === 'da_collegare')
	const w = ambiente({ centralino: { dati: cifra(randomBytes(32), Buffer.from('{}')) } }); await w.s.avvia()
	prova('chiave sbagliata: non sovrascrive la copia', w.s.pubblico().stato === 'errore_chiave' && w.c.copie.length === 0)
	const e = ambiente(); await e.s.avvia()
	await e.chiama('POST', '/collega', {}); await dorme(10)
	prova('sessione vuota: coerente dopo il riavvio', e.c.gen === 1)
	const nuovo = ambiente({ centralino: { dati: e.c.dati, gen: e.c.gen } }); await nuovo.s.avvia()
	prova('collegamento a meta\' non riparte da solo', nuovo.m.aperti === 0 && nuovo.s.pubblico().stato === 'da_collegare')
}

console.log('Il telefono rifiuta il codice')
{
	const a = ambiente(); await a.s.avvia()
	const [, primo] = await a.chiama('POST', '/collega', {})
	prova('mostra il codice', primo.codice === 'ABCD1234' && a.m.chiesti === 1)
	a.m.sess.creds.registered = true // il telefono ha risposto, ma pair-success non arriva
	prova('non risulta collegato', a.s.pubblico().registrato === false && a.s.pubblico().stato === 'in_collegamento')
	a.s._v.creds = null // come dopo la scadenza del tempo
	const [, secondo] = await a.chiama('POST', '/collega', {})
	prova('riottiene un codice, non riprende una sessione a meta\'', !secondo.riconnetto)
	const b = ambiente({ servizio: { durataCodice: 20 } }); await b.s.avvia()
	await b.chiama('POST', '/collega', {})
	b.m.sess.creds.registered = true
	await dorme(60)
	prova('allo scadere torna da collegare, con un motivo', b.s.pubblico().stato === 'da_collegare' && /nuovo/.test(b.s.pubblico().motivo))
	const [, terzo] = await b.chiama('POST', '/collega', {})
	prova('dopo la scadenza da\' un codice nuovo', terzo.codice === 'ABCD1234' && b.m.chiesti === 2)
}

console.log(`\n${ok} passati, ${ko} falliti`)
process.exit(ko ? 1 : 0)
