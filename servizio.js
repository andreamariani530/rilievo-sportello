// Il servizio senza la rete: stato, regole, code, sessione cifrata. Il motore e il Centralino gli si passano da fuori.
import { createCipheriv, createDecipheriv, createHash, randomBytes, timingSafeEqual } from 'node:crypto'
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { gunzipSync, gzipSync } from 'node:zlib'

const ORIGINI = new Set(['app', 'decisione', 'voce', 'jarvis'])
const USCITE = { LOGGED_OUT: 401, BLOCCATO: 403, SOSTITUITO: 440, RIAVVIA: 515 }
const MAX_CODA = 300
const uguale = (a, b) => { const x = createHash('sha256').update(String(a)).digest(); const y = createHash('sha256').update(String(b)).digest(); return timingSafeEqual(x, y) }
const adesso = () => new Date().toISOString()
const pausa = (ms) => new Promise((r) => setTimeout(r, ms))
const giornoRoma = () => new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Rome' })

export function cifra(chiave, chiaro) {
	const iv = randomBytes(12)
	const c = createCipheriv('aes-256-gcm', chiave, iv)
	const corpo = Buffer.concat([c.update(gzipSync(chiaro)), c.final()])
	return Buffer.concat([iv, c.getAuthTag(), corpo])
}

export function decifra(chiave, dati) {
	const d = createDecipheriv('aes-256-gcm', chiave, dati.subarray(0, 12))
	d.setAuthTag(dati.subarray(12, 28))
	return gunzipSync(Buffer.concat([d.update(dati.subarray(28)), d.final()]))
}

