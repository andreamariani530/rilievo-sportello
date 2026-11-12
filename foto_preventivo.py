#!/usr/bin/env python3
"""Il listino da una FOTO di un preventivo vecchio.

PERCHE' ESISTE
Il 12/11/2026 Marco, giardiniere arrivato da TikTok, ha scritto: «sarebbe piu' comodo
poter allegare l'immagine e non solo il file». Un artigiano il preventivo vecchio lo ha
quasi sempre come foto del foglio, scansione o immagine di WhatsApp: dentro non c'e'
testo, quindi il telefono da solo non lo sa leggere.

COME FUNZIONA
Il telefono rimpicciolisce la foto (o le pagine di un PDF scansionato) e la manda qui.
Qui la legge Claude di Anthropic (Haiku 5.5, che vede le immagini) e torna il testo
delle righe, una per riga, nella forma che l'app sa gia' leggere:
    «Sfalcio prato 350 mq 0,30 105,00»
Poi l'app fa la strada di sempre: leggiRigheListino -> proponiVoci.

COSA NON SI FA
La foto passa e si butta: non si salva da nessuna parte, e nel registro del server
finiscono solo numeri (quante pagine, quanti pezzi letti), mai il contenuto.

I TETTI (un file per giorno, solo impronte e numeri)
- con l'accesso: 15 letture al giorno per account (una lettura = un preventivo, fino a 4 pagine);
- senza accesso: 3 al giorno per telefono e 6 per indirizzo di rete;
- in tutto: FOTO_PREVENTIVO_TETTO_GIORNO (200 se non c'e').
Una lettura che non riesce non si conta.
"""
import base64, hashlib, json, os, pathlib, threading, time
import urllib.error, urllib.request

import archivio

MODELLO = os.environ.get("FOTO_PREVENTIVO_MODELLO") or "claude-haiku-5-5"
RISERVA = "claude-sonnet-5-5"          # se il modello piccolo non c'e' piu'
INDIRIZZO_API = os.environ.get("ASSISTENTE_INDIRIZZO_API") or "https://api.anthropic.com/v1/messages"
TETTO_ACCOUNT = 15
TETTO_TELEFONO = 3
TETTO_RETE = 6
MAX_PAGINE = 4
MAX_FOTO = 2 * 1024 * 1024             # byte di una pagina, gia' rimpicciolita dal telefono
MAX_RISPOSTA = 2000                    # pezzi: un preventivo lungo sta comodo

NON_PREVENTIVO = "NESSUN_PREVENTIVO"
SENZA_PREZZI = "NESSUN_PREZZO"

ISTRUZIONI = """Ricevi la foto (o le pagine) di un preventivo o di una fattura di un artigiano italiano
(giardiniere, imbianchino, idraulico, elettricista, muratore, piastrellista...).
Trascrivi SOLO le righe delle lavorazioni o dei materiali, una per riga, in questa forma:
descrizione quantita unita prezzo_unitario importo
Esempi:
Sfalcio prato 350 mq 0,30 105,00
Potatura siepe 40 m 9,00 360,00
Smaltimento verde a corpo 80,00
Regole:
- Scrivi descrizione, quantita, unita e prezzi COME STANNO SUL FOGLIO: non inventare, non calcolare, non correggere.
- Se manca la quantita o il prezzo unitario, scrivi solo quello che c'e'.
- Numeri all'italiana: virgola per i decimali, niente simbolo dell'euro, niente separatore delle migliaia.
- Unita brevi: mq, m, ml, ore, pz, kg, a corpo, cad.
- Niente intestazione, indirizzi, dati del cliente, totali, IVA, sconti, note, condizioni di pagamento.
- Niente altro testo prima o dopo le righe.
Se l'immagine non e' un preventivo, una fattura o un elenco di lavori, scrivi solo: NESSUN_PREVENTIVO
Se e' un preventivo ma non c'e' nessun prezzo leggibile, scrivi solo: NESSUN_PREZZO"""

_chiave = threading.RLock()


def _impronta(s):
    return hashlib.sha256(("rilievo-foto-preventivo|" + str(s)).encode("utf-8")).hexdigest()[:24]


def _file_conti():
    d = pathlib.Path(archivio.CARTELLA) / "foto-preventivo"
    d.mkdir(parents=True, exist_ok=True)
    return d / ("conti-%s.json" % time.strftime("%Y-%m-%d", time.gmtime()))


def _leggi():
    try:
        return json.loads(_file_conti().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"account": {}, "telefono": {}, "rete": {}, "totale": 0}


def _scrivi(c):
    f = _file_conti()
    t = f.with_suffix(".tmp")
    t.write_text(json.dumps(c), encoding="utf-8")
    os.replace(t, f)


def tetto_totale():
    try:
        return max(1, int(os.environ.get("FOTO_PREVENTIVO_TETTO_GIORNO") or 200))
    except ValueError:
        return 200


def _chi_conta(account, telefono, ip):
    if account:
        return [("account", _impronta(account), TETTO_ACCOUNT)]
    fuori = [("rete", _impronta(ip or "?"), TETTO_RETE)]
    tel = str(telefono or "").strip()[:80]
    if tel:
        fuori.insert(0, ("telefono", _impronta(tel), TETTO_TELEFONO))
    return fuori


