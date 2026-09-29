"""La fattura elettronica: dall'app al servizio SdI (Openapi in prova, Aruba), e lo stato.

Dal 29 settembre 2026 la strada scelta e' Openapi (Aruba Premium costa troppo):
vedi OpenapiProva piu' sotto. Solo sandbox finche' Andrea non decide.

Le fatture di Rilievo partono dall'account Aruba Premium multicedente di Andrea
(decisione del 27 settembre 2026: la fattura sta dentro Rilievo e si paga
nell'abbonamento). Le credenziali stanno SOLO sul server, nelle variabili
d'ambiente, e non vengono mai scritte in un file del progetto:

    ARUBA_UTENTE     il nome utente dell'account Aruba
    ARUBA_PASSWORD   la sua password
    ARUBA_AMBIENTE   "demo" (prova di Aruba) oppure "vero"

Finche' le credenziali non ci sono (oggi, 28 settembre 2026: le demo sono state
chieste ad Aruba il 27, richiesta 19076965A), risponde un ARUBA FINTO che si
comporta come quello vero: accetta il file, lo chiama come lo chiamerebbe
Aruba, dice «Inviata» e dopo una ventina di secondi «Consegnata». Sa anche
scartare, per provare i messaggi: partita IVA del cliente 00000000000, oppure
codice destinatario SCARTA0. Ogni sua risposta porta `prova: true`.

Per passare ad Aruba vero non si tocca il codice: si mettono le tre variabili
sul server e si riaccende.

Quello che arriva all'app e' sempre della stessa forma, finto o vero:

    invia  -> {"file": "IT01879020517_a1b2c.xml.p7m", "stato": "inviata", "prova": bool}
    stato  -> {"file": ..., "stato": "inviata" | "consegnata" | "non_consegnata" | "scartata",
               "codice": "00305", "motivo": "...", "prova": bool}
"""
import base64, json, os, random, re, string, threading, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

TRASMITTENTE = "IT01879020517"          # Aruba PEC S.p.A., come nel file

# Gli indirizzi di Aruba. Demo e vero hanno la stessa strada, cambia la casa.
ARUBA = {
    "demo": {"auth": "https://demoauth.fatturazioneelettronica.aruba.it",
             "ws": "https://demows.fatturazioneelettronica.aruba.it"},
    "vero": {"auth": "https://auth.fatturazioneelettronica.aruba.it",
             "ws": "https://ws.fatturazioneelettronica.aruba.it"},
}

# Gli stati di Aruba, detti come li capisce l'app.
STATI_ARUBA = {
    "presa in carico": "inviata", "inviata": "inviata", "in elaborazione": "inviata",
    "consegnata": "consegnata", "accettata": "consegnata", "decorrenza termini": "consegnata",
    "non consegnata": "non_consegnata", "recapito impossibile": "non_consegnata",
    "scartata": "scartata", "errore elaborazione": "scartata", "rifiutata": "scartata",
}


class FatturaSbagliata(Exception):
    """Il file non si puo' mandare: non e' una fattura ben scritta."""


class ArubaNonRisponde(Exception):
    """Aruba non ha risposto, o ha risposto con un errore suo."""


def leggi(xml):
    """Il file e' una fattura scritta bene? Torna i dati che servono per mandarla."""
    if not isinstance(xml, str) or not xml.strip():
        raise FatturaSbagliata("Manca la fattura.")
    if len(xml.encode("utf-8")) > 5 * 1024 * 1024:
        raise FatturaSbagliata("La fattura e' troppo grande.")
    try:
        radice = ET.fromstring(xml.encode("utf-8"))
    except ET.ParseError as e:
        raise FatturaSbagliata("La fattura non e' scritta bene (%s)." % e)
    if not radice.tag.endswith("FatturaElettronica"):
        raise FatturaSbagliata("Questo file non e' una fattura elettronica.")

    def testo(percorso):
        el = radice.find(percorso)
        return (el.text or "").strip() if el is not None else ""

    return {
        "cedente": testo("./FatturaElettronicaHeader/CedentePrestatore/DatiAnagrafici/IdFiscaleIVA/IdCodice"),
        "nome": (testo("./FatturaElettronicaHeader/CedentePrestatore/DatiAnagrafici/Anagrafica/Denominazione")
                 or " ".join(x for x in (testo("./FatturaElettronicaHeader/CedentePrestatore/DatiAnagrafici/Anagrafica/Nome"),
                                         testo("./FatturaElettronicaHeader/CedentePrestatore/DatiAnagrafici/Anagrafica/Cognome")) if x)),
        "email": testo("./FatturaElettronicaHeader/CedentePrestatore/Contatti/Email"),
        "cliente_piva": testo("./FatturaElettronicaHeader/CessionarioCommittente/DatiAnagrafici/IdFiscaleIVA/IdCodice"),
        "cliente_cf": testo("./FatturaElettronicaHeader/CessionarioCommittente/DatiAnagrafici/CodiceFiscale"),
        "codice": testo("./FatturaElettronicaHeader/DatiTrasmissione/CodiceDestinatario"),
        "numero": testo("./FatturaElettronicaBody/DatiGenerali/DatiGeneraliDocumento/Numero"),
    }


