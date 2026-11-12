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

DALL'11 NOVEMBRE 2026: IL CODICE PER MAIL PRIMA DEL SI'
Andrea ha chiesto di rendere piu' solida l'accettazione senza spendere. Dopo nome e
spunta il server manda un codice di 6 cifre alla mail del cliente: quella che la ditta
ha nella scheda del cliente (arriva dall'app come `mail_cliente`) oppure, se non c'e',
quella che il cliente scrive. Il si' vale solo col codice giusto, e nell'accettazione
resta anche la mail confermata. Resta una firma elettronica semplice, ma legata a una
casella che il cliente controlla davvero. Il codice vale 15 minuti, 5 tentativi; al
massimo 5 mail l'ora per link. Se la posta non parte, si accetta come prima (senza
codice): un cliente che vuole dire si' non deve restare bloccato da un guasto nostro.

DAL 4 OTTOBRE 2026 (idee prese ai concorrenti, cartellino E della notte)
  - il cliente spunta le voci facoltative e accetta il totale che ha scelto lui;
  - oltre ad «Accetto» puo' dire «No, grazie» (col motivo) o «Vorrei cambiare qualcosa»;
  - se la ditta ha l'IBAN, dopo il si' vede come pagare l'acconto e preme
    «Ho fatto il bonifico»: la ditta lo sa e controlla in banca;
  - il link scade quando scade il preventivo (prima valeva 180 giorni, mentre la
    carta diceva 30: il cliente poteva accettare prezzi vecchi).
Tutto con moduli normali: la pagina continua a non far girare nessuno script.

DAL 12 NOVEMBRE 2026: I LAVORI IN PIU' LI CONFERMA L'ARTIGIANO
Andrea: «se sono sicuro che devo uscire per uno sfalcio prato posso aver pianificato solo
quello e aver preso altri lavori, o non aver portato gli attrezzi giusti». Quindi:
  - le voci facoltative spuntate dal cliente NON entrano piu' nel si': il preventivo
    base e' accettato, le voci in piu' restano «da confermare» in `in_piu`, fuori dal
    totale accettato, dall'acconto e dalla fattura;
  - il cliente puo' chiederle anche dopo aver accettato (/p/<link>/aggiungi);
  - l'artigiano risponde dall'app («Va bene, lo faccio» / «Non questa volta», /link/in-piu):
    solo le confermate entrano in `accettato.scelte` e nel totale. Si puo' cambiare idea;
  - all'artigiano arriva una mail per ogni evento del cliente (accettato, lavoro in piu',
    vorrebbe cambiare, no, bonifico), una sola, se nell'app ha lasciato acceso l'avviso;
    al cliente arriva l'esito, se ha una mail. Mai per i codici di prova o degli amici.
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


_MAIL = re.compile(r"[^\s@<>\"',;]+@[^\s@<>\"',;]+\.[A-Za-z]{2,}")


def mail_buona(x):
    m = str(x or "").strip()[:120]
    return m.lower() if _MAIL.fullmatch(m) else ""


def mail_coperta(m):
    """m•••••@gmail.com: si capisce dove guardare senza mostrarla intera a chi apre il link."""
    if "@" not in str(m or ""):
        return ""
    chi, dove = m.split("@", 1)
    return chi[:1] + "•" * max(3, min(6, len(chi) - 1)) + "@" + dove


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
    fuori = {"pagamento": None, "facoltative": [], "base": None, "scade_il": None, "mail_cliente": "",
             "mail_ditta": "", "da": ""}
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
    fuori["mail_cliente"] = mail_buona(extra.get("mail_cliente"))
    # lavori in piu' (12/11/2026): dove avvisare l'artigiano, e da quale codice arriva
    fuori["mail_ditta"] = mail_buona(extra.get("mail_ditta"))
    da = str(extra.get("da") or "").strip().lower()
    fuori["da"] = da if re.fullmatch(r"[a-z0-9-]{2,30}", da) else ""
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
                "risposta": None, "bonifico": None, "mail_cliente": ex["mail_cliente"],
                "mail_ditta": ex["mail_ditta"], "da": ex["da"], "in_piu": []}
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
            "scade": d.get("scade"), "risposta": d.get("risposta"), "bonifico": d.get("bonifico"),
            "in_piu": [dict(x) for x in (d.get("in_piu") or [])]}


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


CODICE_MINUTI = 15
CODICE_TENTATIVI = 5
CODICE_MAIL_ORA = 5


def _impronta_codice(lid, codice):
    return hashlib.sha256(("c:" + lid + ":" + str(codice)).encode()).hexdigest()


def _adesso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _metti_in_piu(d, scelte):
    """Le voci spuntate dal cliente diventano «da confermare». Torna quelle nuove."""
    gia = {x["k"] for x in (d.get("in_piu") or [])}
    fac = {v["k"]: v for v in (d.get("facoltative") or [])}
    nuove = []
    for k in scelte or []:
        if k in fac and k not in gia:
            nuove.append({"k": k, "nome": fac[k]["nome"], "importo": fac[k]["importo"],
                          "chiesto": _adesso(), "esito": None, "deciso": None})
    d["in_piu"] = (d.get("in_piu") or []) + nuove
    return nuove


def confermate(d):
    return sorted(x["k"] for x in (d.get("in_piu") or []) if x.get("esito") == "si")


def _segna_si(d, nome, ip, browser, scelte, verifica=None):
    # lavori in piu' (12/11/2026): si accetta il preventivo base; le voci spuntate
    # aspettano il si' dell'artigiano e non entrano nel totale accettato
    _metti_in_piu(d, scelte)
    gia_si = confermate(d)
    d["accettato"] = {"quando": _adesso(), "nome": nome,
                      "ip": str(ip or "")[:60], "browser": str(browser or "")[:200],
                      "impronta": d.get("impronta"), "scelte": gia_si, "totale": totale_con(d, gia_si),
                      "verifica": verifica}
    d.pop("in_attesa", None)


