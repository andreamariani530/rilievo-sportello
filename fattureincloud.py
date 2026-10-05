"""Il collegamento con Fatture in Cloud: la fattura parte dal programma dell'artigiano.

PERCHE' ESISTE
Molti artigiani hanno gia' Fatture in Cloud, con la loro numerazione e il loro
commercialista collegato. Per loro (4 ottobre 2026, piano in
`App preventivi artigiani/fatture-in-cloud/piano-collegamento.md`) la fattura fatta in
Rilievo parte dal LORO account: premono «Collega Fatture in Cloud» una volta, dicono si'
sul sito di Fatture in Cloud, e da li' in poi c'e' il tasto «Mandala dal tuo Fatture in
Cloud». Nessuna password passa da noi: Fatture in Cloud ci da' un permesso (OAuth) che
l'artigiano toglie quando vuole.

LE CHIAVI
Solo variabili d'ambiente del server, mai nei file:
  FIC_CLIENT_ID, FIC_CLIENT_SECRET  i due codici dell'app «Rilievo» (id app 24897)
  FIC_CHIAVE_CIFRA                  la chiave con cui si cifrano i permessi sul disco
  FIC_RITORNO (facoltativa)         l'indirizzo di ritorno; di base quello di Render
Senza FIC_CLIENT_ID e FIC_CLIENT_SECRET risponde un Fatture in Cloud FINTO (classe
Finto), che si comporta come quello vero: ogni risposta porta `prova: true` e i
collaudi girano senza rete. Con i due codici ma senza FIC_CHIAVE_CIFRA il collegamento
resta spento (`pronto: false`): i permessi in chiaro sul disco non ci vanno mai.

DOVE STANNO I PERMESSI
Un file per account in `archivio.CARTELLA/fic/<id_account>.json`, sul disco che
sopravvive ai riavvii. Dentro: la ditta scelta, e i due permessi cifrati (Fernet). Il
permesso corto vale 24 ore; quello lungo serve a rinnovarlo e vale un anno dall'ultimo
rinnovo. Il rinnovo ha un lucchetto per account: due fatture insieme rinnoverebbero due
volte, e il secondo rinnovo butterebbe via il primo.

LA NUMERAZIONE
La decide Fatture in Cloud, non Rilievo: l'artigiano ha gia' la sua serie li', e due
numerazioni parallele sarebbero un errore fiscale. Rilievo si annota il numero che torna.

Quello che arriva all'app e' sempre della stessa forma, finto o vero:
    manda   -> {"id": 123, "numero": "7", "stato": "inviata", "prova": bool}
    come_sta-> {"id": 123, "stato": "inviata" | "consegnata" | "non_consegnata" | "scartata"
                | "da_mandare", "motivo": "...", "codice": "...", "prova": bool}
"""
import base64, copy, hashlib, json, os, pathlib, re, secrets, threading, time
import urllib.error, urllib.parse, urllib.request

import archivio

BASE = "https://api-v2.fattureincloud.it"
RITORNO = "https://rilievo-sportello.onrender.com/fic/ritorno"
PERMESSI = "issued_documents.invoices:a settings:r entity.clients:r"
DURATA_STATE = 600            # l'indirizzo per collegarsi vale 10 minuti, una volta sola
MARGINE = 120                 # un permesso che scade fra meno di due minuti si rinnova prima
DURATA_CODICI = 300           # i codici IVA si rileggono ogni 5 minuti: se l'artigiano li corregge, li vediamo subito

# ei_status di Fatture in Cloud, detti come li capisce gia' l'app
STATI = {
    "attempt": "inviata", "sent": "inviata", "pending": "inviata", "processing": "inviata",
    "accepted": "consegnata", "manual_accepted": "consegnata", "no_response": "consegnata",
    "not_delivered": "non_consegnata",
    "error": "scartata", "discarded": "scartata", "rejected": "scartata", "manual_rejected": "scartata",
    "missing": "da_mandare", "not_sent": "da_mandare",
}
METODI = {"MP01", "MP02", "MP05", "MP08", "MP12"}   # contanti, assegno, bonifico, carta, RIBA


class Scaduto(Exception):
    """Il collegamento non vale piu': l'artigiano lo deve rifare."""


class NonCollegato(Exception):
    """Questo account non ha collegato Fatture in Cloud."""


class Rifiutata(Exception):
    """La fattura non parte: il motivo e' detto in chiaro, per l'artigiano."""


class NonRisponde(Exception):
    """Fatture in Cloud non ha risposto, o ha risposto con un errore suo."""


MSG_SCADUTO = "Il collegamento con Fatture in Cloud è scaduto. Ricollegalo da Impostazioni."
MSG_NON_COLLEGATO = "Fatture in Cloud non è collegato. Collegalo da Impostazioni."


# ---------------------------------------------------------------- configurazione

def _ambiente(nome):
    return (os.environ.get(nome) or "").strip()


