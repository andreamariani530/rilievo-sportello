#!/usr/bin/env python3
"""Chi arriva dal sito chiede l'accesso, e Andrea lo fa entrare.

Andrea, 1/11/2026: «quando chiedi l'accesso ti fara' mettere una mail, un numero di
telefono, un modo dove mandarglielo, e da li' poi avra' il suo codice [...] dobbiamo avere
traccia di chi fa entrare cosa dentro l'applicazione». I link gia' dati restano come sono.

Il sito manda nome, mestiere, zona, mail e/o telefono e da dove arriva (Google, TikTok...).
Qui si scrive la richiesta con il suo codice personale, ancora spento, e parte un avviso ad
Andrea con il link alla pagina dove decide. Con il suo si' il codice si accende: se c'e' la
mail il link parte da solo, se c'e' solo il telefono la pagina prepara il messaggio WhatsApp.
Un codice acceso compare in visite.da_chi(), quindi aperture e passi hanno un nome.
Sta sul disco dell'archivio: sopravvive ai caricamenti, non serve ricaricare il server.
"""
import datetime, hashlib, html, json, os, pathlib, re, secrets, threading, time, unicodedata, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

import archivio
import avvisi
import posta

MAIL_BUONA = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,30}")
PER_ORA_DA_UNO = 5                  # richieste all'ora dallo stesso indirizzo
AL_GIORNO = 30                      # richieste al giorno in tutto: il telefono di Andrea non si riempie
MESTIERE_MASSIMO = 60

_l = threading.Lock()
_recenti = {}                       # impronta dell'indirizzo -> ore delle richieste; solo in memoria


def _file():
    return pathlib.Path(archivio.CARTELLA) / "ingresso" / "richieste.json"


def _leggi():
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except Exception:                   # noqa: BLE001
        return []


def _scrivi(tutte):
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    t = f.with_suffix(".tmp")
    t.write_text(json.dumps(tutte, ensure_ascii=False, indent=1), encoding="utf-8")
    t.replace(f)


def _ora():
    return datetime.datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M:%S")


def _testo(x, quanto):
    return re.sub(r"\s+", " ", str(x or "")).strip()[:quanto]


def _telefono(x):
    t = re.sub(r"[^\d+]", "", str(x or ""))
    return t if 8 <= len(t.lstrip("+")) <= 15 else ""


def provenienza(referrer):
    """Da dove arriva, in una parola: Google, TikTok... Mai l'indirizzo intero."""
    h = (urllib.parse.urlparse(str(referrer or "")).hostname or "").lower()
    if not h or h.endswith("rilievoapp.it"):
        return "diretto"
    for nome, pezzo in (("Google", "google."), ("Bing", "bing."), ("TikTok", "tiktok."), ("Instagram", "instagram."),
                        ("Facebook", "facebook."), ("YouTube", "youtube.")):
        if pezzo in h:
            return nome
    return h[:60]


def _codice_nuovo(nome, tutte):
    base = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:18].strip("-") or "sito"
    usati = {r.get("codice") for r in tutte}
    while True:
        c = "%s-%s" % (base, secrets.token_hex(2))
        if c not in usati:
            return c


def _base():
    return (os.environ.get("RILIEVO_INDIRIZZO") or "https://rilievoapp.it").rstrip("/")


def _link_decidi(r):
    return _base() + "/ingresso/decidi/" + r["gettone"]


def _avvisa(r, titolo):
    testo = "%s, %s, %s. Arriva da: %s. Contatto: %s" % (
        r["nome"], r["mestiere"] or "mestiere non detto", r["zona"] or "zona non detta",
        r["provenienza"], " · ".join(x for x in (r["mail"], r["telefono"]) if x))

    def manda():
        topic = (os.environ.get("RILIEVO_NTFY_TOPIC") or "").strip()
        if topic:
            try:
                q = urllib.request.Request("https://ntfy.sh/" + topic, data=(testo + "\nTocca per decidere.").encode("utf-8"),
                                           method="POST", headers={"Title": titolo.encode("utf-8").decode("latin-1", "ignore"),
                                                                   "Tags": "bust_in_silhouette", "Click": _link_decidi(r)})
                urllib.request.urlopen(q, timeout=15).read()
            except Exception as e:      # noqa: BLE001
                print("     ingresso: avviso non partito:", e, flush=True)
        avvisi._mail(titolo, testo + "\n\nDecidi qui: " + _link_decidi(r))
    threading.Thread(target=manda, daemon=True).start()
    try:
        import centralino
        centralino.aggiungi("rilievo", titolo, testo, livello="need", tipo="accesso",
                            link="/ingresso/decidi/" + r["gettone"], chiave="ingresso:%s:%s" % (r["codice"], r["ora"]))
    except Exception as e:              # noqa: BLE001
        print("     ingresso: centralino non raggiunto:", e, flush=True)


