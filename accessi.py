#!/usr/bin/env python3
"""Gli accessi: mail e password, come in ogni app.

PERCHE' ESISTE
Il 3 ottobre 2026 sera Andrea ha scelto: «sì, mail e password come tutti». Prima la
copia del lavoro si ritrovava con una chiave di 16 segni che aveva solo il telefono:
un giardiniere non se la ricorda, e persa quella era perso tutto. Adesso:
  - il primo giorno l'artigiano mette la sua mail e sceglie una password;
  - sul telefono nuovo entra con mail e password e ritrova tutto;
  - se la dimentica, «Password dimenticata?» gli manda una mail per sceglierne una nuova.
Il prezzo, detto ad Andrea e scritto nell'informativa: il lavoro lo teniamo noi sul
server, protetto, e non lo guardiamo; non e' piu' chiuso con una chiave che abbiamo solo noi.

COSA SI CONSERVA, E COSA NO
  - la password mai: solo il risultato di scrypt con un sale suo;
  - i gettoni di accesso (quello che il telefono tiene in tasca dopo essere entrato) mai:
    solo la loro impronta sha256. Chi legge il disco non entra al posto di nessuno;
  - il gettone per rimettere la password vale un'ora e una volta sola.
Un file per account, in `RILIEVO_ARCHIVIO/accessi/`, col nome dell'impronta della mail.

I TENTATIVI
Dieci password sbagliate in un quarto d'ora per la stessa mail, o cinquanta dallo stesso
indirizzo di rete, e per un po' si risponde «aspetta». Cosi' nessuno prova le password
una dopo l'altra.
"""
import hashlib, hmac, json, os, pathlib, re, secrets, threading, time

import archivio

_chiave = threading.RLock()
_tentativi = {}                     # "mail:..." o "ip:..." -> [istanti degli sbagli]
DURATA_RESET = 3600
MAX_SESSIONI = 20


# ---- l'indirizzo di rete vero di chi chiede (14/11/2026) -----------------------
# Su Render davanti al server c'e' Cloudflare. X-Forwarded-For lo scrive anche chi
# chiede: la sua PRIMA voce si puo' inventare, e cosi' il blocco dopo troppi tentativi
# si aggirava cambiando un'intestazione. CF-Connecting-IP e True-Client-IP invece li
# mette Cloudflare e li riscrive se qualcuno li manda da fuori: ci si fida solo di quelli.
# In locale (niente Cloudflare) vale l'indirizzo della connessione.
_IP_BUONO = re.compile(r"[0-9A-Fa-f:.]{3,45}")


def ip_vero(intestazioni, indirizzo=""):
    """Torna (ip, da dove l'ho preso)."""
    for nome in ("CF-Connecting-IP", "True-Client-IP"):
        v = str((intestazioni.get(nome) if intestazioni else "") or "").strip()
        if v and _IP_BUONO.fullmatch(v):
            return v, nome.lower()
    if intestazioni and intestazioni.get("X-Forwarded-For"):
        # ripiego (come prima del 14/11): se un giorno Cloudflare non ci fosse piu'
        v = str(intestazioni.get("X-Forwarded-For")).split(",")[0].strip()
        if _IP_BUONO.fullmatch(v):
            return v, "x-forwarded-for"
    return str(indirizzo or "").strip(), "connessione"


def _cartella():
    d = pathlib.Path(archivio.CARTELLA) / "accessi"
    d.mkdir(parents=True, exist_ok=True)
    return d


def mail_pulita(m):
    m = str(m or "").strip().lower()
    return m if re.fullmatch(r"[^\s@<>\"']{1,64}@[^\s@<>\"']{1,190}\.[a-z]{2,24}", m) else ""


def id_account(mail):
    """Il nome dell'account sul disco e nella copia: 64 cifre esadecimali."""
    return hashlib.sha256(("rilievo-account:" + mail).encode()).hexdigest()


def _file(mail):
    return _cartella() / (id_account(mail) + ".json")


def _leggi(mail):
    f = _file(mail)
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:                       # noqa: BLE001
        return None


def _scrivi(mail, dati):
    f = _file(mail)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def _impasta(password, sale):
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(sale),
                          n=2 ** 14, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32).hex()


def _impronta(gettone):
    return hashlib.sha256(("rilievo-gettone:" + str(gettone or "")).encode()).hexdigest()


def password_buona(p):
    p = str(p or "")
    if len(p) < 8:
        return "La password deve avere almeno 8 caratteri."
    if len(p) > 200:
        return "La password è troppo lunga."
    return ""


def _troppi(*chiavi, limite=(10, 50)):
    adesso = time.time()
    for k, massimo in zip(chiavi, limite):
        _tentativi[k] = [t for t in _tentativi.get(k, []) if adesso - t < 900]
        if len(_tentativi[k]) >= massimo:
            return True
    return False


def _sbagliato(*chiavi):
    for k in chiavi:
        _tentativi.setdefault(k, []).append(time.time())


def _nuova_sessione(conto, da):
    g = secrets.token_urlsafe(32)
    sess = conto.setdefault("sessioni", {})
    sess[_impronta(g)] = {"creata": int(time.time()), "da": str(da or "")[:60]}
    # le piu' vecchie se ne vanno: nessuno ha venti telefoni
    for vecchia in sorted(sess, key=lambda k: sess[k]["creata"])[:-MAX_SESSIONI]:
        sess.pop(vecchia, None)
    return g