def restano(account, telefono, ip):
    with _chiave:
        c = _leggi()
    resta = tetto_totale() - int(c.get("totale", 0))
    for tipo, chi, tetto in _chi_conta(account, telefono, ip):
        resta = min(resta, tetto - int(c.get(tipo, {}).get(chi, 0)))
    return max(0, resta)


def _conta(account, telefono, ip):
    with _chiave:
        c = _leggi()
        for tipo, chi, _ in _chi_conta(account, telefono, ip):
            c.setdefault(tipo, {})[chi] = int(c.get(tipo, {}).get(chi, 0)) + 1
        c["totale"] = int(c.get("totale", 0)) + 1
        _scrivi(c)


def _immagini(lista):
    """Da quello che manda il telefono alle immagini per Anthropic. Solo JPEG e PNG,
    ognuna sotto MAX_FOTO. Torna (immagini, motivo)."""
    if not isinstance(lista, list) or not lista:
        return None, "Non mi e' arrivata nessuna foto."
    if len(lista) > MAX_PAGINE:
        return None, "Al massimo %d pagine per volta." % MAX_PAGINE
    fuori = []
    for x in lista:
        s = str(x or "")
        tipo = "image/jpeg"
        if s.startswith("data:"):
            testa, _, s = s.partition(",")
            if "image/png" in testa:
                tipo = "image/png"
            elif "image/jpeg" not in testa:
                return None, "Questa foto non la so aprire. Riprova con una foto normale."
        try:
            grezzi = base64.b64decode(s, validate=True)
        except Exception:                      # noqa: BLE001
            return None, "Questa foto non e' arrivata intera. Riprova."
        if len(grezzi) > MAX_FOTO:
            return None, "Questa foto e' troppo grande. Riprova."
        if not (grezzi[:3] == b"\xff\xd8\xff" or grezzi[:8] == b"\x89PNG\r\n\x1a\n"):
            return None, "Questa foto non la so aprire. Riprova con una foto normale."
        fuori.append({"type": "image", "source": {"type": "base64", "media_type": tipo,
                                                  "data": base64.b64encode(grezzi).decode()}})
    return fuori, None


def _chiedi(immagini, chiave, modello):
    contenuto = list(immagini) + [{"type": "text", "text": "Trascrivi le righe di questo preventivo."}]
    corpo = {"model": modello, "max_tokens": MAX_RISPOSTA, "system": ISTRUZIONI,
             "messages": [{"role": "user", "content": contenuto}]}
    req = urllib.request.Request(
        INDIRIZZO_API, data=json.dumps(corpo).encode("utf-8"), method="POST",
        headers={"x-api-key": chiave, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


NON_LETTA = "Non sono riuscito a leggere la foto. Riprova, oppure incolla le righe qui sotto."


def leggi(lista, account=None, telefono=None, ip=None):
    """Torna (codice, dati). Mai la chiave, mai un errore tecnico all'artigiano."""
    chiave = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not chiave:
        return 503, {"errore": NON_LETTA, "spento": True}
    immagini, motivo = _immagini(lista)
    if not immagini:
        return 400, {"errore": motivo}
    if restano(account, telefono, ip) <= 0:
        if account:
            msg = ("Oggi hai gia' letto %d preventivi da foto. Domani si riparte; intanto puoi "
                   "incollare le righe qui sotto." % TETTO_ACCOUNT)
        else:
            msg = "Per leggere altre foto entra con la tua mail e la tua password."
        return 429, {"errore": msg, "finite": True}
    r = None
    for modello in (MODELLO, RISERVA):
        try:
            r = _chiedi(immagini, chiave, modello)
            break
        except urllib.error.HTTPError as e:
            # solo il codice: il corpo dell'errore potrebbe ripetere pezzi della richiesta
            print("  foto preventivo: Anthropic ha risposto %s (%s)" % (e.code, modello))
            if e.code not in (400, 404) or modello == RISERVA:
                return 503, {"errore": NON_LETTA}
        except Exception as e:                  # noqa: BLE001
            print("  foto preventivo: niente risposta da Anthropic:", type(e).__name__)
            return 503, {"errore": NON_LETTA}
    if not r or r.get("stop_reason") == "refusal":
        return 503, {"errore": NON_LETTA}
    pezzi = [b.get("text", "") for b in (r.get("content") or []) if b.get("type") == "text"]
    testo = "\n".join(p for p in pezzi if p).strip()
    u = r.get("usage") or {}
    print("  foto preventivo: %d pagine, %s pezzi letti, %s scritti"
          % (len(immagini), u.get("input_tokens"), u.get("output_tokens")))
    if not testo:
        return 503, {"errore": NON_LETTA}
    _conta(account, telefono, ip)
    righe = [x.strip(" -*\t") for x in testo.splitlines() if x.strip(" -*\t")]
    if len(righe) <= 2 and any(NON_PREVENTIVO in x for x in righe):
        return 200, {"testo": "", "nessuno": "preventivo",
                     "messaggio": "In questa foto non vedo un preventivo. Fotografa il foglio "
                                  "con le lavorazioni e i prezzi."}
    if len(righe) <= 2 and any(SENZA_PREZZI in x for x in righe):
        return 200, {"testo": "", "nessuno": "prezzi",
                     "messaggio": "Ho letto il preventivo ma non ci trovo nessun prezzo. "
                                  "Prova con un preventivo che ha i prezzi delle lavorazioni."}
    righe = [x for x in righe if NON_PREVENTIVO not in x and SENZA_PREZZI not in x]
    return 200, {"testo": "\n".join(righe), "righe": len(righe)}
