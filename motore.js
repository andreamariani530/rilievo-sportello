// Il collegamento vero con WhatsApp. Tutto cio' che tocca Baileys sta qui, cosi' il resto si collauda senza rete.
import makeWASocket, {
	BufferJSON, Browsers, DisconnectReason, downloadMediaMessage, fetchLatestWaWebVersion, generateMessageIDV2,
	getContentType, initAuthCreds, jidNormalizedUser, normalizeMessageContent, proto
} from 'baileys'
import pino from 'pino'

export { BufferJSON }

const TETTO_FILE = 16 * 1024 * 1024
const IGNORATI = /(@g\.us|@broadcast|@newsletter|@bot|@call)$/
const TIPI_FILE = { imageMessage: 'image', audioMessage: 'audio', videoMessage: 'video', documentMessage: 'document', stickerMessage: 'sticker', documentWithCaptionMessage: 'document' }
const STATI = { 3: 'delivered', 4: 'read', 5: 'read', 0: 'failed' }

export const nuoveCreds = () => initAuthCreds()

export function creaAuth(s) {
	return {
		creds: s.creds,
		keys: {
			get: async (tipo, ids) => {
				const fuori = {}
				for (const id of ids) {
					let v = s.chiavi[`${tipo}-${id}`]
					if (v !== undefined && v !== null) {
						if (tipo === 'app-state-sync-key') v = proto.Message.AppStateSyncKeyData.fromObject(v)
						fuori[id] = v
					}
				}
				return fuori
			},
			set: async (dati) => {
				for (const tipo in dati) {
					for (const id in dati[tipo]) {
						const v = dati[tipo][id]
						if (v) s.chiavi[`${tipo}-${id}`] = v
						else delete s.chiavi[`${tipo}-${id}`]
					}
				}
				s.cambiata()
			}
		}
	}
}

const cifre = (jid) => String(jid || '').split('@')[0].split(':')[0].replace(/\D/g, '')

// Traduce un messaggio di Baileys nella forma che il Centralino sa gia' leggere. Null = da ignorare.
export function traduci(m, jidPersona) {
	const contenuto = normalizeMessageContent(m.message)
	if (!contenuto || !m.key?.id) return null
	const tipoBaileys = getContentType(contenuto)
	const base = {
		id: m.key.id, dir: m.key.fromMe ? 'out' : 'in', from: cifre(jidPersona), nome: m.pushName || '',
		timestamp: Number(m.messageTimestamp) || Math.floor(Date.now() / 1000)
	}
	if (tipoBaileys === 'conversation') return { ...base, type: 'text', text: { body: contenuto.conversation } }
	if (tipoBaileys === 'extendedTextMessage') return { ...base, type: 'text', text: { body: contenuto.extendedTextMessage.text || '' } }
	const tipoFile = TIPI_FILE[tipoBaileys]
	if (tipoFile) {
		const x = contenuto[tipoBaileys] || {}
		return { ...base, type: tipoFile, [tipoFile]: { id: m.key.id, mime_type: x.mimetype || '', caption: x.caption || '', filename: x.fileName || '', byte: Number(x.fileLength) || 0 } }
	}
	if (tipoBaileys === 'reactionMessage') return { ...base, type: 'reaction', reaction: { emoji: contenuto.reactionMessage.text || '' } }
	if (tipoBaileys === 'locationMessage' || tipoBaileys === 'liveLocationMessage') {
		const l = contenuto[tipoBaileys]
		return { ...base, type: 'location', location: { name: l.name || '', address: l.address || '', latitude: l.degreesLatitude, longitude: l.degreesLongitude } }
	}
	if (tipoBaileys === 'contactMessage') return { ...base, type: 'contacts', contacts: [{ name: { formatted_name: contenuto.contactMessage.displayName || '' } }] }
	return null
}

