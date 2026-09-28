"""La fattura elettronica: dall'app ad Aruba, e da Aruba lo stato.

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
import base64, json, os, random, string, threading, time
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


# ------------------------------------------------------------ chi risponde

_sportello = {"chi": None}


def sportello():
    """Aruba vero se sul server ci sono le credenziali, se no quello finto."""
    if _sportello["chi"] is None:
        utente = os.environ.get("ARUBA_UTENTE") or ""
        password = os.environ.get("ARUBA_PASSWORD") or ""
        if utente and password:
            _sportello["chi"] = ArubaVero(utente, password, os.environ.get("ARUBA_AMBIENTE") or "demo")
        else:
            _sportello["chi"] = ArubaFinto()
    return _sportello["chi"]


def come_sta():
    """Per l'app, prima di fare una fattura: e' una prova o parte davvero?"""
    s = sportello()
    return {"pronto": True, "prova": bool(s.prova),
            "aruba": "finto" if isinstance(s, ArubaFinto) else s.ambiente}
