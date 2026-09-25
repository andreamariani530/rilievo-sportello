#!/usr/bin/env python3
"""L'archivio condiviso di una ditta: i lavori fuori dal telefono.

PERCHE' ESISTE
Il 24 settembre 2026 una ditta di spurghi ha chiesto Rilievo per una cosa sola:
vedere l'elenco dei lavori con lo stato di ognuno, in tre persone. Fino a oggi
tutto quello che si scrive in Rilievo sta nella memoria del telefono di chi lo
usa, e due telefoni non si vedono. Questo file e' il posto dove i lavori stanno
una volta sola, e dove tutti guardano.

CHI NON HA UNA DITTA NON PASSA MAI DI QUI. Chi usa Rilievo da solo continua a
lavorare sul suo telefono, identico a prima: l'archivio si accende dando a
qualcuno un codice che nomina una ditta, non si impone a nessuno.

DOVE STANNO I DATI
Una cartella, un file per ditta. La cartella si sceglie con la variabile
d'ambiente RILIEVO_ARCHIVIO, e questo e' voluto:

  - sul piano gratuito di Render il disco si azzera a ogni riavvio, e per
    costruire e provare va benissimo: si perde della roba finta.
  - il giorno che si paga il disco permanente (0,25 $ al giga al mese) si punta
    RILIEVO_ARCHIVIO li' dentro e non si tocca una riga di codice.

Non e' un database perche' non serve: una ditta di tre persone fa qualche
centinaio di lavori l'anno, e un file JSON letto e riscritto intero e' piu'
facile da salvare, da leggere a occhio e da rimettere a posto se si rompe.

COME SI RISOLVE CHE IN DUE CAMBIANO LA STESSA COSA
Campo per campo, vince l'ultimo che ha toccato QUEL campo. Non l'ultimo che ha
premuto salva. Il motivo e' pratico: in cantiere capita che il capo corregga il
prezzo mentre l'operaio sposta lo stato, e con la regola dell'ultimo che salva
uno dei due lavori sparirebbe senza che nessuno se ne accorga. Cosi' invece
sopravvivono tutti e due, perche' hanno toccato campi diversi.

Se due persone toccano davvero lo stesso campo nello stesso identico secondo,
vince il codice piu' basso in ordine alfabetico. Non e' giusto, e' solo
prevedibile: serve perche' due telefoni che si sincronizzano in ordine diverso
devono finire con lo stesso risultato, se no si rincorrono per sempre.

La storia degli spostamenti non si sovrascrive mai: si uniscono le righe di
tutti e due, e le doppie si buttano. Cosi' «chi lo ha spostato e quando» resta
vero anche dopo una sincronizzazione andata storta.

L'OROLOGIO
Ogni volta che si scrive, un contatore della ditta sale di uno e finisce dentro
ogni riga toccata (`battuta`). Un telefono che si ricollega chiede «dammi tutto
quello che e' cambiato dopo la battuta 41» e si porta a casa solo quello. E' il
pezzo su cui poggia il lavoro senza campo.
"""
import json, os, pathlib, re, threading, time

QUI = pathlib.Path(__file__).parent
CARTELLA = pathlib.Path(os.environ.get("RILIEVO_ARCHIVIO") or (QUI / "archivio"))

# una scrittura per volta: il server risponde su piu' fili contemporaneamente e
# due salvataggi nello stesso istante si mangerebbero a vicenda
_chiave = threading.RLock()

# i campi di un lavoro. Sono questi e basta: quello che arriva in piu' si butta,
# cosi' un telefono aggiornato male non puo' riempire l'archivio di spazzatura.
CAMPI = ("cliente", "indirizzo", "telefono", "prezzo", "stato", "chi_segue",
         "note", "quando", "cancellato")

# cosa puo' cambiare chi ha il ruolo "squadra" (l'operaio): lo stato, chi lo
# segue e le note. Il prezzo e l'indirizzo li mette il capo.
CAMPI_DELLA_SQUADRA = ("stato", "chi_segue", "note")

