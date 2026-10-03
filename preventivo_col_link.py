#!/usr/bin/env python3
"""Il preventivo col link: il cliente lo apre dal suo telefono e lo accetta.

PERCHE' ESISTE
Fino al 3 ottobre 2026 il preventivo arrivava al cliente solo come PDF su
WhatsApp, e si poteva firmare solo col dito sul telefono dell'artigiano, davanti
a lui. Klivo manda un link: il cliente apre, legge, accetta da casa, e l'artigiano
sa se l'ha aperto. Era la seconda mancanza del confronto del 3/10.

COME FUNZIONA
1. Il telefono manda il corpo del documento, lo stesso che diventa il PDF.
2. Il server lo conserva e risponde con due segreti: il link da dare al cliente
   (`/p/<link>`) e la chiave per seguirlo, che resta sul telefono dell'artigiano
   (con quella si legge se e' stato aperto, se e' stato accettato, e si ritira).
3. Il cliente apre il link, legge, scrive il suo nome e preme «Accetto».
4. Il telefono dell'artigiano chiede ogni tanto come va e segna il preventivo
   accettato da solo.

LA SICUREZZA, PERCHE' QUESTA PAGINA STA SULLO STESSO SITO DELL'APP
Quello che arriva dal telefono e' HTML, e un HTML scritto con cattiveria potrebbe
provare a leggere la memoria dell'app di chi lo apre. Quindi tre cose:
  - prima di conservarlo si tolgono script, gestori di eventi, iframe, form e link
    `javascript:`;
  - la pagina va al browser con una Content-Security-Policy che non lascia girare
    nessuno script e la chiude in una sandbox senza `allow-same-origin`: il browser
    la tratta come un sito a parte, senza accesso alla memoria di Rilievo;
  - l'unico modulo che la pagina puo' spedire va solo a questo server.

COSA VALE L'ACCETTAZIONE
E' un'accettazione scritta con firma elettronica semplice: nome scritto dal
cliente, giorno e ora, indirizzo di rete, browser, e l'impronta del documento
che aveva davanti. Basta per dire «il cliente ha accettato questo preventivo».
Non e' la firma elettronica avanzata con marca temporale che vanta Klivo, e
l'app non deve dire che lo sia.
"""
import hashlib, html, json, os, pathlib, re, secrets, threading, time

import archivio

MAX_CORPO = 6 * 1024 * 1024        # le foto del giardino stanno dentro come data:
DURATA_GIORNI = 180
_chiave = threading.RLock()
_ALFABETO = "abcdefghijkmnpqrstuvwxyz23456789"
_creati = {}                        # indirizzo -> [istanti]: al massimo 40 link l'ora


def _cartella():
    d = pathlib.Path(archivio.CARTELLA) / "link"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _segreto(n):
    return "".join(secrets.choice(_ALFABETO) for _ in range(n))


def _id_buono(x):
    return bool(re.fullmatch(r"[a-z2-9]{14}", str(x or "")))


def _file(lid):
    return _cartella() / (lid + ".json")


def _leggi(lid):
    if not _id_buono(lid) or not _file(lid).is_file():
        return None
    try:
        return json.loads(_file(lid).read_text(encoding="utf-8"))
    except Exception:                       # noqa: BLE001
        return None


def _scrivi(lid, dati):
    tmp = _file(lid).with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _file(lid))