# ------------------------------------------------------------ l'Aruba finto

class ArubaFinto:
    """Si comporta come Aruba, senza mandare niente a nessuno."""
    prova = True

    def __init__(self, secondi=None):
        # quanto ci mette la risposta dello Stato: una ventina di secondi, come
        # quello vero nei giorni buoni. Il collaudo lo accorcia.
        self.secondi = float(secondi if secondi is not None
                             else os.environ.get("FATTURA_FINTA_SECONDI") or 25)
        self.fatture = {}
        self.chiave = threading.Lock()

    def invia(self, xml):
        dati = leggi(xml)
        nome = "%s_%s.xml.p7m" % (TRASMITTENTE, "".join(
            random.choice(string.ascii_lowercase + string.digits) for _ in range(5)))
        scarto = None
        if dati["cliente_piva"] == "00000000000":
            scarto = ("00305", "IdFiscaleIVA del cessionario non valido")
        elif dati["codice"].upper() == "SCARTA0":
            scarto = ("00311", "CodiceDestinatario non valido")
        with self.chiave:
            self.fatture[nome] = {"quando": time.time(), "scarto": scarto, "numero": dati["numero"]}
        return {"file": nome, "stato": "inviata", "prova": True}

    def stato(self, nome):
        with self.chiave:
            f = self.fatture.get(nome)
        if f is None:
            # il server si e' riacceso e il finto ha perso la memoria: una fattura
            # di prova col nome giusto la si da' per consegnata, cosi' l'app non
            # resta appesa per sempre
            if nome.startswith(TRASMITTENTE + "_") and nome.endswith(".xml.p7m"):
                return {"file": nome, "stato": "consegnata", "prova": True}
            return None
        if time.time() - f["quando"] < self.secondi:
            return {"file": nome, "stato": "inviata", "prova": True}
        if f["scarto"]:
            return {"file": nome, "stato": "scartata", "codice": f["scarto"][0],
                    "motivo": f["scarto"][1], "prova": True}
        return {"file": nome, "stato": "consegnata", "prova": True}


# ------------------------------------------------------------ l'Aruba vero