# Gli stati di partenza di una ditta nuova. NON sono i passaggi del giardiniere:
# sono i passaggi di chiunque venda un lavoro a qualcuno. Ogni ditta li
# rinomina, ne toglie e ne aggiunge: uno che fa spurghi e uno che pota siepi non
# hanno gli stessi passaggi, e questo e' un paletto di Andrea, non un dettaglio.
STATI_DI_PARTENZA = [
    {"chiave": "da-chiamare", "nome": "Da chiamare",        "chiude": False},
    {"chiave": "chiamato",    "nome": "Chiamato",           "chiude": False},
    {"chiave": "preventivo",  "nome": "Preventivo mandato", "chiude": False},
    {"chiave": "accettato",   "nome": "Accettato",          "chiude": False},
    {"chiave": "da-fare",     "nome": "Da fare",            "chiude": False},
    {"chiave": "fatto",       "nome": "Fatto",              "chiude": False},
    {"chiave": "pagato",      "nome": "Pagato",             "chiude": True},
    {"chiave": "niente",      "nome": "Non se ne fa niente", "chiude": True},
]

MAX_LUNGHEZZA = 4000            # quanto puo' essere lungo un campo di testo
MAX_STORIA = 200                # quante righe di storia si tengono per lavoro


# ------------------------------------------------- roba da poco

def adesso():
    """L'istante, al millesimo, nella stessa identica forma che scrive il telefono
    (`new Date().toISOString()`). Il millesimo non e' pignoleria: al secondo, due
    persone che toccano lo stesso campo nello stesso secondo finiscono in parita', e
    una delle due modifiche sparisce senza che nessuno se ne accorga."""
    t = time.time()
    return "%s.%03dZ" % (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)),
                         int((t % 1) * 1000))


def _istante(t):
    """Mette in riga un istante che arriva senza millesimi, se no il confronto
    fra «...:33Z» e «...:33.500Z» lo vincerebbe quello scritto peggio."""
    t = _testo(t)[:30]
    if re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", t):
        return t[:-1] + ".000Z"
    return t


def _pulisci_nome_file(s):
    return re.sub(r"[^A-Z0-9-]", "", str(s or "").upper())[:24]


def _testo(v):
    return str(v if v is not None else "")[:MAX_LUNGHEZZA]


def _soldi(v):
    """450 · 450,00 · 1.250,50 · «€ 300»: sono tutti modi in cui un artigiano
    scrive un prezzo, e tornano tutti un numero. Quello che non e' un prezzo
    torna zero, che si vede subito ed e' meglio di un salvataggio rifiutato."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(float(v), 2)
    t = re.sub(r"[^0-9,.\-]", "", str(v or ""))
    if not t:
        return 0.0
    if "," in t:                      # la virgola e' il decimale, il punto le migliaia
        t = t.replace(".", "").replace(",", ".")
    try:
        return round(float(t), 2)
    except ValueError:
        return 0.0


def chiave_stato(s):
    """Il nome di uno stato ridotto a qualcosa che si puo' scrivere in un indirizzo."""
    s = re.sub(r"[^a-z0-9]+", "-", str(s or "").strip().lower()).strip("-")
    return s[:40]


def _file_della_ditta(ditta):
    d = _pulisci_nome_file(ditta)
    if not d:
        raise ValueError("Senza ditta non c'e' nessun archivio.")
    return CARTELLA / ("%s.json" % d)


# ------------------------------------------------- aprire e chiudere il cassetto

def _vuoto(ditta):
    return {"ditta": _pulisci_nome_file(ditta), "orologio": 0,
            "stati": [dict(x) for x in STATI_DI_PARTENZA],
            "lavori": {}, "nato": adesso()}


def leggi(ditta):
    """Tutto l'archivio di una ditta. Se non c'e' ancora, ne torna uno vuoto."""
    f = _file_della_ditta(ditta)
    try:
        roba = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _vuoto(ditta)
    if not isinstance(roba, dict) or not isinstance(roba.get("lavori"), dict):
        return _vuoto(ditta)
    roba.setdefault("ditta", _pulisci_nome_file(ditta))
    roba.setdefault("orologio", 0)
    if not roba.get("stati"):
        roba["stati"] = [dict(x) for x in STATI_DI_PARTENZA]
    return roba