def _troppe(ip):
    adesso = time.time()
    impronta = hashlib.sha256(str(ip or "").encode()).hexdigest()[:16]
    ore = [t for t in _recenti.get(impronta, []) if adesso - t < 3600]
    if len(ore) >= PER_ORA_DA_UNO:
        return True
    ore.append(adesso)
    _recenti[impronta] = ore
    return False


def chiedi(d, ip=""):
    """Torna (codice http, risposta). Non dice mai a chi chiede il suo codice: lo manda Andrea."""
    d = d if isinstance(d, dict) else {}
    if d.get("sito_web"):               # il campo nascosto lo riempiono solo i robot
        return 200, {"ok": True}
    nome = _testo(d.get("nome"), 80)
    mail = _testo(d.get("mail"), 200).lower()
    telefono = _telefono(d.get("telefono"))
    if not nome:
        return 400, {"errore": "Scrivi il tuo nome o quello della ditta."}
    if mail and not MAIL_BUONA.fullmatch(mail):
        return 400, {"errore": "Questa mail non sembra giusta: la ricontrolli?"}
    if d.get("telefono") and not telefono:
        return 400, {"errore": "Questo numero non sembra giusto: lo ricontrolli?"}
    if not mail and not telefono:
        return 400, {"errore": "Lascia una mail o un telefono: serve per mandarti il link."}
    if _troppe(ip):
        return 429, {"errore": "Hai già mandato la richiesta: ti scriviamo noi."}
    with _l:
        tutte = _leggi()
        for r in tutte:
            if (mail and r.get("mail") == mail) or (telefono and r.get("telefono") == telefono):
                _avvisa(r, "Chiede di nuovo l'accesso: " + r["nome"])
                return 200, {"ok": True}
        oggi = _ora()[:10]
        if sum(1 for r in tutte if str(r.get("ora", "")).startswith(oggi)) >= AL_GIORNO:
            return 429, {"errore": "Troppe richieste oggi: riprova domani o scrivi a info@rilievoapp.it."}
        r = {"codice": _codice_nuovo(nome, tutte), "gettone": secrets.token_urlsafe(24), "stato": "in attesa",
             "ora": _ora(), "nome": nome, "mestiere": _testo(d.get("mestiere"), MESTIERE_MASSIMO),
             "zona": _testo(d.get("zona"), 80), "mail": mail, "telefono": telefono,
             "provenienza": provenienza(d.get("da_dove"))}
        tutte.append(r)
        _scrivi(tutte)
    print("  ingresso: richiesta da %s (%s)" % (r["codice"], r["provenienza"]), flush=True)
    _avvisa(r, "Chiede l'accesso a Rilievo: " + nome)
    return 200, {"ok": True}


def accesi():
    """I codici accesi da Andrea, nel formato di da-chi.json."""
    return {r["codice"]: {"ditta": "%s (dal sito, %s)" % (r["nome"], r["provenienza"]), "n": "sito"}
            for r in _leggi() if r.get("stato") == "approvata"}


def elenco():
    return [{k: v for k, v in r.items() if k != "gettone"} for r in _leggi()]


def _messaggio(r):
    return ("Ciao %s, grazie per la richiesta! Questo è il tuo link personale per provare Rilievo:\n%s/?da=%s\n\n"
            "Basta registrarsi con una mail e una password: è totalmente gratuito.\n\n"
            "Per averla sul telefono come un'app:\n%s/telefono?da=%s\n\n"
            "Se ti blocchi o manca qualcosa che fai spesso, scrivimi pure: rispondo io. Buon lavoro!\nAndrea, Rilievo"
            % (r["nome"], _base(), r["codice"], _base(), r["codice"]))


def _wa(r):
    t = r["telefono"].lstrip("+")
    if len(t) <= 10 and t.startswith("3"):
        t = "39" + t
    return "https://wa.me/%s?text=%s" % (t, urllib.parse.quote(_messaggio(r)))


def decidi(gettone, si):
    """Il si' o il no di Andrea. Rifare lo stesso gesto non rimanda la mail."""
    with _l:
        tutte = _leggi()
        r = next((x for x in tutte if x.get("gettone") == gettone), None)
        if not r:
            return None
        if r["stato"] != "in attesa":
            return r
        r["stato"] = "approvata" if si else "rifiutata"
        r["deciso"] = _ora()
        manda_ora = si and r.get("mail")
        if manda_ora:
            try:
                r["mail_partita"] = bool(posta.manda(r["mail"], "Il tuo link per provare Rilievo", _messaggio(r),
                                                     rispondi_a="info@rilievoapp.it"))
            except Exception as e:      # noqa: BLE001
                print("     ingresso: mail non partita:", e, flush=True)
                r["mail_partita"] = False
        _scrivi(tutte)
    return r


