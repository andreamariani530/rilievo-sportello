#!/usr/bin/env python3
"""Le richieste che arrivano dal link della ditta (4 ottobre 2026).

PERCHE' ESISTE
Idea presa a Jobber, Deep Lawn, ServiceM8 e PrevAI, scelta da Andrea il 4/10: ogni ditta
ha un suo indirizzo, `/chiedi/<codice>`, da mettere sul sito, su WhatsApp, sul furgone.
Il cliente scrive nome, telefono, indirizzo e cosa gli serve; la richiesta arriva
nell'app dell'artigiano (in Richieste) e gli arriva una mail. Clienti nuovi mentre
lavora o dorme, senza telefonate perse nel furgone.

COME FUNZIONA
  - Il link si fa solo con l'accesso (mail e password): il server sa a chi portare
    la richiesta. Un codice per ditta; «Fai un link nuovo» spegne quello vecchio.
  - Le richieste aspettano in una cassetta per account (`richieste/<account>.json`),
    NON dentro la copia del lavoro: cosi' non litigano con i telefoni che salvano.
    L'app le prende, le mette in Richieste e dice al server che le ha viste.
  - La pagina del cliente non fa girare nessuno script (stessa gabbia del preventivo
    col link). Solo un modulo normale.

LE DIFESE
  - massimo 20 richieste al giorno per ditta e 6 l'ora dallo stesso indirizzo di rete;
  - un campo nascosto che le persone non vedono: chi lo riempie e' un programma, e la
    sua richiesta finisce nel nulla (ma gli si risponde «grazie», cosi' non riprova);
  - testi tagliati a misura, niente HTML che passi.
"""
import hashlib, html, json, os, pathlib, re, secrets, threading, time

import archivio

_chiave = threading.RLock()
_ALFABETO = "abcdefghijkmnpqrstuvwxyz23456789"
MAX_GIORNO = 20
MAX_ORA_IP = 6
MAX_IN_ATTESA = 200
_per_ip = {}                        # impronta dell'indirizzo -> [istanti]


def _cartella():
    d = pathlib.Path(archivio.CARTELLA) / "richieste"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _leggi_json(f, vuoto):
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else vuoto
    except Exception:                       # noqa: BLE001
        return vuoto


def _scrivi_json(f, dati):
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def _codici():
    return _leggi_json(_cartella() / "codici.json", {})


def _codice_buono(c):
    return bool(re.fullmatch(r"[a-z2-9]{10}", str(c or "")))


def _cassetta(account):
    return _cartella() / (re.sub(r"[^a-f0-9]", "", account)[:64] + ".json")


def link_di(account, mail, ditta="", tel="", rifai=False):
    """Il codice della ditta: lo stesso di sempre, oppure uno nuovo che spegne il vecchio."""
    with _chiave:
        tutti = _codici()
        vecchio = next((c for c, v in tutti.items() if v.get("account") == account), None)
        if vecchio and not rifai:
            # nome e telefono si aggiornano solo se arrivano: una chiamata senza non li cancella
            if str(ditta or "").strip():
                tutti[vecchio]["ditta"] = str(ditta)[:120]
            if re.sub(r"\D", "", str(tel or "")):
                tutti[vecchio]["tel"] = re.sub(r"[^\d+ ]", "", str(tel))[:20]
            tutti[vecchio]["mail"] = mail
            _scrivi_json(_cartella() / "codici.json", tutti)
            return vecchio
        if vecchio:
            del tutti[vecchio]
        nuovo = "".join(secrets.choice(_ALFABETO) for _ in range(10))
        while nuovo in tutti:
            nuovo = "".join(secrets.choice(_ALFABETO) for _ in range(10))
        tutti[nuovo] = {"account": account, "mail": mail, "ditta": str(ditta or "")[:120],
                        "tel": re.sub(r"[^\d+ ]", "", str(tel or ""))[:20], "creato": int(time.time()), "giorni": {}}
        _scrivi_json(_cartella() / "codici.json", tutti)
        return nuovo


def di_chi(codice):
    if not _codice_buono(codice):
        return None
    return _codici().get(codice)


def _pulito(x, n):
    return re.sub(r"\s+", " ", str(x or "")).strip()[:n]