# 14/11/2026: prima le registrazioni non avevano nessun limite. Ognuna scrive un file sul
# disco (1 GB) e fa partire la mail di benvenuto da info@: un programma poteva riempire il
# disco o usare la nostra posta per scrivere a indirizzi a caso. Un artigiano vero si
# registra una volta: 5 account l'ora dallo stesso indirizzo di rete e 60 l'ora in tutto
# bastano anche a una fiera con tutti sulla stessa rete.
REGISTRAZIONI_PER_IP = 5
REGISTRAZIONI_IN_TUTTO = 60
_registrazioni = {}                 # ip -> [istanti], "*" -> [istanti]


def _registrazioni_troppe(ip):
    adesso = time.time()
    for k, massimo in ((ip or "?", REGISTRAZIONI_PER_IP), ("*", REGISTRAZIONI_IN_TUTTO)):
        _registrazioni[k] = [t for t in _registrazioni.get(k, []) if adesso - t < 3600]
        if len(_registrazioni[k]) >= massimo:
            return True
    for vecchio in [k for k, v in _registrazioni.items() if not v]:
        _registrazioni.pop(vecchio, None)
    return False


def registra(mail, password, da="", ip=""):
    """Torna (gettone, None) oppure (None, motivo, codice)."""
    m = mail_pulita(mail)
    if not m:
        return None, "Questa mail non sembra scritta bene.", 400
    no = password_buona(password)
    if no:
        return None, no, 400
    with _chiave:
        if _leggi(m):
            return None, "Con questa mail c'è già un accesso. Entra con la tua password.", 409
        if _registrazioni_troppe(ip):
            return None, "Troppe registrazioni nuove da questa rete. Riprova fra un'ora.", 429
        for k in (ip or "?", "*"):
            _registrazioni.setdefault(k, []).append(time.time())
        sale = secrets.token_hex(16)
        conto = {"mail": m, "sale": sale, "password": _impasta(password, sale),
                 "creato": int(time.time()), "sessioni": {}, "reset": None}
        g = _nuova_sessione(conto, da)
        _scrivi(m, conto)
    return g, None, 200


def entra(mail, password, ip="", da=""):
    m = mail_pulita(mail)
    if not m:
        return None, "Questa mail non sembra scritta bene.", 400
    with _chiave:
        if _troppi("mail:" + m, "ip:" + ip):
            return None, "Troppi tentativi. Aspetta un quarto d'ora, oppure usa «Password dimenticata?».", 429
        conto = _leggi(m)
        giusta = conto and hmac.compare_digest(_impasta(str(password or ""), conto["sale"]), conto["password"])
        if not giusta:
            _sbagliato("mail:" + m, "ip:" + ip)
            return None, "Mail o password sbagliate.", 401
        g = _nuova_sessione(conto, da)
        _scrivi(m, conto)
    return g, None, 200


def chi_e(mail, gettone):
    """La mail se il gettone e' buono per quella mail, se no stringa vuota."""
    m = mail_pulita(mail)
    if not m or not gettone:
        return ""
    conto = _leggi(m)
    if conto and _impronta(gettone) in (conto.get("sessioni") or {}):
        return m
    return ""


def creato(mail):
    """Quando e' nato l'account (secondi), per contare i giorni della prova."""
    c = _leggi(mail_pulita(mail))
    return int((c or {}).get("creato") or 0)


def esci(mail, gettone):
    m = mail_pulita(mail)
    with _chiave:
        conto = _leggi(m) if m else None
        if conto and (conto.get("sessioni") or {}).pop(_impronta(gettone), None):
            _scrivi(m, conto)
            return True
    return False


def chiedi_reset(mail, ip=""):
    """Torna il gettone per la mail di reset, oppure None (mail che non c'e', o troppi)."""
    m = mail_pulita(mail)
    if not m:
        return None, m
    with _chiave:
        if _troppi("reset:" + m, "resetip:" + ip, limite=(3, 20)):
            return None, m
        _sbagliato("reset:" + m, "resetip:" + ip)
        conto = _leggi(m)
        if not conto:
            return None, m
        g = secrets.token_urlsafe(24)
        conto["reset"] = {"impronta": _impronta(g), "scade": int(time.time()) + DURATA_RESET}
        _scrivi(m, conto)
    return g, m


def nuova_password(mail, gettone_reset, password, da=""):
    m = mail_pulita(mail)
    no = password_buona(password)
    if no:
        return None, no, 400
    with _chiave:
        conto = _leggi(m) if m else None
        r = (conto or {}).get("reset") or {}
        if (not conto or not r or time.time() > r.get("scade", 0)
                or not hmac.compare_digest(r.get("impronta", ""), _impronta(gettone_reset))):
            return None, "Il link per cambiare la password è scaduto o è già stato usato. Chiedine uno nuovo.", 400
        conto["sale"] = secrets.token_hex(16)
        conto["password"] = _impasta(password, conto["sale"])
        conto["reset"] = None
        conto["sessioni"] = {}             # chi era dentro con la password vecchia, esce
        g = _nuova_sessione(conto, da)
        _scrivi(m, conto)
    return g, None, 200


def quanti():
    return {"account": len(list(_cartella().glob("*.json")))}