def _si_possibile(d):
    """None se si puo' ancora dire si', altrimenti (ok, messaggio) da tornare."""
    if not _vivo(d):
        return False, "Questo preventivo non è più valido."
    if d.get("accettato"):
        return True, "Era già accettato."
    if (d.get("risposta") or {}).get("tipo") == "no":
        return False, "Hai già risposto di no. Se ci hai ripensato, scrivi a chi te l’ha mandato."
    return None


def accetta(lid, nome, ip="", browser="", con=None):
    """Il si' senza codice: resta per quando la posta non parte. Torna (True, msg) o (False, motivo)."""
    nome = re.sub(r"\s+", " ", str(nome or "")).strip()[:80]
    if len(nome) < 3:
        return False, "Scrivi il tuo nome e cognome per accettare."
    with _chiave:
        d = _leggi(lid)
        no = _si_possibile(d)
        if no:
            return no
        _segna_si(d, nome, ip, browser, scelte_buone(d, con))
        _scrivi(lid, d)
    return True, "Accettato."


def chiedi_codice(lid, nome, con=None, mail_scritta="", ip="", browser="", manda=None):
    """Primo passo del si': prepara il codice e lo manda.
    Torna (stato, messaggio): stato e' "mandato", "accettato" (posta guasta: si' senza
    codice, come prima dell'11/11), "gia" (era gia' accettato) oppure "errore"."""
    nome = re.sub(r"\s+", " ", str(nome or "")).strip()[:80]
    if len(nome) < 3:
        return "errore", "Scrivi il tuo nome e cognome per accettare."
    with _chiave:
        d = _leggi(lid)
        no = _si_possibile(d)
        if no:
            return ("gia" if no[0] else "errore"), no[1]
        mail = d.get("mail_cliente") or mail_buona(mail_scritta)
        if not mail:
            return "errore", ("Scrivi la tua mail: ti mandiamo un codice per confermare." if not str(mail_scritta or "").strip()
                              else "Questa mail non sembra giusta. Controllala.")
        adesso = time.time()
        mandati = [x for x in (d.get("codici_mandati") or []) if adesso - x < 3600]
        if len(mandati) >= CODICE_MAIL_ORA:
            return "errore", "Ti abbiamo già mandato diversi codici. Guarda nella posta (anche fra lo spam) o riprova fra un’ora."
        codice = "%06d" % secrets.randbelow(1000000)
        d["in_attesa"] = {"nome": nome, "scelte": scelte_buone(d, con), "mail": mail,
                          "codice": _impronta_codice(lid, codice), "scade": int(adesso + CODICE_MINUTI * 60),
                          "tentativi": 0, "mandato": int(adesso),
                          "ip": str(ip or "")[:60], "browser": str(browser or "")[:200]}
        d["codici_mandati"] = mandati + [adesso]
        _scrivi(lid, d)
        ditta, titolo = d.get("ditta") or "", d.get("titolo") or ""
    partita = False
    if manda:
        testo = (f"Buongiorno {nome},\n\n"
                 f"il codice per accettare {_il_preventivo(titolo)}" + (f" di {ditta}" if ditta else "") + f" è:\n\n    {codice}\n\n"
                 f"Scrivilo nella pagina del preventivo. Vale {CODICE_MINUTI} minuti.\n\n"
                 "Se non hai chiesto tu di accettare questo preventivo, ignora questa mail: senza il codice non succede niente.\n\n"
                 "Rilievo")
        # a379 (si' di Andrea 12/11 sera): il cliente vede la sua ditta, non «Rilievo», e il codice
        # subito nell'oggetto. Prima «Rilievo» + «Il codice per accettare...» sembrava spam.
        oggetto = f"{codice} è il codice per accettare il preventivo" + (f" di {ditta.strip()}" if ditta.strip() else "")
        mittente = (ditta.strip()[:60] + " tramite Rilievo") if ditta.strip() else "Rilievo"
        try:
            import inspect
            try:
                col_nome = "nome" in inspect.signature(manda).parameters
            except (TypeError, ValueError):
                col_nome = False
            partita = bool(manda(mail, oggetto, testo, nome=mittente) if col_nome else manda(mail, oggetto, testo))
        except Exception:                   # noqa: BLE001
            partita = False
    if partita:
        return "mandato", ""
    # la posta non e' partita: non si blocca il cliente, si accetta come prima
    with _chiave:
        d = _leggi(lid)
        att = d.get("in_attesa") or {}
        if d.get("accettato") or not att:
            return "gia", "Era già accettato."
        _segna_si(d, att["nome"], att["ip"], att["browser"], att["scelte"],
                  {"tipo": "nessuna", "perche": "la mail col codice non è partita", "mail": att["mail"]})
        _scrivi(lid, d)
    return "accettato", ""


def conferma_codice(lid, codice, ip="", browser=""):
    """Secondo passo: il codice giusto chiude il si'. Torna (True, '') o (False, motivo)."""
    codice = re.sub(r"\D", "", str(codice or ""))[:6]
    with _chiave:
        d = _leggi(lid)
        no = _si_possibile(d)
        if no:
            return no
        att = d.get("in_attesa")
        if not att:
            return False, "Scrivi il tuo nome e premi «Accetto»: ti mandiamo il codice."
        if time.time() > att.get("scade", 0):
            return False, "Il codice è scaduto. Premi «Mandami un codice nuovo»."
        if att.get("tentativi", 0) >= CODICE_TENTATIVI:
            return False, "Troppi codici sbagliati. Premi «Mandami un codice nuovo»."
        if len(codice) != 6 or not secrets.compare_digest(att.get("codice", ""), _impronta_codice(lid, codice)):
            att["tentativi"] = att.get("tentativi", 0) + 1
            _scrivi(lid, d)
            resta = CODICE_TENTATIVI - att["tentativi"]
            return False, ("Il codice non è giusto. Controlla l’ultima mail arrivata." if resta > 0
                           else "Troppi codici sbagliati. Premi «Mandami un codice nuovo».")
        _segna_si(d, att["nome"], ip or att.get("ip"), browser or att.get("browser"), att.get("scelte") or [],
                  {"tipo": "codice per mail", "mail": att["mail"],
                   "mandato": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(att.get("mandato") or time.time()))})
        _scrivi(lid, d)
    return True, ""