def prova():
    """Senza i due codici dell'app si lavora col finto."""
    return not (_ambiente("FIC_CLIENT_ID") and _ambiente("FIC_CLIENT_SECRET"))


def pronto():
    """Il collegamento si puo' usare? Col vero serve anche la chiave per cifrare."""
    return prova() or bool(_ambiente("FIC_CHIAVE_CIFRA"))


def ritorno_url():
    return _ambiente("FIC_RITORNO") or RITORNO


def _fernet():
    from cryptography.fernet import Fernet
    seme = _ambiente("FIC_CHIAVE_CIFRA")
    if not seme:
        if not prova():
            raise NonRisponde("Manca FIC_CHIAVE_CIFRA: il collegamento resta spento.")
        seme = "rilievo-solo-per-il-finto"     # col finto non c'e' niente di vero da proteggere
    # qualunque stringa va bene: la chiave Fernet si ricava sempre allo stesso modo
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("rilievo-fic:" + seme).encode()).digest()))


def _cifra(t):
    return _fernet().encrypt(t.encode()).decode()


def _decifra(t):
    return _fernet().decrypt(t.encode()).decode()


# ---------------------------------------------------------------- il finto

class Finto:
    """Fatture in Cloud finto, con le stesse strade e le stesse risposte del vero.

    Sa anche fare le cose brutte, per provare i messaggi:
      - cliente con partita IVA 00000000000, o nome che comincia con «SCARTA»: lo SdI
        la scarta, col motivo detto da loro;
      - codice destinatario «VERIFI0»: il loro controllo del file trova un errore;
      - revoca(refresh): l'artigiano ha tolto l'app dal suo lato.
    Un permesso lungo usato una volta non vale piu', come nel vero.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.codici = {}           # code -> scadenza
        self.corti = {}            # access -> scadenza
        self.lunghi = set()        # refresh validi
        self.rinnovi = 0
        self.documenti = {}        # id -> documento
        self.prossimo = {}         # company -> numero
        self.ditte = [{"id": 1631991, "name": "Rilievo Prove", "type": "company"}]
        self.codici_iva = [
            {"id": 0, "value": 22, "description": "22%", "ei_type": "", "is_disabled": False},
            {"id": 3, "value": 10, "description": "10%", "ei_type": "", "is_disabled": False},
            {"id": 4, "value": 4, "description": "4%", "ei_type": "", "is_disabled": False},
            {"id": 21, "value": 0, "description": "Regime forfettario", "ei_type": "2.2", "is_disabled": False},
            {"id": 66, "value": 0, "description": "Escluso art. 15", "ei_type": "1", "is_disabled": False},
        ]
        self.durata = 86400

    # il passaggio dal sito di Fatture in Cloud: l'artigiano ha detto si'
    def autorizza(self):
        c = "c/" + secrets.token_urlsafe(16)
        self.codici[c] = time.time() + 60
        return c

    def revoca(self, refresh=None):
        with self._lock:
            if refresh:
                self.lunghi.discard(refresh)
            else:
                self.lunghi.clear()
            self.corti.clear()

    def _permessi(self):
        a, r = "a/" + secrets.token_urlsafe(24), "r/" + secrets.token_urlsafe(24)
        self.corti[a] = time.time() + self.durata
        self.lunghi.add(r)
        return {"token_type": "bearer", "access_token": a, "refresh_token": r, "expires_in": self.durata}

    def chiama(self, metodo, via, corpo=None, token=None):
        with self._lock:
            return self._chiama(metodo, via, corpo, token)

    def _chiama(self, metodo, via, corpo, token):
        corpo = corpo or {}
        via = via.split("?", 1)[0]
        if via == "/oauth/token" and metodo == "POST":
            if corpo.get("client_id") is None or corpo.get("client_secret") is None:
                return 400, {"error": "invalid_client"}
            if corpo.get("grant_type") == "authorization_code":
                if corpo.get("redirect_uri") != ritorno_url():
                    return 400, {"error": "invalid_request", "error_description": "redirect_uri"}
                if self.codici.pop(corpo.get("code"), 0) < time.time():
                    return 400, {"error": "invalid_grant"}
                return 200, self._permessi()
            if corpo.get("grant_type") == "refresh_token":
                r = corpo.get("refresh_token")
                if r not in self.lunghi:
                    return 400, {"error": "invalid_grant"}
                self.lunghi.discard(r)
                self.rinnovi += 1
                return 200, self._permessi()
            return 400, {"error": "unsupported_grant_type"}
        if self.corti.get(token or "", 0) < time.time():
            return 401, {"error": {"message": "Unauthorized"}}
        if via == "/user/companies":
            return 200, {"data": {"companies": copy.deepcopy(self.ditte)}}
        m = re.fullmatch(r"/c/(\d+)(/.*)", via)
        if not m or int(m.group(1)) not in [d["id"] for d in self.ditte]:
            return 404, {"error": {"message": "Not found"}}
        cid, resto = int(m.group(1)), m.group(2)
        if resto == "/info/vat_types":
            return 200, {"data": copy.deepcopy(self.codici_iva)}
        if resto == "/issued_documents/totals" and metodo == "POST":
            d = corpo.get("data") or {}
            netto = sum(float(r.get("qty", 1)) * float(r.get("net_price", 0)) for r in d.get("items_list") or [])
            dovuto = round(netto * 1.22 if any((r.get("vat") or {}).get("id") == 0 for r in d.get("items_list") or [])
                           else netto, 2)
            return 200, {"data": {"amount_net": round(netto, 2), "amount_due": dovuto,
                                  "stamp_duty": d.get("stamp_duty", 0)}}
        if resto == "/issued_documents" and metodo == "POST":
            d = copy.deepcopy(corpo.get("data") or {})
            if d.get("items_list") and d.get("payments_list"):
                _, t = self._chiama("POST", "/c/%d/issued_documents/totals" % cid, {"data": d}, token)
                if abs(sum(float(x.get("amount", 0)) for x in d["payments_list"]) - t["data"]["amount_due"]) > 0.005:
                    return 422, {"error": {"message": "Il totale dei pagamenti non corrisponde al totale da pagare."}}
            validi = {v["id"] for v in self.codici_iva}
            if d.get("type") != "invoice" or not d.get("items_list") or not (d.get("entity") or {}).get("name"):
                return 422, {"error": {"message": "Dati mancanti"}}
            if any((r.get("vat") or {}).get("id") not in validi for r in d["items_list"]):
                return 422, {"error": {"message": "Aliquota IVA non valida"}}
            n = self.prossimo.get(cid, 1)
            self.prossimo[cid] = n + 1
            did = 9000 + len(self.documenti) + 1
            d.update({"id": did, "number": n, "ei_status": "not_sent"})
            self.documenti[did] = d
            return 200, {"data": {"id": did, "number": n, "type": "invoice"}}
        m = re.fullmatch(r"/issued_documents/(\d+)(/e_invoice/(xml_verify|send|error_reason))?", resto)
        if not m or int(m.group(1)) not in self.documenti:
            return 404, {"error": {"message": "Not found"}}
        d, cosa = self.documenti[int(m.group(1))], m.group(3)
        ent = d.get("entity") or {}
        brutta = ent.get("vat_number") == "00000000000" or str(ent.get("name", "")).upper().startswith("SCARTA")
        if cosa is None and metodo == "DELETE":
            self.documenti.pop(d["id"], None)
            return 200, {}
        if cosa is None:
            if d["ei_status"] == "sent" and time.time() - d.get("_mandata", 0) > 2:
                d["ei_status"] = "rejected" if brutta else "accepted"
            return 200, {"data": {k: v for k, v in d.items() if not k.startswith("_")}}
        if cosa == "xml_verify":
            if ent.get("ei_code") == "VERIFI0":
                return 422, {"error": {"message": "Validation XML", "validation_result": [
                    "Il codice destinatario del cliente non è valido"]}}
            return 200, {"data": {"success": True}}
        if cosa == "send":
            d["ei_status"], d["_mandata"] = "sent", time.time()
            return 200, {"data": {"name": "IT01641790702_%05d.xml" % d["id"], "date": time.strftime("%Y-%m-%d")}}
        if cosa == "error_reason":
            if d["ei_status"] != "rejected":
                return 404, {"error": {"message": "Not found"}}
            return 200, {"data": {"reason": "Il codice fiscale del cliente risulta sbagliato.",
                                  "code": "00306", "date": time.strftime("%Y-%m-%d")}}
        return 404, {"error": {"message": "Not found"}}


FINTO = Finto()


# ---------------------------------------------------------------- la strada

def _sicuro(base):
    """I permessi vanno solo a Fatture in Cloud (o al portatile, nei collaudi)."""
    return base == BASE or re.fullmatch(r"http://127\.0\.0\.1:\d+", base or "") is not None


def _chiama(metodo, via, corpo=None, token=None):
    """(codice, json). Col finto senza rete; col vero a api-v2.fattureincloud.it."""
    if prova():
        return FINTO.chiama(metodo, via, corpo, token)
    base = _ambiente("FIC_BASE") or BASE
    if not _sicuro(base):
        raise NonRisponde("Indirizzo di Fatture in Cloud non ammesso.")
    dati = json.dumps(corpo).encode() if corpo is not None else None
    h = {"Accept": "application/json", "User-Agent": "Rilievo/1"}
    if dati is not None:
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + via, data=dati, method=metodo, headers=h)
    apri = urllib.request.build_opener(urllib.request.ProxyHandler({})) if base != BASE else urllib.request.build_opener()
    try:
        with apri.open(req, timeout=30) as r:
            testo = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(testo) if testo.strip() else {})
    except urllib.error.HTTPError as e:
        testo = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(testo) if testo.strip() else {}
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise NonRisponde("Fatture in Cloud non risponde (%s)." % type(e).__name__)


def _credenziali():
    if prova():
        return "finto", "finto"
    return _ambiente("FIC_CLIENT_ID"), _ambiente("FIC_CLIENT_SECRET")


# ---------------------------------------------------------------- il disco

def _cartella():
    d = pathlib.Path(archivio.CARTELLA) / "fic"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(account):
    if not re.fullmatch(r"[0-9a-f]{64}", str(account or "")):
        raise NonCollegato(MSG_NON_COLLEGATO)
    return _cartella() / (account + ".json")


def _leggi(account):
    f = _file(account)
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _scrivi(account, dati):
    f = _file(account)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, f)


def _salva_permessi(account, ditta, permessi, prima=None):
    dati = dict(prima or {})
    dati.update({"company_id": int(ditta["id"]), "ditta": str(ditta.get("name") or "")[:120],
                 "a": _cifra(permessi["access_token"]), "r": _cifra(permessi["refresh_token"]),
                 "scade": int(time.time()) + int(permessi.get("expires_in") or 86400),
                 "rinnovato": int(time.time()), "prova": prova()})
    dati.setdefault("collegato", int(time.time()))
    _scrivi(account, dati)
    return dati


# ---------------------------------------------------------------- collegare

_states = {}          # state -> (account, scadenza)
_scelte = {}          # gettone della scelta -> (account, ditte, permessi cifrati, scadenza)
_lucchetti = {}
_guardia = threading.Lock()
_codici_iva = {}      # company_id -> (quando, elenco)


def _pulisci(d):
    adesso = time.time()
    for k in [k for k, v in d.items() if v[-1] < adesso]:
        d.pop(k, None)


def indirizzo_collega(account):
    """L'indirizzo da aprire per dire si' su Fatture in Cloud."""
    _file(account)
    st = secrets.token_urlsafe(24)
    with _guardia:
        _pulisci(_states)
        _states[st] = (account, time.time() + DURATA_STATE)
    if prova():
        # col finto il si' e' gia' dato: si torna subito al nostro ritorno
        return "/fic/ritorno?" + urllib.parse.urlencode({"code": FINTO.autorizza(), "state": st})
    cid, _ = _credenziali()
    return BASE + "/oauth/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": ritorno_url(),
        "scope": PERMESSI, "state": st}, quote_via=urllib.parse.quote)


def _prendi_state(st):
    with _guardia:
        _pulisci(_states)
        v = _states.pop(str(st or ""), None)       # una volta sola
    return v[0] if v else None


def ritorno(code, state):
    """Torna ("ok", ditta) / ("scegli", gettone, ditte) / ("no", motivo)."""
    account = _prendi_state(state)
    if not account:
        return ("no", "Il collegamento è scaduto o è già stato usato. Riprova da Impostazioni.")
    if not code:
        return ("no", "Su Fatture in Cloud non è stato dato il permesso.")
    cid, segreto = _credenziali()
    codice, r = _chiama("POST", "/oauth/token", {"grant_type": "authorization_code", "client_id": cid,
                                                 "client_secret": segreto, "redirect_uri": ritorno_url(),
                                                 "code": code})
    if codice != 200 or not r.get("access_token") or not r.get("refresh_token"):
        return ("no", "Fatture in Cloud non ha dato il permesso. Riprova fra qualche minuto.")
    codice, d = _chiama("GET", "/user/companies", token=r["access_token"])
    ditte = [x for x in ((d.get("data") or {}).get("companies") or []) if isinstance(x, dict) and x.get("id")]
    if codice != 200 or not ditte:
        return ("no", "Su questo account di Fatture in Cloud non trovo nessuna ditta.")
    if len(ditte) == 1:
        dati = _salva_permessi(account, ditte[0], r)
        return ("ok", dati["ditta"])
    g = secrets.token_urlsafe(24)
    with _guardia:
        _pulisci(_scelte)
        _scelte[g] = (account, [{"id": x["id"], "name": x.get("name") or ""} for x in ditte],
                      {"a": _cifra(r["access_token"]), "r": _cifra(r["refresh_token"]),
                       "expires_in": r.get("expires_in")}, time.time() + DURATA_STATE)
    return ("scegli", g, [{"id": x["id"], "nome": x.get("name") or ""} for x in ditte])


def scegli(gettone, ditta_id):
    """L'artigiano con piu' ditte ha scelto quale collegare."""
    with _guardia:
        _pulisci(_scelte)
        v = _scelte.pop(str(gettone or ""), None)
    if not v:
        return ("no", "La scelta è scaduta. Riprova da Impostazioni.")
    account, ditte, p, _ = v
    ditta = next((x for x in ditte if str(x["id"]) == str(ditta_id)), None)
    if not ditta:
        return ("no", "Questa ditta non è fra le tue su Fatture in Cloud.")
    dati = _salva_permessi(account, ditta, {"access_token": _decifra(p["a"]), "refresh_token": _decifra(p["r"]),
                                            "expires_in": p.get("expires_in")})
    return ("ok", dati["ditta"])


def stato(account):
    """Per l'app: collegato si' o no, e a che ditta."""
    d = _leggi(account)
    return {"pronto": pronto(), "prova": prova(), "collegato": bool(d),
            "ditta": (d or {}).get("ditta", ""), "dal": (d or {}).get("collegato", 0)}


def scollega(account):
    """Cancella i permessi dal disco. Dal loro lato si toglie in «App collegate»."""
    f = _file(account)
    with _lucchetto(account):
        f.unlink(missing_ok=True)
    return True


# ---------------------------------------------------------------- il permesso

def _lucchetto(account):
    with _guardia:
        return _lucchetti.setdefault(account, threading.Lock())


def permesso(account):
    """(company_id, permesso valido). Rinnova da solo; se non si puo', il file se ne va."""
    with _lucchetto(account):
        d = _leggi(account)
        if not d:
            raise NonCollegato(MSG_NON_COLLEGATO)
        if d.get("scade", 0) - MARGINE > time.time():
            try:
                return d["company_id"], _decifra(d["a"])
            except Exception:                           # noqa: BLE001 (chiave cambiata)
                _file(account).unlink(missing_ok=True)
                raise Scaduto(MSG_SCADUTO)
        try:
            lungo = _decifra(d["r"])
        except Exception:                               # noqa: BLE001
            _file(account).unlink(missing_ok=True)
            raise Scaduto(MSG_SCADUTO)
        cid, segreto = _credenziali()
        codice, r = _chiama("POST", "/oauth/token", {"grant_type": "refresh_token", "client_id": cid,
                                                     "client_secret": segreto, "refresh_token": lungo})
        if codice in (400, 401, 403) or (codice == 200 and not r.get("access_token")):
            # tolta dal loro lato, o passato piu' di un anno: si ricollega
            _file(account).unlink(missing_ok=True)
            raise Scaduto(MSG_SCADUTO)
        if codice != 200:
            raise NonRisponde("Fatture in Cloud non risponde (%s)." % codice)
        r.setdefault("refresh_token", lungo)
        d = _salva_permessi(account, {"id": d["company_id"], "name": d.get("ditta")}, r, prima=d)
        return d["company_id"], r["access_token"]