def _scrivi(roba):
    """Salva l'archivio. Prima in un file accanto, poi lo si sposta al suo posto:
    cosi' un server che muore a meta' scrittura non lascia un archivio monco."""
    CARTELLA.mkdir(parents=True, exist_ok=True)
    f = _file_della_ditta(roba["ditta"])
    provvisorio = f.with_suffix(".json.mentre-scrivo")
    provvisorio.write_text(json.dumps(roba, ensure_ascii=False), encoding="utf-8")
    os.replace(provvisorio, f)


# ------------------------------------------------- gli stati

def stati(ditta):
    return leggi(ditta).get("stati") or [dict(x) for x in STATI_DI_PARTENZA]


def scrivi_stati(ditta, elenco, chi):
    """I nomi degli stati li sceglie la ditta. Li cambia solo il capo.

    Torna (nuovi_stati, motivo_del_no). Uno stato usato da un lavoro non si puo'
    cancellare: se sparisse, quei lavori finirebbero in nessuna linguetta e
    sembrerebbero persi. Si rinomina, quello si'.
    """
    with _chiave:
        roba = leggi(ditta)
        puliti, viste = [], set()
        for x in (elenco or []):
            if not isinstance(x, dict):
                continue
            nome = _testo(x.get("nome")).strip()[:40]
            if not nome:
                continue
            k = chiave_stato(x.get("chiave") or nome)
            if not k or k in viste:
                continue
            viste.add(k)
            puliti.append({"chiave": k, "nome": nome, "chiude": bool(x.get("chiude"))})
        if not puliti:
            return None, "Serve almeno uno stato."
        if len(puliti) > 20:
            return None, "Venti stati sono gia' troppi: chi li deve usare non li ricorda."
        usati = set()
        for lav in roba["lavori"].values():
            if not lav.get("cancellato"):
                usati.add(lav.get("stato") or "")
        persi = sorted(u for u in usati if u and u not in viste)
        if persi:
            nomi = {s["chiave"]: s["nome"] for s in (roba.get("stati") or [])}
            return None, ("Non posso togliere «%s»: ci sono ancora dei lavori li' dentro. "
                          "Spostali prima, oppure cambiagli solo il nome."
                          % (nomi.get(persi[0]) or persi[0]))
        roba["stati"] = puliti
        roba["orologio"] = int(roba.get("orologio") or 0) + 1
        roba["stati_tocco"] = {"quando": adesso(), "chi": (chi or {}).get("codice", "")}
        _scrivi(roba)
        return puliti, ""


# ------------------------------------------------- i lavori

def _nuovo(id_lavoro):
    return {"id": id_lavoro, "cliente": "", "indirizzo": "", "telefono": "",
            "prezzo": 0.0, "stato": "", "chi_segue": "", "note": "",
            "quando": "", "cancellato": False,
            "tocchi": {}, "storia": [], "battuta": 0}


def _valore_pulito(campo, v):
    if campo == "prezzo":
        return _soldi(v)
    if campo == "cancellato":
        return bool(v)
    if campo == "stato":
        return chiave_stato(v)
    return _testo(v)


def _vince(quando_nuovo, chi_nuovo, quando_vecchio, chi_vecchio):
    """Chi ha toccato per ultimo questo campo? A parita' esatta, il codice piu'
    basso in ordine alfabetico: non e' giusto, e' prevedibile, e serve perche'
    due telefoni che si sincronizzano in ordine diverso devono finire uguali."""
    if not quando_vecchio:
        return True
    if quando_nuovo > quando_vecchio:
        return True
    if quando_nuovo < quando_vecchio:
        return False
    # stesso istante e stessa persona: non e' un conflitto, e' lei che si corregge.
    # Senza questa riga uno che tocca due volte lo stesso campo nello stesso secondo
    # si bloccherebbe da solo, e la seconda modifica sparirebbe in silenzio.
    if str(chi_nuovo or "") == str(chi_vecchio or ""):
        return True
    return str(chi_nuovo or "") < str(chi_vecchio or "")