def altra_mail(lid):
    """Il cliente ha sbagliato a scrivere la mail: si torna al primo modulo."""
    with _chiave:
        d = _leggi(lid)
        if d and d.get("in_attesa") and not d.get("accettato"):
            d.pop("in_attesa", None)
            _scrivi(lid, d)


def in_attesa(d):
    att = (d or {}).get("in_attesa")
    return att if att and not d.get("accettato") else None


def firmato_sul_posto(corpo, ditta="", titolo="", telefono="", da_ip="", extra=None, nome="",
                      browser="", indirizzo="https://rilievoapp.it", manda=None):
    """La copia di un preventivo firmato col dito davanti all'artigiano (firma sul posto (11/11/2026)).
    Si conserva come un link gia' accettato, con l'ora del server, e se c'e' la mail del
    cliente gli si manda la sua copia. Torna (risposta, None) oppure (None, errore)."""
    nome = re.sub(r"\s+", " ", str(nome or "")).strip()[:80]
    if len(nome) < 2:
        return None, "Manca il nome di chi ha firmato."
    r, errore = metti(corpo, ditta, titolo, telefono, da_ip, extra)
    if not r:
        return None, errore
    lid = r["link"]
    with _chiave:
        d = _leggi(lid)
        d["accettato"] = {"quando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "nome": nome,
                          "ip": str(da_ip or "")[:60], "browser": str(browser or "")[:200],
                          "impronta": d.get("impronta"), "scelte": [], "totale": totale_con(d, []),
                          "verifica": {"tipo": "firma sul posto"}}
        _scrivi(lid, d)
        mail, quando = d.get("mail_cliente") or "", d["accettato"]["quando"]
    r["quando"] = quando
    r["copia"] = "senza_mail"
    if mail and manda:
        url = indirizzo.rstrip("/") + "/p/" + lid
        chi = str(ditta or "").strip()
        testo = (f"Buongiorno {nome},\n\n"
                 f"ecco la tua copia del preventivo che hai firmato" + (f" con {chi}" if chi else "") +
                 f" il {_data(quando)}:\n\n{url}\n\n"
                 "La trovi sempre a questo indirizzo. Se qualcosa non torna, rispondi a chi ti ha fatto il preventivo.\n\n"
                 "Rilievo")
        try:
            partita = bool(manda(mail, "La tua copia del preventivo firmato" + (f" · {chi}" if chi else ""), testo))
        except Exception:                   # noqa: BLE001
            partita = False
        r["copia"] = "mandata" if partita else "non_partita"
        if partita:
            r["mail"] = mail
            with _chiave:
                d = _leggi(lid)
                d["accettato"]["verifica"]["copia_a"] = mail
                _scrivi(lid, d)
    return r, None


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
            # controllo 12/11 sera: si ricorda quanto chiedeva la pagina quando ha pagato
            d["bonifico"] = {"quando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "importo": _importo_da_pagare(d)}
            _scrivi(lid, d)
        return True


def aggiungi_dopo(lid, con=None):
    """Il cliente, dopo il si', chiede uno o piu' lavori in piu'. Torna (True, nuove) o (False, motivo)."""
    with _chiave:
        d = _leggi(lid)
        if not _vivo(d) or not d.get("accettato"):
            return False, "Questo preventivo non è più valido."
        scelte = scelte_buone(d, con)
        if not scelte:
            return False, "Spunta il lavoro che vorresti aggiungere."
        nuove = _metti_in_piu(d, scelte)
        if nuove:
            _scrivi(lid, d)
    return True, nuove


def decidi_in_piu(lid, gestione, chi, esito):
    """La risposta dell'artigiano: esito "si" o "no" per le voci `chi` (numeri k).
    Si puo' cambiare idea. Torna la risposta per l'app oppure None."""
    if esito not in ("si", "no"):
        return None
    ks = set()
    for x in (chi if isinstance(chi, list) else [chi])[:30]:
        try:
            ks.add(int(x))
        except (TypeError, ValueError):
            pass
    with _chiave:
        d = _leggi(lid)
        if not _vale(d, gestione) or d.get("ritirato") or not d.get("accettato"):
            return None
        cambiate = 0
        for x in d.get("in_piu") or []:
            if x["k"] in ks and x.get("esito") != esito:
                x["esito"], x["deciso"] = esito, _adesso()
                cambiate += 1
        si = confermate(d)
        d["accettato"]["scelte"] = si
        d["accettato"]["totale"] = totale_con(d, si)
        if cambiate:
            _scrivi(lid, d)
        return {"in_piu": [dict(x) for x in d.get("in_piu") or []], "scelte": si,
                "totale": d["accettato"]["totale"], "cambiate": cambiate}


# ---- le mail: all'artigiano quando il cliente risponde, al cliente l'esito -----
# ?entra=1 (controllo 12/11 sera): apre l'app e chiede mail e password anche su un telefono nuovo
INDIRIZZO_APP = "https://rilievoapp.it/?entra=1"
INDIRIZZO_SITO = "https://rilievoapp.it"
_FINTE = ("@example.", "collaud")


def niente_posta(d, mail):
    """Nessuna mail per i codici di prova o degli amici, ne' a indirizzi finti dei collaudi."""
    m = str(mail or "").lower()
    if not m or any(x in m for x in _FINTE):
        return True
    try:
        import avvisi
        no = set(avvisi._CODICI_SENZA_BENVENUTO)
    except Exception:                       # noqa: BLE001
        no = {"prova-andrea", "marco-472d"}
    no |= {x.strip().lower() for x in (os.environ.get("RILIEVO_BENVENUTO_NO") or "").split(",") if x.strip()}
    if (d.get("da") or "") in no:
        return True
    return (d.get("ditta") or "").strip() == "Giardini Conti"      # la dimostrazione


def _il_preventivo(titolo):
    """«il preventivo «Sfalcio»», ma «Preventivo n. 12» senza ripetere la parola."""
    titolo = str(titolo or "").strip()
    if not titolo or titolo.lower() == "il preventivo":
        return "il preventivo"
    return f"«{titolo}»" if titolo.lower().startswith("preventivo") else f"il preventivo «{titolo}»"


def _voci_scritte(voci):
    return "\n".join(f"- {v['nome']} · {_euro(v['importo'])}" for v in voci)


def _elenco(nomi):
    nomi = [n[:1].lower() + n[1:] for n in nomi]
    return nomi[0] if len(nomi) == 1 else ", ".join(nomi[:-1]) + " e " + nomi[-1]


def avvisa_ditta(lid, cose, manda):
    """Una mail all'artigiano per ogni evento nuovo del cliente: cose fra "accettato",
    "in_piu", "cambia", "no", "bonifico". Ogni evento si scrive una volta sola (segnato
    prima di spedire). Il si' e i lavori in piu' arrivati insieme vanno in una mail sola.
    Torna True se e' partita."""
    with _chiave:
        d = _leggi(lid)
        if not d or not d.get("mail_ditta") or niente_posta(d, d["mail_ditta"]):
            return False
        fatti = d.setdefault("avvisi_ditta", {})
        nuove = []
        if "in_piu" in cose:
            visti = set(fatti.get("in_piu") or [])
            nuove = [x for x in d.get("in_piu") or [] if x["k"] not in visti and x.get("esito") is None]
            if nuove:
                fatti["in_piu"] = sorted(visti | {x["k"] for x in nuove})
        altre = [c for c in ("accettato", "cambia", "no", "bonifico") if c in cose and not fatti.get(c)]
        for c in altre:
            fatti[c] = _adesso()
        if not nuove and not altre:
            return False
        _scrivi(lid, d)
    a, T = d["mail_ditta"], d.get("titolo") or "il preventivo"
    acc, risp = d.get("accettato") or {}, d.get("risposta") or {}
    N = acc.get("nome") or "Il cliente"
    piede = "\n\nTi scriviamo solo quando un cliente risponde. Lo spegni in Impostazioni."
    mail = []
    lavoro = "un lavoro" if len(nuove) == 1 else "dei lavori"
    totale = f"Totale accettato: {_euro(acc['totale'])}.\n\n" if acc.get("totale") is not None else ""
    chiedi = (f"\n\nPrima di farlo ti serve il tuo sì. Apri Rilievo e rispondi: {INDIRIZZO_APP}")
    if "accettato" in altre and nuove:
        mail.append((f"{N} ha accettato il preventivo e vorrebbe aggiungere {lavoro} · {T}",
                     f"Buone notizie: {N} ha accettato {_il_preventivo(T)}.\n\n" + totale +
                     f"Vorrebbe aggiungere anche:\n{_voci_scritte(nuove)}" + chiedi + piede))
    elif "accettato" in altre:
        mail.append((f"{N} ha accettato il preventivo · {T}",
                     f"Buone notizie: {N} ha accettato {_il_preventivo(T)}.\n\n" + totale +
                     f"Apri Rilievo e fissa il giorno del lavoro: {INDIRIZZO_APP}" + piede))
    elif nuove:
        mail.append((f"{N} vorrebbe aggiungere {lavoro} · {T}",
                     f"{N} vorrebbe aggiungere al preventivo «{T}»:\n{_voci_scritte(nuove)}" + chiedi + piede))
    if "cambia" in altre:
        mail.append((f"Il cliente vorrebbe cambiare qualcosa · {T}",
                     f"Il cliente ha scritto: «{str(risp.get('testo') or '')[:300]}».\n\n"
                     f"Lo trovi in Oggi su Rilievo: {INDIRIZZO_APP}" + piede))
    if "no" in altre:
        mail.append((f"Il cliente ha detto no · {T}",
                     f"Motivo: {dict(MOTIVI_NO).get(risp.get('motivo'), 'Altro')}.\n\n"
                     + (f"Ha scritto: «{str(risp.get('nota') or '')[:200]}».\n\n" if risp.get("nota") else "") +
                     f"Su Rilievo il preventivo è fra i rifiutati: {INDIRIZZO_APP}" + piede))
    if "bonifico" in altre:
        pagato = (d.get("bonifico") or {}).get("importo")
        mail.append((f"{N} dice di aver pagato · {T}",
                     f"{N} dice di aver fatto il bonifico" + (f" di {_euro(pagato)}" if pagato else "") + ".\n\n"
                     f"Controlla in banca. Quando lo vedi arrivato, segna su Rilievo che è pagato: {INDIRIZZO_APP}" + piede))
    ok = False
    for oggetto, testo in mail:
        try:
            ok = bool(manda(a, oggetto, testo)) or ok
        except Exception as e:              # noqa: BLE001
            print("  avviso alla ditta non partito:", type(e).__name__)
    return ok


def avvisa_cliente(lid, manda):
    """Al cliente l'esito dei lavori in piu' appena deciso. Una mail per ogni esito nuovo."""
    with _chiave:
        d = _leggi(lid)
        acc = (d or {}).get("accettato") or {}
        a = (acc.get("verifica") or {}).get("mail") or (d or {}).get("mail_cliente") or ""
        if not d or not acc or not mail_buona(a) or niente_posta(d, a):
            return False
        fatti = d.setdefault("avvisi_cliente", {})
        si, no = [], []
        for x in d.get("in_piu") or []:
            if x.get("esito") in ("si", "no") and fatti.get(str(x["k"])) != x["esito"]:
                fatti[str(x["k"])] = x["esito"]
                (si if x["esito"] == "si" else no).append(x)
        if not si and not no:
            return False
        _scrivi(lid, d)
    ditta = (d.get("ditta") or "").strip() or "La ditta"
    T = d.get("titolo") or "il preventivo"
    url = INDIRIZZO_SITO + "/p/" + lid
    # 3: se il cliente risponde, la risposta va alla ditta, non a info@
    rispondi_a = d.get("mail_ditta") if mail_buona(d.get("mail_ditta") or "") else ""
    rispondi = f"Per rispondere a {ditta} basta rispondere a questa mail.\n\n" if rispondi_a else ""
    ok = False
    if si:
        tot = acc.get("totale")
        testo = (f"Buongiorno,\n\n{ditta} ha confermato: farà anche {_elenco([x['nome'] for x in si])}."
                 + (f" Nuovo totale {_euro(tot)}." if tot is not None else "")
                 + f"\n\nIl preventivo aggiornato è qui: {url}\n\n{rispondi}Rilievo")
        cosa = "il lavoro in più" if len(si) == 1 else "i lavori in più"
        try:
            ok = bool(manda(a, f"{ditta} ha confermato {cosa} · {T}", testo, rispondi_a=rispondi_a)) or ok
        except Exception as e:              # noqa: BLE001
            print("  esito al cliente non partito:", type(e).__name__)
    if no:
        testo = (f"Buongiorno,\n\n{ditta} per questa volta non riesce ad aggiungere {_elenco([x['nome'] for x in no])}. "
                 f"Il resto del lavoro resta confermato: ne parlate di persona.\n\nIl preventivo è qui: {url}\n\n{rispondi}Rilievo")
        cosa = "Il lavoro in più" if len(no) == 1 else "I lavori in più"
        try:
            ok = bool(manda(a, f"{cosa}, per questa volta no · {T}", testo, rispondi_a=rispondi_a)) or ok
        except Exception as e:              # noqa: BLE001
            print("  esito al cliente non partito:", type(e).__name__)
    return ok


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
.carta{max-width:920px;margin:12px auto}
.carta .foglio{margin:0 auto 18px!important}
.accetta{max-width:920px;margin:0 auto 24px;padding:26px 20px;font-family:'EB Garamond','Cormorant',Georgia,serif;font-size:17px;scroll-margin-top:12px;color:#2B2822;border-top:1px solid #D9D1C2}
.accetta h2{font-weight:400;font-size:24px;margin:0 0 6px}
.accetta p{margin:6px 0 14px;color:#4A463F;line-height:1.5}
.accetta input[type=text],.accetta input[type=email]{width:100%;box-sizing:border-box;font-size:19px;padding:14px;border:1px solid #BDB4A3;background:#fff;font-family:inherit;margin-bottom:8px}
.accetta label.ok{display:flex;gap:10px;align-items:flex-start;margin:6px 0;padding:10px 0;min-height:44px;box-sizing:border-box;font-size:16px}
.accetta label.ok input{width:22px;height:22px;margin-top:2px;flex:0 0 22px}
.accetta button{font-size:18px;padding:15px 28px;border:0;background:#2B2822;color:#F4EFE6;font-family:inherit;letter-spacing:.04em}
.accetta .fatto{font-size:22px;color:#4F6B4A}
.accetta .errore{color:#9A4B3A}
.accetta .contatti a{color:#2B2822;margin-right:18px;white-space:nowrap;display:inline-flex;align-items:center;min-height:44px}
.accetta input.codice{font-size:26px;letter-spacing:.3em}
.accetta input.codice::placeholder{letter-spacing:normal;font-size:19px}
.accetta .ancora{display:flex;flex-wrap:wrap;gap:10px;margin:4px 0 16px}
.accetta .tasto-nascosto{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}
.cambiato{max-width:920px;margin:12px auto 0;padding:14px 20px;box-sizing:border-box;font-family:'EB Garamond','Cormorant',Georgia,serif;font-size:17px;color:#2B2822;background:#FBF8F2;border:1px solid #D9D1C2}
.nota{font-size:15px;color:#5E584E}
.accetta button.secondario,.pagare button.secondario{background:#fff;color:#2B2822;border:1px solid #2B2822;font-size:16px;padding:12px 20px}
.aggiungi{border:1px solid #D9D1C2;padding:14px 16px 16px;margin:0 0 18px;background:#FBF8F2}
.aggiungi h3{font-weight:400;font-size:20px;margin:0 0 4px}
.aggiungi .totale-scelto{font-size:18px;margin:10px 0 12px}
.altre{margin-top:26px;border-top:1px solid #D9D1C2;padding-top:14px}
.altre summary{cursor:pointer;font-size:17px;color:#4A463F;padding:10px 0;min-height:44px;box-sizing:border-box}
.altre form{margin:14px 0 6px}
.altre textarea,.altre input[type=text]{width:100%;box-sizing:border-box;font-size:17px;padding:12px;border:1px solid #BDB4A3;background:#fff;font-family:inherit;margin:4px 0 10px}
.pagare{max-width:920px;margin:12px auto 0;padding:22px 20px;font-family:'EB Garamond','Cormorant',Georgia,serif;color:#2B2822;background:#FBF8F2;border:1px solid #D9D1C2;box-sizing:border-box}
.pagare h2{font-weight:400;font-size:22px;margin:0}
.pagare .cifra{font-size:30px;margin:4px 0 8px}
.pagare p{line-height:1.55;margin:6px 0 12px}
.pagare .iban{font-family:ui-monospace,Menlo,monospace;font-size:14px;letter-spacing:.02em;word-break:normal}
.accetta input[type=checkbox],.accetta input[type=radio]{accent-color:#2B2822}
.pagare button{font-size:18px;padding:15px 28px;border:0;background:#2B2822;color:#F4EFE6;font-family:inherit}
.pagare .fatto{font-size:19px;color:#4F6B4A}
/* lavori in piu' (12/11/2026) */
.in-piu{max-width:920px;margin:12px auto 0;padding:16px 20px;box-sizing:border-box;font-family:'EB Garamond','Cormorant',Georgia,serif;font-size:17px;color:#2B2822;background:#FBF8F2;border:1px solid #D9D1C2}
.in-piu p{margin:4px 0 8px;line-height:1.5}
.in-piu .attesa{color:#8A6A2E}
.in-piu .si{color:#4F6B4A}
.in-piu .no{color:#9A4B3A}
.accetta .aggiungi button,.in-piu button{min-height:44px}
/* sul link si accetta qui sotto: le righe per la firma a penna non servono */
.carta .firma-doc{display:none}
/* firma sul posto (11/11/2026): la copia firmata mostra la firma */
.carta.posto .firma-doc{display:grid}
</style>"""


def _euro(x):
    v = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "€ " + (v[:-3] if v.endswith(",00") else v)


def _iban_scritto(iban):
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def _importo_da_pagare(d):
    """Quanto chiede adesso il riquadro «Come pagare»: se il cliente ha aggiunto delle voci,
    l'acconto segue il totale che ha scelto."""
    pg = d.get("pagamento") or {}
    importo = pg.get("importo")
    acc = d.get("accettato") or {}
    if importo is not None and acc.get("totale") is not None and d.get("base"):
        importo = round(importo * acc["totale"] / d["base"], 2) if pg.get("acconto") else acc["totale"]
    return importo


def _come_pagare(d, e, ditta, con_tasto):
    pg = d.get("pagamento")
    if not pg:
        return ""
    cosa = "l’acconto" if pg.get("acconto") else "il preventivo"
    importo = _importo_da_pagare(d)
    righe = (f'<div class="pagare"><h2>Come pagare {cosa}</h2>'
             f'<p class="cifra">{e(_euro(importo))}</p>'
             f'<p>Bonifico a <b>{e(pg.get("intestato") or ditta)}</b><br>IBAN<br><span class="iban">{e(_iban_scritto(pg["iban"]))}</span>'
             + (f'<br>Causale: {e(pg["causale"])}' if pg.get("causale") else '') + '</p>')
    pagato = (d.get("bonifico") or {}).get("importo")
    if d.get("bonifico") and pagato and importo - pagato >= 0.01:
        # controllo 12/11 sera: dopo il bonifico la ditta ha confermato un lavoro in piu'
        righe += (f'<p class="fatto">Hai detto di aver fatto il bonifico di {e(_euro(pagato))}. '
                  f'Con i lavori in più confermati la cifra è salita: mancano {e(_euro(round(importo - pagato, 2)))}.</p>'
                  f'<p class="nota">Puoi mandarli con un altro bonifico, oppure mettervi d’accordo con {ditta or "la ditta"}.</p>')
    elif d.get("bonifico"):
        righe += (f'<p class="fatto">Grazie: hai detto di aver fatto il bonifico' + (f' di {e(_euro(pagato))}' if pagato else '')
                  + f'. {ditta or 'La ditta'} controlla e ti conferma.</p>')
    elif con_tasto:
        righe += (f'<form method="post" action="/p/{e(d["_lid"])}/pagato#rispondi">'
                  f'<button type="submit">Ho fatto il bonifico</button></form>'
                  f'<p class="nota">Premilo dopo averlo fatto: {ditta or 'la ditta'} lo sa e controlla in banca.</p>')
    return righe + '</div>'


def pagina(lid, stili_app, messaggio="", errore="", con=None, nome="", mail=""):
    """L'HTML intero della pagina per il cliente, oppure None se il link non c'e'."""
    d = _leggi(lid)
    if not d:
        return None
    d["_lid"] = lid
    e = html.escape
    ditta = e(d.get("ditta") or "")
    chi = ditta or "La ditta"            # all'inizio di una frase
    tel = d.get("telefono") or ""
    def _contatti(domanda):
        if not tel:
            return ""
        wa = tel.lstrip("+")
        if not wa.startswith("39") and len(wa) <= 10:
            wa = "39" + wa
        return (f'<p class="contatti">{domanda} '
                f'<a href="tel:{e(tel)}">Chiama</a><a href="https://wa.me/{e(wa)}" target="_blank" rel="noopener">Scrivi su WhatsApp</a></p>')
    if _scaduto(d):
        if not d.get("ritirato") and not d.get("accettato"):
            corpo = (f'<div class="banda"><div class="chi">{ditta}</div><h1>Il preventivo è scaduto</h1></div>'
                     f'<div class="accetta"><p>Il tempo per accettarlo è passato. Chiedi a {ditta or "chi te l’ha mandato"} di rinnovarlo: '
                     f'ti manda il link nuovo.</p>{_contatti("Per chiederlo:")}</div>')
        else:
            corpo = (f'<div class="banda"><div class="chi">{ditta}</div><h1>'
                     + ('La pagina del preventivo è chiusa' if d.get("accettato") and not d.get("ritirato") else 'Questo preventivo non è più valido')
                     + '</h1></div>'
                     + ('<div class="accetta"><p>Questo preventivo era stato accettato. Dopo sei mesi la pagina si chiude: '
                        f'se ti serve una copia, chiedila a {ditta or "chi te l’ha mandato"}.</p>{_contatti("Per chiederla:")}</div>'
                        if d.get("accettato") and not d.get("ritirato") else
                        '<div class="accetta"><p>Chi te l’ha mandato lo ha ritirato o ne ha fatto uno nuovo. '
                        f'Chiedigli il link aggiornato.</p>{_contatti("Per chiederlo:")}</div>'))
        return _intera(e(d.get("titolo") or "Preventivo"), stili_app, corpo)
    acc = d.get("accettato")
    risp = d.get("risposta") or {}
    # dopo il no la domanda «prima di decidere» non ha piu' senso
    contatti = _contatti("Per qualsiasi cosa:" if acc else "Se ci ripensi:" if risp.get("tipo") == "no"
                         else "Una domanda prima di decidere?")
    fac = d.get("facoltative") or []
    if acc:
        nomi = [v["nome"] for v in fac if v["k"] in (acc.get("scelte") or [])]
        con_che = (", con " + ", ".join(n[:1].lower() + n[1:] for n in nomi)) if nomi else ""
        tot = f' Totale {e(_euro(acc["totale"]))}.' if nomi and acc.get("totale") is not None else ""
        ver = acc.get("verifica") or {}
        posto = ver.get("tipo") == 'firma sul posto'
        conf = (f' Firmato via mail con OTP, codice mandato a {e(mail_coperta(ver.get("mail","")))}.'
                if ver.get("tipo") == "codice per mail" else "")
        fondo = (_come_pagare(d, e, ditta, True) + _lavori_in_piu(d, e, ditta, con) +
                 f'<div class="accetta"><p class="fatto">Accettato da {e(acc.get("nome",""))}{e(con_che)}, '
                 f'il {e(_data(acc.get("quando","")))}.{tot}</p>'
                 + (f'<p class="nota">{conf.strip()}</p>' if conf else '') +
                 (f'<p>Firmato sul posto, davanti a {ditta or "chi ha fatto il preventivo"}. Questa è la tua copia: '
                    f'la trovi sempre a questo indirizzo.</p>' if posto else f'<p>{chi} lo ha già saputo. Grazie.</p>')
                 + f'{contatti}</div>')
    elif risp.get("tipo") == "no":
        fondo = (f'<div class="accetta" id="rispondi"><p class="fatto">Hai risposto di no.</p>'
                 f'<p>{chi} lo sa. Grazie di averlo detto.</p>{contatti}</div>')
    else:
        scelte = scelte_buone(d, con)
        riquadro = ""
        if fac:
            tot = totale_con(d, scelte)
            riquadro = ('<div class="aggiungi"><h3>Puoi aggiungere</h3>'
                        f'<p class="nota" style="margin:0 0 6px">Se aggiungi un lavoro, {ditta or "la ditta"} ti conferma '
                        'se riesce a farlo nella stessa giornata.</p>'
                        + "".join(f'<label class="ok"><input type="checkbox" name="con" value="{v["k"]}"'
                                  f'{" checked" if v["k"] in scelte else ""}><span>{e(v["nome"])}'
                                  f' <b>+ {e(_euro(v["importo"]))}</b> IVA compresa</span></label>' for v in fac)
                        + (f'<p class="totale-scelto">Con quello che hai scelto: <b>{e(_euro(tot))}</b>, IVA compresa, '
                           f'se {ditta or "la ditta"} ti conferma i lavori in più.</p>'
                           if scelte and tot is not None else '')
                        + '<button type="submit" name="fai" value="conto" formnovalidate class="secondario">Rifai il conto</button></div>')
        gia = (f'<p class="fatto" style="font-size:18px">Hai chiesto di cambiare: “{e(risp.get("testo",""))}”. '
               f'{chi} lo sa e ti risponde.</p>') if risp.get("tipo") == "cambia" else ""
        motivi = "".join(f'<label class="ok"><input type="radio" name="motivo" value="{k}">'
                         f'<span>{e(n)}</span></label>' for k, n in MOTIVI_NO)
        sul_cambio = errore.startswith("Scrivi cosa vorresti")
        att = in_attesa(d)
        mail_nota = d.get("mail_cliente") or ""
        if mail_nota:
            campo_mail = (f'<p class="nota">Ti mandiamo un codice a {e(mail_coperta(mail_nota))} per confermare che sei tu.</p>')
        else:
            campo_mail = (f'<input type="email" name="mail" value="{e(str(mail or "")[:120])}" placeholder="La tua mail" '
                          f'autocomplete="email" inputmode="email" required>'
                          f'<p class="nota" style="margin-top:6px">Ti mandiamo un codice per confermare che sei tu.</p>')
        if att:
            # secondo passo: il codice arrivato per mail
            fondo = (f'<div class="accetta" id="rispondi"><h2>Controlla la posta</h2>'
                     f'<p>Firma via mail con OTP: abbiamo mandato un codice di 6 cifre a <b>{e(mail_coperta(att.get("mail","")))}</b>. '
                     f'Scrivilo qui per firmare il preventivo.</p>'
                     + (f'<p class="errore">{e(errore)}</p>' if errore else '') +
                     f'<form method="post" action="/p/{lid}/codice#rispondi">'
                     f'<input type="text" name="codice" placeholder="Il codice" inputmode="numeric" autocomplete="one-time-code" '
                     f'pattern="[0-9 ]{{6,7}}" maxlength="7" required class="codice">'
                     f'<p style="margin-top:14px"><button type="submit">Firma e accetto</button></p></form>'
                     f'<p class="nota">Non arriva? Guarda anche fra lo spam. Vale {CODICE_MINUTI} minuti.</p>'
                     f'<div class="ancora"><form method="post" action="/p/{lid}/accetto#rispondi">'
                     f'<input type="hidden" name="nome" value="{e(att.get("nome",""))}"><input type="hidden" name="ok" value="1">'
                     + (f'<input type="hidden" name="mail" value="{e(att.get("mail",""))}">' if not mail_nota else '')
                     + "".join(f'<input type="hidden" name="con" value="{k}">' for k in (att.get("scelte") or [])) +
                     f'<button type="submit" class="secondario">Mandami un codice nuovo</button></form> '
                     + ('' if mail_nota else
                        f'<form method="post" action="/p/{lid}/altra#rispondi">'
                        f'<button type="submit" class="secondario">Ho sbagliato la mail</button></form>')
                     + f'</div>{contatti}</div>')
            avviso = ""
            corpo = (f'<div class="banda"><div class="chi">{ditta}</div><h1>{e(d.get("titolo") or "Il tuo preventivo")}</h1></div>'
                     + fondo + f'<div class="carta">{d.get("corpo","")}</div>')
            return _intera(e(d.get("titolo") or "Preventivo"), stili_app, corpo)
        fondo = (f'<div class="accetta" id="rispondi">{gia}<h2>Ti va bene?</h2>'
                 f'<p>Se il preventivo è come lo vuoi, scrivi il tuo nome e premi «Accetto». '
                 f'{chi} lo sa subito.</p>'
                 + (f'<p class="errore">{e(errore)}</p>' if errore and not sul_cambio else '') +
                 f'<form method="post" action="/p/{lid}/accetto#rispondi"><button type="submit" class="tasto-nascosto" tabindex="-1" aria-hidden="true">Accetto</button>{riquadro}'
                 f'<input type="text" name="nome" value="{e(str(nome or "")[:80])}" placeholder="Nome e cognome" autocomplete="name" required minlength="3">'
                 f'{campo_mail}'
                 f'<label class="ok"><input type="checkbox" name="ok" value="1" required>'
                 f'<span>Ho letto il preventivo e lo accetto.</span></label>'
                 f'<button type="submit">Accetto</button></form>{contatti}'
                 f'<p class="nota">Restano segnati il tuo nome, la mail confermata, il giorno e l’ora. Valgono le condizioni '
                 f'scritte nel preventivo.</p>'
                 f'<details class="altre" id="cambia"{" open" if sul_cambio else ""}><summary>Non ti va bene così?</summary>'
                 f'<form method="post" action="/p/{lid}/risposta#cambia"><input type="hidden" name="tipo" value="cambia">'
                 + (f'<p class="errore">{e(errore)}</p>' if sul_cambio else '') +
                 f'<p>Cosa vorresti cambiare?</p><textarea name="testo" rows="3" required minlength="3" '
                 f'placeholder="Per esempio: senza la siepe, oppure a novembre"></textarea>'
                 f'<button type="submit" class="secondario">Vorrei cambiare qualcosa</button></form>'
                 f'<form method="post" action="/p/{lid}/risposta#rispondi"><input type="hidden" name="tipo" value="no">'
                 f'<p>Non lo vuoi fare? Ci aiuta sapere perché.</p>{motivi}'
                 f'<input type="text" name="testo" placeholder="Se vuoi, due parole">'
                 f'<button type="submit" class="secondario">No, grazie</button></form></details></div>')
    avviso = ""
    if acc and acc.get("scelte") and acc.get("totale") is not None:
        avviso = (f'<p class="cambiato">Qui sotto c’è il preventivo com’era prima delle voci che hai aggiunto. '
                  f'Con quelle il totale è <b>{e(_euro(acc["totale"]))}</b>, IVA compresa.</p>')
    # dopo il si' si torna in cima, dove sta «Come pagare»
    corpo = (f'<div class="banda"{' id="rispondi"' if acc else ''}><div class="chi">{ditta}</div><h1>{e(d.get("titolo") or "Il tuo preventivo")}</h1></div>'
             + (fondo + avviso if acc else '') +
             f'<div class="carta{" posto" if acc and ((acc.get("verifica") or {}).get("tipo") == "firma sul posto") else ""}">{d.get("corpo","")}</div>'
             + ('' if acc else fondo))
    return _intera(e(d.get("titolo") or "Preventivo"), stili_app, corpo)


def _lavori_in_piu(d, e, ditta, con=None):
    """Dopo il si': i lavori in piu' (in attesa, confermati, non questa volta) e, finche'
    restano voci da chiedere, il modulo per chiederle. Solo moduli, nessuno script."""
    chi = ditta or "La ditta"
    righe = ""
    for x in d.get("in_piu") or []:
        voce = f'{e(x["nome"])} · {e(_euro(x["importo"]))}'
        if x.get("esito") == "si":
            righe += f'<p class="si">Confermato da {chi}: {voce}</p>'
        elif x.get("esito") == "no":
            righe += (f'<p class="no">{chi} per questa volta non riesce ad aggiungere: {e(x["nome"])}. '
                      'Il resto del lavoro resta confermato: ne parlate di persona.</p>')
        else:
            righe += f'<p class="attesa">In attesa di conferma: {voce}</p>'
    attesa = sum(1 for x in d.get("in_piu") or [] if x.get("esito") is None)
    if righe and attesa == 1:
        righe += f'<p class="nota">{chi} ti conferma se riesce a farlo nella stessa giornata. Finché non conferma, non entra nel totale.</p>'
    elif righe and attesa > 1:
        righe += f'<p class="nota">{chi} ti conferma se riesce a farli nella stessa giornata. Finché non conferma, non entrano nel totale.</p>'
    chiesti = {x["k"] for x in d.get("in_piu") or []} | set((d.get("accettato") or {}).get("scelte") or [])
    restano = [v for v in d.get("facoltative") or [] if v["k"] not in chiesti]
    modulo = ""
    if restano:
        scelte = scelte_buone(d, con)
        modulo = (f'<form method="post" action="/p/{e(d["_lid"])}/aggiungi#in-piu"><p><b>Vuoi aggiungere un lavoro?</b></p>'
                  + "".join(f'<label class="ok" style="display:flex;gap:10px;align-items:flex-start;margin:10px 0">'
                            f'<input type="checkbox" name="con" value="{v["k"]}"{" checked" if v["k"] in scelte else ""} '
                            f'style="width:22px;height:22px;flex:0 0 22px"><span>{e(v["nome"])} <b>+ {e(_euro(v["importo"]))}</b> IVA compresa</span></label>'
                            for v in restano)
                  + f'<p class="nota">Se aggiungi un lavoro, {ditta or "la ditta"} ti conferma se riesce a farlo nella stessa giornata.</p>'
                  '<button type="submit" class="secondario" style="font-size:16px;padding:12px 20px;background:#fff;color:#2B2822;border:1px solid #2B2822">Chiedi di aggiungerlo</button></form>')
    if not righe and not modulo:
        return ""
    return f'<div class="in-piu" id="in-piu">{righe}{modulo}</div>'


def pagina_mancante(stili_app=""):
    """Il link sbagliato o finito: prima era una riga bianca senza stile, minuscola sul telefono."""
    corpo = ('<div class="banda"><h1>Questo link non funziona</h1></div>'
             '<div class="accetta"><p>Forse è scaduto, è stato ritirato o è stato copiato a metà. '
             'Chiedi a chi te l’ha mandato il link giusto.</p></div>')
    return _intera("Rilievo", stili_app, corpo)


def _data(iso):
    """Il giorno e l'ora in Italia: il server di Render vive in ora di Greenwich."""
    try:
        import datetime, zoneinfo
        t = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        t = t.astimezone(zoneinfo.ZoneInfo("Europe/Rome"))
        mesi = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
                "settembre", "ottobre", "novembre", "dicembre"]
        return f"{t.day} {mesi[t.month - 1]} {t.year} alle {t:%H:%M}"
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
        # fino a </head>, non al primo <script>: dal 1/11/2026 c'e' lo script dei dati per
        # Google prima degli stili, e il preventivo del cliente usciva senza stile
        testa = t.split("</head>", 1)[0]
        _stili_cache.update(mtime=m, testo="".join(re.findall(r"<style\b[^>]*>.*?</style>", testa, re.S)))
    return _stili_cache["testo"]
