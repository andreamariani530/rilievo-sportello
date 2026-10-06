import os
#!/usr/bin/env python3
"""Gli accessi: mail e password, come in Rilievo.

COSA SI CONSERVA
  - la password mai: solo il risultato di scrypt con un sale suo;
  - i gettoni di accesso (quello che il telefono tiene dopo essere entrato) mai:
    solo la loro impronta sha256. Chi legge il disco non entra al posto di nessuno.
Un file per account in `archivio/accessi/`, col nome dell'impronta della mail.

I TENTATIVI
Dieci password sbagliate in un quarto d'ora per la stessa mail, o cinquanta dallo
stesso indirizzo di rete, e per un po' si risponde «aspetta».

Niente «password dimenticata» per ora: l'app e' per Andrea e il server non manda
mail. Se serve, si cancella il suo file in accessi/ e si rientra da capo (i progressi
restano, sono in un'altra cartella con lo stesso nome).
"""
import hashlib, hmac, re, secrets, threading, time

import archivio

_chiave = threading.RLock()
_tentativi = {}
MAX_SESSIONI = 20


def mail_pulita(m):
    m = str(m or "").strip().lower()
    return m if re.fullmatch(r"[^\s@<>\"']{1,64}@[^\s@<>\"']{1,190}\.[a-z]{2,24}", m) else ""


def id_account(mail):
    return hashlib.sha256(("inglese-account:" + mail).encode()).hexdigest()


def _file(mail):
    return archivio.cartella("accessi") / (id_account(mail) + ".json")


def _leggi(mail):
    return archivio.leggi_json(_file(mail))


def _scrivi(mail, dati):
    archivio.scrivi_json(_file(mail), dati)


def _impasta(password, sale):
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(sale),
                          n=2 ** 14, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32).hex()


def _impronta(gettone):
    return hashlib.sha256(("inglese-gettone:" + str(gettone or "")).encode()).hexdigest()


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


def _nuova_sessione(conto):
    g = secrets.token_urlsafe(32)
    sess = conto.setdefault("sessioni", {})
    sess[_impronta(g)] = {"creata": int(time.time())}
    for vecchia in sorted(sess, key=lambda k: sess[k]["creata"])[:-MAX_SESSIONI]:
        sess.pop(vecchia, None)
    return g


def registra(mail, password):
    """Torna (gettone, None, 200) oppure (None, motivo, codice)."""
    m = mail_pulita(mail)
    if not m:
        return None, "Questa mail non sembra scritta bene.", 400
    no = password_buona(password)
    if no:
        return None, no, 400
    # Server pubblico: solo le mail scritte in INGLESE_MAIL_AMMESSE possono aprire un accesso,
    # cosi' nessun estraneo usa l'insegnante (e la sua spesa). Vuota = tutti (solo in locale).
    ammesse = {mail_pulita(x) for x in (os.environ.get("INGLESE_MAIL_AMMESSE") or "").split(",") if x.strip()}
    if ammesse and m not in ammesse:
        return None, "Questa app è privata: con questa mail non si può aprire un accesso.", 403
    with _chiave:
        if _leggi(m):
            return None, "Con questa mail c'è già un accesso. Entra con la tua password.", 409
        sale = secrets.token_hex(16)
        conto = {"mail": m, "sale": sale, "password": _impasta(password, sale),
                 "creato": int(time.time()), "sessioni": {}}
        g = _nuova_sessione(conto)
        _scrivi(m, conto)
    return g, None, 200


def entra(mail, password, ip=""):
    m = mail_pulita(mail)
    if not m:
        return None, "Questa mail non sembra scritta bene.", 400
    with _chiave:
        if _troppi("mail:" + m, "ip:" + ip):
            return None, "Troppi tentativi. Aspetta un quarto d'ora.", 429
        conto = _leggi(m)
        giusta = conto and hmac.compare_digest(_impasta(str(password or ""), conto["sale"]),
                                               conto["password"])
        if not conto:
            # sul piano gratuito di Render il disco si svuota ai riavvii: l'app, sentendo
            # questo, ricrea l'accesso con la stessa mail e password e rimanda i progressi
            _sbagliato("mail:" + m, "ip:" + ip)
            return None, "NESSUN_ACCOUNT", 401
        if not giusta:
            _sbagliato("mail:" + m, "ip:" + ip)
            return None, "Mail o password sbagliate.", 401
        g = _nuova_sessione(conto)
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


def esci(mail, gettone):
    m = mail_pulita(mail)
    with _chiave:
        conto = _leggi(m) if m else None
        if conto and (conto.get("sessioni") or {}).pop(_impronta(gettone), None):
            _scrivi(m, conto)
            return True
    return False
