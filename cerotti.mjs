// Correzioni a baileys@7.0.0-rc14, la versione fissata nel file di blocco. Sono le stesse delle
// richieste aperte sul progetto originale (WhiskeySockets/Baileys), non ancora pubblicate su npm:
//   #2872 e #2559 codice di abbinamento   #2765 collegamento su iPhone   #2789 non risultare online
// Se un pezzo non si trova esattamente, la costruzione si ferma: meglio niente che un ibrido.
import { readFileSync, writeFileSync } from 'node:fs'

const LIB = new URL('./node_modules/baileys/lib/', import.meta.url)
const VERSIONE = '7.0.0-rc14'
const pacchetto = JSON.parse(readFileSync(new URL('../package.json', LIB)))
if (pacchetto.version !== VERSIONE) throw new Error(`versione inattesa di baileys: ${pacchetto.version}`)

const CEROTTI = [
	['Socket/socket.js', '#2872 registrazione finche il codice non e confermato',
		`if (!creds.me) {\n            node = generateRegistrationNode(creds, config);`,
		`if (!creds.me || (!!creds.pairingCode && !creds.registered)) {\n            node = generateRegistrationNode(creds, config);`],
	['Socket/socket.js', '#2872 nonce a un byte nullo',
		`tag: 'link_code_pairing_nonce',\n                            attrs: {},\n                            content: '0'`,
		`tag: 'link_code_pairing_nonce',\n                            attrs: {},\n                            content: Buffer.alloc(1)`],
	['Socket/messages-recv.js', '#2872 solo la notifica primary_hello',
		`const linkCodeCompanionReg = getBinaryNodeChild(node, 'link_code_companion_reg');\n`,
		`const linkCodeCompanionReg = getBinaryNodeChild(node, 'link_code_companion_reg');\n                if (linkCodeCompanionReg?.attrs?.stage !== 'primary_hello') break;\n`],
	['Utils/validate-connection.js', '#2872 registrato dopo pair-success',
		`platform: platformNode?.attrs.name\n`,
		`platform: platformNode?.attrs.name,\n        registered: true\n`],
	['Socket/messages-recv.js', '#2765 conferma anche prima di essere collegati',
		`const stanza = buildAckStanza(node, errorCode, authState.creds.me.id);`,
		`const meId = authState.creds.me?.id;\n        if (node.tag === 'message' && !meId) {\n            throw new Boom('cannot ack a message before creds.me is set');\n        }\n        const stanza = buildAckStanza(node, errorCode, meId);`],
	['Socket/socket.js', '#2765 il segreto del QR si legge a ogni disegno',
		`const qr = buildPairingQRData(ref, noiseKeyB64, identityKeyB64, advB64, browser);\n            ev.emit('connection.update', { qr });`,
		`refCorrenteQR = ref;\n            const qr = buildPairingQRData(ref, noiseKeyB64, identityKeyB64, creds.advSecretKey, browser);\n            ev.emit('connection.update', { qr });`],
	['Socket/socket.js', '#2765 variabili del QR',
		`const advB64 = creds.advSecretKey;\n`,
		`ridisegnaQR = () => {\n            if (refCorrenteQR !== undefined) {\n                ev.emit('connection.update', { qr: buildPairingQRData(refCorrenteQR, noiseKeyB64, identityKeyB64, creds.advSecretKey, browser) });\n            }\n        };\n`],
	['Socket/socket.js', '#2765 il server ritira il segreto del QR',
		`ws.on('CB:iq,type:set,pair-device', async (stanza) => {`,
		`let refCorrenteQR;\n    let ridisegnaQR;\n    ws.on('CB:notification,type:companion_reg_refresh', (node) => {\n        const figli = ['companion_reg_refresh', 'pair-device-rotate-qr'];\n        if (creds.me || !figli.some(tag => getBinaryNodeChild(node, tag))) {\n            return;\n        }\n        const advSecretKey = randomBytes(32).toString('base64');\n        creds.advSecretKey = advSecretKey;\n        ev.emit('creds.update', { advSecretKey });\n        ridisegnaQR?.();\n    });\n    ws.on('CB:iq,type:set,pair-device', async (stanza) => {`],
	['Socket/socket.js', '#2559 se WhatsApp rifiuta la richiesta del codice, si vede subito',
		`await sendNode({\n            tag: 'iq',\n            attrs: {\n                to: S_WHATSAPP_NET,\n                type: 'set',\n                id: generateMessageTag(),\n                xmlns: 'md'\n            },\n            content: [\n                {\n                    tag: 'link_code_companion_reg',`,
		`await query({\n            tag: 'iq',\n            attrs: {\n                to: S_WHATSAPP_NET,\n                type: 'set',\n                id: generateMessageTag(),\n                xmlns: 'md'\n            },\n            content: [\n                {\n                    tag: 'link_code_companion_reg',`],
	['Socket/socket.js', '#2789 mai una presenza senza nome',
		`if (creds.me?.name !== name) {`,
		`if (typeof name === 'string' && name.length > 0 && creds.me?.name !== name) {`]
]

const testi = new Map()
for (const [file, nome, prima, dopo] of CEROTTI) {
	const u = new URL(file, LIB)
	const t = testi.get(file) ?? readFileSync(u, 'utf8')
	const pezzi = t.split(prima)
	if (pezzi.length !== 2) throw new Error(`cerotto non applicabile (${pezzi.length - 1} punti): ${nome}`)
	testi.set(file, pezzi.join(dopo))
	console.log('cerotto:', nome)
}
for (const [file, t] of testi) writeFileSync(new URL(file, LIB), t)