export function creaServizio(o) {
	const { chiave, chiaveCifra, cartella, centralino, creaMotore, nuoveCreds, BufferJSON, qrSvg } = o
	const tetto = o.tetto ?? 30
	const pausaInvio = o.pausaInvio ?? 6000
	const tentativiOra = o.tentativiOra ?? 5
	const attesa = o.attesa ?? ((n) => Math.min(15 * 60000, 5000 * 2 ** n) + Math.random() * 2000)
	const ritardoCopia = o.ritardoCopia ?? 3000

	mkdirSync(cartella, { recursive: true, mode: 0o700 })
	const fileLocale = join(cartella, 'sessione.bin')

	let gen = 0
	let n = 0
	let fermo = false            // un'altra copia del servizio ha preso il posto di questa
	let motore = null
	let cambiata = false
	let timerCopia = null
	let timerRiprova = null
	let tentativi = 0
	let codiceAttivo = null
	let timerCodice = null
	const durataCodice = o.durataCodice ?? 170000
	let qrAttivo = null
	let inviiInCorso = 0
	let codaInvii = Promise.resolve()
	let ultimoInvio = 0
	const tentativiCollega = []
	const inviatiCid = new Map()
	const visti = new Set()
	const codaFile = []
	let codaEventi = []
	let invioCoda = false

	const v = {
		creds: null, chiavi: {}, rubrica: {},
		stato: 'avvio', motivo: '', da_quando: adesso(), collegato_dal: '', ultimo_messaggio: '', ultimo_telefono: '', ultimo_aperto: '',
		oggi: { giorno: '', n: 0 }
	}

	const sessione = {
		get creds() { return v.creds },
		get chiavi() { return v.chiavi },
		cambiata: () => segna(),
		ricorda: (wa, jid) => { if (v.rubrica[wa] !== jid) { v.rubrica[wa] = jid; segna() } }
	}

	function serializza() {
		return Buffer.from(JSON.stringify({ n, creds: v.creds, chiavi: v.chiavi, rubrica: v.rubrica, coda: codaEventi, stato: v.stato, motivo: v.motivo, da_quando: v.da_quando, collegato_dal: v.collegato_dal, ultimo_messaggio: v.ultimo_messaggio, ultimo_telefono: v.ultimo_telefono, ultimo_aperto: v.ultimo_aperto, oggi: v.oggi }, BufferJSON.replacer))
	}

	function segna() {
		cambiata = true
		if (!timerCopia && !fermo) timerCopia = setTimeout(() => { timerCopia = null; salva().catch(() => {}) }, ritardoCopia)
	}

	let salvando = null
	async function salva() {
		if (salvando) { await salvando; if (!cambiata) return }
		if (fermo || !cambiata) return
		salvando = (async () => {
			cambiata = false
			n += 1
			const dati = cifra(chiaveCifra, serializza())
			const tmp = fileLocale + '.tmp'
			writeFileSync(tmp, dati, { mode: 0o600 })
			renameSync(tmp, fileLocale)
			chmodSync(fileLocale, 0o600)
			const r = await centralino.scriviSessione(gen, dati)
			if (r === 'superata') superata()
			else if (r !== 'ok') { cambiata = true; riprovaCopia() }
		})().finally(() => { salvando = null })
		await salvando
	}

	function riprovaCopia() {
		if (timerCopia || fermo) return
		timerCopia = setTimeout(() => { timerCopia = null; salva().catch(() => {}) }, 15000)
	}

	function superata() {
		fermo = true
		console.log('sessione: un altro avvio ha preso il posto di questo')
		motore?.chiudi()
		motore = null
		cambiaStato('sostituito', 'Il servizio si è riavviato: questa copia si ferma.', false)
	}

	function cambiaStato(stato, motivo = '', manda = true) {
		const cambiato = v.stato !== stato
		v.stato = stato
		v.motivo = motivo
		if (cambiato) v.da_quando = adesso()
		segna()
		console.log('stato:', stato)
		if (manda) centralino.mandaStato(pubblico()).catch(() => {})
	}

	function contaOggi() {
		const g = giornoRoma()
		if (v.oggi.giorno !== g) v.oggi = { giorno: g, n: 0 }
		return v.oggi
	}

	function pubblico() {
		const o2 = contaOggi()
		return {
			stato: v.stato, motivo: v.motivo, da_quando: v.da_quando, collegato_dal: v.collegato_dal, ultimo_messaggio: v.ultimo_messaggio,
			ultimo_telefono: v.ultimo_telefono, ultimo_aperto: v.ultimo_aperto, oggi: o2.n, tetto, in_coda: codaEventi.length + codaFile.length,
			registrato: abbinato(), codice_attivo: !!(codiceAttivo && codiceAttivo.scade > Date.now())
		}
	}

	// --- la connessione

	// «registered» diventa vero appena il telefono risponde al codice, anche se poi lo rifiuta.
	// Il collegamento e' vero solo dopo pair-success, che porta con se' «account».
	const abbinato = () => !!v.creds?.registered && !!v.creds?.account

	function avvisiMotore(tipo, d) {
		if (tipo === 'aperto') {
			tentativi = 0
			clearTimeout(timerRiprova)
			codiceAttivo = null
			qrAttivo = null
			v.ultimo_aperto = adesso()
			v.collegato_dal = v.collegato_dal || adesso()
			cambiaStato('collegato')
		} else if (tipo === 'chiuso') chiusa(d.codice)
		else if (tipo === 'messaggio') entra(d.voce)
		else if (tipo === 'file') file(d)
		else if (tipo === 'stato') accoda({ genere: 'stato', id: d.id, status: d.status, recipient_id: d.recipient_id })
	}

	function nuovoMotore() {
		motore?.chiudi()
		motore = creaMotore({ sessione, numero: o.numero, avvisa: avvisiMotore })
		return motore
	}

	function dimentica(stato, motivo) {
		motore = null
		v.creds = null
		v.chiavi = {}
		v.collegato_dal = ''
		codiceAttivo = null
		qrAttivo = null
		cambiaStato(stato, motivo)
		salva().catch(() => {})
	}

	function chiusa(codice) {
		console.log('connessione chiusa, codice', codice)
		motore = null
		if (fermo) return
		const registrato = abbinato()
		if (codice === USCITE.RIAVVIA) {
			connetti().catch(() => {})
			return
		}
		if (!registrato) {
			// durante il collegamento non si riprova da soli: riparte solo col tocco di Andrea
			const rifiutato = codice === USCITE.LOGGED_OUT
			dimentica('da_collegare', rifiutato ? 'WhatsApp ha rifiutato il collegamento.' : (codiceAttivo ? 'Il codice è scaduto.' : ''))
			return
		}
		if (codice === USCITE.LOGGED_OUT) {
			dimentica('scollegato', 'WhatsApp ha tolto questo dispositivo. Serve un nuovo collegamento.')
			return
		}
		if (codice === USCITE.BLOCCATO) {
			motore = null
			cambiaStato('bloccato', 'WhatsApp rifiuta questo numero: mi fermo e non riprovo.')
			return
		}
		if (codice === USCITE.SOSTITUITO) {
			cambiaStato('sostituito', 'Un altro collegamento ha preso il posto di questo. Mi fermo.')
			return
		}
		cambiaStato('riprovo', 'Connessione caduta: riprovo con calma.')
		const ms = attesa(tentativi++)
		clearTimeout(timerRiprova)
		timerRiprova = setTimeout(() => connetti().catch(() => chiusa(0)), ms)
	}

	async function connetti(opz) {
		const m = nuovoMotore()
		await m.apri(opz)
		return m
	}

	function colleghiRecenti() {
		const taglio = Date.now() - 3600000
		while (tentativiCollega.length && tentativiCollega[0] < taglio) tentativiCollega.shift()
		return tentativiCollega.length
	}

	async function collega(modo) {
		if (fermo) return [409, { errore: 'Il servizio si sta riavviando. Riprova fra un minuto.' }]
		if (abbinato()) {
			if (v.stato === 'collegato') return [200, { gia: true, frase: 'È già collegato.' }]
			if (v.stato === 'bloccato') return [409, { errore: 'WhatsApp rifiuta questo numero. Controlla sul telefono prima di riprovare.' }]
			tentativi = 0
			await connetti()
			return [200, { riconnetto: true, frase: 'Riprovo a collegarmi con la sessione che ho.' }]
		}
		if (modo === 'codice' && !o.numero) return [503, { errore: 'Manca il numero sul servizio.' }]
		if (codiceAttivo && codiceAttivo.scade > Date.now()) return [200, { codice: codiceAttivo.codice, scade: codiceAttivo.scade }]
		if (colleghiRecenti() >= tentativiOra) return [429, { errore: `Hai fatto ${tentativiOra} prove in un'ora. Mi fermo per non far insospettire WhatsApp. Riprova più tardi.` }]
		tentativiCollega.push(Date.now())
		v.creds = nuoveCreds()
		v.chiavi = {}
		segna()
		cambiaStato('in_collegamento', '')
		const qr = await new Promise((risolvi, rifiuta) => {
			const t = setTimeout(() => rifiuta(new Error('nessuna risposta')), 45000)
			connetti({ qr: (q) => { clearTimeout(t); qrAttivo = { qr: q, quando: Date.now() }; risolvi(q) } }).catch((e) => { clearTimeout(t); rifiuta(e) })
		}).catch(() => null)
		if (!qr || !motore) {
			motore?.chiudi()
			dimentica('da_collegare', 'WhatsApp non ha risposto al primo saluto.')
			return [502, { errore: 'WhatsApp non ha risposto. Riprova fra poco.' }]
		}
		if (modo === 'qr') return [200, { qr_svg: qrSvg ? qrSvg(qr) : '', scade: Date.now() + 55000 }]
		try {
			const codice = await motore.chiedi_codice()
			codiceAttivo = { codice, scade: Date.now() + durataCodice }
			clearTimeout(timerCodice)
			timerCodice = setTimeout(() => {
				if (abbinato()) return
				console.log('codice scaduto senza collegamento')
				motore?.chiudi()
				dimentica('da_collegare', 'Il codice è scaduto o il telefono non l\'ha accettato. Chiedine uno nuovo.')
			}, durataCodice)
			return [200, { codice, scade: codiceAttivo.scade }]
		} catch {
			motore?.chiudi()
			dimentica('da_collegare', 'WhatsApp non ha dato il codice.')
			return [502, { errore: 'WhatsApp non ha dato il codice. Riprova fra poco.' }]
		}
	}

	async function scollega() {
		if (!abbinato()) return [200, { gia: true }]
		await motore?.esci()
		dimentica('da_collegare', 'Scollegato da te.')
		return [200, { scollegato: true }]
	}

	// --- l'arrivo dei messaggi

	function entra(voce) {
		if (visti.has(voce.id)) return
		visti.add(voce.id)
		if (visti.size > 5000) visti.delete(visti.values().next().value)
		v.ultimo_messaggio = adesso()
		if (voce.dir === 'out') v.ultimo_telefono = adesso()
		accoda({ genere: 'messaggio', voce })
	}

	function file(d) {
		const id = d.voce.id
		if (d.troppo) return accoda({ genere: 'file-assente', id, wa: d.voce.from, motivo: 'Più di 16 MB: resta su WhatsApp.', stato: 'troppo grande' })
		if (d.errore) return accoda({ genere: 'file-assente', id, wa: d.voce.from, motivo: 'Il file non si scarica. Guardalo su WhatsApp dal telefono.', stato: 'non scaricato' })
		if (codaFile.length >= 20) return accoda({ genere: 'file-assente', id, wa: d.voce.from, motivo: 'Troppi file in attesa. Guardalo su WhatsApp dal telefono.', stato: 'non scaricato' })
		codaFile.push({ id, wa: d.voce.from, dati: d.dati, mime: d.voce[d.voce.type]?.mime_type || '' })
		svuota()
	}

	function accoda(e) {
		codaEventi.push(e)
		if (codaEventi.length > MAX_CODA) codaEventi = codaEventi.slice(-MAX_CODA)
		segna()
		svuota()
	}

	// Una sola consegna alla volta. Se Rilievo non risponde si riprova con calma, e il doppione lo ferma l'id dall'altra parte.
	async function svuota() {
		if (invioCoda) return
		invioCoda = true
		let nFallite = 0
		try {
			while (codaEventi.length || codaFile.length) {
				try {
					if (codaEventi.length) {
						const pezzo = codaEventi.slice(0, 20)
						await centralino.mandaEventi(pezzo)
						codaEventi = codaEventi.slice(pezzo.length)
						segna()
					} else {
						const f = codaFile[0]
						await centralino.mandaFile(f)
						codaFile.shift()
					}
					nFallite = 0
				} catch {
					nFallite += 1
					await pausa(o.attesaCoda ? o.attesaCoda(nFallite) : Math.min(60000, 2000 * 2 ** nFallite))
				}
			}
		} finally {
			invioCoda = false
		}
	}

	// --- l'invio: uno alla volta, con pausa e tetto. La prova del tocco e' l'origine dichiarata dal Centralino.

	async function invia(d) {
		const { a, testo, cid, origine } = d || {}
		if (!ORIGINI.has(origine)) return [403, { errore: 'Manca il tocco di Andrea: non parte niente.' }]
		const wa = String(a || '').replace(/\D/g, '')
		const t = String(testo || '').trim()
		if (!wa || !t || t.length > 4096) return [400, { errore: 'Messaggio non valido.' }]
		if (!cid) return [400, { errore: 'Manca il riferimento del messaggio.' }]
		const gia = inviatiCid.get(cid)
		if (gia) return [200, { id: await gia, gia: true }]
		if (v.stato !== 'collegato' || !motore) return [409, { errore: 'WhatsApp scollegato. Il messaggio non è partito.', scollegato: true }]
		if (contaOggi().n >= tetto) return [429, { errore: `Oggi sono già partiti ${tetto} messaggi da qui: è il tetto. Riparte domani, o rispondi dal telefono.` }]
		if (inviiInCorso >= 2) return [429, { errore: 'Un messaggio alla volta: aspetta qualche secondo.' }]
		inviiInCorso += 1
		contaOggi().n += 1
		const jid = v.rubrica[wa] || `${wa}@s.whatsapp.net`
		const lavoro = codaInvii.then(async () => {
			const manca = ultimoInvio + pausaInvio - Date.now()
			if (manca > 0) await pausa(manca)
			try {
				return await motore.manda(jid, t)
			} finally {
				ultimoInvio = Date.now()
			}
		})
		codaInvii = lavoro.catch(() => {})
		inviatiCid.set(cid, lavoro)
		if (inviatiCid.size > 1000) inviatiCid.delete(inviatiCid.keys().next().value)
		try {
			const id = await lavoro
			segna()
			return [200, { id }]
		} catch {
			inviatiCid.delete(cid)
			contaOggi().n -= 1
			return [502, { errore: 'WhatsApp non ha preso il messaggio. Non è partito.' }]
		} finally {
			inviiInCorso -= 1
		}
	}

	async function provaVia() {
		if (abbinato() || motore) return [409, { errore: 'Prova possibile solo da scollegato.' }]
		const provvisorie = { creds: nuoveCreds(), chiavi: {}, cambiata() {}, ricorda() {} }
		const esito = await new Promise((risolvi) => {
			const t = setTimeout(() => risolvi(false), 45000)
			const m = creaMotore({ sessione: provvisorie, numero: '', avvisa: () => {} })
			m.apri({ qr: () => { clearTimeout(t); m.chiudi(); risolvi(true) } }).catch(() => { clearTimeout(t); risolvi(false) })
		})
		return [200, { arriva_al_saluto: esito }]
	}

	// --- avvio

	async function avvia() {
		let aperta = null
		for (let i = 0; !aperta; i++) {
			aperta = await centralino.apriSessione().catch(() => null)
			if (!aperta) {
				if (i === 0) cambiaStato('attendo_centralino', 'Aspetto il Centralino per riprendere la sessione.', false)
				await pausa(o.attesaAvvio ? o.attesaAvvio(i) : Math.min(60000, 3000 * 2 ** i))
			}
		}
		gen = aperta.gen
		const copie = []
		if (aperta.dati) copie.push(aperta.dati)
		if (existsSync(fileLocale)) copie.push(readFileSync(fileLocale))
		let migliore = null
		for (const c of copie) {
			try {
				const s = JSON.parse(decifra(chiaveCifra, c).toString(), BufferJSON.reviver)
				if (!migliore || s.n > migliore.n) migliore = s
			} catch {
				cambiaStato('errore_chiave', 'La copia della sessione non si apre con questa chiave. Non la tocco.')
				fermo = true
				return
			}
		}
		if (migliore) {
			n = migliore.n
			Object.assign(v, { creds: migliore.creds, chiavi: migliore.chiavi || {}, rubrica: migliore.rubrica || {}, stato: migliore.stato, motivo: migliore.motivo, da_quando: migliore.da_quando, collegato_dal: migliore.collegato_dal || '', ultimo_messaggio: migliore.ultimo_messaggio || '', ultimo_telefono: migliore.ultimo_telefono || '', ultimo_aperto: migliore.ultimo_aperto || '', oggi: migliore.oggi || v.oggi })
			codaEventi = migliore.coda || []
			for (const e of codaEventi) if (e.genere === 'messaggio') visti.add(e.voce.id)
			svuota()
		}
		if (abbinato()) {
			if (v.stato === 'bloccato') return centralino.mandaStato(pubblico()).catch(() => {})
			cambiaStato('in_collegamento', 'Riprendo la sessione.')
			await connetti().catch(() => chiusa(0))
		} else {
			v.creds = null
			cambiaStato('da_collegare', v.stato === 'scollegato' ? v.motivo : '')
		}
	}

	async function fine() {
		clearTimeout(timerCopia)
		clearTimeout(timerRiprova)
		cambiata = true
		await salva().catch(() => {})
		motore?.chiudi()
	}

	// --- le richieste

	async function gestisci(metodo, percorso, leggiCorpo, chiaveData) {
		if (metodo === 'GET' && percorso === '/salute') return [200, { vivo: true }]
		if (!chiaveData || !uguale(chiaveData, chiave)) return [401, { errore: 'Serve la chiave.' }]
		if (metodo === 'GET' && percorso === '/stato') return [200, pubblico()]
		if (metodo !== 'POST') return [404, { errore: 'Non c\'è.' }]
		const d = await leggiCorpo()
		if (percorso === '/invia') return invia(d)
		if (percorso === '/collega') return collega(d?.modo === 'qr' ? 'qr' : 'codice')
		if (percorso === '/scollega') return scollega()
		if (percorso === '/prova-via') return provaVia()
		return [404, { errore: 'Non c\'è.' }]
	}

	return { avvia, fine, gestisci, pubblico, _v: v, _salva: salva, _codaVuota: () => !codaEventi.length && !codaFile.length }
}