# ---- togliere dall'HTML tutto quello che potrebbe fare qualcosa ---------------
_VIA_BLOCCHI = re.compile(r"<\s*(script|iframe|object|embed|form|style|link|meta|base|frame|frameset|svg|math|template|noscript)\b.*?(<\s*/\s*\1\s*>|$)", re.I | re.S)
_VIA_SOLI = re.compile(r"<\s*/?\s*(script|iframe|object|embed|form|input|button|textarea|select|link|meta|base|frame|frameset|svg|math|template|noscript)\b[^>]*>", re.I)
_VIA_EVENTI = re.compile(r"\s(on[a-z]+|formaction|srcdoc)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_VIA_JS = re.compile(r"(href|src|action|xlink:href)\s*=\s*([\"']?)\s*(javascript|vbscript|data:text/html)[^\"'\s>]*\2", re.I)


def pulisci(corpo):
    c = str(corpo or "")
    for _ in range(3):                      # i trucchi annidati cadono al secondo giro
        c = _VIA_BLOCCHI.sub("", c)
        c = _VIA_SOLI.sub("", c)
        c = _VIA_EVENTI.sub(" ", c)
        c = _VIA_JS.sub(r'\1="#"', c)
    return c


def metti(corpo, ditta="", titolo="", telefono="", da_ip=""):
    """Conserva un preventivo e torna (link, chiave per seguirlo) oppure (None, errore)."""
    corpo = str(corpo or "")
    if not corpo.strip():
        return None, "Il preventivo e' vuoto."
    if len(corpo.encode()) > MAX_CORPO:
        return None, "Il preventivo e' troppo grande per il link (oltre 6 MB di foto)."
    adesso = time.time()
    with _chiave:
        recenti = [t for t in _creati.get(da_ip, []) if adesso - t < 3600]
        if len(recenti) >= 40:
            return None, "Troppi link in un'ora da questo telefono. Riprova piu' tardi."
        _creati[da_ip] = recenti + [adesso]
        lid = _segreto(14)
        while _file(lid).exists():
            lid = _segreto(14)
        pulito = pulisci(corpo)
        dati = {"creato": int(adesso), "scade": int(adesso + DURATA_GIORNI * 86400),
                "gestione": hashlib.sha256(("g:" + (g := _segreto(24))).encode()).hexdigest(),
                "ditta": str(ditta or "")[:120], "titolo": str(titolo or "")[:160],
                "telefono": re.sub(r"[^\d+]", "", str(telefono or ""))[:20],
                "corpo": pulito,
                "impronta": hashlib.sha256(pulito.encode()).hexdigest(),
                "aperture": [], "accettato": None, "ritirato": None}
        _scrivi(lid, dati)
    return {"link": lid, "gestione": g}, None


def _vale(dati, gestione):
    return dati and secrets.compare_digest(
        dati.get("gestione", ""), hashlib.sha256(("g:" + str(gestione or "")).encode()).hexdigest())


def stato(lid, gestione):
    d = _leggi(lid)
    if not _vale(d, gestione):
        return None
    ap = d.get("aperture") or []
    return {"aperto": len(ap), "prima_volta": ap[0] if ap else None,
            "ultima_volta": ap[-1] if ap else None,
            "accettato": d.get("accettato"), "ritirato": d.get("ritirato"),
            "scade": d.get("scade")}


def ritira(lid, gestione):
    with _chiave:
        d = _leggi(lid)
        if not _vale(d, gestione):
            return False
        if not d.get("ritirato"):
            d["ritirato"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _scrivi(lid, d)
        return True


def segna_aperto(lid):
    with _chiave:
        d = _leggi(lid)
        if not d:
            return
        ap = d.get("aperture") or []
        if len(ap) < 200:
            ap.append(time.strftime("%Y-%m-%dT%H:%M:%S"))
        d["aperture"] = ap
        _scrivi(lid, d)


def accetta(lid, nome, ip="", browser=""):
    """Torna (True, messaggio) oppure (False, motivo)."""
    nome = re.sub(r"\s+", " ", str(nome or "")).strip()[:80]
    if len(nome) < 3:
        return False, "Scrivi il tuo nome e cognome per accettare."
    with _chiave:
        d = _leggi(lid)
        if not d or d.get("ritirato") or time.time() > d.get("scade", 0):
            return False, "Questo preventivo non e' piu' valido."
        if d.get("accettato"):
            return True, "Era gia' accettato."
        d["accettato"] = {"quando": time.strftime("%Y-%m-%dT%H:%M:%S"), "nome": nome,
                          "ip": str(ip or "")[:60], "browser": str(browser or "")[:200],
                          "impronta": d.get("impronta")}
        _scrivi(lid, d)
    return True, "Accettato."


# ---- la pagina che vede il cliente -------------------------------------------
REGOLE = ("default-src 'none'; img-src data: https:; style-src 'unsafe-inline'; "
          "font-src data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'; "
          "sandbox allow-forms allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation")

_STILE_PAGINA = """
<style>
html,body{background:#F4EFE6;margin:0}
.banda{max-width:920px;margin:0 auto;padding:22px 20px 6px;font-family:'Cormorant',Georgia,serif;color:#2B2822}
.banda .chi{font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#6E7F66}
.banda h1{font-weight:400;font-size:28px;margin:4px 0 0}
.carta{max-width:920px;margin:12px auto;background:#fff;box-shadow:0 1px 0 #E2DACB}
.carta .foglio{margin:0 auto 18px!important}
.accetta{max-width:920px;margin:0 auto 60px;padding:26px 20px;font-family:Georgia,serif;color:#2B2822;border-top:1px solid #D9D1C2}
.accetta h2{font-weight:400;font-size:24px;margin:0 0 6px}
.accetta p{margin:6px 0 14px;color:#4A463F;line-height:1.5}
.accetta input[type=text]{width:100%;box-sizing:border-box;font-size:19px;padding:14px;border:1px solid #BDB4A3;background:#fff;font-family:inherit}
.accetta label.ok{display:flex;gap:10px;align-items:flex-start;margin:14px 0;font-size:16px}
.accetta label.ok input{width:22px;height:22px;margin-top:2px}
.accetta button{font-size:18px;padding:15px 28px;border:0;background:#2B2822;color:#F4EFE6;font-family:inherit;letter-spacing:.04em}
.accetta .fatto{font-size:22px;color:#4F6B4A}
.accetta .errore{color:#9A4B3A}
.accetta .contatti a{color:#2B2822;margin-right:18px}
.nota{font-size:13px;color:#7A746A}
</style>"""


def pagina(lid, stili_app, messaggio="", errore=""):
    """L'HTML intero della pagina per il cliente, oppure None se il link non c'e'."""
    d = _leggi(lid)
    if not d:
        return None
    e = html.escape
    ditta = e(d.get("ditta") or "")
    if d.get("ritirato") or time.time() > d.get("scade", 0):
        corpo = (f'<div class="banda"><div class="chi">{ditta}</div><h1>Questo preventivo non e\' piu\' valido</h1></div>'
                 '<div class="accetta"><p>Chi te l\'ha mandato lo ha ritirato o ne ha fatto uno nuovo. '
                 'Chiedigli il link aggiornato.</p></div>')
        return _intera(e(d.get("titolo") or "Preventivo"), stili_app, corpo)
    tel = d.get("telefono") or ""
    contatti = ""
    if tel:
        wa = tel.lstrip("+")
        if not wa.startswith("39") and len(wa) <= 10:
            wa = "39" + wa
        contatti = (f'<p class="contatti">Una domanda prima di decidere? '
                    f'<a href="tel:{e(tel)}">Chiama</a><a href="https://wa.me/{e(wa)}" target="_blank" rel="noopener">Scrivi su WhatsApp</a></p>')
    acc = d.get("accettato")
    if acc:
        fondo = (f'<div class="accetta"><p class="fatto">Accettato da {e(acc.get("nome",""))}, '
                 f'il {e(_data(acc.get("quando","")))}.</p>'
                 f'<p>{ditta} lo ha gia\' saputo. Grazie.</p>{contatti}</div>')
    else:
        fondo = (f'<div class="accetta"><h2>Ti va bene?</h2>'
                 f'<p>Se il preventivo e\' come lo vuoi, scrivi il tuo nome e premi «Accetto». '
                 f'{ditta} lo sa subito.</p>'
                 + (f'<p class="errore">{e(errore)}</p>' if errore else '') +
                 f'<form method="post" action="/p/{lid}/accetto">'
                 f'<input type="text" name="nome" placeholder="Nome e cognome" autocomplete="name" required minlength="3">'
                 f'<label class="ok"><input type="checkbox" name="ok" value="1" required>'
                 f'<span>Ho letto il preventivo e lo accetto.</span></label>'
                 f'<button type="submit">Accetto</button></form>{contatti}'
                 f'<p class="nota">Rimangono segnati il tuo nome, il giorno e l\'ora. Il preventivo resta '
                 f'valido per le condizioni scritte sopra.</p></div>')
    corpo = (f'<div class="banda"><div class="chi">{ditta}</div><h1>{e(d.get("titolo") or "Il tuo preventivo")}</h1></div>'
             f'<div class="carta">{d.get("corpo","")}</div>{fondo}')
    return _intera(e(d.get("titolo") or "Preventivo"), stili_app, corpo)


def _data(iso):
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)", iso or "")
    return f"{m[3]}/{m[2]}/{m[1]} alle {m[4]}:{m[5]}" if m else iso


def _intera(titolo, stili_app, corpo):
    return ('<!doctype html><html lang="it"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow">'
            f'<title>{titolo}</title>{stili_app}{_STILE_PAGINA}</head><body>{corpo}</body></html>')


_stili_cache = {"mtime": None, "testo": ""}


def stili_dell_app(app_html):
    """I <style> dell'app servita (caratteri compresi): il preventivo si vede uguale al PDF."""
    p = pathlib.Path(app_html)
    if not p.is_file():
        return ""
    m = p.stat().st_mtime
    if _stili_cache["mtime"] != m:
        t = p.read_text(encoding="utf-8", errors="replace")
        testa = t.split("<script", 1)[0]
        _stili_cache.update(mtime=m, testo="".join(re.findall(r"<style\b[^>]*>.*?</style>", testa, re.S)))
    return _stili_cache["testo"]