// Un motore e' una connessione alla volta. Chi lo crea decide se e quando riprovare.
export function creaMotore({ sessione, numero, avvisa, attesaQR = 45000 }) {
	let sock = null
	let chiuso = false
	const contenutiInviati = new Map()
	const propri = new Set()

	const persona = async (key) => {
		const alt = key.remoteJidAlt
		if (alt && !alt.endsWith('@lid')) return jidNormalizedUser(alt)
		const j = key.remoteJid || ''
		if (j.endsWith('@lid')) {
			try {
				const pn = await sock.signalRepository.lidMapping.getPNForLID(j)
				if (pn) return jidNormalizedUser(pn)
			} catch { /* resta il lid */ }
		}
		return jidNormalizedUser(j)
	}

	const apri = async ({ qr: quandoQR } = {}) => {
		let version
		try {
			const v = await fetchLatestWaWebVersion({})
			if (v?.version && !v.error) version = v.version
		} catch { /* si usa quella della libreria */ }
		sock = makeWASocket({
			...(version ? { version } : {}),
			auth: creaAuth(sessione),
			logger: pino({ level: 'silent' }),
			browser: Browsers.macOS('Chrome'),
			markOnlineOnConnect: false,
			syncFullHistory: false,
			shouldSyncHistoryMessage: () => false,
			generateHighQualityLinkPreview: false,
			getMessage: async (k) => contenutiInviati.get(k.id)
		})
		sock.ev.on('creds.update', (u) => {
			if (u.registered && !u.account && !sessione.creds.account) console.log('il telefono ha risposto al codice')
			if (u.account) console.log('collegamento confermato da WhatsApp')
			Object.assign(sessione.creds, u)
			sessione.cambiata()
		})
		// WhatsApp manda alcuni stati come <status>: senza conferma chiude la connessione ogni 50 minuti (#2858)
		sock.ws.on('CB:status', (n) => { sock.sendMessageAck(n).catch(() => {}) })
		sock.ev.on('connection.update', async (u) => {
			if (u.qr && quandoQR) quandoQR(u.qr)
			if (u.connection === 'open') {
				sock.sendPresenceUpdate('unavailable').catch(() => {})
				avvisa('aperto', {})
			}
			if (u.connection === 'close' && !chiuso) {
				const codice = u.lastDisconnect?.error?.output?.statusCode || 0
				avvisa('chiuso', { codice })
			}
		})
		sock.ev.on('messages.upsert', async ({ messages }) => {
			for (const m of messages) {
				try {
					const j = m.key?.remoteJid || ''
					if (!j || IGNORATI.test(j) || propri.has(m.key.id)) continue
					const jp = await persona(m.key)
					const voce = traduci(m, jp)
					if (!voce || !voce.from) continue
					sessione.ricorda(voce.from, j)
					const media = voce.type && voce[voce.type]?.id ? voce[voce.type] : null
					avvisa('messaggio', { voce })
					if (media && voce.dir === 'in') {
						if (media.byte > TETTO_FILE) { avvisa('file', { voce, troppo: true }); continue }
						downloadMediaMessage(m, 'buffer', {}, { reuploadRequest: sock.updateMediaMessage, logger: pino({ level: 'silent' }) })
							.then((dati) => avvisa('file', { voce, dati }))
							.catch(() => avvisa('file', { voce, errore: true }))
					}
				} catch (e) {
					console.log('messaggio non letto:', e?.name || 'errore')
				}
			}
		})
		sock.ev.on('messages.update', async (agg) => {
			for (const { key, update } of agg) {
				if (!key?.fromMe || update?.status === undefined || update.status === null) continue
				const s = STATI[update.status]
				if (s) avvisa('stato', { id: key.id, status: s, recipient_id: cifre(await persona(key)) })
			}
		})
		return sock
	}

	return {
		apri,
		chiedi_codice: async () => sock.requestPairingCode(numero),
		manda: async (jid, testo) => {
			const id = generateMessageIDV2(sock.user?.id)
			propri.add(id)
			if (propri.size > 2000) propri.delete(propri.values().next().value)
			const contenuto = { text: testo }
			const r = await sock.sendMessage(jid, contenuto, { messageId: id })
			contenutiInviati.set(id, proto.Message.fromObject({ conversation: testo }))
			if (contenutiInviati.size > 200) contenutiInviati.delete(contenutiInviati.keys().next().value)
			return r?.key?.id || id
		},
		esci: async () => { chiuso = true; try { await sock?.logout() } catch { /* gia' fuori */ } },
		chiudi: () => { chiuso = true; try { sock?.end(undefined) } catch { /* gia' chiuso */ } },
		attesaQR
	}
}

export const MOTIVI = DisconnectReason