def ricevi(codice, campi, ip=""):
    """Una richiesta dal modulo. Torna (stato, messaggio, chi) con stato 'ok', 'errore' o 'finto'."""
    ditta = di_chi(codice)
    if not ditta:
        return "errore", "Questo link non funziona piu'. Chiedi alla ditta quello nuovo.", None
    if _pulito(campi.get("sito"), 200):
        return "finto", "", None              # il campo nascosto: solo un programma lo riempie
    nome, tel = _pulito(campi.get("nome"), 80), _pulito(campi.get("tel"), 30)
    indirizzo, cosa = _pulito(campi.get("indirizzo"), 200), str(campi.get("cosa") or "").strip()[:1000]
    if len(nome) < 2:
        return "errore", "Scrivi il tuo nome.", None
    if len(re.sub(r"\D", "", tel)) < 6:
        return "errore", "Scrivi un numero di telefono: la ditta ti richiama lì.", None
    if len(cosa) < 3:
        return "errore", "Scrivi in due parole cosa ti serve.", None
    adesso = time.time()
    chi_ip = hashlib.sha256(("ip:" + str(ip or "")).encode()).hexdigest()[:16]
    with _chiave:
        recenti = [t for t in _per_ip.get(chi_ip, []) if adesso - t < 3600]
        if len(recenti) >= MAX_ORA_IP:
            return "errore", "Hai mandato tante richieste in poco tempo. Riprova fra un'ora, o chiama la ditta.", None
        tutti = _codici()
        d = tutti.get(codice)
        oggi = time.strftime("%Y-%m-%d", time.gmtime(adesso))
        giorni = {k: v for k, v in (d.get("giorni") or {}).items() if k == oggi}
        if giorni.get(oggi, 0) >= MAX_GIORNO:
            return "errore", "Oggi la ditta ha ricevuto tante richieste. Riprova domani, o chiamala.", None
        giorni[oggi] = giorni.get(oggi, 0) + 1
        d["giorni"] = giorni
        _scrivi_json(_cartella() / "codici.json", tutti)
        _per_ip[chi_ip] = recenti + [adesso]
        f = _cassetta(d["account"])
        attesa = _leggi_json(f, [])[-(MAX_IN_ATTESA - 1):]
        nuova = {"id": "rl" + secrets.token_hex(6), "quando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(adesso)),
                 "nome": nome, "tel": tel, "indirizzo": indirizzo, "cosa": cosa}
        attesa.append(nuova)
        _scrivi_json(f, attesa)
    return "ok", "", {"ditta": d, "richiesta": nuova}


def in_attesa(account):
    with _chiave:
        return _leggi_json(_cassetta(account), [])


def viste(account, ids):
    ids = {str(x) for x in (ids or [])[:500]}
    with _chiave:
        f = _cassetta(account)
        resta = [r for r in _leggi_json(f, []) if r.get("id") not in ids]
        _scrivi_json(f, resta)
        return len(resta)


# ---- la pagina che vede il cliente -------------------------------------------
_STILE = """
<style>
/* nomi con rl-: lo stile dell'app viaggia con la pagina, e lì «.chiedi» e' il tasto della chat */
html,body{background:#F4EFE6;margin:0}
.rl-pagina{max-width:620px;margin:0 auto;padding:26px 20px 60px;font-family:Georgia,serif;color:#2B2822}
.rl-pagina .chi{font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#6E7F66}
.rl-pagina h1{font-family:'Cormorant',Georgia,serif;font-weight:400;font-size:32px;margin:4px 0 6px}
.rl-pagina p{line-height:1.55;color:#4A463F;margin:6px 0 16px}
.rl-pagina label{display:block;margin:14px 0 4px;font-size:15px;color:#4A463F}
.rl-pagina input,.rl-pagina textarea{width:100%;box-sizing:border-box;font-size:18px;padding:13px;border:1px solid #BDB4A3;background:#fff;font-family:inherit;color:#2B2822}
.rl-pagina textarea{min-height:110px}
.rl-pagina button{margin-top:20px;font-size:18px;padding:15px 28px;border:0;background:#2B2822;color:#F4EFE6;font-family:inherit;letter-spacing:.04em}
.rl-pagina .errore{color:#9A4B3A}
.rl-pagina .fatto{font-family:'Cormorant',Georgia,serif;font-size:26px;color:#4F6B4A}
.rl-pagina .nascosto{position:absolute;left:-5000px;width:1px;height:1px;overflow:hidden}
.rl-pagina .nota{font-size:13px;color:#7A746A;margin-top:18px}
.rl-pagina .contatti a{color:#2B2822;margin-right:18px}
</style>"""