_STILE = ("<!doctype html><html lang=it><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
          "<meta name=robots content=noindex><title>Rilievo: richiesta di accesso</title><style>"
          "body{font-family:'EB Garamond','Cormorant',Georgia,serif;background:#FBF8F2;color:#1B1917;margin:0 auto;padding:34px 22px;max-width:560px;line-height:1.5}"
          "h1{font-weight:400;font-size:30px;margin:0 0 18px}dl{margin:0 0 24px;border-top:1px solid #E2DCD0}"
          "dt{font:12px system-ui,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:#8A8377;margin-top:12px}"
          "dd{margin:2px 0 12px;font-size:19px;border-bottom:1px solid #E2DCD0;padding-bottom:10px;overflow-wrap:anywhere}"
          ".tasto{display:block;width:100%;box-sizing:border-box;text-align:center;font:16px system-ui,sans-serif;padding:16px;border-radius:999px;"
          "border:0;background:#3E4A3A;color:#FFFDF9;text-decoration:none;margin:0 0 12px;cursor:pointer}"
          ".vuoto{background:none;border:1px solid #8A8377;color:#4A453E}p{font-size:18px;color:#4A453E}</style>")


def _quando(ora):
    """«2026-11-10 21:14:03» come lo salva il server diventa «10 novembre 2026 alle 21:14»."""
    mesi = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
            "settembre", "ottobre", "novembre", "dicembre"]
    try:
        g, h = str(ora).split(" ")
        a, m, d = g.split("-")
        return "%d %s %s alle %s" % (int(d), mesi[int(m) - 1], a, h[:5])
    except Exception:                       # noqa: BLE001
        return ora


def _tel(t):
    c = "".join(x for x in str(t or "") if x.isdigit() or x == "+")
    if len(c) == 10 and c.startswith("3"):
        return "%s %s %s" % (c[:3], c[3:6], c[6:])
    return t


def pagina(gettone):
    """La pagina dove Andrea decide. Chi non ha il gettone non vede niente."""
    r = next((x for x in _leggi() if x.get("gettone") == gettone), None)
    if not r:
        return None
    e = html.escape
    righe = "".join("<dt>%s</dt><dd>%s</dd>" % (e(a), e(b)) for a, b in (
        ("Chi", r["nome"]), ("Mestiere", r["mestiere"] or "non detto"), ("Zona", r["zona"] or "non detta"),
        ("Mail", r["mail"] or "non lasciata"), ("Telefono", _tel(r["telefono"]) or "non lasciato"),
        ("Arriva da", r["provenienza"]), ("Quando", _quando(r["ora"])), ("Codice", r["codice"])))
    if r["stato"] == "in attesa":
        fine = ("<form method=post action='/ingresso/decidi/%s/si'><button class=tasto>Sì, fallo entrare</button></form>"
                "<form method=post action='/ingresso/decidi/%s/no'><button class='tasto vuoto'>No, non ora</button></form>"
                % (e(gettone), e(gettone)))
        if r["mail"]:
            fine = "<p>Con il sì, il link parte da solo per mail a %s.</p>" % e(r["mail"]) + fine
    elif r["stato"] == "rifiutata":
        fine = "<p>Hai detto di no. Il codice resta spento.</p>"
    else:
        fine = "<p><b>Il codice è acceso.</b> "
        if r.get("mail"):
            fine += "Mail a %s: %s.</p>" % (e(r["mail"]), "partita" if r.get("mail_partita") else "NON partita, mandagli il link a mano")
        else:
            fine += "Adesso mandagli il messaggio.</p>"
        if r.get("telefono"):
            fine += "<a class=tasto href='%s'>Manda su WhatsApp</a>" % e(_wa(r), quote=True)
        if r.get("mail") and not r.get("mail_partita"):
            fine += "<a class='tasto vuoto' href='mailto:%s?subject=%s&body=%s'>Scrivigli la mail, già pronta</a>" % (
                e(r["mail"], quote=True), urllib.parse.quote("Il tuo link per Rilievo"), urllib.parse.quote(_messaggio(r)))
        fine += "<p style='overflow-wrap:anywhere'>Il suo link: <a href='%s/?da=%s'>%s/?da=%s</a></p>" % (
            e(_base()), e(r["codice"]), e(_base()), e(r["codice"]))
    return _STILE + "<h1>Richiesta di accesso</h1><dl>" + righe + "</dl>" + fine + "</html>"
