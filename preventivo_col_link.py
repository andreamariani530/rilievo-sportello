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

DAL 4 OTTOBRE 2026 (idee prese ai concorrenti, cartellino E della notte)
  - il cliente spunta le voci facoltative e accetta il totale che ha scelto lui;
  - oltre ad «Accetto» puo' dire «No, grazie» (col motivo) o «Vorrei cambiare qualcosa»;
  - se la ditta ha l'IBAN, dopo il si' vede come pagare l'acconto e preme
    «Ho fatto il bonifico»: la ditta lo sa e controlla in banca;
  - il link scade quando scade il preventivo (prima valeva 180 giorni, mentre la
    carta diceva 30: il cliente poteva accettare prezzi vecchi).
Tutto con moduli normali: la pagina continua a non far girare nessuno script.
"""
import datetime, hashlib, html, json, os, pathlib, re, secrets, threading, time

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


_IBAN = re.compile(r"IT\d{2}[A-Z]\d{10}[A-Z0-9]{12}")
MOTIVI_NO = [("prezzo", "Il prezzo"), ("tempi", "I tempi"), ("altro_giardiniere", "Ho scelto un altro"),
             ("rimandato", "Rimando più avanti"), ("altro", "Altro")]


def _soldi(x):
    try:
        v = round(float(x), 2)
    except (TypeError, ValueError):
        return None
    return v if 0 <= v < 10000000 else None


def _extra_buoni(extra):
    """Quello che arriva dall'app oltre al documento, controllato pezzo per pezzo."""
    extra = extra if isinstance(extra, dict) else {}
    fuori = {"pagamento": None, "facoltative": [], "base": None, "scade_il": None}
    pg = extra.get("pagamento")
    if isinstance(pg, dict):
        iban = re.sub(r"\s", "", str(pg.get("iban") or "")).upper()
        importo = _soldi(pg.get("importo"))
        if _IBAN.fullmatch(iban) and importo and importo > 0:
            fuori["pagamento"] = {"iban": iban, "intestato": str(pg.get("intestato") or "")[:80],
                                  "importo": importo, "causale": str(pg.get("causale") or "")[:140],
                                  "acconto": bool(pg.get("acconto"))}
    fac = extra.get("facoltative")
    if isinstance(fac, list):
        visti = set()
        for v in fac[:30]:
            if not isinstance(v, dict):
                continue
            try:
                k = int(v.get("k"))
            except (TypeError, ValueError):
                continue
            imp = _soldi(v.get("importo"))
            nome = re.sub(r"\s+", " ", str(v.get("nome") or "")).strip()[:120]
            if 0 <= k < 1000 and k not in visti and imp is not None and nome:
                visti.add(k)
                fuori["facoltative"].append({"k": k, "nome": nome, "importo": imp})
    fuori["base"] = _soldi(extra.get("base"))
    if not fuori["facoltative"] or fuori["base"] is None:
        fuori["facoltative"], fuori["base"] = [], fuori["base"]
    sc = str(extra.get("scade_il") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", sc):
        fuori["scade_il"] = sc
    return fuori


def _fine_del_giorno(iso):
    """La mezzanotte italiana alla fine di quel giorno, in secondi."""
    try:
        import zoneinfo
        g = datetime.date.fromisoformat(iso) + datetime.timedelta(days=1)
        return int(datetime.datetime(g.year, g.month, g.day, tzinfo=zoneinfo.ZoneInfo("Europe/Rome")).timestamp())
    except Exception:                       # noqa: BLE001
        return None


def metti(corpo, ditta="", titolo="", telefono="", da_ip="", extra=None):
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
        ex = _extra_buoni(extra)
        scade = int(adesso + DURATA_GIORNI * 86400)
        fine = _fine_del_giorno(ex["scade_il"]) if ex["scade_il"] else None
        if fine:
            # mai oltre i 180 giorni, e mai un link che nasce gia' morto
            scade = max(int(adesso + 86400), min(scade, fine))
        dati = {"creato": int(adesso), "scade": scade,
                "gestione": hashlib.sha256(("g:" + (g := _segreto(24))).encode()).hexdigest(),
                "ditta": str(ditta or "")[:120], "titolo": str(titolo or "")[:160],
                "telefono": re.sub(r"[^\d+]", "", str(telefono or ""))[:20],
                "corpo": pulito,
                "impronta": hashlib.sha256(pulito.encode()).hexdigest(),
                "aperture": [], "accettato": None, "ritirato": None,
                "pagamento": ex["pagamento"], "facoltative": ex["facoltative"], "base": ex["base"],
                "risposta": None, "bonifico": None}
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
            "scade": d.get("scade"), "risposta": d.get("risposta"), "bonifico": d.get("bonifico")}