def pagina(codice, stili_app, errore="", fatto=False, valori=None):
    d = di_chi(codice)
    e = html.escape
    if not d:
        corpo = ('<div class="rl-pagina"><h1>Questo link non funziona più</h1>'
                 '<p>La ditta ne ha fatto uno nuovo. Chiedi a chi te l’ha dato quello giusto.</p></div>')
        return _intera("Richiesta", stili_app, corpo), 404
    v = valori or {}
    ditta = e(d.get("ditta") or "La ditta")
    tel = d.get("tel") or ""
    contatti = (f'<p class="contatti">Preferisci chiamare? <a href="tel:{e(re.sub(r"[^\d+]", "", tel))}">{e(tel)}</a></p>') if tel else ""
    if fatto:
        corpo = (f'<div class="rl-pagina"><div class="chi">{ditta}</div><p class="fatto">Richiesta mandata. Grazie!</p>'
                 f'<p>{ditta} l’ha già ricevuta e ti richiama al numero che hai scritto.</p>{contatti}</div>')
        return _intera("Richiesta mandata", stili_app, corpo), 200
    corpo = (f'<div class="rl-pagina"><div class="chi">{ditta}</div><h1>Chiedi un preventivo</h1>'
             f'<p>Scrivi chi sei, dove e cosa ti serve: {ditta} ti richiama per fissare un sopralluogo.</p>'
             + (f'<p class="errore">{e(errore)}</p>' if errore else '') +
             f'<form method="post" action="/chiedi/{e(codice)}">'
             f'<label for="nome">Nome e cognome</label><input id="nome" name="nome" autocomplete="name" required minlength="2" value="{e(v.get("nome", ""))}">'
             f'<label for="tel">Telefono</label><input id="tel" name="tel" type="tel" autocomplete="tel" required value="{e(v.get("tel", ""))}">'
             f'<label for="indirizzo">Indirizzo del lavoro</label><input id="indirizzo" name="indirizzo" autocomplete="street-address" '
             f'placeholder="Via, numero e paese" value="{e(v.get("indirizzo", ""))}">'
             f'<label for="cosa">Cosa ti serve</label><textarea id="cosa" name="cosa" required minlength="3" '
             f'placeholder="Per esempio: tagliare il prato e la siepe, circa 300 metri">{e(v.get("cosa", ""))}</textarea>'
             f'<div class="nascosto" aria-hidden="true"><label for="sito">Lascia vuoto</label><input id="sito" name="sito" tabindex="-1" autocomplete="off"></div>'
             f'<button type="submit">Manda la richiesta</button></form>{contatti}'
             f'<p class="nota">I tuoi dati vanno solo a {ditta}, per richiamarti. Non li usiamo per altro.</p></div>')
    return _intera("Chiedi un preventivo a " + (d.get("ditta") or "la ditta"), stili_app, corpo), 200


def _intera(titolo, stili_app, corpo):
    return ('<!doctype html><html lang="it"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow">'
            f'<title>{html.escape(titolo)}</title>{stili_app}{_STILE}</head><body>{corpo}</body></html>')


def mail_alla_ditta(d, r):
    """Oggetto e testo della mail che avvisa l'artigiano."""
    paese = (r.get("indirizzo") or "").split(",")[-1].strip()
    oggetto = "Nuova richiesta da " + r["nome"] + (", " + paese if paese else "")
    testo = ("Ti ha scritto " + r["nome"] + " dal tuo link per le richieste.\n\n"
             "Telefono: " + r["tel"] + "\n" + ("Indirizzo: " + r["indirizzo"] + "\n" if r.get("indirizzo") else "") +
             "Cosa gli serve: " + r["cosa"] + "\n\n"
             "La trovi anche in Rilievo, in Richieste, appena apri l'app.\n\nRilievo")
    return oggetto, testo