def _unisci_storia(vecchia, nuova):
    viste, fuori = set(), []
    for riga in list(vecchia or []) + list(nuova or []):
        if not isinstance(riga, dict):
            continue
        r = {"quando": _istante(riga.get("quando")),
             "chi": _testo(riga.get("chi"))[:40],
             "nome": _testo(riga.get("nome"))[:60],
             "cosa": _testo(riga.get("cosa"))[:20],
             "da": _testo(riga.get("da"))[:60], "a": _testo(riga.get("a"))[:60]}
        impronta = (r["quando"], r["chi"], r["cosa"], r["da"], r["a"])
        if impronta in viste:
            continue
        viste.add(impronta)
        fuori.append(r)
    fuori.sort(key=lambda r: r["quando"])
    return fuori[-MAX_STORIA:]


def salva(ditta, chi, arrivati):
    """Mette dentro quello che arriva da un telefono e torna cosa ne e' stato.

    `chi` e' quello che torna da chi_e() nel server: dice il codice, il nome e
    il ruolo. `arrivati` e' l'elenco dei lavori come li ha quel telefono.

    Torna {"orologio": n, "salvati": [id...], "respinti": [{id, perche}],
           "lavori": [i lavori come sono adesso]}.
    """
    quando_ora = adesso()
    codice = (chi or {}).get("codice", "")
    nome_chi = (chi or {}).get("nome", "") or codice
    ruolo = (chi or {}).get("ruolo", "")
    salvati, respinti, tornati = [], [], []

    with _chiave:
        roba = leggi(ditta)
        cambiato = False
        battuta = int(roba.get("orologio") or 0) + 1

        for grezzo in (arrivati or []):
            if not isinstance(grezzo, dict):
                continue
            id_lavoro = re.sub(r"[^A-Za-z0-9_-]", "", _testo(grezzo.get("id")))[:40]
            if not id_lavoro:
                respinti.append({"id": "", "perche": "Questo lavoro non ha un numero suo."})
                continue

            gia_c_e = id_lavoro in roba["lavori"]
            if not gia_c_e and ruolo != "capo" and not (chi or {}).get("posso", {}).get("mette"):
                respinti.append({"id": id_lavoro,
                                 "perche": "I lavori nuovi li mette dentro il capo."})
                continue
            if not gia_c_e and grezzo.get("cancellato"):
                continue            # cancellare una cosa che non c'e': niente da fare

            lav = roba["lavori"].get(id_lavoro) or _nuovo(id_lavoro)
            tocchi = lav.get("tocchi") or {}
            tocchi_arrivati = grezzo.get("tocchi") if isinstance(grezzo.get("tocchi"), dict) else {}
            mette = bool((chi or {}).get("posso", {}).get("mette"))
            qualcosa = False
            fermati = []

            for campo in CAMPI:
                if campo not in grezzo:
                    continue
                valore = _valore_pulito(campo, grezzo.get(campo))
                if valore == lav.get(campo):
                    continue                    # questo campo non cambia: niente da fare
                # prima il permesso: un campo che non cambia non arriva mai fin qui
                # (l'abbiamo saltato sopra), quindi qui si dice sempre la stessa cosa
                # a parita' di gesto, e non a seconda di chi ha salvato un attimo prima
                if not mette and campo not in CAMPI_DELLA_SQUADRA:
                    fermati.append(campo)
                    continue
                if campo == "cancellato" and valore and not mette:
                    fermati.append(campo)
                    continue
                t = tocchi_arrivati.get(campo) if isinstance(tocchi_arrivati.get(campo), dict) else {}
                quando_nuovo = _istante(t.get("quando")) or quando_ora
                chi_nuovo = _testo(t.get("chi"))[:40] or codice
                vecchio = tocchi.get(campo) or {}
                if not _vince(quando_nuovo, chi_nuovo,
                              _istante(vecchio.get("quando")), vecchio.get("chi")):
                    continue                    # qualcun altro ha toccato questo campo dopo
                if campo == "stato" and lav.get("stato"):
                    lav["storia"] = _unisci_storia(lav.get("storia"), [{
                        "quando": quando_nuovo, "chi": chi_nuovo, "nome": nome_chi,
                        "cosa": "stato", "da": lav.get("stato") or "", "a": valore}])
                lav[campo] = valore
                tocchi[campo] = {"quando": quando_nuovo, "chi": chi_nuovo}
                qualcosa = True

            if isinstance(grezzo.get("storia"), list):
                unita = _unisci_storia(lav.get("storia"), grezzo["storia"])
                if unita != lav.get("storia"):
                    lav["storia"] = unita
                    qualcosa = True

            if fermati:
                nomi_campi = {"cliente": "il cliente", "indirizzo": "l'indirizzo",
                              "telefono": "il telefono", "prezzo": "il prezzo",
                              "quando": "la data", "cancellato": "cancellarlo"}
                respinti.append({"id": id_lavoro, "campi": fermati,
                                 "perche": "Questo lo cambia il capo: %s."
                                           % ", ".join(nomi_campi.get(c, c) for c in fermati)})

            if not qualcosa and gia_c_e:
                tornati.append(_fuori(lav))
                continue

            if not lav.get("quando"):
                lav["quando"] = quando_ora
            if not lav.get("stato"):
                primo = (roba.get("stati") or STATI_DI_PARTENZA)[0]
                lav["stato"] = primo["chiave"]
            if not gia_c_e:
                # la prima riga della storia: chi lo ha aperto e quando. Serve a
                # rispondere «questo chi l'ha messo dentro?» senza chiedere in giro
                lav["storia"] = _unisci_storia(lav.get("storia"), [{
                    "quando": quando_ora, "chi": codice, "nome": nome_chi,
                    "cosa": "aperto", "da": "", "a": lav["stato"]}])
            lav["tocchi"] = tocchi
            lav["battuta"] = battuta
            roba["lavori"][id_lavoro] = lav
            salvati.append(id_lavoro)
            tornati.append(_fuori(lav))
            cambiato = True

        if cambiato:
            roba["orologio"] = battuta
            _scrivi(roba)

    return {"orologio": int(roba.get("orologio") or 0), "salvati": salvati,
            "respinti": respinti, "lavori": tornati}