def ritira(lid, gestione):
    with _chiave:
        d = _leggi(lid)
        if not _vale(d, gestione):
            return False
        if not d.get("ritirato"):
            d["ritirato"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _scrivi(lid, d)
        return True


def segna_aperto(lid):
    with _chiave:
        d = _leggi(lid)
        if not d:
            return
        ap = d.get("aperture") or []
        if len(ap) < 200:
            ap.append(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        d["aperture"] = ap
        _scrivi(lid, d)


def scelte_buone(d, con):
    """Le voci facoltative spuntate che esistono davvero in quel preventivo."""
    ci = {v["k"] for v in (d.get("facoltative") or [])}
    fuori = []
    for x in (con or [])[:30]:
        try:
            k = int(x)
        except (TypeError, ValueError):
            continue
        if k in ci and k not in fuori:
            fuori.append(k)
    return sorted(fuori)


def totale_con(d, scelte):
    base = d.get("base")
    if base is None:
        return None
    return round(base + sum(v["importo"] for v in (d.get("facoltative") or []) if v["k"] in scelte), 2)


def _scaduto(d):
    """Ritirato, oppure passata la validita'. Dopo il si' la validita' non conta piu':
    il cliente deve poter rileggere cosa ha accettato e come pagare (180 giorni)."""
    if d.get("ritirato"):
        return True
    if d.get("accettato"):
        return time.time() > d.get("creato", 0) + DURATA_GIORNI * 86400
    return time.time() > d.get("scade", 0)


def _vivo(d):
    return bool(d) and not _scaduto(d)


def accetta(lid, nome, ip="", browser="", con=None):
    """Torna (True, messaggio) oppure (False, motivo)."""
    nome = re.sub(r"\s+", " ", str(nome or "")).strip()[:80]
    if len(nome) < 3:
        return False, "Scrivi il tuo nome e cognome per accettare."
    with _chiave:
        d = _leggi(lid)
        if not _vivo(d):
            return False, "Questo preventivo non è più valido."
        if d.get("accettato"):
            return True, "Era già accettato."
        if (d.get("risposta") or {}).get("tipo") == "no":
            return False, "Hai già risposto di no. Se ci hai ripensato, scrivi a chi te l’ha mandato."
        scelte = scelte_buone(d, con)
        d["accettato"] = {"quando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "nome": nome,
                          "ip": str(ip or "")[:60], "browser": str(browser or "")[:200],
                          "impronta": d.get("impronta"), "scelte": scelte, "totale": totale_con(d, scelte)}
        _scrivi(lid, d)
    return True, "Accettato."


def rispondi(lid, tipo, motivo="", testo=""):
    """«No, grazie» o «Vorrei cambiare qualcosa». Torna (True, '') oppure (False, motivo)."""
    testo = re.sub(r"[ \t]+", " ", str(testo or "")).strip()[:600]
    with _chiave:
        d = _leggi(lid)
        if not _vivo(d):
            return False, "Questo preventivo non è più valido."
        if d.get("accettato"):
            return False, "Il preventivo è già accettato."
        if (d.get("risposta") or {}).get("tipo") == "no":
            return True, ""
        quando = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if tipo == "no":
            m = str(motivo or "")
            d["risposta"] = {"tipo": "no", "motivo": m if m in dict(MOTIVI_NO) else "altro",
                             "nota": testo[:200], "quando": quando}
        elif tipo == "cambia":
            if len(testo) < 3:
                return False, "Scrivi cosa vorresti cambiare."
            d["risposta"] = {"tipo": "cambia", "testo": testo, "quando": quando}
        else:
            return False, "Non ho capito la risposta."
        _scrivi(lid, d)
    return True, ""


def segna_bonifico(lid):
    """Il cliente dice di aver fatto il bonifico. Vale solo dopo il si', e una volta."""
    with _chiave:
        d = _leggi(lid)
        if not d or not d.get("accettato") or not d.get("pagamento") or _scaduto(d):
            return False
        if not d.get("bonifico"):
            d["bonifico"] = {"quando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            _scrivi(lid, d)
        return True


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
.accetta button.secondario,.pagare button.secondario{background:#fff;color:#2B2822;border:1px solid #2B2822;font-size:16px;padding:12px 20px}
.aggiungi{border:1px solid #D9D1C2;padding:14px 16px 16px;margin:0 0 18px;background:#FBF8F2}
.aggiungi h3{font-weight:400;font-size:20px;margin:0 0 4px}
.aggiungi .totale-scelto{font-size:18px;margin:10px 0 12px}
.altre{margin-top:26px;border-top:1px solid #D9D1C2;padding-top:14px}
.altre summary{cursor:pointer;font-size:17px;color:#4A463F}
.altre form{margin:14px 0 6px}
.altre textarea,.altre input[type=text]{width:100%;box-sizing:border-box;font-size:17px;padding:12px;border:1px solid #BDB4A3;background:#fff;font-family:inherit;margin:4px 0 10px}
.pagare{max-width:920px;margin:12px auto 0;padding:22px 20px;font-family:Georgia,serif;color:#2B2822;background:#FBF8F2;border:1px solid #D9D1C2;box-sizing:border-box}
.pagare h2{font-weight:400;font-size:22px;margin:0}
.pagare .cifra{font-size:30px;margin:4px 0 8px}
.pagare p{line-height:1.55;margin:6px 0 12px}
.pagare .iban{font-family:ui-monospace,Menlo,monospace;font-size:14px;letter-spacing:.02em;word-break:normal}
.accetta input[type=checkbox],.accetta input[type=radio]{accent-color:#2B2822}
.pagare button{font-size:18px;padding:15px 28px;border:0;background:#2B2822;color:#F4EFE6;font-family:inherit}
.pagare .fatto{font-size:19px;color:#4F6B4A}
/* sul link si accetta qui sotto: le righe per la firma a penna non servono */
.carta .firma-doc{display:none}
</style>"""


def _euro(x):
    v = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "€ " + (v[:-3] if v.endswith(",00") else v)


def _iban_scritto(iban):
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def _come_pagare(d, e, ditta, con_tasto):
    pg = d.get("pagamento")
    if not pg:
        return ""
    cosa = "l’acconto" if pg.get("acconto") else "il preventivo"
    importo = pg["importo"]
    acc = d.get("accettato") or {}
    if acc.get("totale") is not None and d.get("base"):
        # il cliente ha aggiunto delle voci: l'acconto segue il totale che ha scelto
        importo = round(importo * acc["totale"] / d["base"], 2) if pg.get("acconto") else acc["totale"]
    righe = (f'<div class="pagare"><h2>Come pagare {cosa}</h2>'
             f'<p class="cifra">{e(_euro(importo))}</p>'
             f'<p>Bonifico a <b>{e(pg.get("intestato") or ditta)}</b><br>IBAN<br><span class="iban">{e(_iban_scritto(pg["iban"]))}</span>'
             + (f'<br>Causale: {e(pg["causale"])}' if pg.get("causale") else '') + '</p>')
    if d.get("bonifico"):
        righe += f'<p class="fatto">Grazie: hai detto di aver fatto il bonifico. {ditta} controlla e ti conferma.</p>'
    elif con_tasto:
        righe += (f'<form method="post" action="/p/{e(d["_lid"])}/pagato">'
                  f'<button type="submit">Ho fatto il bonifico</button></form>'
                  f'<p class="nota">Premilo dopo averlo fatto: {ditta} lo sa e controlla in banca.</p>')
    return righe + '</div>'


def pagina(lid, stili_app, messaggio="", errore="", con=None, nome=""):
    """L'HTML intero della pagina per il cliente, oppure None se il link non c'e'."""
    d = _leggi(lid)
    if not d:
        return None
    d["_lid"] = lid
    e = html.escape
    ditta = e(d.get("ditta") or "")
    if _scaduto(d):
        if not d.get("ritirato") and not d.get("accettato"):
            corpo = (f'<div class="banda"><div class="chi">{ditta}</div><h1>Il preventivo è scaduto</h1></div>'
                     f'<div class="accetta"><p>Il preventivo valeva fino a una certa data, scritta nelle condizioni, '
                     f'e quella data è passata. Chiedi a {ditta} di rinnovarlo: ti manda il link nuovo.</p></div>')
        else:
            corpo = (f'<div class="banda"><div class="chi">{ditta}</div><h1>Questo preventivo non è più valido</h1></div>'
                     '<div class="accetta"><p>Chi te l’ha mandato lo ha ritirato o ne ha fatto uno nuovo. '
                     'Chiedigli il link aggiornato.</p></div>')
        return _intera(e(d.get("titolo") or "Preventivo"), stili_app, corpo)
    tel = d.get("telefono") or ""
    contatti = ""
    domanda = "Per qualsiasi cosa:" if d.get("accettato") else "Una domanda prima di decidere?"
    if tel:
        wa = tel.lstrip("+")
        if not wa.startswith("39") and len(wa) <= 10:
            wa = "39" + wa
        contatti = (f'<p class="contatti">{domanda} '
                    f'<a href="tel:{e(tel)}">Chiama</a><a href="https://wa.me/{e(wa)}" target="_blank" rel="noopener">Scrivi su WhatsApp</a></p>')
    acc = d.get("accettato")
    risp = d.get("risposta") or {}
    fac = d.get("facoltative") or []
    if acc:
        nomi = [v["nome"] for v in fac if v["k"] in (acc.get("scelte") or [])]
        con_che = (", con " + ", ".join(n[:1].lower() + n[1:] for n in nomi)) if nomi else ""
        tot = f' Totale {e(_euro(acc["totale"]))}.' if nomi and acc.get("totale") is not None else ""
        fondo = (_come_pagare(d, e, ditta, True) +
                 f'<div class="accetta"><p class="fatto">Accettato da {e(acc.get("nome",""))}{e(con_che)}, '
                 f'il {e(_data(acc.get("quando","")))}.{tot}</p>'
                 f'<p>{ditta} lo ha già saputo. Grazie.</p>{contatti}</div>')
    elif risp.get("tipo") == "no":
        fondo = (f'<div class="accetta"><p class="fatto">Hai risposto di no.</p>'
                 f'<p>{ditta} lo sa. Grazie di averlo detto: se ci ripensi, scrivigli.</p>{contatti}</div>')
    else:
        scelte = scelte_buone(d, con)
        riquadro = ""
        if fac:
            tot = totale_con(d, scelte)
            riquadro = ('<div class="aggiungi"><h3>Puoi aggiungere</h3>'
                        + "".join(f'<label class="ok"><input type="checkbox" name="con" value="{v["k"]}"'
                                  f'{" checked" if v["k"] in scelte else ""}><span>{e(v["nome"])}'
                                  f' <b>+ {e(_euro(v["importo"]))}</b></span></label>' for v in fac)
                        + (f'<p class="totale-scelto">Con quello che hai scelto: <b>{e(_euro(tot))}</b>, IVA compresa.</p>'
                           if scelte and tot is not None else '')
                        + '<button type="submit" name="fai" value="conto" formnovalidate class="secondario">Rifai il conto</button></div>')
        gia = (f'<p class="fatto" style="font-size:18px">Hai chiesto di cambiare: “{e(risp.get("testo",""))}”. '
               f'{ditta} lo sa e ti risponde.</p>') if risp.get("tipo") == "cambia" else ""
        motivi = "".join(f'<label class="ok"><input type="radio" name="motivo" value="{k}"{" checked" if k == "prezzo" else ""}>'
                         f'<span>{e(n)}</span></label>' for k, n in MOTIVI_NO)
        fondo = (f'<div class="accetta">{gia}<h2>Ti va bene?</h2>'
                 f'<p>Se il preventivo è come lo vuoi, scrivi il tuo nome e premi «Accetto». '
                 f'{ditta} lo sa subito.</p>'
                 + (f'<p class="errore">{e(errore)}</p>' if errore else '') +
                 f'<form method="post" action="/p/{lid}/accetto">{riquadro}'
                 f'<input type="text" name="nome" value="{e(str(nome or "")[:80])}" placeholder="Nome e cognome" autocomplete="name" required minlength="3">'
                 f'<label class="ok"><input type="checkbox" name="ok" value="1" required>'
                 f'<span>Ho letto il preventivo e lo accetto.</span></label>'
                 f'<button type="submit">Accetto</button></form>{contatti}'
                 f'<p class="nota">Restano segnati il tuo nome, il giorno e l’ora. Valgono le condizioni '
                 f'scritte nel preventivo.</p>'
                 f'<details class="altre"><summary>Non ti va bene così?</summary>'
                 f'<form method="post" action="/p/{lid}/risposta"><input type="hidden" name="tipo" value="cambia">'
                 f'<p>Cosa vorresti cambiare?</p><textarea name="testo" rows="3" required minlength="3" '
                 f'placeholder="Per esempio: senza la siepe, oppure a novembre"></textarea>'
                 f'<button type="submit" class="secondario">Vorrei cambiare qualcosa</button></form>'
                 f'<form method="post" action="/p/{lid}/risposta"><input type="hidden" name="tipo" value="no">'
                 f'<p>Non lo vuoi fare? Ci aiuta sapere perché.</p>{motivi}'
                 f'<input type="text" name="testo" placeholder="Se vuoi, due parole">'
                 f'<button type="submit" class="secondario">No, grazie</button></form></details></div>')
    corpo = (f'<div class="banda"><div class="chi">{ditta}</div><h1>{e(d.get("titolo") or "Il tuo preventivo")}</h1></div>'
             + (fondo if acc else '') +
             f'<div class="carta">{d.get("corpo","")}</div>' + ('' if acc else fondo))
    return _intera(e(d.get("titolo") or "Preventivo"), stili_app, corpo)


def _data(iso):
    """Il giorno e l'ora in Italia: il server di Render vive in ora di Greenwich."""
    try:
        import datetime, zoneinfo
        t = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        t = t.astimezone(zoneinfo.ZoneInfo("Europe/Rome"))
        return t.strftime("%d/%m/%Y alle %H:%M")
    except Exception:                       # noqa: BLE001
        return iso


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
