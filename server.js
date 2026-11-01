// whatsapp-jarvis: Jarvis come dispositivo collegato del WhatsApp Business di Andrea.
// Non risponde mai da solo: legge, inoltra al Centralino, e manda solo cio' che il Centralino chiede dopo un tocco.
// Nei log non finiscono mai testi, numeri, codici o chiavi.
import { createServer } from 'node:http'
import qrcode from 'qrcode-generator'
import { BufferJSON, creaMotore, nuoveCreds } from './motore.js'
import { creaServizio } from './servizio.js'

const e = process.env
const CHIAVE = (e.JARVIS_CHIAVE || '').trim()
const CIFRA = (e.WA_CIFRA || '').trim()
const BASE = (e.CENTRALINO_URL || 'https://rilievoapp.it').replace(/\/$/, '')
if (CHIAVE.length < 32 || !/^[0-9a-f]{64}$/.test(CIFRA)) {
	console.error('Mancano JARVIS_CHIAVE o WA_CIFRA.')
	process.exit(1)
}

const qrSvg = (testo) => { const q = qrcode(0, 'L'); q.addData(testo); q.make(); return q.createSvgTag({ cellSize: 6, margin: 12, scalable: true }) }

async function chiama(percorso, opz = {}, ms = 10000) {
	return fetch(BASE + '/centralino/wa-servizio/' + percorso, {
		...opz, headers: { 'X-WA-Chiave': CHIAVE, ...(opz.headers || {}) }, signal: AbortSignal.timeout(ms)
	})
}

const jsonPost = (percorso, corpo, ms) => chiama(percorso, { method: 'POST', body: JSON.stringify(corpo), headers: { 'Content-Type': 'application/json' } }, ms)

const centralino = {
	apriSessione: async () => {
		const r = await jsonPost('sessione/apri', {})
		if (!r.ok) throw new Error('apri ' + r.status)
		const d = await r.json()
		return { gen: d.gen, dati: d.dati ? Buffer.from(d.dati, 'base64') : null }
	},
	scriviSessione: async (gen, dati) => {
		try {
			const r = await chiama('sessione?gen=' + gen, { method: 'POST', body: dati, headers: { 'Content-Type': 'application/octet-stream' } }, 30000)
			return r.status === 409 ? 'superata' : (r.ok ? 'ok' : 'errore')
		} catch { return 'errore' }
	},
	mandaStato: async (stato) => { await jsonPost('stato', stato) },
	mandaEventi: async (eventi) => {
		const r = await jsonPost('arrivi', { eventi }, 20000)
		if (!r.ok) throw new Error('arrivi ' + r.status)
	},
	mandaFile: async (f) => {
		const q = new URLSearchParams({ id: f.id, wa: f.wa, mime: f.mime })
		const r = await chiama('file?' + q, { method: 'POST', body: f.dati, headers: { 'Content-Type': 'application/octet-stream' } }, 60000)
		if (!r.ok && r.status < 500) return
		if (!r.ok) throw new Error('file ' + r.status)
	}
}

const s = creaServizio({
	chiave: CHIAVE, chiaveCifra: Buffer.from(CIFRA, 'hex'), cartella: e.WA_CARTELLA || '/tmp/whatsapp-jarvis', numero: (e.WA_NUMERO || '').replace(/\D/g, ''),
	tetto: Number(e.WA_TETTO_GIORNO) || 30, pausaInvio: Number(e.WA_PAUSA_MS) || 6000,
	centralino, creaMotore, nuoveCreds, BufferJSON, qrSvg
})

function corpo(req) {
	return new Promise((risolvi, rifiuta) => {
		const pezzi = []
		let tot = 0
		req.on('data', (p) => { tot += p.length; if (tot > 256 * 1024) { rifiuta(new Error('troppo')); req.destroy() } else pezzi.push(p) })
		req.on('end', () => { try { risolvi(pezzi.length ? JSON.parse(Buffer.concat(pezzi).toString()) : {}) } catch { risolvi({}) } })
		req.on('error', rifiuta)
	})
}

const server = createServer(async (req, res) => {
	let codice = 500
	let d = { errore: 'Errore del servizio.' }
	try {
		const u = new URL(req.url, 'http://x')
		;[codice, d] = await s.gestisci(req.method, u.pathname, () => corpo(req), req.headers['x-wa-chiave'])
	} catch (err) {
		console.log('richiesta fallita:', err?.name || 'errore')
	}
	res.writeHead(codice, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
	res.end(JSON.stringify(d))
})

server.listen(Number(e.PORT) || 10000, () => console.log('whatsapp-jarvis in ascolto'))
s.avvia().catch((err) => console.log('avvio fallito:', err?.name || 'errore'))

for (const segnale of ['SIGTERM', 'SIGINT']) {
	process.on(segnale, async () => {
		console.log('chiusura')
		const t = setTimeout(() => process.exit(0), 20000)
		await s.fine().catch(() => {})
		clearTimeout(t)
		process.exit(0)
	})
}
process.on('unhandledRejection', (err) => console.log('errore non gestito:', err?.name || 'errore'))
process.on('uncaughtException', (err) => console.log('eccezione:', err?.name || 'errore'))