def _fuori(lav):
    """Il lavoro come lo vede l'app: niente di nascosto, ma copiato."""
    return json.loads(json.dumps(lav, ensure_ascii=False))


def lavori(ditta, dalla=0):
    """I lavori della ditta. Con `dalla` solo quello che e' cambiato dopo.

    I cancellati tornano lo stesso, con cancellato true: servono all'altro
    telefono per togliersi dall'elenco una cosa che non c'e' piu'.
    """
    roba = leggi(ditta)
    try:
        dalla = int(dalla or 0)
    except (TypeError, ValueError):
        dalla = 0
    fuori = [_fuori(l) for l in roba["lavori"].values()
             if int(l.get("battuta") or 0) > dalla]
    fuori.sort(key=lambda l: (l.get("quando") or "", l.get("id") or ""))
    return {"ditta": roba.get("ditta"), "orologio": int(roba.get("orologio") or 0),
            "stati": roba.get("stati") or [dict(x) for x in STATI_DI_PARTENZA],
            "da_capo": not dalla, "quanti": len(fuori), "lavori": fuori}


def come_va(ditta):
    """Due numeri per capire a colpo d'occhio se dentro c'e' qualcosa."""
    roba = leggi(ditta)
    vivi = [l for l in roba["lavori"].values() if not l.get("cancellato")]
    chiusi = {s["chiave"] for s in (roba.get("stati") or []) if s.get("chiude")}
    return {"ditta": roba.get("ditta"), "orologio": int(roba.get("orologio") or 0),
            "quanti": len(vivi),
            "aperti": len([l for l in vivi if l.get("stato") not in chiusi]),
            "dove_sta": str(CARTELLA)}