class ArubaVero:
    """Le chiamate ad Aruba, come da manuale delle API (auth, upload, getByFilename).

    Non e' ancora stato provato contro Aruba: le credenziali demo non sono
    arrivate. Il giorno che arrivano, la prima cosa da fare e' un invio sulla
    demo e un confronto delle risposte con quello che si legge qui sotto.
    """
    prova = False

    def __init__(self, utente, password, ambiente="demo"):
        self.utente, self.password = utente, password
        self.ambiente = "vero" if ambiente == "vero" else "demo"
        self.indirizzi = ARUBA[self.ambiente]
        # la demo di Aruba non arriva allo Stato: per l'app e' ancora una prova
        self.prova = self.ambiente != "vero"
        self.token, self.scade = None, 0
        self.chiave = threading.Lock()

    def _chiama(self, url, dati=None, forma=None, token=None, metodo=None):
        intestazioni = {"Accept": "application/json"}
        corpo = None
        if forma is not None:
            corpo = urllib.parse.urlencode(forma).encode()
            intestazioni["Content-Type"] = "application/x-www-form-urlencoded"
        elif dati is not None:
            corpo = json.dumps(dati).encode()
            intestazioni["Content-Type"] = "application/json;charset=UTF-8"
        if token:
            intestazioni["Authorization"] = "Bearer " + token
        req = urllib.request.Request(url, data=corpo, headers=intestazioni,
                                     method=metodo or ("POST" if corpo else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            # si riporta il codice, mai le credenziali
            raise ArubaNonRisponde("Aruba ha risposto %s" % e.code)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise ArubaNonRisponde("Aruba non risponde (%s)" % type(e).__name__)

    def _entra(self):
        with self.chiave:
            if self.token and time.time() < self.scade - 60:
                return self.token
            r = self._chiama(self.indirizzi["auth"] + "/auth/signin",
                             forma={"grant_type": "password", "username": self.utente,
                                    "password": self.password})
            if not r.get("access_token"):
                raise ArubaNonRisponde("Aruba non ha dato l'accesso")
            self.token = r["access_token"]
            self.scade = time.time() + int(r.get("expires_in") or 1800)
            return self.token

    def invia(self, xml):
        dati = leggi(xml)
        corpo = {"dataFile": base64.b64encode(xml.encode("utf-8")).decode(),
                 "credential": "", "domain": ""}
        # account multicedente: si dice ad Aruba per conto di chi parte la fattura
        if dati["cedente"]:
            corpo["senderPIVA"] = "IT" + dati["cedente"]
        r = self._chiama(self.indirizzi["ws"] + "/services/invoice/upload", dati=corpo,
                         token=self._entra())
        codice = str(r.get("errorCode") or "0000")
        if codice not in ("0000", "0"):
            raise FatturaSbagliata(r.get("errorDescription") or ("Aruba l'ha rifiutata (%s)" % codice))
        return {"file": r.get("uploadFileName") or "", "stato": "inviata", "prova": self.prova}

    def stato(self, nome):
        r = self._chiama(self.indirizzi["ws"] + "/services/invoice/out/getByFilename?" +
                         urllib.parse.urlencode({"filename": nome, "includePdf": "false",
                                                 "includeFile": "false"}),
                         token=self._entra())
        fatture = r.get("invoices") or []
        grezzo = str((fatture[0].get("status") if fatture else r.get("status")) or "").strip()
        stato = STATI_ARUBA.get(grezzo.lower(), "inviata")
        fuori = {"file": nome, "stato": stato, "prova": self.prova}
        if stato == "scartata":
            fuori["motivo"] = (fatture[0].get("statusDescription") if fatture else "") or grezzo
            fuori["codice"] = str(r.get("errorCode") or "")
        return fuori


# ------------------------------------------------------------ Openapi (solo prova)

# Solo la sandbox. La chiave di Andrea vale anche in produzione, quindi qui gli
# indirizzi sono fissi e cominciano sempre con "test.": la produzione si accende
# solo quando lo decide Andrea, cambiando il codice di proposito.
OPENAPI_SDI = "https://test.sdi.openapi.it"
OPENAPI_OAUTH = "https://test.oauth.openapi.it"
OPENAPI_TRASMITTENTE = "10442360961"     # Openapi S.p.A.: lo SdI chiama cosi' i file
OPENAPI_PERMESSI = [
    "GET:test.sdi.openapi.it/invoices", "POST:test.sdi.openapi.it/invoices",
    "GET:test.sdi.openapi.it/invoices_notifications",
    "GET:test.sdi.openapi.it/business_registry_configurations",
    "POST:test.sdi.openapi.it/business_registry_configurations",
    "PATCH:test.sdi.openapi.it/business_registry_configurations",
]

# Lo stato che Openapi scrive sulla fattura ("marking"), detto come lo capisce l'app.
STATI_OPENAPI = {
    "sent": "inviata", "sending": "inviata", "queued": "inviata", "pending": "inviata",
    "delivered": "consegnata", "delivered-pa": "consegnata", "accepted-pa": "consegnata",
    "deadline-terms": "consegnata",
    "not-delivered": "non_consegnata",
    "rejected": "scartata", "rejected-pa": "scartata",
}


def _solo_prova(url):
    if not url.startswith(("https://test.sdi.openapi.it/", "https://test.oauth.openapi.it/")):
        raise ArubaNonRisponde("Openapi: indirizzo non di prova, fermato")
    return url


class OpenapiProva:
    """Le fatture vanno alla sandbox di Openapi (test.sdi.openapi.it), un account
    solo per tutte le ditte: ogni partita IVA ha la sua configurazione, che si crea
    da sola al primo invio.

    Provato dal vivo il 29 settembre 2026 (vedi LAVORO-IN-CORSO.md):
    - senza configurazione della ditta, POST /invoices risponde errore 387;
    - la configurazione si crea con apply_signature e apply_legal_storage a false,
      poi POST /invoices con l'XML (Content-Type application/xml) torna data.uuid;
    - la fattura appena mandata ha marking "sent" e lo SdI la chiama IT10442360961...;
    - OGNI chiamata, anche una lettura, costa credito: finito, risponde 402 (errore 611);
    - 12345678903 risulta gia' presa da un altro account della sandbox (errore 230).
    """
    prova = True

    def __init__(self, token=None, email=None, chiave=None):
        self.token_fisso = token or None
        self.email, self.chiave_api = email or "", chiave or ""
        self.token, self.scade = self.token_fisso, (float("inf") if self.token_fisso else 0)
        self.configurate = set()
        self.chiave = threading.Lock()

    # -- rete
    def _chiama(self, url, dati=None, xml=None, token=None, metodo=None, base=None):
        _solo_prova(url)
        intestazioni = {"Accept": "application/json"}
        corpo = None
        if xml is not None:
            corpo = xml.encode("utf-8")
            intestazioni["Content-Type"] = "application/xml"
        elif dati is not None:
            corpo = json.dumps(dati).encode()
            intestazioni["Content-Type"] = "application/json"
        if base:
            intestazioni["Authorization"] = "Basic " + base
        elif token:
            intestazioni["Authorization"] = "Bearer " + token
        req = urllib.request.Request(url, data=corpo, headers=intestazioni,
                                     method=metodo or ("POST" if corpo else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")
            except ValueError:
                return e.code, {}
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise ArubaNonRisponde("Openapi non risponde (%s)" % type(e).__name__)

    def _entra(self, rifai=False):
        """Il token della sandbox: quello dato, o uno fatto qui con mail e chiave,
        con i soli permessi che servono e solo su test.sdi.openapi.it."""
        with self.chiave:
            if self.token_fisso:
                return self.token_fisso
            if self.token and not rifai and time.time() < self.scade - 3600:
                return self.token
            base = base64.b64encode(("%s:%s" % (self.email, self.chiave_api)).encode()).decode()
            codice, r = self._chiama(OPENAPI_OAUTH + "/token", base=base,
                                     dati={"scopes": OPENAPI_PERMESSI, "ttl": 30 * 86400})
            tok = r.get("token") or ((r.get("data") or {}).get("token") if isinstance(r.get("data"), dict) else None)
            if codice != 200 or not tok:
                raise ArubaNonRisponde("Openapi non ha dato l'accesso (%s)" % codice)
            self.token = tok
            self.scade = float(r.get("expire") or (time.time() + 30 * 86400))
            return self.token

    def _sdi(self, via, **kw):
        codice, r = self._chiama(OPENAPI_SDI + via, token=self._entra(), **kw)
        if codice == 401 and not self.token_fisso:
            codice, r = self._chiama(OPENAPI_SDI + via, token=self._entra(rifai=True), **kw)
        if codice in (401, 403):
            raise ArubaNonRisponde("Openapi rifiuta l'accesso (%s)" % codice)
        if codice == 402:
            raise ArubaNonRisponde("Openapi: credito finito (402)")
        if codice >= 500:
            raise ArubaNonRisponde("Openapi ha risposto %s" % codice)
        return codice, r

    # -- la ditta
    def _mail_della_ditta(self, dati):
        """Openapi vuole una mail diversa per ogni partita IVA. Si usa quella
        dell'account con un +partitaIVA: arriva nella stessa casella."""
        if "@" in self.email:
            nome, dominio = self.email.split("@", 1)
            return "%s+%s@%s" % (nome, dati["cedente"], dominio)
        if dati.get("email"):
            return dati["email"]
        return "fatture+%s@rilievo.example" % dati["cedente"]

    def assicura_ditta(self, dati):
        piva = dati["cedente"]
        if not piva:
            raise FatturaSbagliata("Nella fattura manca la tua partita IVA.")
        if piva in self.configurate:
            return
        codice, r = self._sdi("/business_registry_configurations/" + urllib.parse.quote(piva))
        if codice == 200 and r.get("success"):
            conf = r.get("data") or {}
            if conf.get("active") is False:
                self._sdi("/business_registry_configurations/%s/activate" % urllib.parse.quote(piva),
                          dati={"active": True}, metodo="PATCH")
            self.configurate.add(piva)
            return
        codice, r = self._sdi("/business_registry_configurations", dati={
            "fiscal_id": piva, "name": (dati.get("nome") or piva)[:80],
            "email": self._mail_della_ditta(dati),
            "apply_signature": False, "apply_legal_storage": False})
        if codice in (200, 201) and r.get("success"):
            self.configurate.add(piva)
            return
        if str(r.get("error")) == "230":
            # la partita IVA e' gia' registrata da un altro account di Openapi
            raise FatturaSbagliata("La tua partita IVA risulta gia' collegata a un altro account "
                                   "del servizio delle fatture: va liberata prima di mandare da qui.")
        raise ArubaNonRisponde("Openapi non ha registrato la ditta (%s)" % codice)

    # -- invio e stato
    def invia(self, xml):
        dati = leggi(xml)
        self.assicura_ditta(dati)
        # il trasmittente e' chi porta il file allo SdI: con Openapi e' Openapi
        xml = re.sub(r"(<IdTrasmittente>\s*<IdPaese>IT</IdPaese>\s*<IdCodice>)[^<]*(</IdCodice>)",
                     r"\g<1>%s\g<2>" % OPENAPI_TRASMITTENTE, xml, count=1)
        codice, r = self._sdi("/invoices", xml=xml)
        uuid = ((r.get("data") or {}).get("uuid") if isinstance(r.get("data"), dict) else None)
        if codice in (200, 201) and uuid:
            return {"file": uuid, "stato": "inviata", "prova": True}
        if str(r.get("error")) == "387":
            self.configurate.discard(dati["cedente"])
            raise ArubaNonRisponde("Openapi: la ditta non e' pronta a mandare (387)")
        if codice in (400, 422):
            raise FatturaSbagliata("Dentro la fattura c'e' un dato che il servizio non accetta: "
                                   + str(r.get("message") or "controlla i dati e riprova")[:200])
        raise ArubaNonRisponde("Openapi ha risposto %s" % codice)

    def _scarto(self, uuid):
        """Codice e motivo dello scarto, dalla notifica NS dello SdI."""
        codice, r = self._sdi("/invoices_notifications/" + urllib.parse.quote(uuid))
        for n in (r.get("data") or []) if codice == 200 else []:
            if not isinstance(n, dict) or str(n.get("type")) not in ("NS", "MC", "AT"):
                continue
            errori = ((n.get("message") or {}).get("lista_errori") or {}).get("Errore")
            if isinstance(errori, list):
                errori = errori[0] if errori else {}
            if isinstance(errori, dict) and (errori.get("Codice") or errori.get("Descrizione")):
                return str(errori.get("Codice") or ""), str(errori.get("Descrizione") or "")
        return "", ""

    def stato(self, nome):
        if not re.fullmatch(r"[0-9a-fA-F-]{20,60}", nome or ""):
            return None
        codice, r = self._sdi("/invoices/" + urllib.parse.quote(nome))
        if codice == 404 or not isinstance(r.get("data"), dict):
            return None
        marca = str(r["data"].get("marking") or "").strip().lower()
        stato = STATI_OPENAPI.get(marca, "inviata")
        fuori = {"file": nome, "stato": stato, "prova": True}
        if stato == "scartata":
            cod, motivo = self._scarto(nome)
            fuori["codice"] = cod
            fuori["motivo"] = motivo or str(r["data"].get("notice") or "Lo SdI l'ha scartata")
        return fuori


# ------------------------------------------------------------ chi risponde

_sportello = {"chi": None}


def sportello():
    """Chi manda le fatture, deciso dalle variabili d'ambiente del server:
    - OPENAPI_EMAIL e OPENAPI_CHIAVE (il server si fa da solo il token della
      sandbox), oppure OPENAPI_TOKEN: Openapi, SOLO in prova;
    - ARUBA_UTENTE e ARUBA_PASSWORD: Aruba (demo o vero);
    - niente: l'Aruba finto, come prima."""
    if _sportello["chi"] is None:
        utente = os.environ.get("ARUBA_UTENTE") or ""
        password = os.environ.get("ARUBA_PASSWORD") or ""
        oa_token = (os.environ.get("OPENAPI_TOKEN") or "").strip()
        oa_email = (os.environ.get("OPENAPI_EMAIL") or "").strip()
        oa_chiave = (os.environ.get("OPENAPI_CHIAVE") or "").strip()
        if oa_token or (oa_email and oa_chiave):
            _sportello["chi"] = OpenapiProva(token=oa_token or None, email=oa_email, chiave=oa_chiave)
        elif utente and password:
            _sportello["chi"] = ArubaVero(utente, password, os.environ.get("ARUBA_AMBIENTE") or "demo")
        else:
            _sportello["chi"] = ArubaFinto()
    return _sportello["chi"]


def come_sta():
    """Per l'app, prima di fare una fattura: e' una prova o parte davvero?"""
    s = sportello()
    if isinstance(s, OpenapiProva):
        return {"pronto": True, "prova": True, "openapi": "prova"}
    return {"pronto": True, "prova": bool(s.prova),
            "aruba": "finto" if isinstance(s, ArubaFinto) else s.ambiente}