def _con_permesso(account, metodo, via, corpo=None):
    """Una chiamata alla ditta collegata. Un 401 vuol dire permesso scaduto prima del
    previsto: si rinnova una volta e si riprova."""
    cid, tok = permesso(account)
    codice, r = _chiama(metodo, "/c/%d%s" % (cid, via), corpo, tok)
    if codice == 401:
        with _lucchetto(account):
            d = _leggi(account)
            if d:
                d["scade"] = 0
                _scrivi(account, d)
        cid, tok = permesso(account)
        codice, r = _chiama(metodo, "/c/%d%s" % (cid, via), corpo, tok)
        if codice == 401:
            _file(account).unlink(missing_ok=True)
            raise Scaduto(MSG_SCADUTO)
    return codice, r


def codici_iva(account):
    cid = (_leggi(account) or {}).get("company_id")
    if not cid:
        raise NonCollegato(MSG_NON_COLLEGATO)
    quando, elenco = _codici_iva.get(cid, (0, None))
    if elenco is not None and time.time() - quando < DURATA_CODICI:
        return elenco
    codice, r = _con_permesso(account, "GET", "/info/vat_types")
    if codice != 200 or not isinstance(r.get("data"), list):
        raise NonRisponde("Non riesco a leggere le aliquote IVA da Fatture in Cloud (%s)." % codice)
    _codici_iva[cid] = (time.time(), r["data"])
    return r["data"]


# ---------------------------------------------------------------- la traduzione

def _numero(x, dec=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise Rifiutata("Nella fattura c'è un importo scritto male.")
    if v != v or v in (float("inf"), float("-inf")):
        raise Rifiutata("Nella fattura c'è un importo scritto male.")
    return round(v, dec)


def _testo(x, quanto):
    return re.sub(r"\s+", " ", str(x or "")).strip()[:quanto]


def _natura(v):
    """«N2.2», «2.2», «N2_2» sono la stessa natura."""
    return re.sub(r"[^0-9.]", "", str(v or "").replace("_", ".")).strip(".")


def codice_iva(elenco, aliquota, forfettario=False, natura="N2.2"):
    """Il codice IVA della ditta che va su questa riga, o il motivo per cui non c'e'."""
    al = _numero(aliquota)
    attivi = [v for v in elenco or [] if isinstance(v, dict) and not v.get("is_disabled")]
    if forfettario or al == 0:
        voluta = _natura("N2.2" if forfettario else (natura or "N2.2"))
        for v in attivi:
            if _numero(v.get("value", -1)) == 0 and _natura(v.get("ei_type")) == voluta:
                return v["id"]
        # nel registro del server, per capire cosa manda davvero Fatture in Cloud (niente dati personali)
        # ripiego: Fatture in Cloud a volte lascia vuota la natura ma la scrive nella descrizione
        for v in attivi:
            testo = " ".join(str(v.get(k) or "") for k in ("description", "notes", "ei_description")).lower()
            if _numero(v.get("value", -1)) == 0 and not _natura(v.get("ei_type")) and forfettario and (
                    "forfett" in testo or "190/2014" in testo or "190 del 23" in testo or voluta in _natura(testo)):
                return v["id"]
        print("  aliquote dalla ditta:", [(v.get("id"), v.get("value"), v.get("ei_type"), v.get("is_disabled"),
                                           str(v.get("description") or "")[:40])
                                          for v in elenco or [] if isinstance(v, dict)][:40], flush=True)
        spenta = any(isinstance(v, dict) and v.get("is_disabled") and _numero(v.get("value", -1)) == 0
                     and _natura(v.get("ei_type")) == voluta for v in elenco or [])
        if spenta:
            raise Rifiutata("Nel tuo Fatture in Cloud l'aliquota a 0%% con natura N%s c'è, ma è spenta. "
                            "Accendila in Impostazioni, Aliquote IVA, e riprova." % voluta)
        raise Rifiutata("Nel tuo Fatture in Cloud manca l'aliquota a 0%% con natura N%s%s. "
                        "Aggiungila in Impostazioni, Aliquote IVA: valore 0%%, natura «N%s», "
                        "e riprova." % (voluta, " (regime forfettario)" if forfettario else "", voluta))
    for v in attivi:
        if _numero(v.get("value", -1)) == al and not _natura(v.get("ei_type")):
            return v["id"]
    raise Rifiutata("Nel tuo Fatture in Cloud manca l'aliquota IVA al %s%%. "
                    "Aggiungila fra le aliquote IVA del tuo Fatture in Cloud e rimanda la fattura."
                    % (("%g" % al).replace(".", ",")))


def piva_valida(p):
    """Partita IVA italiana: 11 cifre e l'ultima e' il controllo (la stessa regola dello SdI)."""
    if not re.fullmatch(r"\d{11}", p or "") or p == "0" * 11:
        return False
    tot = 0
    for i, c in enumerate(p[:10]):
        n = int(c)
        if i % 2:
            n *= 2
            n = n - 9 if n > 9 else n
        tot += n
    return (10 - tot % 10) % 10 == int(p[10])


def traduci(f, elenco):
    """La fattura dell'app nei campi di Fatture in Cloud.

    f = {data, forfettario, natura, cliente: {nome, piva, cf, indirizzo, cap, comune, provincia,
         sdi, pec}, righe: [{descrizione, quantita, unita, prezzo, aliquota}],
         sconti: [{aliquota, meno}], totale, bollo, pagamento: {metodo, scadenza, iban}}
    Niente numero: lo decide Fatture in Cloud."""
    if not isinstance(f, dict):
        raise Rifiutata("Manca la fattura.")
    data = str(f.get("data") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
        raise Rifiutata("Manca la data della fattura.")
    forf = bool(f.get("forfettario"))
    cl = f.get("cliente") if isinstance(f.get("cliente"), dict) else {}
    nome = _testo(cl.get("nome"), 80)
    piva = re.sub(r"\s", "", str(cl.get("piva") or "")).upper().removeprefix("IT")
    cf = re.sub(r"\s", "", str(cl.get("cf") or "")).upper()
    if not nome:
        raise Rifiutata("Manca il nome del cliente.")
    if not piva and not cf:
        raise Rifiutata("Manca il codice fiscale o la partita IVA del cliente.")
    if piva and not piva_valida(piva):
        raise Rifiutata("La partita IVA di %s (%s) non è valida: controllala nella scheda del cliente. "
                        "Se è un privato, lascia vuota la partita IVA e metti solo il codice fiscale." % (nome, piva))
    if not (cl.get("indirizzo") and re.fullmatch(r"\d{5}", str(cl.get("cap") or "")) and cl.get("comune")):
        raise Rifiutata("Manca l'indirizzo del cliente, con CAP e comune.")
    sdi = str(cl.get("sdi") or "").strip().upper()
    sdi = sdi if re.fullmatch(r"[A-Z0-9]{7}", sdi) else "0000000"
    entita = {"name": nome, "tax_code": cf,
              "address_street": _testo(cl.get("indirizzo"), 60), "address_postal_code": str(cl["cap"]),
              "address_city": _testo(cl.get("comune"), 60),
              "address_province": str(cl.get("provincia") or "").strip().upper()[:2],
              "country": "Italia", "e_invoice": True, "ei_code": sdi}
    if piva:
        entita["vat_number"] = piva
    pec = str(cl.get("pec") or "").strip()
    if sdi == "0000000" and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", pec):
        entita["certified_email"] = pec

    righe = [r for r in (f.get("righe") or []) if isinstance(r, dict)]
    if not righe:
        raise Rifiutata("La fattura non ha nessuna riga.")
    voci = []
    for r in righe[:200]:
        al = 0 if forf else _numero(r.get("aliquota"))
        voce = {"name": _testo(r.get("descrizione"), 1000) or "Lavoro",
                "qty": _numero(r.get("quantita") if r.get("quantita") is not None else 1, 8),
                "net_price": _numero(r.get("prezzo"), 8),
                "vat": {"id": codice_iva(elenco, al, forf, f.get("natura"))}}
        u = _testo(r.get("unita"), 10)
        if u:
            voce["measure"] = u
        voci.append(voce)
    for s in (f.get("sconti") or [])[:10]:
        if isinstance(s, dict) and _numero(s.get("meno")) > 0:
            al = 0 if forf else _numero(s.get("aliquota"))
            voci.append({"name": "Sconto", "qty": 1, "net_price": -_numero(s.get("meno")),
                         "vat": {"id": codice_iva(elenco, al, forf, f.get("natura"))}})

    pg = f.get("pagamento") if isinstance(f.get("pagamento"), dict) else {}
    metodo = str(pg.get("metodo") or "MP05").upper()
    metodo = metodo if metodo in METODI else "MP05"
    scad = str(pg.get("scadenza") or "")
    scad = scad if re.fullmatch(r"\d{4}-\d{2}-\d{2}", scad) and scad >= data else data
    totale = _numero(f.get("totale"))
    if totale <= 0:
        raise Rifiutata("Il totale della fattura è zero: non si può mandare.")
    doc = {"type": "invoice", "e_invoice": True, "date": data, "entity": entita,
           "items_list": voci,
           "payments_list": [{"amount": totale, "due_date": scad, "status": "not_paid"}],
           "ei_data": {"payment_method": metodo}}
    iban = re.sub(r"\s", "", str(pg.get("iban") or "")).upper()
    if metodo == "MP05" and re.fullmatch(r"IT\d{2}[A-Z]\d{10}[0-9A-Z]{12}", iban):
        doc["ei_data"]["bank_iban"] = iban
    if forf and f.get("bollo"):
        # il bollo da 2 euro: Fatture in Cloud lo somma a quello che il cliente deve pagare,
        # quindi la rata deve contenerlo (da riprovare dal vivo col collaudo 10)
        doc["stamp_duty"] = 2
        doc["payments_list"][0]["amount"] = round(totale + 2, 2)
    return doc


def _testi_dentro(x, fuori, prof=0):
    """Raccoglie le frasi d'errore ovunque stiano: liste, dizionari, testo."""
    if prof > 5 or len(fuori) >= 4:
        return
    if isinstance(x, str):
        t = re.sub(r"\s+", " ", x).strip()
        if t and t not in fuori:
            fuori.append(t)
    elif isinstance(x, list):
        for y in x:
            _testi_dentro(y, fuori, prof + 1)
    elif isinstance(x, dict):
        for v in x.values():          # xml_errors, errors, messages...: si guarda tutto
            _testi_dentro(v, fuori, prof + 1)


def _errore_loro(r):
    e = (r or {}).get("error") if isinstance(r, dict) else None
    if e is None and isinstance(r, dict):
        e = r.get("data") or r
    if isinstance(e, dict):
        dettagli = []
        _testi_dentro(e.get("validation_result"), dettagli)
        _testi_dentro(e.get("errors"), dettagli)
        _testi_dentro(e.get("details") or e.get("detail"), dettagli)
        if not dettagli:
            _testi_dentro(e, dettagli)
        if dettagli:
            print("  errore da Fatture in Cloud:", json.dumps(r, ensure_ascii=False)[:1500], flush=True)
            return "; ".join(dettagli[:3])[:400]
        return str(e.get("message") or "")[:300]
    return str(e or "")[:300]


# ---------------------------------------------------------------- mandare

def _rata_giusta(account, doc):
    """La rata deve essere uguale al «totale da pagare» che calcola Fatture in Cloud (col bollo o no,
    secondo le impostazioni della ditta). Se lo chiediamo a loro, non sbagliamo mai di 2 euro."""
    codice, t = _con_permesso(account, "POST", "/issued_documents/totals", {"data": doc})
    dovuto = (t.get("data") or {}).get("amount_due") if codice == 200 and isinstance(t, dict) else None
    try:
        dovuto = round(float(dovuto), 2)
    except (TypeError, ValueError):
        return doc
    nostro = doc["payments_list"][0]["amount"]
    if abs(dovuto - nostro) > 2.005:          # piu' del bollo: qualcosa non torna, meglio fermarsi
        raise Rifiutata("Per Fatture in Cloud il totale è %s euro, per Rilievo %s: controlla le righe e riprova."
                        % (("%.2f" % dovuto).replace(".", ","), ("%.2f" % nostro).replace(".", ",")))
    doc["payments_list"][0]["amount"] = dovuto
    return doc


def manda(account, f):
    """Crea la fattura nel Fatture in Cloud dell'artigiano, la fa controllare, la manda."""
    doc = _rata_giusta(account, traduci(f, codici_iva(account)))
    codice, r = _con_permesso(account, "POST", "/issued_documents", {"data": doc})
    if codice == 422:
        raise Rifiutata("Fatture in Cloud non accetta la fattura: %s." % (_errore_loro(r) or "un dato non torna"))
    if codice != 200 or not (r.get("data") or {}).get("id"):
        raise NonRisponde("Fatture in Cloud non ha creato la fattura (%s)." % codice)
    did, numero = r["data"]["id"], r["data"].get("number")
    num = str(numero) + str(r["data"].get("numeration") or "") if numero is not None else ""
    codice, v = _con_permesso(account, "GET", "/issued_documents/%d/e_invoice/xml_verify" % did)
    if codice != 200 or (v.get("data") or {}).get("success") is False:
        # il loro controllo ha trovato un errore: non si manda, e la bozza si toglie
        # perche' non resti nel suo Fatture in Cloud un numero mai partito
        _con_permesso(account, "DELETE", "/issued_documents/%d" % did)
        raise Rifiutata("Il controllo di Fatture in Cloud ha trovato un errore: %s. Correggi e rimandala."
                        % (_errore_loro(v) or "un dato non torna"))
    codice, s = _con_permesso(account, "POST", "/issued_documents/%d/e_invoice/send" % did, {"data": {}})
    if codice != 200:
        motivo = _errore_loro(s)
        raise Rifiutata("La fattura n. %s è nel tuo Fatture in Cloud ma non è partita%s. "
                        "Puoi mandarla da lì, o riprovare." % (num or did, (": " + motivo) if motivo else ""))
    return {"id": did, "numero": num, "stato": "inviata", "prova": prova()}


def controlla(account, f):
    """Solo per chi prova: crea la fattura e la fa controllare a Fatture in Cloud, ma NON la manda.
    Se il controllo passa, la bozza resta nel suo Fatture in Cloud da guardare; se no si toglie."""
    doc = _rata_giusta(account, traduci(f, codici_iva(account)))
    codice, r = _con_permesso(account, "POST", "/issued_documents", {"data": doc})
    if codice == 422:
        raise Rifiutata("Fatture in Cloud non accetta la fattura: %s." % (_errore_loro(r) or "un dato non torna"))
    if codice != 200 or not (r.get("data") or {}).get("id"):
        raise NonRisponde("Fatture in Cloud non ha creato la fattura (%s)." % codice)
    did, numero = r["data"]["id"], r["data"].get("number")
    num = str(numero) + str(r["data"].get("numeration") or "") if numero is not None else ""
    codice, v = _con_permesso(account, "GET", "/issued_documents/%d/e_invoice/xml_verify" % did)
    if codice != 200 or (v.get("data") or {}).get("success") is False:
        _con_permesso(account, "DELETE", "/issued_documents/%d" % did)
        raise Rifiutata("Il controllo di Fatture in Cloud ha trovato un errore: %s."
                        % (_errore_loro(v) or "un dato non torna"))
    return {"id": did, "numero": num, "controllo": "passato", "mandata": False, "prova": prova()}


def solo_controllo(mail):
    """Chi vede il tasto «Solo controllo»: le mail in FIC_SOLO_CONTROLLO (separate da virgola)."""
    lista = os.environ.get("FIC_SOLO_CONTROLLO", "andreamariani530a@gmail.com")
    return str(mail or "").strip().lower() in {x.strip().lower() for x in lista.split(",") if x.strip()}


def come_sta(account, did):
    try:
        did = int(did)
    except (TypeError, ValueError):
        raise Rifiutata("Questa fattura non la trovo.")
    codice, r = _con_permesso(account, "GET", "/issued_documents/%d?fieldset=detailed" % did)
    if codice == 404:
        raise Rifiutata("Questa fattura non la trovo nel tuo Fatture in Cloud: forse è stata cancellata.")
    if codice != 200:
        raise NonRisponde("Fatture in Cloud non risponde (%s)." % codice)
    ei = str((r.get("data") or {}).get("ei_status") or "")
    st = STATI.get(ei, "inviata")
    fuori = {"id": did, "stato": st, "prova": prova()}
    if st == "scartata":
        codice, e = _con_permesso(account, "GET", "/issued_documents/%d/e_invoice/error_reason" % did)
        d = e.get("data") if codice == 200 and isinstance(e.get("data"), dict) else {}
        fuori["motivo"] = _testo(d.get("reason"), 400) or "Lo SdI l'ha scartata. Guarda il motivo nel tuo Fatture in Cloud."
        fuori["codice"] = _testo(d.get("code"), 10)
    return fuori
