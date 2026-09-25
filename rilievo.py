#!/usr/bin/env python3
"""Il rilievo dall'indirizzo: foto dall'alto, confini catastali, metri quadri.

Due modi di usarlo.

    python3 rilievo.py "Via 4 Novembre 105/B, Quartiano"
        prepara il rilievo e lo salva in rilievi/<nome>.json (con dentro la foto).
        Quel file si carica dentro Rilievo dal tasto "Carica un rilievo".

    python3 rilievo.py --servizio
        resta acceso e risponde all'applicazione mentre la usi, così il rilievo
        si prepara premendo un tasto invece che da riga di comando.
        Ferma con Ctrl-C.

Da dove arrivano i dati:

  * l'indirizzo diventa un punto sulla mappa con Nominatim di OpenStreetMap;
  * il punto diventa una particella con il servizio cartografico dell'Agenzia
    delle Entrate (foglio, numero di particella, ingombro);
  * i metri quadri si contano sulla mappa catastale disegnata dall'Agenzia:
    si riempie la particella a partire dal punto e si contano i pixel, poi si
    tolgono i fabbricati. Un pixel vale una misura nota, quindi i pixel
    diventano metri quadri;
  * la foto dall'alto arriva dalle immagini satellitari Esri World Imagery.

Il catasto qualche volta sbaglia, e l'indirizzo può cadere sul civico sbagliato.
Per questo ogni numero che esce di qui si corregge a mano dentro l'applicazione.
"""
import argparse, base64, io, json, math, os, pathlib, re, sys, time, unicodedata
import urllib.parse, urllib.request
import logging
import xml.etree.ElementTree as ET
import archivio            # l'archivio condiviso della ditta
from PIL import Image, ImageDraw, ImageFilter

QUI          = pathlib.Path(__file__).parent
CARTELLA     = QUI / "rilievi"
NOMINATIM    = "https://nominatim.openstreetmap.org/search"
# Photon legge gli stessi dati di OpenStreetMap ma non chiude la porta a chi chiama
# da un server in cloud. Il 19 settembre 2026 "Via del Borgo 13, Longana" si trovava
# da qui e non si trovava da Render: la via e' scritta "Via del Borgo Longana" e solo
# OpenStreetMap la conosce. Serve una seconda porta per gli stessi dati.
PHOTON       = "https://photon.komoot.io/api/"
INDIRIZZI_ESRI = ("https://geocode.arcgis.com/arcgis/rest/services/World/"
                  "GeocodeServer/findAddressCandidates")
CATASTO      = "https://wms.cartografia.agenziaentrate.gov.it/inspire/wms/ows01.php"
# La seconda porta del catasto, su un altro indirizzo di rete. Dà le stesse
# particelle dello sportello "che cosa c'e' in questo punto", ma con il confine
# disegnato punto per punto invece del solo riquadro. Serve soprattutto perche'
# e' una porta diversa: il 20 settembre 2026 la prima ha iniziato a rispondere
# "InvalidFormat ERRX-2" alle domande che partivano da Render, mentre le stesse
# domande, identiche, passavano da qualunque altra rete.
CATASTO_WFS  = "https://wfs.cartografia.agenziaentrate.gov.it/inspire/wfs/owfs01.php"
SATELLITE    = ("https://services.arcgisonline.com/arcgis/rest/services/"
                "World_Imagery/MapServer")
AGENTE       = "Rilievo/1.0 (applicazione per artigiani del verde)"
FONTE        = ("Cartografia catastale dell'Agenzia delle Entrate · "
                "foto dall'alto Esri World Imagery")

# colori: gli stessi dell'applicazione
VERDE   = (122, 143, 106)
CARTA   = (247, 241, 229)
SCURO   = (38, 36, 32)

# quanto deve prevalere il verde su rosso e blu perche' un pixel conti come verde
SOGLIA_VERDE = 15


# ---------------------------------------------------------------- rete

def prendi(url, dati=None, tentativi=3, attesa=1.5, tempo=60):
    """Una chiamata in rete, con due secondi tentativi se la prima non va."""
    if dati:
        url = url + "?" + urllib.parse.urlencode(dati)
    ultimo = None
    for n in range(tentativi):
        try:
            richiesta = urllib.request.Request(url, headers={"User-Agent": AGENTE})
            with urllib.request.urlopen(richiesta, timeout=tempo) as r:
                return r.read()
        except Exception as e:                      # noqa: BLE001
            ultimo = e
            time.sleep(attesa * (n + 1))
    raise RuntimeError("Non risponde: %s (%s)" % (url.split("?")[0], ultimo))


# ------------------------------------------------- dall'indirizzo al punto

# i tipi di risultato di OpenStreetMap che sono una casa. Non una localita' o una
# cascina, che sono un punto che puo' stare lontano dalla casa.
CASE_OSM = ("house", "building", "yes", "residential", "apartments", "detached")


def _e_casa_osm(t):
    """OpenStreetMap chiama "place" il tipo generale di un civico e "house" quello
    preciso: guardarne uno solo faceva perdere il civico giusto. Il 19 settembre 2026
    Via del Borgo 13 a Longana tornava col numero civico giusto e non veniva mai
    considerata "precisa", perche' il suo addresstype e' "place"."""
    return (t.get("type") in CASE_OSM or t.get("addresstype") in CASE_OSM
            or t.get("class") == "building")


def civico_scritto(indirizzo):
    """Il numero civico scritto a mano, pronto da confrontare: "105/B" -> "105b",
    "12 bis" -> "12bis". Si cerca in fondo al primo pezzo ("Via Roma 10, Milano")
    o nel secondo pezzo da solo ("Via Roma, 10, Milano"). None se non c'e'."""
    pezzi = [p.strip() for p in indirizzo.split(",")]
    for i, p in enumerate(pezzi[:2]):
        if i == 0:
            m = re.search(r"\s(\d{1,4})\s*(?:/\s*)?([a-z]{1,3})?$", p, re.I)
        else:
            m = re.fullmatch(r"(\d{1,4})\s*(?:/\s*)?([a-z]{1,3})?", p, re.I)
        if m:
            return m.group(1).lstrip("0") + (m.group(2) or "").lower()
    return None


def civico_da_leggere(c):
    """"105b" -> "105/B", "12bis" -> "12 bis": come lo scriverebbe una persona."""
    m = re.match(r"(\d+)(\D*)$", c or "")
    if not m:
        return c or ""
    n, lettere = m.group(1), m.group(2)
    if not lettere:
        return n
    return n + ("/" + lettere.upper() if len(lettere) == 1 else " " + lettere)


def _stesso_civico(scritto, trovato):
    """Il civico trovato dal servizio e' proprio quello scritto? Il 12 non e' il 3,
    e il 105/B non e' il 105: sono case diverse."""
    if not scritto or not trovato:
        return False
    for t in re.split(r"[;,]", str(trovato)):
        if re.sub(r"[\s/]+", "", t).lower().lstrip("0") == scritto:
            return True
    return False


# District e Nbrhd sono la frazione: Esri scrive "Longana" li' e "Ravenna" in City,
# e senza leggerli la casa giusta veniva buttata via (vedi _stesso_posto).
CAMPI_ESRI = "Addr_type,Match_addr,City,District,Nbrhd,AddNum,Subregion,StName"


def _punto_esri(c):
    a = c.get("attributes", {})
    return {
        "lat": float(c["location"]["y"]), "lon": float(c["location"]["x"]),
        "indirizzo": a.get("Match_addr") or c.get("address") or "",
        "comune_nome": a.get("City") or "",
        "provincia": a.get("Subregion") or "",
        "civico_trovato": str(a.get("AddNum") or ""),
    }


def _chiedi_a_esri(dati, quanti=10):
    """Una domanda al servizio indirizzi di Esri. None se non risponde (diverso da
    "risponde ma non trova niente", che e' una lista vuota)."""
    try:
        d = json.loads(prendi(INDIRIZZI_ESRI, dict(
            dati, f="json", countryCode="ITA", maxLocations=quanti, outFields=CAMPI_ESRI,
        ), tentativi=1, tempo=12).decode("utf-8"))
    except Exception:                                   # noqa: BLE001
        return None
    return d.get("candidates", [])


def _punto_da_esri(indirizzo, civico):
    """Il servizio indirizzi di Esri conosce i numeri civici italiani; OpenStreetMap
    spesso no, e allora mette il punto a meta' della via e si misura la casa sbagliata
    (successo il 10 settembre 2026: Via 4 Novembre a Quartiano, il 105/B e il 3 davano
    lo stesso punto).

    Torna due cose: il punto esatto, se Esri ha trovato proprio il civico scritto, e
    il punto vicino, se ha trovato una casa vera della stessa via ma con un altro
    numero. Il vicino non e' mai "preciso": e' la casa accanto.
    Attenzione: qui il comune non si guarda. Serve solo quando il comune scritto non
    si riconosce, e allora il risultato non e' mai preciso (vedi punto_dall_indirizzo)."""
    vicino = None
    for c in _chiedi_a_esri({"SingleLine": indirizzo}, 3) or []:
        a = c.get("attributes", {})
        if a.get("Addr_type") not in ("PointAddress", "Subaddress") or c.get("score", 0) < 90:
            continue
        if _stesso_civico(civico, a.get("AddNum")):
            return dict(_punto_esri(c), preciso=True), None
        vicino = vicino or dict(_punto_esri(c), preciso=False)
    return None, vicino


def _da_photon(domanda, quanti=8, intorno=None):
    """Photon, gli stessi dati di OpenStreetMap ma senza la porta chiusa ai server.
    None se non risponde; lista (anche vuota) se risponde."""
    dati = {"q": domanda, "limit": quanti, "lang": "it"}
    if intorno:
        dati["lat"], dati["lon"] = "%.5f" % intorno[0], "%.5f" % intorno[1]
    try:
        d = json.loads(prendi(PHOTON, dati, tentativi=1, tempo=12).decode("utf-8"))
    except Exception:                                   # noqa: BLE001
        return None
    trovati = []
    for x in d.get("features", []):
        a = x.get("properties", {}) or {}
        if (a.get("countrycode") or "IT") != "IT":
            continue
        try:
            lon, lat = (float(v) for v in x["geometry"]["coordinates"][:2])
        except Exception:                               # noqa: BLE001
            continue
        strada = a.get("street") or a.get("name") or ""
        comune = a.get("city") or a.get("county") or ""
        scritto = ", ".join(x for x in [
            (strada + " " + (a.get("housenumber") or "")).strip(),
            a.get("district") or "", comune, a.get("postcode") or ""] if x)
        trovati.append({
            "lat": lat, "lon": lon,
            "indirizzo": scritto or (a.get("name") or ""),
            "comune_nome": comune,
            "provincia": a.get("county") or "",
            "civico_trovato": str(a.get("housenumber") or ""),
            "strada": strada,
            "posto_vicino": a.get("district") or a.get("locality") or "",
            "cap": str(a.get("postcode") or ""),
            "casa": a.get("osm_value") in ("house", "residential", "building", "yes")
                    or bool(a.get("housenumber")),
        })
    return trovati


def _via_da_photon(via, posto, civico, cap=""):
    """La via cercata su Photon, ma tenuta solo se cade nel posto scritto.
    Serve dove Esri non arriva: le vie delle frazioni piccole ci sono solo in
    OpenStreetMap, e spesso scritte per esteso ("Via del Borgo Longana")."""
    lat, lon = posto["dove"]
    nomi = {_pulito(posto["comune"])}
    trovati = _da_photon(via + ", " + posto["comune"] + ((" " + cap) if cap else ""),
                         8, (lat, lon))
    if not trovati:
        return None
    esatto = vicino = strada = None
    for p in trovati:
        if _distanza_km((p["lat"], p["lon"]), (lat, lon)) > 12:
            continue
        dove = {_pulito(p["comune_nome"]), _pulito(p["posto_vicino"])}
        if not (nomi & dove):
            continue
        if not _stessa_strada(via, p.get("strada") or p["indirizzo"]):
            continue
        if cap and p.get("cap") and p["cap"] != cap:
            continue
        pulito = {k: v for k, v in p.items()
                  if k not in ("strada", "posto_vicino", "cap", "casa")}
        pulito["comune_nome"] = posto["comune"]
        pulito["provincia"] = posto["provincia"] or p.get("provincia", "")
        if p["civico_trovato"] and _stesso_civico(civico, p["civico_trovato"]):
            esatto = esatto or dict(pulito, preciso=True)
        elif p["civico_trovato"]:
            vicino = vicino or dict(pulito, preciso=False)
        else:
            strada = strada or dict(pulito, preciso=False, civico_trovato="")
    return esatto or vicino or strada


def suggerimenti_indirizzo(indirizzo, cap="", quanti=6):
    """Gli indirizzi possibili per quello che e' stato scritto, da tutte le porte
    che abbiamo, da far scegliere a mano.

    E' la via d'uscita quando l'indirizzo scritto non si trova o cade nel posto
    sbagliato: invece di un errore e basta, si mettono in fila i posti veri che
    somigliano a quello che ha scritto, e lui tocca quello giusto."""
    via, nomi, sigla, cap_scritto = _pezzi_indirizzo(indirizzo)
    cap = (cap or cap_scritto or "").strip()
    scritta = ", ".join([via] + nomi) if via else (indirizzo or "")
    domanda = (scritta + (" " + cap if cap else "") + (" " + sigla if sigla else "")).strip()
    civico = civico_scritto(via)
    fuori = []

    def aggiungi(p, fonte):
        if not p or not p.get("lat"):
            return
        for g in fuori:
            if _distanza_km((g["lat"], g["lon"]), (p["lat"], p["lon"])) < 0.025:
                return
        testo = " ".join((p.get("indirizzo") or "").split())
        if not testo:
            return
        fuori.append({
            "testo": testo[:160],
            "lat": round(float(p["lat"]), 7), "lon": round(float(p["lon"]), 7),
            "comune": p.get("comune_nome") or "", "provincia": p.get("provincia") or "",
            "civico": str(p.get("civico_trovato") or ""),
            "cap": str(p.get("cap") or ""),
            "fonte": fonte,
            "preciso": bool(civico and _stesso_civico(civico, p.get("civico_trovato"))),
        })

    for c in _chiedi_a_esri({"SingleLine": domanda,
                             "category": "Point Address,Subaddress,Street Address,Street Name"},
                            8) or []:
        if c.get("score", 0) >= 70:
            aggiungi(_punto_esri(c), "Esri")
    for p in _da_photon(domanda, 8) or []:
        aggiungi(p, "OpenStreetMap")
    if len(fuori) < quanti:
        try:
            for x in json.loads(prendi(NOMINATIM, {
                    "q": domanda, "format": "jsonv2", "limit": 8,
                    "countrycodes": "it", "addressdetails": 1}, tentativi=1, tempo=12).decode("utf-8")):
                aggiungi(_punto_da_osm(x, False), "OpenStreetMap")
        except Exception:                               # noqa: BLE001
            pass
    # prima il civico scritto, poi chi almeno un civico ce l'ha
    fuori.sort(key=lambda s: (0 if s["preciso"] else 1, 0 if s["civico"] else 1))
    return fuori[:quanti]


def _punto_da_osm(t, preciso):
    a = t.get("address", {})
    comune = (a.get("village") or a.get("town") or a.get("city")
              or a.get("municipality") or a.get("hamlet") or "")
    return {
        "lat": float(t["lat"]), "lon": float(t["lon"]),
        "indirizzo": t.get("display_name", ""),
        "comune_nome": comune,
        "provincia": a.get("county") or "",
        "civico_trovato": str(a.get("house_number") or ""),
        "preciso": preciso,
    }


# ------------------------------------------------- il comune scritto va rispettato
#
# Il 15 settembre 2026 "Via Dante 10, Milano" dava Via Dante 10 ad ARLUNO, e per di piu'
# "preciso": Esri ha cinque Via Dante 10 in provincia di Milano e si prendeva il primo col
# numero giusto, senza guardare il comune. "Via Emilia 5, Paullo" dava Via Masere 5 a
# Paullo di Casina (Reggio Emilia): OpenStreetMap conosce una frazione che si chiama
# Paullo, e una via qualunque col 5. Da allora: prima si trova il posto scritto (comune o
# frazione), poi si cerca la via solo li' dentro, e un risultato in un altro comune non si
# accetta mai.

class IndirizzoNonTrovato(RuntimeError):
    """Il comune si trova, ma quella via in quel comune no."""


def _pulito(t):
    """Per confrontare nomi: senza accenti, apostrofi e maiuscole."""
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


# il tipo di strada e le parolette non servono a riconoscerla: "Via Dante" e' "Dante"
TIPI_STRADA = {"via", "viale", "vle", "v", "corso", "cso", "piazza", "pza", "p", "piazzale",
               "largo", "vicolo", "strada", "str", "localita", "loc", "contrada", "borgo",
               "frazione", "fraz", "salita", "galleria", "passaggio", "vicinale", "privata"}
PAROLETTE = {"di", "del", "della", "dei", "degli", "delle", "de", "d", "da", "e", "al",
             "alla", "l", "lo", "la", "le", "il"}
NUMERI_A_PAROLE = {"uno": "1", "primo": "1", "due": "2", "tre": "3", "quattro": "4",
                   "cinque": "5", "sei": "6", "sette": "7", "otto": "8", "nove": "9",
                   "dieci": "10", "undici": "11", "dodici": "12", "venti": "20",
                   "ventiquattro": "24", "venticinque": "25", "ventotto": "28", "trenta": "30"}
ROMANI = {"i": 1, "v": 5, "x": 10, "l": 50}


def _numero(p):
    """"quattro" -> "4", "iv" -> "4", "xx" -> "20": via IV Novembre e' via 4 Novembre."""
    if p in NUMERI_A_PAROLE:
        return NUMERI_A_PAROLE[p]
    if re.fullmatch(r"[ivxl]{2,6}", p):
        tot, prima = 0, 0
        for ch in reversed(p):
            v = ROMANI[ch]
            tot, prima = (tot - v, prima) if v < prima else (tot + v, v)
        return str(tot)
    return p


def _chiave_strada(t):
    parole = [p for p in _pulito(t).split() if p not in PAROLETTE]
    if parole and parole[0] in TIPI_STRADA:
        parole = parole[1:]
    return {_numero(p) for p in parole if not re.fullmatch(r"\d+[a-z]{0,3}", p) or len(parole) > 1}


def _stessa_strada(scritta, trovata):
    """"Via Dante 10" e "Dante Alighieri" sono la stessa via; "Via Emilia" e
    "Via Masere" no. Il civico scritto non conta."""
    a = _chiave_strada(re.sub(r"\s\d{1,4}\s*(?:/\s*[a-z]{1,3}|\s(?:bis|ter))?$", "", scritta.strip(), flags=re.I))
    b = _chiave_strada(trovata)
    return bool(a and b) and (a <= b or b <= a)


def _pezzi_indirizzo(indirizzo):
    """Spacchetta l'indirizzo scritto a mano: via, posti, sigla, CAP.

    I posti sono tutti i nomi scritti dopo la via, **dal piu' piccolo al piu'
    grande**: "Via del Borgo 13, Longana, Ravenna" -> ["Longana", "Ravenna"].
    Prima si teneva solo l'ultimo, e la frazione (che e' proprio il pezzo che dice
    dove si e') andava persa: si cercava Via del Borgo in tutto il comune di
    Ravenna e usciva un'altra via (Andrea, 19 settembre 2026).

    "Longana(Ravenna)" scritto attaccato sono due posti, non uno solo."""
    pezzi = [p.strip() for p in re.split(r"[,;\n]", indirizzo or "") if p.strip()]
    pezzi = [p for p in pezzi if _pulito(p) not in ("italia", "italy")]
    if not pezzi:
        return "", [], "", ""
    if len(pezzi) == 1:
        m = re.match(r"^(.*\d{1,4}(?:\s*/\s*[a-z]{1,3}|\s+(?:bis|ter))?)\s+(?:a\s+|in\s+)?([^\d\s/].*)$",
                     pezzi[0], re.I)
        if not m:
            return pezzi[0], [], "", ""
        pezzi = [m.group(1), m.group(2)]
    via = pezzi[0]
    if len(pezzi) >= 3 and re.fullmatch(r"\d{1,4}\s*(?:/\s*[a-z]{1,3}|bis|ter)?", pezzi[1], re.I):
        via, pezzi = via + " " + pezzi[1], [via] + pezzi[2:]
    aperti = []
    for p in pezzi[1:]:
        m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", p)
        if m:
            if m.group(1).strip():
                aperti.append(m.group(1).strip())
            if m.group(2).strip():
                aperti.append(m.group(2).strip())
        else:
            aperti.append(p)
    sigla, cap, posti = "", "", []
    for p in aperti:
        m = re.search(r"\b(\d{5})\b", p)
        if m:
            cap = cap or m.group(1)
            p = " ".join(re.sub(r"\b\d{5}\b", " ", p).split())
        if re.fullmatch(r"[A-Z]{2}", p.strip()):
            sigla = sigla or p.strip()
            continue
        m = re.search(r"\s*\b([A-Z]{2})\s*$", p)
        if m and m.start() > 0:
            sigla, p = sigla or m.group(1), p[:m.start()]
        p = p.strip()
        if p:
            posti.append(p)
    return via, posti, sigla, cap


def leggi_indirizzo(indirizzo):
    """"Via Dante 10, 20121 Milano (MI)" -> ("Via Dante 10", "Milano", "MI").
    Il posto e' il piu' grande dei nomi scritti dopo la via, cioe' l'ultimo."""
    via, posti, sigla, _cap = _pezzi_indirizzo(indirizzo)
    return via, (posti[-1] if posti else ""), sigla


def _distanza_km(a, b):
    dy = (a[0] - b[0]) * 111.32
    dx = (a[1] - b[1]) * 111.32 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def _posti(posto, sigla="", cap=""):
    """Dove sta il posto scritto: comuni con quel nome, e frazioni con quel nome
    (Quartiano -> Mulazzano). Prima i comuni, poi le frazioni. None se Esri non risponde.
    Col CAP scritto la domanda e' piu' stretta, e un omonimo lontano non entra."""
    cand = _chiedi_a_esri({"SingleLine": " ".join(x for x in (posto, sigla, cap) if x).strip(),
                           "category": "City,Neighborhood,Postal,Populated Place"}, 10)
    if cand is None:
        return None
    solo_cap = bool(cap) and _pulito(posto) == _pulito(cap)
    voluto, trovati = _pulito(posto), []
    for c in cand:
        a = c.get("attributes", {})
        comune = a.get("City") or ""
        if a.get("Addr_type") not in ("Locality", "PostalLoc") or not comune:
            continue
        primo = _pulito((a.get("Match_addr") or "").split(",")[0])
        if solo_cap:
            # il posto e' il CAP: vale il comune che il CAP indica
            tipo = 0
        elif _pulito(comune) == voluto:
            tipo = 0
        elif primo == voluto:
            tipo = 1
        else:
            continue
        dove = (float(c["location"]["y"]), float(c["location"]["x"]))
        if any(_pulito(t["comune"]) == _pulito(comune) and _distanza_km(t["dove"], dove) < 30
               for t in trovati):
            continue
        trovati.append({"comune": comune, "provincia": a.get("Subregion") or "",
                        "dove": dove, "tipo": tipo})
    trovati.sort(key=lambda t: t["tipo"])
    return trovati


def _stesso_posto(posto, a):
    """Il candidato sta nel posto cercato?

    Vale il comune, ma vale anche la frazione. Esri, per una casa di Longana, scrive
    "Ravenna" in City e "Longana" in District: guardando solo City, la casa giusta di
    Via del Borgo Longana 13 veniva buttata via, e al suo posto usciva un'altra Via del
    Borgo a San Marco, **a 1,8 km**. E' l'errore che Andrea ha segnalato il 23 settembre
    2026 ("longana che manda in un altro posto")."""
    voluto = _pulito(posto["comune"])
    if not voluto:
        return False
    return voluto in {_pulito(a.get("City")), _pulito(a.get("District")),
                      _pulito(a.get("Nbrhd"))}


def _via_nel_posto(via, posto, civico):
    """La via cercata solo intorno al comune trovato, e poi tenuta solo se e' proprio
    in quel comune. Civico uguale -> preciso; altro civico della via -> la casa accanto;
    solo la via -> sulla via, non preciso."""
    lat, lon = posto["dove"]
    riquadro = "%f,%f,%f,%f" % (lon - 0.11, lat - 0.08, lon + 0.11, lat + 0.08)
    esatto = vicino = strada = None
    for c in _chiedi_a_esri({
            "SingleLine": via + ", " + posto["comune"], "searchExtent": riquadro,
            "category": "Point Address,Subaddress,Street Address,Street Name"}, 20) or []:
        a = c.get("attributes", {})
        if not _stesso_posto(posto, a) or c.get("score", 0) < 80:
            continue
        if a.get("StName") and not _stessa_strada(via, a.get("StName")):
            continue
        p = _punto_esri(c)
        if a.get("Addr_type") in ("PointAddress", "Subaddress"):
            if _stesso_civico(civico, a.get("AddNum")):
                esatto = esatto or dict(p, preciso=True)
            else:
                vicino = vicino or dict(p, preciso=False)
        else:
            strada = strada or dict(p, preciso=False, civico_trovato="")
    return esatto or vicino or strada


def _via_da_osm(via, posto, civico, scritto):
    """Se Esri non conosce la via, OpenStreetMap, ma solo dentro il riquadro del comune,
    solo con la via giusta e solo nel comune (o nella frazione) scritto."""
    lat, lon = posto["dove"]
    try:
        trovati = json.loads(prendi(NOMINATIM, {
            "q": via + ", " + posto["comune"], "format": "jsonv2", "limit": 10,
            "countrycodes": "it", "addressdetails": 1, "bounded": 1,
            "viewbox": "%f,%f,%f,%f" % (lon - 0.11, lat + 0.08, lon + 0.11, lat - 0.08),
        }, tentativi=2).decode("utf-8"))
    except Exception:                                   # noqa: BLE001
        return None
    nomi = {_pulito(posto["comune"]), _pulito(scritto)}
    buoni = []
    for t in trovati:
        a = t.get("address", {})
        posti = {_pulito(a.get(k)) for k in ("city", "town", "village", "municipality", "hamlet", "suburb")}
        if not (nomi & posti) or not _stessa_strada(via, a.get("road", "")):
            continue
        buoni.append(t)
    for t in buoni:
        if (_e_casa_osm(t)
                and _stesso_civico(civico, t.get("address", {}).get("house_number"))):
            return dict(_punto_da_osm(t, True), comune_nome=posto["comune"], provincia=posto["provincia"])
    if buoni:
        buoni.sort(key=lambda t: 0 if _e_casa_osm(t) else 1)
        return dict(_punto_da_osm(buoni[0], False), comune_nome=posto["comune"], provincia=posto["provincia"])
    return None


def _la_via_da_tutte_le_porte(via, posto, civico, nome, cap=""):
    """La via cercata in tutte le porte che abbiamo, e vince chi ha trovato **proprio
    il civico scritto**.

    Prima si prendeva la prima risposta che arrivava, nell'ordine Esri, Photon,
    OpenStreetMap. Cosi' una risposta approssimativa di Esri (la via giusta ma il civico
    sbagliato) fermava la ricerca, e non si chiedeva nemmeno a chi il civico giusto ce
    l'aveva. Caso vero: "Via del Borgo Longana 13/O" a Longana. Esri conosce solo il 13 e
    rispondeva a **35 metri**, sulla particella del vicino; OpenStreetMap ha il 13/O ed e'
    a **2 metri**, sulla casa. Adesso, se la prima porta non trova il civico esatto, si
    bussa anche alle altre, e se una lo trova vince lei.

    Quando nessuno trova il civico esatto non cambia niente: resta l'ordine di prima."""
    esri = _via_nel_posto(via, posto, civico)
    if esri and esri.get("preciso"):
        return esri
    photon = _via_da_photon(via, posto, civico, cap)
    if photon and photon.get("preciso"):
        return photon
    osm = _via_da_osm(via, posto, civico, nome)
    if osm and osm.get("preciso"):
        return osm
    return esri or photon or osm


def punto_dall_indirizzo(indirizzo, cap=""):
    """Indirizzo scritto a mano -> latitudine, longitudine, indirizzo per esteso.

    Il CAP e' facoltativo: se c'e', restringe la ricerca del posto. Serve per le
    frazioni piccole, dove lo stesso nome di via torna in mezzo comune.

    "preciso" vuol dire una cosa sola: e' stato trovato proprio il civico scritto,
    nel comune scritto. Una casa qualunque della via, una localita', un civico diverso,
    un comune che non si e' potuto controllare: non e' preciso, e l'applicazione avvisa
    di controllare la particella."""
    via, nomi, sigla, cap_scritto = _pezzi_indirizzo(indirizzo)
    cap = (cap or cap_scritto or "").strip()
    civico = civico_scritto(via)
    # i nomi si provano dal piu' piccolo al piu' grande: prima la frazione scritta
    # (Longana), e solo se li' la via non c'e' il comune intero (Ravenna). Col CAP
    # e senza nessun nome, il posto lo dice il CAP.
    if not nomi and cap:
        nomi = [cap]
    posto, posti, trovati = "", None, []
    for nome in nomi[:2]:
        posto = nome
        posti = _posti(nome, sigla, cap)
        trovati = []
        for p in (posti or [])[:3]:
            # un posto lontano da quello dove si e' gia' trovato qualcosa e' un omonimo: basta
            if trovati and _distanza_km(p["dove"], trovati[0][0]["dove"]) > 5:
                break
            t = _la_via_da_tutte_le_porte(via, p, civico, nome, cap)
            if t:
                trovati.append((p, t))
                if t["preciso"]:
                    break
        if trovati:
            break
    if trovati:
        # Esri a volte chiama "comune" anche una frazione (Quartiano, che e' di Mulazzano):
        # fra i posti nello stesso punto vince il risultato migliore. Prima il civico
        # esatto, poi una casa vera della via, per ultima la via.
        p, trovato = min(trovati, key=lambda x: 0 if x[1]["preciso"]
                         else 1 if x[1].get("civico_trovato") else 2)
        trovato = dict(trovato, comune_verificato=True, civico_scritto=civico)
        if not trovato.get("provincia"):
            trovato["provincia"] = p["provincia"]
        # trovato in un posto con lo stesso nome ma lontano dal primo (i due Castro, Paullo
        # e la frazione Paullo di Casina): puo' non essere quello voluto, e si dice
        if _distanza_km(p["dove"], posti[0]["dove"]) > 5:
            trovato["preciso"] = False
            trovato["avviso_comune"] = (
                "C'è più di un posto che si chiama %s: l'indirizzo è stato trovato a %s%s. "
                "Se non è questo, aggiungi la sigla della provincia (per esempio «%s MI»)."
                % (posto, trovato.get("comune_nome") or p["comune"],
                   " (%s)" % trovato["provincia"] if trovato.get("provincia") else "", posto))
        return trovato
    if posti:
        via_sola = re.sub(r"\s\d{1,4}\s*(?:/\s*[a-z]{1,3}|\s(?:bis|ter))?$", "", via.strip(), flags=re.I)
        p = posti[0]
        raise IndirizzoNonTrovato(
            "%s a %s%s non si trova sulla mappa. Guarda se è uno di questi indirizzi, "
            "oppure scrivi le misure a mano."
            % (via_sola, p["comune"], " (%s)" % p["provincia"] if p["provincia"] else ""))
    # il comune non e' scritto, o non si riconosce: la ricerca di prima, ma mai "preciso"
    trovato = _punto_senza_comune(indirizzo, civico)
    return dict(trovato, preciso=False, comune_verificato=False, civico_scritto=civico,
                avviso_comune=(
                    "Nell'indirizzo non c'è il comune: l'ho trovato a %s. Controlla che sia il posto giusto."
                    if not posto else
                    "Non riconosco il comune «" + posto + "»: l'indirizzo è stato trovato a %s. "
                    "Controlla che sia il posto giusto.")
                % ((trovato.get("comune_nome") or "?")
                   + (" (%s)" % trovato["provincia"] if trovato.get("provincia") else "")))


def _punto_senza_comune(indirizzo, civico):
    """La ricerca di prima del 15 settembre 2026, che non guarda il comune."""
    esatto, vicino = _punto_da_esri(indirizzo, civico)
    if esatto:
        return esatto
    grezzo = prendi(NOMINATIM, {
        "q": indirizzo, "format": "jsonv2", "limit": 5,
        "countrycodes": "it", "addressdetails": 1,
    })
    trovati = json.loads(grezzo.decode("utf-8"))
    if not trovati:
        if vicino:
            return vicino
        raise RuntimeError("Questo indirizzo non si trova sulla mappa: %s" % indirizzo)
    for t in trovati:
        if (_e_casa_osm(t)
                and _stesso_civico(civico, t.get("address", {}).get("house_number"))):
            return _punto_da_osm(t, True)
    # niente civico giusto: meglio una casa vera della via (Esri) che il centro della via
    if vicino:
        return vicino
    # meglio una casa che una via intera: la via è una riga, la casa è un punto
    trovati.sort(key=lambda t: 0 if _e_casa_osm(t) else 1)
    return _punto_da_osm(trovati[0], False)


# ------------------------------------------------- dal punto alla particella

def _riquadro(lat, lon, meta_lato_m):
    """Riquadro quadrato sul terreno, in gradi, attorno a un punto."""
    dlat = meta_lato_m / 111320.0
    dlon = dlat / max(0.2, math.cos(math.radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


class CatastoOccupato(RuntimeError):
    """Upstream cadastral request failure; original details stay in server logs."""


ULTIMO_RIFIUTO = {"quando": "", "testo": "", "ora": 0}

# quando ha detto di no l'ultima volta, porta per porta. Se ne tiene uno per
# ciascuna: il 21 settembre 2026 un solo registro faceva si' che il no della
# seconda porta mettesse in pausa anche la prima, cioe' proprio quella su cui si
# stava ripiegando.
ULTIMO_NO = {"wms": 0.0, "wfs": 0.0}


def registra_errore_catasto(testo, porta="wms"):
    try:
        root = ET.fromstring(testo)
        errors = [e for e in root.iter()
                  if e.tag.split('}')[-1] in ('ServiceException', 'ExceptionText')]
        details = ' | '.join((e.get('code', '') + ' ' + ''.join(e.itertext())).strip()
                             for e in errors)
    except ET.ParseError:
        details = 'Invalid XML error response'
    details = ' '.join(details.split())[:2000]
    ULTIMO_RIFIUTO["quando"] = time.strftime("%Y-%m-%d %H:%M:%S")
    ULTIMO_RIFIUTO["testo"] = details
    ULTIMO_RIFIUTO["ora"] = time.time()
    ULTIMO_NO[porta] = time.time()
    logging.getLogger(__name__).error('Cadastral service rejected request (%s): %s',
                                      porta, details)


# un punto di prova sempre uguale: una particella qualunque, in aperta campagna
PUNTO_DI_PROVA = (45.3273257, 9.3542738)


def catasto_risponde():
    """Una sola domanda di prova a ogni porta del catasto, e si dice com'e' andata.

    Quando un rilievo non riesce, la domanda vera e': e' rotta la nostra
    applicazione o e' l'Agenzia che non risponde? Questa e' la risposta, e si
    legge da fuori senza entrare nei registri del server.

    Le porte sono due e si guastano separatamente, quindi si provano tutt'e due:
    "risponde" vuol dire che almeno una da' foglio e particella, che e' quello
    che conta per chi sta facendo un sopralluogo.
    """
    partito = time.time()
    esito = {"wfs": "", "wms": "", "particella": ""}

    try:
        trovata = _particella_dal_wfs(PUNTO_DI_PROVA[0], PUNTO_DI_PROVA[1],
                                      insisti=True)
        esito["wfs"] = "risponde"
        if trovata:
            esito["particella"] = trovata["codice"]
    except CatastoOccupato:
        esito["wfs"] = "rifiuta"
    except Exception as e:                          # noqa: BLE001
        esito["wfs"] = "non raggiungibile: " + str(e)[:150]

    try:
        html = _chiedi_al_catasto(PUNTO_DI_PROVA[0], PUNTO_DI_PROVA[1],
                                  "CP.CadastralParcel", "text/html", insisti=True)
        esito["wms"] = "risponde"
        trovato = re.search(r"NationalCadastralReference</th><td>([^<]*)<", html)
        if trovato and not esito["particella"]:
            esito["particella"] = trovato.group(1).strip()
    except CatastoOccupato:
        esito["wms"] = "rifiuta"
    except Exception as e:                          # noqa: BLE001
        esito["wms"] = "non raggiungibile: " + str(e)[:150]

    porte = [esito["wfs"], esito["wms"]]
    if "risponde" in porte:
        stato = "risponde"
    elif "rifiuta" in porte:
        stato = "rifiuta"
    else:
        stato = "non raggiungibile"
    return {"catasto": stato, "secondi": round(time.time() - partito, 1),
            "particella": esito["particella"],
            "porte": {"confini (WFS)": esito["wfs"], "punto (WMS)": esito["wms"]},
            "ultimo_rifiuto": dict(ULTIMO_RIFIUTO)}


RIPOSO_CATASTO = 90.0

# di quanto si allarga la scatola a ogni tentativo, in metri. Il primo giro e'
# la scatola voluta; gli altri sono la stessa domanda scritta con numeri un po'
# diversi, che e' l'unica cosa che smuove un rifiuto sempre uguale.
SCARTI = (0.0, 1.0, 2.0, 5.0)


def catasto_in_pausa(porta="wms"):
    """Da quanto questa porta del catasto ci ha detto di no l'ultima volta.

    Quando rifiuta, rifiuta per un po': continuare a interrogarla fa aspettare
    l'artigiano dieci secondi per sentirsi dire di no un'altra volta. Meglio
    passare subito all'altra porta, o alla foto da disegnare, e riprovare
    qualche minuto dopo.
    """
    quando = ULTIMO_NO.get(porta) or 0
    return quando and (time.time() - quando) < RIPOSO_CATASTO


def _chiedi_al_catasto(lat, lon, strato, formato, lato_m=180.0, lati=700, insisti=False):
    if not insisti and catasto_in_pausa():
        raise CatastoOccupato(
            "Il catasto ha appena rifiutato le nostre domande: aspetto un minuto "
            "prima di richiedere. Intanto il sopralluogo va avanti sulla foto.")
    y0, x0, y1, x1 = _riquadro(lat, lon, lato_m / 2)
    domanda = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo",
        "LAYERS": strato, "QUERY_LAYERS": strato, "CRS": "EPSG:6706",
        "BBOX": "%f,%f,%f,%f" % (y0, x0, y1, x1),
        "WIDTH": lati, "HEIGHT": lati, "I": lati // 2, "J": lati // 2,
        "INFO_FORMAT": formato, "FEATURE_COUNT": 1,
    }
    for scarto in SCARTI:
        if scarto:
            # Il no del catasto non dipende solo dal fatto che sia occupato: a
            # parita' di tutto il resto, certe scatole vengono rifiutate sempre e
            # la stessa scatola larga un metro di piu' passa (verificato il 21
            # settembre 2026 su Genova e su Rivolta d'Adda). Quindi non si
            # ripete la domanda identica: si sposta di un metro e si richiede.
            y0, x0, y1, x1 = _riquadro(lat, lon, (lato_m + scarto) / 2)
            domanda["BBOX"] = "%f,%f,%f,%f" % (y0, x0, y1, x1)
        testo = prendi(CATASTO, domanda).decode("utf-8", "replace")
        if "ServiceException" not in testo:
            return testo
    # si segna il no solo quando si rinuncia davvero: un rifiuto a cui si e'
    # rimediato al secondo tentativo non deve mettere in pausa il minuto dopo
    registra_errore_catasto(testo)
    raise CatastoOccupato(
        "Non siamo riusciti a ottenere i confini dal catasto. "
        "Puoi continuare il sopralluogo e inserire le misure prese sul posto.")


def _leggi_codice_catastale(codice):
    """Il codice scritto dal catasto -> comune, sezione, foglio, particella.

    Due forme: F801_001300.416 (comune F801, nessuna sezione, foglio 0013,
    allegato 00, particella 416) e D969A006900.479 (Genova, sezione A, foglio
    0069, particella 479). Il trattino basso sta al posto della sezione nei
    comuni che non ne hanno. Prima si leggeva solo la prima forma: a Genova e a
    Bari il foglio restava vuoto e l'app scartava tutto il rilievo (14 settembre
    2026).
    """
    m = re.match(r"^([A-Z]\d{3})([A-Z_]?)(\d{4})(\w*)\.(.+)$", codice)
    if m:
        return (m.group(1), m.group(2).strip("_"), str(int(m.group(3))), m.group(5))
    # una forma mai vista: si tiene quello che si riconosce, mai un foglio vuoto
    cifre = re.search(r"(\d{4})\w*\.", codice)
    return (codice[:4], "",
            str(int(cifre.group(1))) if cifre else "?",
            codice.rsplit(".", 1)[-1] or codice)


def _e_una_particella_vera(particella):
    """Strade e acque non sono lotti.

    Il catasto disegna anche la sede stradale e i corsi d'acqua, e li chiama
    STRADA001, ACQUA002 e cosi' via. Se il punto dell'indirizzo cade in mezzo
    alla via non si deve rispondere "questa e' la tua particella": si dice che
    li' non c'e' nulla, e chi ha chiamato cerca poco piu' in la'.
    """
    return bool(re.match(r"^\d", (particella or "").strip()))


def _dentro_il_confine(punti, lat, lon):
    """Il punto sta dentro questo confine? Il raggio che attraversa il bordo."""
    dentro = False
    n = len(punti)
    for i in range(n):
        y1, x1 = punti[i]
        y2, x2 = punti[(i + 1) % n]
        if (x1 > lon) != (x2 > lon):
            taglio = y1 + (lon - x1) * (y2 - y1) / (x2 - x1)
            if taglio > lat:
                dentro = not dentro
    return dentro


def _chiedi_al_wfs(lat, lon, lato_m=14.0, insisti=False):
    """La seconda porta del catasto: le particelle dentro un quadratino.

    Si chiede il minimo indispensabile, perche' il filtro che sta davanti al
    catasto rifiuta le domande con parametri che non si aspetta: aggiungere
    COUNT, per dire, fa tornare "Richiesta non valida" al posto dei dati.
    """
    if not insisti and catasto_in_pausa("wfs"):
        raise CatastoOccupato(
            "La seconda porta del catasto ha appena rifiutato: aspetto un minuto "
            "prima di richiedere.")
    for scarto in SCARTI:
        d = ((lato_m + scarto) / 2) / 111320.0
        dlon = d / max(0.2, math.cos(math.radians(lat)))
        try:
            # due tentativi di rete e non tre: qui si puo' ripiegare sull'altra
            # porta, e far aspettare l'artigiano mezzo minuto per ogni punto
            # toccato sulla foto sarebbe peggio del guasto
            grezzo = prendi(CATASTO_WFS, {
                "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
                "TYPENAMES": "CP:CadastralParcel",
                "BBOX": "%f,%f,%f,%f,urn:ogc:def:crs:EPSG::6706"
                        % (lat - d, lon - dlon, lat + d, lon + dlon),
            }, tentativi=2, tempo=25)
        except Exception:                           # noqa: BLE001
            # irraggiungibile: vale come un no, se no ogni punto ricomincia da capo
            ULTIMO_NO["wfs"] = time.time()
            raise
        testo = grezzo.decode("utf-8", "replace")
        if "ServiceException" not in testo and "ExceptionReport" not in testo:
            return testo
    registra_errore_catasto(testo, "wfs")
    raise CatastoOccupato(
        "La seconda porta del catasto non manda le particelle in questo momento.")


def _particella_dal_wfs(lat, lon, insisti=False):
    """Il punto -> la particella che lo contiene, letta dalla seconda porta.

    Torna lo stesso foglietto della prima porta (codice, comune, sezione,
    foglio, particella, riquadro), piu' il confine disegnato punto per punto.
    None se li' non c'e' nessun lotto. Se la porta non risponde alza
    CatastoOccupato, cosi' chi ha chiamato sa che deve provare l'altra.
    """
    radice = ET.fromstring(_chiedi_al_wfs(lat, lon, insisti=insisti))
    for pezzo in radice.iter():
        if not pezzo.tag.endswith("}CadastralParcel"):
            continue
        codice = ""
        for campo in pezzo:
            if campo.tag.endswith("NATIONALCADASTRALREFERENCE"):
                codice = (campo.text or "").strip()
        if not codice:
            continue
        confini = [e for e in pezzo.iter() if e.tag.endswith("}posList")]
        for confine in confini:
            n = [float(v) for v in (confine.text or "").split()]
            punti = list(zip(n[0::2], n[1::2]))       # il catasto scrive lat poi lon
            if len(punti) < 3 or not _dentro_il_confine(punti, lat, lon):
                continue
            comune, sezione, foglio, part = _leggi_codice_catastale(codice)
            if not _e_una_particella_vera(part):
                return None
            lats = [q[0] for q in punti]
            lons = [q[1] for q in punti]
            return {"codice": codice, "comune": comune, "sezione": sezione,
                    "foglio": foglio, "particella": part,
                    "riquadro": {"lon0": min(lons), "lat0": min(lats),
                                 "lon1": max(lons), "lat1": max(lats)},
                    "confine": punti}
    return None


def particella_nel_punto(lat, lon, atteso=None):
    """Il punto -> foglio, particella, comune, ingombro. None se li' non c'e' nulla.
    Con `atteso`, se il punto cade su un'altra particella si risponde col solo
    codice, senza chiedere anche l'ingombro: una domanda al catasto in meno.

    Si bussa prima alla seconda porta (WFS): risponde con una domanda sola
    invece di due, e da' il confine vero invece del riquadro. Se quella tace si
    torna alla prima, che e' quella che si usava fino al 20 settembre 2026.
    """
    try:
        trovata = _particella_dal_wfs(lat, lon)
    except Exception:                               # noqa: BLE001
        pass                                        # la porta e' chiusa: si prova l'altra
    else:
        if trovata is None:
            return None
        if atteso and trovata["codice"] != atteso:
            return {"codice": trovata["codice"], "riquadro": None}
        return trovata

    html = _chiedi_al_catasto(lat, lon, "CP.CadastralParcel", "text/html")
    rif = re.search(r"NationalCadastralReference</th><td>([^<]+)<", html)
    if not rif:
        return None
    codice = rif.group(1).strip()
    if not codice:
        # il catasto sotto carico a volte manda la casella vuota: meglio "qui non c'e'
        # niente" (si cerca poco piu' in la') che un rilievo senza foglio, che l'app
        # scarta (Andrea, 14 settembre 2026, Quartiano 105/B)
        return None
    if atteso and codice != atteso:
        return {"codice": codice, "riquadro": None}
    comune, sezione, foglio, part = _leggi_codice_catastale(codice)

    gml = _chiedi_al_catasto(lat, lon, "CP.CadastralParcel",
                             "application/vnd.ogc.gml")
    box = re.search(r"<gml:coordinates>([\d.,\s-]+)</gml:coordinates>", gml)
    riquadro = None
    if box:
        n = [float(v) for v in re.split(r"[,\s]+", box.group(1).strip())]
        riquadro = {"lon0": n[0], "lat0": n[1], "lon1": n[2], "lat1": n[3]}
    return {"codice": codice, "comune": comune, "sezione": sezione, "foglio": foglio,
            "particella": part, "riquadro": riquadro}


def cerca_la_particella(lat, lon, passo_m=6.0, giri=4):
    """Se il punto cade in mezzo alla strada, si guarda poco intorno."""
    prova = particella_nel_punto(lat, lon)
    if prova:
        return prova, lat, lon
    for giro in range(1, giri + 1):
        raggio = passo_m * giro
        for grado in range(0, 360, 45):
            a = math.radians(grado)
            dlat = (raggio * math.cos(a)) / 111320.0
            dlon = (raggio * math.sin(a)) / (111320.0 * math.cos(math.radians(lat)))
            prova = particella_nel_punto(lat + dlat, lon + dlon)
            if prova:
                return prova, lat + dlat, lon + dlon
    return None, lat, lon


# ------------------------------------------------- dalle mappe ai metri quadri

def area_confine_mq(punti):
    """Area in metri quadri di un confine catastale [lat, lon].

    Su distanze da particella basta proiettare localmente latitudine e longitudine:
    la longitudine viene accorciata col coseno della latitudine media. La formula
    del laccio calcola poi il poligono, senza dipendere dal punto toccato.
    """
    if not punti or len(punti) < 3:
        return 0.0
    lat0 = sum(p[0] for p in punti) / len(punti)
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 111320.0
    xy = [(p[1] * kx, p[0] * ky) for p in punti]
    return abs(sum(x1 * y2 - x2 * y1
                   for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))) / 2.0

def forma_del_confine(punti):
    """Quanto e' grande e che forma ha un confine catastale: area, lato piu' lungo
    dell'ingombro, e compattezza (4*pi*area diviso perimetro al quadrato).

    La compattezza vale 1 per un cerchio e scende verso zero per una striscia lunga
    e stretta. Sulle case vere di Andrea sta fra 0,70 e 0,79; la particella che il
    catasto restituisce a Via Torino 5 di Casalmaiocco vale 0,17."""
    if not punti or len(punti) < 3:
        return 0.0, 0.0, 0.0
    lat0 = sum(p[0] for p in punti) / len(punti)
    kx = 111320.0 * math.cos(math.radians(lat0))
    area = area_confine_mq(punti)
    giro = 0.0
    for (a, b) in zip(punti, punti[1:] + punti[:1]):
        giro += math.hypot((a[1] - b[1]) * kx, (a[0] - b[0]) * 111320.0)
    lati_ = [p[0] for p in punti]
    lungh = [p[1] for p in punti]
    piu_lungo = max((max(lati_) - min(lati_)) * 111320.0,
                    (max(lungh) - min(lungh)) * kx)
    return area, piu_lungo, (4 * math.pi * area / (giro * giro) if giro else 0.0)


def e_una_strada(confine):
    """Quella particella e' una strada (o una fascia di parcheggi), non il lotto di
    una casa.

    Serve perche' a Via Torino 5 di Casalmaiocco il catasto, sotto il civico, non ha
    la palazzina: ha **la carreggiata coi parcheggi**, 16.086 mq lunghi 349 metri, e
    l'applicazione li mostrava come se fossero il giardino del cliente. Andrea lo ha
    segnalato il 22 settembre 2026: "non segna proprio la palazzina, il catasto segna
    la strada, i parcheggi, nulla di palazzina".

    Si riconosce dalla forma, senza conoscere il posto: grande, lunghissima e per
    niente compatta. Le tre condizioni valgono insieme apposta, cosi' un lotto di
    campagna grande ma ben fatto (Monticelli d'Ongina, 4.238 mq con compattezza 0,29)
    non ci finisce dentro. La soglia della compattezza e' 0,35 e non 0,25 perche'
    la particella vera di Via Torino vale 0,17 solo grazie al suo bordo frastagliato:
    una strada disegnata come un rettangolo pulito di 349 x 46 metri vale 0,32, ed e'
    una strada uguale."""
    area, piu_lungo, compattezza = forma_del_confine(confine)
    return area >= 5000.0 and piu_lungo >= 150.0 and compattezza < 0.35


def case_vicine(lat, lon, raggio_m=60.0, lati=420, minimo_mq=12.0, quante=6):
    """I fabbricati del catasto intorno al punto, dal piu' vicino: dove stanno, quanto
    sono grandi, quanto sono lontani.

    Serve quando il civico cade sull'asfalto. I servizi di indirizzi mettono il punto
    **sul bordo della strada**, davanti al cancello, non sul tetto: dove la strada e'
    una particella (Via Torino 5 a Casalmaiocco) la casa del cliente non viene nemmeno
    sfiorata. Spostandosi sul fabbricato piu' vicino si torna sulla sua particella: a
    Casalmaiocco da 16.086 mq di carreggiata a 1.577 mq, che e' il pezzo giusto.

    Sulla mappa dell'Agenzia i fabbricati sono arancioni pieni: la stessa maschera che
    usa misura() per contare il coperto. Le macchie sotto `minimo_mq` non si guardano:
    sono tettoie, pozzetti e sbavature del disegno."""
    y0, x0, y1, x1 = _riquadro(lat, lon, raggio_m)
    fabb = _mappa_catastale(y0, x0, y1, x1, "fabbricati", lati)
    mattoni = _maschera(fabb, lambda p: p[0] > 150 and p[1] < 190 and p[2] < 120
                                        and p[0] - p[2] > 60)
    m_per_px = (2.0 * raggio_m) / lati
    minimo_px = minimo_mq / (m_per_px * m_per_px)
    centro = (lati - 1) / 2.0
    visti = bytearray(lati * lati)
    trovate = []
    for partenza in range(lati * lati):
        if not mattoni[partenza] or visti[partenza]:
            continue
        pila, punti = [partenza], []
        visti[partenza] = 1
        while pila:
            i = pila.pop()
            punti.append(i)
            ix, iy = i % lati, i // lati
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = ix + dx, iy + dy
                if 0 <= nx < lati and 0 <= ny < lati:
                    k = ny * lati + nx
                    if mattoni[k] and not visti[k]:
                        visti[k] = 1
                        pila.append(k)
        if len(punti) < minimo_px:
            continue
        vicino = min(math.hypot(i % lati - centro, i // lati - centro) for i in punti)
        sx = sum(i % lati for i in punti) / len(punti)
        sy = sum(i // lati for i in punti) / len(punti)
        trovate.append({
            "lat": round(y1 - (sy / (lati - 1.0)) * (y1 - y0), 7),
            "lon": round(x0 + (sx / (lati - 1.0)) * (x1 - x0), 7),
            "area_mq": round(len(punti) * m_per_px * m_per_px),
            "distanza_m": round(vicino * m_per_px, 1),
        })
    trovate.sort(key=lambda c: c["distanza_m"])
    return trovate[:quante]


def casa_piu_vicina(lat, lon, raggio_m=60.0, lati=420, minimo_mq=12.0):
    """Solo la piu' vicina, o None. E' quella che si prende quando il civico cade
    sull'asfalto."""
    vicine = case_vicine(lat, lon, raggio_m, lati, minimo_mq, quante=1)
    return vicine[0] if vicine else None


def _mappa_catastale(y0, x0, y1, x1, strati, lati, trasparente="TRUE"):
    dati = prendi(CATASTO, {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
        "LAYERS": strati, "STYLES": "", "CRS": "EPSG:6706",
        "BBOX": "%f,%f,%f,%f" % (y0, x0, y1, x1),
        "WIDTH": lati, "HEIGHT": lati, "FORMAT": "image/png",
        "TRANSPARENT": trasparente,
    })
    try:
        return Image.open(io.BytesIO(dati)).convert("RGB")
    except Exception:                               # noqa: BLE001
        # al posto della mappa e' arrivato un rifiuto scritto: e' lo stesso "no"
        # che da' la domanda sulla particella, e si tratta allo stesso modo
        testo = dati.decode("utf-8", "replace")
        if "ServiceException" in testo:
            registra_errore_catasto(testo)
            raise CatastoOccupato(
                "Il catasto non manda la mappa dei confini in questo momento.")
        raise


def _merc(lat, lon, z):
    """Dal punto sulla terra al punto sulla griglia delle mappe del web."""
    n = 2.0 ** z
    r = math.radians(lat)
    return ((lon + 180.0) / 360.0 * n,
            (1.0 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2.0 * n)


def _foto_dall_alto(y0, x0, y1, x1, lati):
    """La foto dall'alto, presa a tessere e ricucita sul riquadro che serve.

    Non tutta l'Italia è fotografata alla stessa finezza: dove non arrivano le
    foto più ravvicinate il servizio risponde lo stesso, ma con un riquadro
    grigio che dice "map data not yet available". Per non incollare un grigio
    al posto della foto si chiede prima quali tessere esistono davvero, e si
    scende di un passo finché non ci sono tutte.

    Le tessere sono nella proiezione delle mappe del web. Su cento metri la
    differenza con la nostra griglia è meno di un pixel, quindi basta ritagliare.
    """
    ultimo = None
    for z in (21, 20, 19, 18, 17):
        sx0, sy0 = _merc(y1, x0, z)
        sx1, sy1 = _merc(y0, x1, z)
        if (sx1 - sx0) * 256 > 2600:          # troppe tessere da cucire
            continue
        tx0, ty0 = int(math.floor(sx0)), int(math.floor(sy0))
        tx1, ty1 = int(math.floor(sx1)), int(math.floor(sy1))
        larghe, alte = tx1 - tx0 + 1, ty1 - ty0 + 1
        try:
            esistono = json.loads(prendi("%s/tilemap/%d/%d/%d/%d/%d"
                                         % (SATELLITE, z, ty0, tx0, alte, larghe),
                                         tentativi=2).decode())
            if not all(esistono.get("data", [0])):
                continue
            tela = Image.new("RGB", (larghe * 256, alte * 256))
            for tx in range(tx0, tx1 + 1):
                for ty in range(ty0, ty1 + 1):
                    t = prendi("%s/tile/%d/%d/%d" % (SATELLITE, z, ty, tx), tentativi=2)
                    tela.paste(Image.open(io.BytesIO(t)).convert("RGB"),
                               ((tx - tx0) * 256, (ty - ty0) * 256))
        except Exception as e:                # noqa: BLE001
            ultimo = e
            continue
        taglio = tela.crop((int(round((sx0 - tx0) * 256)), int(round((sy0 - ty0) * 256)),
                            int(round((sx1 - tx0) * 256)), int(round((sy1 - ty0) * 256))))
        if taglio.width < 8 or taglio.height < 8:
            continue
        return taglio.resize((lati, lati), Image.LANCZOS)
    raise RuntimeError("La foto dall'alto non arriva (%s)" % ultimo)


# ------------------------------------------------- le foto delle Regioni
#
# Le Regioni pubblicano gratis le ortofoto dei voli AGEA. Sulla carta sono gli
# stessi venti centimetri per pixel delle tessere Esri, ma si leggono molto
# meglio: sono piu' recenti e meno impastate. A Longana, su Esri c'e' ancora un
# cantiere dove adesso ci sono case finite (verificato il 24 settembre 2026).
# Licenza: le ortofoto AGEA sono Creative Commons con attribuzione, quindi si
# usano anche in un prodotto che si vende, purche' il nome della fonte resti
# stampato sulla foto.
#
# Copertura a macchia di leopardo: si prova quella della zona, e se non c'e' o
# tace si torna a Esri senza che nessuno se ne accorga. Il riquadro di ogni
# fonte e' quello dichiarato dal servizio stesso.
FOTO_REGIONI = [
    {"chiave": "lombardia",
     "nome": "Ortofoto 2021 Regione Lombardia (AGEA)",
     "tipo": "arcgis",
     "url": ("https://www.cartografia.servizirl.it/arcgis2/rest/services/"
             "BaseMap/Ortofoto2021/ImageServer/exportImage"),
     "riquadro": (44.6473, 8.4423, 46.6569, 11.5324)},
    {"chiave": "emilia-romagna",
     "nome": "Ortofoto 2020 Regione Emilia-Romagna (AGEA)",
     "tipo": "wms13",
     "url": "https://servizigis.regione.emilia-romagna.it/wms/agea2020_rgb",
     "strato": "Agea2020_RGB",
     "riquadro": (43.7012, 9.1621, 45.1540, 12.8682)},
]
# quanto si aspetta una Regione prima di lasciar perdere: davanti a un cliente
# non si sta fermi, e la foto di scorta c'e' sempre
ATTESA_REGIONE = 9


def _in_metri_mercatore(lat, lon):
    """Il punto nella proiezione delle mappe del web."""
    r = 6378137.0
    return (r * math.radians(lon),
            r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def _foto_vuota(img):
    """Una foto tutta di un colore solo: la Regione ha risposto, ma con niente."""
    try:
        from PIL import ImageStat
        s = ImageStat.Stat(img.convert("RGB"))
        return max(s.stddev) < 3.0
    except Exception:                                  # noqa: BLE001
        return False


def regione_del_punto(lat, lon, chiave=""):
    """La fonte regionale che copre questo punto, se c'e'."""
    for r in FOTO_REGIONI:
        if chiave and r["chiave"] != chiave:
            continue
        a, b, c, d = r["riquadro"]
        if a <= lat <= c and b <= lon <= d:
            return r
    return None


def fonti_del_punto(lat, lon):
    """Le foto fra cui si puo' scegliere in questo punto, per il tasto «Cambia
    la foto». Prima quella che l'app userebbe da sola."""
    fonti = []
    r = regione_del_punto(lat, lon)
    if r:
        fonti.append({"chiave": r["chiave"], "nome": r["nome"]})
    fonti.append({"chiave": "esri", "nome": "Esri World Imagery"})
    return fonti


def _foto_dalla_regione(y0, x0, y1, x1, lati, fonte):
    """La foto dall'alto presa dal servizio della Regione, sullo stesso riquadro."""
    if fonte["tipo"] == "arcgis":
        # il servizio parla in metri della proiezione web: il riquadro si converte
        x_min, y_min = _in_metri_mercatore(y0, x0)
        x_max, y_max = _in_metri_mercatore(y1, x1)
        dati = {"bbox": "%f,%f,%f,%f" % (x_min, y_min, x_max, y_max),
                "bboxSR": "3857", "imageSR": "3857",
                "size": "%d,%d" % (lati, lati), "format": "jpg", "f": "image"}
    else:
        # WMS 1.3.0 in gradi: l'asse va scritto latitudine prima, longitudine dopo
        dati = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
                "LAYERS": fonte["strato"], "STYLES": "", "CRS": "EPSG:4326",
                "BBOX": "%f,%f,%f,%f" % (y0, x0, y1, x1),
                "WIDTH": str(lati), "HEIGHT": str(lati), "FORMAT": "image/jpeg"}
    grezza = prendi(fonte["url"], dati, tentativi=1, tempo=ATTESA_REGIONE)
    img = Image.open(io.BytesIO(grezza)).convert("RGB")
    if img.size != (lati, lati):
        img = img.resize((lati, lati), Image.LANCZOS)
    if _foto_vuota(img):
        raise RuntimeError("la Regione ha risposto con un riquadro vuoto")
    return img


def foto_dall_alto(y0, x0, y1, x1, lati, quale=""):
    """La foto migliore per questo riquadro, e il nome di chi l'ha fatta.

    `quale` serve solo quando il giardiniere la forza a mano: "esri" resta su
    Esri, il nome di una Regione prova soltanto quella. Vuoto: sceglie l'app.
    """
    if quale != "esri":
        fonte = regione_del_punto((y0 + y1) / 2, (x0 + x1) / 2,
                                  "" if quale in ("", "regione") else quale)
        if fonte:
            try:
                return _foto_dalla_regione(y0, x0, y1, x1, lati, fonte), fonte["nome"]
            except Exception as e:                     # noqa: BLE001
                print("     la Regione non ha dato la foto:", e)
    return _foto_dall_alto(y0, x0, y1, x1, lati), "Esri World Imagery"


def _riempi(passabile, lati, semi):
    """Riempie la macchia a partire da uno o più semi. Niente ricorsione:
    una lista di pixel da guardare, che si svuota."""
    dentro = bytearray(lati * lati)
    da_guardare = []
    for x, y in semi:
        i = y * lati + x
        if passabile[i] and not dentro[i]:
            dentro[i] = 1
            da_guardare.append((x, y))
    if not da_guardare:
        return dentro, 0, (0, 0, 0, 0)
    quanti = len(da_guardare)
    minx = min(s[0] for s in da_guardare); maxx = max(s[0] for s in da_guardare)
    miny = min(s[1] for s in da_guardare); maxy = max(s[1] for s in da_guardare)
    while da_guardare:
        x, y = da_guardare.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < lati and 0 <= ny < lati:
                i = ny * lati + nx
                if passabile[i] and not dentro[i]:
                    dentro[i] = 1
                    quanti += 1
                    da_guardare.append((nx, ny))
                    if nx < minx: minx = nx
                    if nx > maxx: maxx = nx
                    if ny < miny: miny = ny
                    if ny > maxy: maxy = ny
    return dentro, quanti, (minx, miny, maxx, maxy)


def _vicino(passabile, lati, px, py, raggio=16):
    """Il pixel buono più vicino al punto: capita che il punto cada su una linea."""
    if passabile[py * lati + px]:
        return (px, py)
    for r in range(1, raggio + 1):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                x, y = px + dx, py + dy
                if 0 <= x < lati and 0 <= y < lati and passabile[y * lati + x]:
                    return (x, y)
    return None


def _allarga(maschera, lati, quanto=7):
    m = Image.frombytes("L", (lati, lati), bytes(255 if b else 0 for b in maschera))
    return m.filter(ImageFilter.MaxFilter(quanto)).tobytes()


def _e_verde(p):
    """Un pixel della foto dall'alto che e' vegetazione: il verde supera rosso e blu
    (indice "excess green", 2G - R - B). Esclude grigi, ghiaia, terra, tetti e le
    ombre troppo scure per dire cosa c'e' sotto."""
    r, g, b = p
    return g > 45 and g >= r and 2 * g - r - b > SOGLIA_VERDE


def _maschera(immagine, prova):
    return bytearray(1 if prova(p) else 0 for p in immagine.getdata())


def lato_inquadratura(alto_m, largo_m):
    """Quanto terreno far entrare nella foto, in metri da un bordo all'altro.

    Il lotto ci deve stare tutto, con un margine che basta a vedere dove finisce,
    e non di piu': su una particella di otto metri un'inquadratura da sessanta
    lascia il giardino grande come un francobollo, e segnare i punti col dito
    diventa impossibile (Casalmaiocco, prova con Cafagna del 17 settembre 2026).
    Dall'altra parte non c'e' piu' il vecchio tetto di 640 m, che tagliava a meta'
    i lotti di campagna (Monticelli d'Ongina, stessa prova).
    """
    grande = max(alto_m, largo_m, 0.0)
    return max(22.0, min(2 * (grande * 0.62 + 6.0), 2200.0))


# Il passo piu' fine che la fonte delle foto abbia davvero. Misurato il 21 settembre
# 2026 in dieci posti d'Italia, da Bormio a Palermo e da Milano a Cagliari: le tessere
# piu' profonde che esistono sono quelle dello zoom 19, cioe' fra 0,206 e 0,235 metri
# di terreno per pixel. Lo zoom 20 e il 21 non ci sono da nessuna parte: il servizio
# risponde che quelle tessere non le ha. Quindi piu' dettaglio di cosi' non si puo'
# chiedere: non e' un limite nostro, e' quanto in alto volava l'aereo.
FINEZZA_FONTE = 0.21


def quanti_pixel(lato_m):
    """Quanti pixel per lato chiedere alle mappe.

    Si punta al passo della fonte, non a uno piu' grosso: chiedere meno pixel di
    quanti la foto ne abbia davvero vuol dire rimpicciolirla e buttare via dettaglio
    che avevamo gia' in mano. Prima si puntava a trenta centimetri per pixel, e su un
    riquadro da 300 metri si consegnavano 1000 pixel dove la fonte ne aveva 1433: un
    terzo del lato buttato. Sotto i 190 metri di riquadro non cambia niente, perche'
    il minimo di 900 e' gia' piu' di quanti pixel la fonte abbia.

    Il tetto di 1600 c'e' perche' i metri quadri si contano pixel per pixel, e il
    conto cresce col quadrato: a 1600 sono un secondo e sette, a 1900 due e mezzo.
    Davanti al cliente quei secondi si sentono.
    """
    return max(900, min(1600, int(lato_m / FINEZZA_FONTE)))


# La foto da guardare non e' la griglia su cui si contano i metri quadri, e non deve
# avere lo stesso tetto. Il conto a pixel costa tempo e resta a 1600; la foto invece
# va lasciata fine quanto la fonte ce l'ha davvero, se no si butta via dettaglio che
# avevamo gia' in mano. A Via Torino 5 le tessere di Esri arrivano a 21 cm per pixel
# (2116 pixel sul riquadro da 444 metri) e la foto veniva consegnata a 28: ingrandendo
# si vedeva la sfocatura, non la casa. Cafagna l'ha detto con parole sue il 23
# settembre 2026, "non si ingrandiva abbastanza".
TETTO_FOTO = 2200
# Il passo delle tessere cambia con la latitudine: 0,206 metri per pixel a Bormio,
# 0,235 a Palermo. Qui si prende il piu' fine d'Italia, se no al nord si consegnava
# qualche pixel in meno di quanti la fonte ne avesse (952 contro 955).
FINEZZA_FOTO = 0.205


def quanti_pixel_foto(lato_m):
    """Quanti pixel deve avere la foto su cui il giardiniere guarda e disegna."""
    return max(900, min(TETTO_FOTO, int(math.ceil(lato_m / FINEZZA_FOTO))))


def misura(semi, riquadri, lati=None, lato_m=None, confini=None, foto_da=""):
    """Conta i metri quadri del lotto: una particella o piu', ognuna col suo punto.

    Il conto si fa sui pixel della mappa che disegna l'Agenzia delle Entrate.
    Si chiede la mappa di un pezzo di terreno di misura nota, quindi si sa
    quanti metri quadri vale un pixel. Poi si riempie ogni particella partendo
    dal suo punto e si contano i pixel: prima il terreno scoperto, poi i
    fabbricati che gli stanno attaccati. Sommati fanno il lotto.

    semi: [(lat, lon), ...], il primo e' quello dell'indirizzo, gli altri li tocca
    il giardiniere sulla foto. riquadri: l'ingombro catastale di ogni particella.

    Il terreno libero non comprende i fabbricati. Prima, quando il punto cadeva sul
    tetto (succede spesso: il servizio indirizzi mette il civico sulla casa), la
    casa veniva contata due volte, come scoperto e come coperto: a San Zenone, il
    13 settembre 2026, 228 mq di "scoperto" erano tutti tetto.
    """
    # il riquadro da disegnare: quello di tutte le particelle insieme, con un po' di aria
    buoni = [q for q in riquadri if q]
    confini = confini or []
    confini_buoni = []
    visti_confini = set()
    for confine in confini:
        if not confine or len(confine) < 3:
            continue
        chiave = tuple((round(p[0], 8), round(p[1], 8)) for p in confine)
        if chiave not in visti_confini:
            visti_confini.add(chiave)
            confini_buoni.append(confine)
    if buoni:
        la0 = min(q["lat0"] for q in buoni); la1 = max(q["lat1"] for q in buoni)
        lo0 = min(q["lon0"] for q in buoni); lo1 = max(q["lon1"] for q in buoni)
        clat, clon = (la0 + la1) / 2, (lo0 + lo1) / 2
        alto_m  = (la1 - la0) * 111320.0
        largo_m = (lo1 - lo0) * 111320.0 * math.cos(math.radians(clat))
        lato = lato_inquadratura(alto_m, largo_m)
    else:
        clat = sum(s[0] for s in semi) / len(semi)
        clon = sum(s[1] for s in semi) / len(semi)
        lato = 110.0
    # chi chiama puo' chiedere un'inquadratura sua: e' il tasto "Allarga la foto"
    # di quando il lotto esce dal quadro
    if lato_m:
        lato = max(22.0, min(float(lato_m), 2200.0))
    meta = lato / 2
    if not lati:
        lati = quanti_pixel(lato)
    y0, x0, y1, x1 = _riquadro(clat, clon, meta)

    mappa = _mappa_catastale(y0, x0, y1, x1, "CP.CadastralParcel", lati)
    fabb  = _mappa_catastale(y0, x0, y1, x1, "fabbricati", lati)
    # la foto si chiede fine quanto la fonte, poi si rimpicciolisce per il conteggio:
    # le tessere scaricate sono le stesse, quindi non costa un giro di rete in piu'
    lati_foto = max(lati, quanti_pixel_foto(lato))
    foto_fine, nome_fonte = foto_dall_alto(y0, x0, y1, x1, lati_foto, foto_da)
    foto = foto_fine if lati_foto == lati else foto_fine.resize((lati, lati), Image.LANCZOS)

    # quanto vale un pixel, in metri e in metri quadri
    m_per_px = (2 * meta) / lati
    mq_px    = m_per_px * m_per_px

    def a_pixel(la, lo):
        return (max(0, min(lati - 1, int(round((lo - x0) / (x1 - x0) * (lati - 1))))),
                max(0, min(lati - 1, int(round((y1 - la) / (y1 - y0) * (lati - 1))))))

    # Il recinto vero. Quando il WFS lo manda, tutto quello che si conta resta qui
    # dentro e il lotto viene dalla geometria, non dalla macchia partita dal tocco.
    poligono = bytearray(lati * lati)
    if confini_buoni:
        tela = Image.new("L", (lati, lati), 0)
        penna = ImageDraw.Draw(tela)
        for confine in confini_buoni:
            penna.polygon([a_pixel(la, lo) for la, lo in confine], fill=1)
        poligono = bytearray(tela.tobytes())

    # sulla mappa dell'Agenzia i fabbricati sono arancioni pieni
    edifici = _maschera(fabb, lambda p: p[0] > 150 and p[1] < 190 and p[2] < 120
                                        and p[0] - p[2] > 60)
    if confini_buoni:
        edifici = bytearray(e & p for e, p in zip(edifici, poligono))
    # sulla mappa delle particelle il terreno è color carta, i confini sono neri,
    # e quello che particella non è (strade, acqua) resta vuoto. Il terreno libero
    # e' quello senza un fabbricato sopra.
    terreno = bytearray(1 if (not e and p[0] > 200 and p[1] > 185 and p[2] > 130
                              and p[0] - p[2] > 25) else 0
                        for p, e in zip(mappa.getdata(), edifici))

    # il recinto: l'ingombro che il catasto dichiara per ogni particella scelta.
    # Fuori di lì il lotto non può stare, quindi non ci si guarda nemmeno.
    # È la rete di sicurezza che impedisce al conto di sbordare sul vicino.
    if buoni and len(buoni) == len(riquadri):
        ammesso = bytearray(lati * lati)
        for q in buoni:
            rx0, ry0 = a_pixel(q["lat1"], q["lon0"])
            rx1, ry1 = a_pixel(q["lat0"], q["lon1"])
            rx0, ry0 = max(0, rx0 - 3), max(0, ry0 - 3)
            rx1, ry1 = min(lati - 1, rx1 + 3), min(lati - 1, ry1 + 3)
            for y in range(ry0, ry1 + 1):
                ammesso[y * lati + rx0: y * lati + rx1 + 1] = b"\x01" * (rx1 - rx0 + 1)
        terreno = bytearray(t & a for t, a in zip(terreno, ammesso))
        edifici = bytearray(e & a for e, a in zip(edifici, ammesso))

    # i punti: quello caduto su un tetto parte dal fabbricato, gli altri dal terreno
    semi_terreno, semi_edifici = [], []
    for la, lo in semi:
        px, py = a_pixel(la, lo)
        if edifici[py * lati + px]:
            semi_edifici.append((px, py))
            continue
        s = _vicino(terreno, lati, px, py)
        if s:
            semi_terreno.append(s)
    if not semi_terreno and not semi_edifici:
        raise RuntimeError("Nel punto trovato non c'è terreno di particella da misurare.")

    # 1. lo scoperto: si riempie il terreno libero a partire dai punti.
    #    Le linee nere del catasto lo fermano: confini del lotto e muri di casa.
    vuoto = bytearray(lati * lati)
    aperto, aperto_px, est_a = _riempi(terreno, lati, semi_terreno) if semi_terreno \
        else (vuoto, 0, None)

    # Senza il recinto del catasto (succede quando l'Agenzia non dice piu' quale
    # particella e', ma continua a disegnare le mappe) il passo 2 qui sotto si
    # prenderebbe anche il capannone del vicino, che il giardino lo tocca appena:
    # a Rivolta d'Adda, il 17 settembre 2026, 858 mq invece di 689. Allora si
    # guardano solo i fabbricati che stanno dentro l'ingombro dello scoperto e non
    # lo sfondano. Quello su cui e' caduto l'indirizzo non si scarta mai: e' la
    # casa del cliente.
    if not buoni and aperto_px and est_a:
        rx0, ry0, rx1, ry1 = est_a
        ammesso = bytearray(lati * lati)
        for y in range(ry0, ry1 + 1):
            ammesso[y * lati + rx0: y * lati + rx1 + 1] = b"\x01" * (rx1 - rx0 + 1)
        edifici = bytearray(e & a for e, a in zip(edifici, ammesso))
        sul_bordo = [(x, y) for y in (ry0, ry1) for x in range(rx0, rx1 + 1)
                     if edifici[y * lati + x]]
        sul_bordo += [(x, y) for x in (rx0, rx1) for y in range(ry0, ry1 + 1)
                      if edifici[y * lati + x]]
        if sul_bordo:
            sfondano, _, _ = _riempi(edifici, lati, sul_bordo)
            if not any(sfondano[y * lati + x] for x, y in semi_edifici):
                edifici = bytearray(0 if f else e for e, f in zip(edifici, sfondano))

    # 2. il coperto: i fabbricati che toccano quel terreno sono di questo lotto.
    #    Si allarga di poco lo scoperto, quel tanto che basta a scavalcare la
    #    linea del muro, e da lì si riempiono le sagome piene dei fabbricati.
    bordo = _allarga(aperto, lati, 7) if aperto_px else vuoto
    semi_c = semi_edifici + [(i % lati, i // lati) for i in range(lati * lati)
                             if edifici[i] and bordo[i]]
    coperti, coperto_px, est_c = _riempi(edifici, lati, semi_c) if semi_c \
        else (bytearray(lati * lati), 0, None)

    dentro = bytearray(a | c for a, c in zip(aperto, coperti))
    parti = [e for e, n in ((est_a, aperto_px), (est_c, coperto_px)) if n]
    estremi = (min(e[0] for e in parti), min(e[1] for e in parti),
               max(e[2] for e in parti), max(e[3] for e in parti)) if parti else (0, 0, 0, 0)

    # 3. il verde: dentro lo scoperto, i pixel che nella foto dall'alto sono verdi.
    #    Lo scoperto del catasto e' tutto il terreno non costruito: cortile,
    #    vialetto, posto auto, piscina. Il verde toglie quello che verde non e'.
    #    Prende anche siepi e chiome degli alberi, e cambia con stagione e ombre:
    #    e' una stima da confermare sul posto, non una misura.
    colori = foto.getdata()
    verde_px = sum(1 for i in range(lati * lati) if aperto[i] and _e_verde(colori[i]))

    if confini_buoni:
        lotto_mq = sum(area_confine_mq(c) for c in confini_buoni)
        poligono_px = sum(poligono)
        # Le classi della foto restano stime, ma sono rapportate all'area vera del
        # confine. Cosi' coperto + scoperto fa sempre esattamente il lotto.
        coperto_px_confine = sum(1 for e, p in zip(edifici, poligono) if e and p)
        coperto_mq = lotto_mq * coperto_px_confine / max(1, poligono_px)
        scoperto_mq = max(0.0, lotto_mq - coperto_mq)
        verde_px_confine = sum(1 for i, p in enumerate(poligono)
                               if p and not edifici[i] and _e_verde(colori[i]))
        scoperto_px_confine = max(1, poligono_px - coperto_px_confine)
        verde_mq = scoperto_mq * verde_px_confine / scoperto_px_confine
        dentro = poligono
    else:
        lotto_mq    = (aperto_px + coperto_px) * mq_px
        coperto_mq  = coperto_px * mq_px
        scoperto_mq = aperto_px * mq_px
        verde_mq    = verde_px * mq_px

    largo = (estremi[2] - estremi[0] + 1) * m_per_px
    alto  = (estremi[3] - estremi[1] + 1) * m_per_px

    # il conto è da guardare due volte se copre molto meno dell'ingombro che il
    # catasto dichiara: vuol dire che un muro ha tagliato il lotto in due pezzi
    sospetto = False
    if buoni:
        # ogni riquadro una volta sola: i punti intorno alla casa che cadono nella
        # stessa particella ripetono il suo riquadro, e sommarlo quattro volte faceva
        # sembrare "tagliato da un muro" un lotto intero (Rivolta d'Adda, 13/9/2026)
        unici = {(q["lat0"], q["lon0"], q["lat1"], q["lon1"]): q for q in buoni}.values()
        recinto_mq = sum((q["lat1"] - q["lat0"]) * 111320.0 *
                         (q["lon1"] - q["lon0"]) * 111320.0 * math.cos(math.radians(clat))
                         for q in unici)
        if recinto_mq > 0 and lotto_mq < recinto_mq * 0.30:
            sospetto = True

    # il lotto che arriva al bordo della foto quasi sempre continua fuori, e allora
    # i metri quadri sono solo il pezzo inquadrato: va detto, e va offerto di
    # allargare (Monticelli d'Ongina, 17 settembre 2026)
    tocca_il_bordo = bool(parti) and (estremi[0] <= 2 or estremi[1] <= 2 or
                                      estremi[2] >= lati - 3 or estremi[3] >= lati - 3)

    lotto_intero = int(round(lotto_mq))
    coperto_intero = min(lotto_intero, int(round(coperto_mq)))
    scoperto_intero = lotto_intero - coperto_intero
    verde_intero = min(int(round(verde_mq)), scoperto_intero)
    disegno = componi(foto, mappa, dentro, lati, nome_fonte)
    return {
        "tocca_il_bordo": tocca_il_bordo,
        # chi ha fatto la foto: finisce stampato sotto e scritto nel rilievo
        "fonte_foto": nome_fonte,
        # la foto pulita e il confine come punti: servono per ricalcare il lotto
        # e spostarlo finche' non sta sul giardino vero
        "foto_pulita": foto_fine,
        # il confine vero del catasto quando c'e', se no il bordo della macchia
        "contorno": (contorno_dal_confine(confini_buoni, y0, x0, y1, x1)
                     or contorno_punti(dentro, lati)),
        # il prato visto dall'alto, gia' in forma di disegno: cosi' il giardiniere
        # conferma quello che deve tagliare invece di ricalcarlo a mano
        "contorni_verde": contorni_verde(dentro, edifici, colori, lati, mq_px),
        "lotto_mq": lotto_intero,
        "coperto_mq": coperto_intero,
        "scoperto_mq": scoperto_intero,
        "verde_mq": verde_intero,
        "ingombro": "%d × %d m" % (round(largo), round(alto)),
        "sospetto": sospetto,
        "dal_confine": bool(confini_buoni),
        "senza_scoperto": aperto_px == 0,
        "metri_per_pixel": round(m_per_px, 4),
        # gli angoli della foto sulla terra e quanti metri e' larga: servono
        # all'applicazione per trasformare un tocco sulla foto in un punto vero
        "angoli": {"lat0": round(y0, 7), "lon0": round(x0, 7), "lat1": round(y1, 7), "lon1": round(x1, 7)},
        "lato_m": round(2 * meta, 2),
        "immagine": disegno,
    }


# ------------------------------------------------- la figura da guardare

def _contorno(maschera, lati, spessore=5):
    """Il contorno della macchia riempita, spesso quanto serve per vederlo."""
    m = Image.frombytes("L", (lati, lati), bytes(255 if b else 0 for b in maschera))
    fuori = m.filter(ImageFilter.MaxFilter(spessore))
    dentro = m.filter(ImageFilter.MinFilter(spessore))
    return Image.frombytes("L", (lati, lati),
                           bytes(255 if a and not b else 0
                                 for a, b in zip(fuori.tobytes(), dentro.tobytes())))


# ------------------------------------------------- il confine come linea di punti
#
# Il confine del catasto disegnato sulla foto non combacia mai al pixel: le foto
# dall'alto hanno qualche metro di scarto e i tetti "cadono" di lato, perche'
# l'aereo non era a piombo sulla casa (Andrea, 19 settembre 2026: "il catasto
# matcha molto male sull'immagine satellitare"). Percio' il confine non serve solo
# come disegno: serve come **punti spostabili**. L'applicazione lo ricalca, lui lo
# trascina finche' non sta sul giardino vero, e i metri quadri li conta da li'.

def _macchia_piu_grande(dentro, lati):
    """La macchia piu' grande, fra quelle che ci sono: il lotto vero. Senza questo
    si partiva dal primo pixel acceso in alto a sinistra, che puo' essere un
    pezzetto staccato di muro, e il giro del contorno finiva subito."""
    visti = bytearray(lati * lati)
    grande, quanti_grande = None, 0
    for i, b in enumerate(dentro):
        if not b or visti[i]:
            continue
        macchia, quanti, _est = _riempi(dentro, lati, [(i % lati, i // lati)])
        visti = bytearray(v | m for v, m in zip(visti, macchia))
        if quanti > quanti_grande:
            grande, quanti_grande = macchia, quanti
    return grande, quanti_grande


def _traccia_bordo(dentro, lati, partenza=None, quanti=0):
    """Il giro del contorno della macchia, pixel per pixel (Moore, in senso orario)."""
    if partenza is None:
        for i, b in enumerate(dentro):
            if b:
                partenza = (i % lati, i // lati)
                break
    if not partenza:
        return []
    pieno = lambda x, y: 0 <= x < lati and 0 <= y < lati and dentro[y * lati + x]
    intorno = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
    giro = [partenza]
    qui, verso = partenza, 0          # si arriva da sinistra: il primo pieno della riga
    for _ in range(4 * (quanti or lati * lati) + 16):
        trovato = False
        for k in range(8):
            d = (verso + 5 + k) % 8   # si riparte da chi sta dietro, girando in tondo
            dx, dy = intorno[d]
            if pieno(qui[0] + dx, qui[1] + dy):
                qui, verso, trovato = (qui[0] + dx, qui[1] + dy), d, True
                break
        if not trovato:
            break
        if qui == partenza and len(giro) > 2:
            break
        giro.append(qui)
    return giro


def _semplifica(punti, tolleranza):
    """Douglas-Peucker: la stessa linea con molti meno punti."""
    if len(punti) < 3:
        return list(punti)
    a, b = punti[0], punti[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    lontano, quale = -1.0, 0
    for i in range(1, len(punti) - 1):
        p = punti[i]
        if l2:
            s = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
            d = math.hypot(p[0] - (a[0] + s * dx), p[1] - (a[1] + s * dy))
        else:
            d = math.hypot(p[0] - a[0], p[1] - a[1])
        if d > lontano:
            lontano, quale = d, i
    if lontano <= tolleranza:
        return [a, b]
    return _semplifica(punti[:quale + 1], tolleranza)[:-1] + _semplifica(punti[quale:], tolleranza)


def _semplifica_anello(punti, tolleranza):
    """Douglas-Peucker su una forma chiusa.

    Un anello non ha un inizio e una fine: se lo si semplifica come una linea,
    il primo punto e l'ultimo coincidono, la distanza fra i due estremi e' zero
    e il taglio cade a caso. Si spezza invece in due tratti fra il primo punto e
    quello piu' lontano da lui, e si semplificano separatamente.
    """
    anello = list(punti)
    if len(anello) > 1 and anello[0] == anello[-1]:
        anello.pop()
    if len(anello) < 4:
        return anello
    a = anello[0]
    lontano = max(range(1, len(anello)),
                  key=lambda i: math.hypot(anello[i][0] - a[0], anello[i][1] - a[1]))
    primo  = _semplifica(anello[:lontano + 1], tolleranza)
    secondo = _semplifica(anello[lontano:] + [a], tolleranza)
    return primo[:-1] + secondo[:-1]


def contorno_dal_confine(confini, y0, x0, y1, x1, quanti=120):
    """Il confine vero del catasto, portato direttamente in frazioni della foto.

    Prima questo contorno si ricavava ridisegnando il poligono su una griglia di
    pixel e poi ritracciandone il bordo: due passaggi che smussano gli angoli,
    spostano i lati e buttano via i pezzi staccati. Il catasto pero' il poligono
    ce lo manda gia' come punti (a Via Torino 5, Casalmaiocco, sono 89 vertici),
    quindi basta convertirlo. Andrea, 23 settembre 2026: "la forma dei metri
    quadri fuori quel punto viene riprodotta male".

    Fra piu' particelle si prende la piu' grande, perche' l'applicazione disegna
    una forma sola. I punti del confine sono (latitudine, longitudine).
    """
    if not confini or x1 == x0 or y1 == y0:
        return []
    piu_grande, quanto = None, -1.0
    for confine in confini:
        if not confine or len(confine) < 3:
            continue
        area = area_confine_mq(confine)
        if area > quanto:
            piu_grande, quanto = confine, area
    if not piu_grande:
        return []
    frazioni = [((lo - x0) / (x1 - x0), (y1 - la) / (y1 - y0)) for la, lo in piu_grande]
    # la tolleranza e' in frazioni del lato della foto: 0,0008 su un'inquadratura
    # di 100 metri vale otto centimetri, cioe' meno di mezzo pixel della foto
    tolleranza = 0.0008
    semplice = _semplifica_anello(frazioni, tolleranza)
    for _ in range(8):
        if len(semplice) <= quanti:
            break
        tolleranza *= 1.6
        semplice = _semplifica_anello(frazioni, tolleranza)
    fuori = []
    for x, y in semplice[:quanti]:
        q = [round(x, 5), round(y, 5)]
        if fuori and math.hypot(q[0] - fuori[-1][0], q[1] - fuori[-1][1]) < 0.0004:
            continue
        fuori.append(q)
    if len(fuori) > 3 and math.hypot(fuori[0][0] - fuori[-1][0],
                                     fuori[0][1] - fuori[-1][1]) < 0.0004:
        fuori.pop()
    return fuori if len(fuori) >= 3 else []


def contorno_punti(dentro, lati, quanti=40):
    """Il confine del lotto come pochi punti, in frazioni della foto (0..1):
    le stesse coordinate con cui l'applicazione disegna le parti del giardino."""
    macchia, quanti = _macchia_piu_grande(dentro, lati)
    if not macchia or quanti < 16:
        return []
    partenza = None
    for i, b in enumerate(macchia):
        if b:
            partenza = (i % lati, i // lati)
            break
    giro = _traccia_bordo(macchia, lati, partenza, quanti)
    if len(giro) < 8:
        return []
    tolleranza = max(1.5, lati / 260.0)
    semplice = _semplifica(giro, tolleranza)
    for _ in range(8):
        if len(semplice) <= quanti:
            break
        tolleranza *= 1.7
        semplice = _semplifica(giro, tolleranza)
    fuori = []
    for x, y in semplice[:quanti]:
        p = [round(x / (lati - 1.0), 4), round(y / (lati - 1.0), 4)]
        if fuori and math.hypot(p[0] - fuori[-1][0], p[1] - fuori[-1][1]) < 0.004:
            continue
        fuori.append(p)
    if len(fuori) > 3 and math.hypot(fuori[0][0] - fuori[-1][0], fuori[0][1] - fuori[-1][1]) < 0.004:
        fuori.pop()
    return fuori


def _punti_della_macchia(macchia, quanti_px, lati, quanti):
    """Il giro di una macchia, ridotto a pochi punti in frazioni della foto (0..1)."""
    partenza = None
    for i, b in enumerate(macchia):
        if b:
            partenza = (i % lati, i // lati)
            break
    if not partenza:
        return []
    giro = _traccia_bordo(macchia, lati, partenza, quanti_px)
    if len(giro) < 8:
        return []
    tolleranza = max(1.5, lati / 260.0)
    semplice = _semplifica_anello(giro, tolleranza)
    for _ in range(8):
        if len(semplice) <= quanti:
            break
        tolleranza *= 1.7
        semplice = _semplifica_anello(giro, tolleranza)
    fuori = []
    for x, y in semplice[:quanti]:
        p = [round(x / (lati - 1.0), 4), round(y / (lati - 1.0), 4)]
        if fuori and math.hypot(p[0] - fuori[-1][0], p[1] - fuori[-1][1]) < 0.004:
            continue
        fuori.append(p)
    if len(fuori) > 3 and math.hypot(fuori[0][0] - fuori[-1][0], fuori[0][1] - fuori[-1][1]) < 0.004:
        fuori.pop()
    return fuori if len(fuori) >= 3 else []


def _lisciata(maschera, lati, passi=((ImageFilter.MinFilter, 3), (ImageFilter.MaxFilter, 5),
                                     (ImageFilter.MinFilter, 3))):
    """Via i puntini isolati, e i buchi piccoli richiusi.

    Il verde contato pixel per pixel e' pieno di granelli: un ciuffo fra due auto,
    l'ombra di un ramo in mezzo al prato. Ricalcare quei granelli non serve a
    nessuno: si apre (via i granelli) e si chiude (via i buchi), e resta la forma
    che un giardiniere riconosce guardando la foto.
    """
    m = Image.frombytes("L", (lati, lati), bytes(255 if b else 0 for b in maschera))
    for filtro, raggio in passi:
        m = m.filter(filtro(raggio))
    return bytearray(1 if b else 0 for b in m.tobytes())


def contorni_verde(dentro, edifici, colori, lati, mq_px, quante=4, minimo_mq=15.0, quanti=26):
    """I pezzi di prato visti dall'alto, come forme gia' disegnate.

    Il numero dei metri di verde c'era gia' (`verde_mq`), ma era solo un numero:
    l'artigiano vedeva "221 mq" e poi doveva ridisegnare a mano dove sta quel
    verde. Qui lo stesso conto restituisce **la forma**, cosi' l'applicazione puo'
    proporre il prato gia' segnato e lui deve solo guardarlo e confermare.
    Resta una stima da foto: prende anche le chiome degli alberi e non vede il
    prato che ci sta sotto (Andrea, 23 settembre 2026).
    """
    verde = bytearray(1 if (dentro[i] and not edifici[i] and _e_verde(colori[i])) else 0
                      for i in range(lati * lati))
    verde = _lisciata(verde, lati)
    pezzi, visti = [], bytearray(lati * lati)
    for i, b in enumerate(verde):
        if not b or visti[i]:
            continue
        macchia, quanti_px, _est = _riempi(verde, lati, [(i % lati, i // lati)])
        visti = bytearray(v | m for v, m in zip(visti, macchia))
        if quanti_px * mq_px >= minimo_mq:
            pezzi.append((quanti_px, macchia))
    pezzi.sort(key=lambda p: -p[0])
    forme = []
    for quanti_px, macchia in pezzi[:quante]:
        punti = _punti_della_macchia(macchia, quanti_px, lati, quanti)
        if punti:
            forme.append(punti)
    return forme


def componi(foto, mappa, dentro, lati, nome_fonte="Esri World Imagery"):
    """Foto dall'alto, confini catastali sopra, e il lotto acceso in mezzo.

    Fuori dal lotto la foto si abbassa di tono, così l'occhio va dove deve.
    """
    m = Image.frombytes("L", (lati, lati), bytes(255 if b else 0 for b in dentro))

    # tutto quello che non è il lotto va indietro di un passo
    velo = Image.blend(foto, Image.new("RGB", foto.size, SCURO), 0.50)
    fondo = Image.composite(foto, velo, m)

    # gli altri confini del catasto, appena accennati
    linee = _maschera(mappa, lambda p: p[0] < 120 and p[1] < 120 and p[2] < 120)
    tratto = Image.frombytes("L", (lati, lati), bytes(105 if b else 0 for b in linee))
    fondo = Image.composite(Image.new("RGB", fondo.size, CARTA), fondo, tratto)

    # il lotto: un velo verde leggero e il contorno marcato
    fondo = Image.composite(Image.blend(fondo, Image.new("RGB", fondo.size, VERDE), 0.22),
                            fondo, m)
    fondo = Image.composite(Image.new("RGB", fondo.size, CARTA), fondo,
                            _contorno(m.tobytes(), lati, 7))
    fondo = Image.composite(Image.new("RGB", fondo.size, VERDE), fondo,
                            _contorno(m.tobytes(), lati, 3))

    # una firma sobria in basso
    d = ImageDraw.Draw(fondo, "RGBA")
    d.rectangle([0, lati - 26, lati, lati], fill=(38, 36, 32, 165))
    d.text((11, lati - 18), "Agenzia delle Entrate  ·  " + nome_fonte,
           fill=(247, 241, 229, 235))
    return fondo


def firma_fonte(immagine, nome_fonte="Esri World Imagery"):
    """La foto senza catasto, con la fascia della fonte in basso.

    Serve quando si consegna la foto pulita come immagine principale del rilievo:
    l'attribuzione a Esri va sempre stampata sopra (il prodotto deve restare
    vendibile legalmente), e `componi` la mette solo sul disegno col catasto.
    """
    fondo = immagine.convert("RGB")
    lati = fondo.size[0]
    d = ImageDraw.Draw(fondo, "RGBA")
    d.rectangle([0, lati - 26, lati, lati], fill=(38, 36, 32, 165))
    d.text((11, lati - 18), nome_fonte, fill=(247, 241, 229, 235))
    return fondo


def in_base64(immagine, lato=TETTO_FOTO, qualita=88):
    """La foto pronta da mandare. Non si rimpicciolisce piu' a 1600: quel taglio
    buttava via un terzo del dettaglio che le tessere avevano gia'. Sopra i 1600
    pixel si scende di qualche punto di qualita', cosi' la foto piu' fine pesa
    poco piu' di quella piu' grossa di prima (954 kB contro 702)."""
    im = immagine.copy()
    # Preserve aspect ratio and never enlarge the source.
    im.thumbnail((lato, lato), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=(qualita if im.width <= 1600 else 85), optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------- il rilievo senza il catasto

LATO_SENZA_CATASTO = 150.0
FONTE_FOTO = "Foto dall'alto Esri World Imagery · confini segnati a mano"

SENZA_CATASTO = (
    "Il catasto dell'Agenzia delle Entrate non risponde. Il sopralluogo va avanti "
    "lo stesso: qui c'è la foto dall'alto di questo indirizzo, segna il giardino "
    "sulla foto e i metri quadri li conto io.")


def solo_foto(lat, lon, lato_m, foto_da=""):
    """La foto dall'alto del posto, senza chiedere niente al catasto.

    E' la via di scorta quando l'Agenzia delle Entrate tace: la foto non dipende
    da loro, e sulla foto si disegna. Un giardiniere davanti al cliente non puo'
    aspettare che un ufficio torni a rispondere.
    """
    lato = max(22.0, min(float(lato_m or LATO_SENZA_CATASTO), 2200.0))
    lati = quanti_pixel_foto(lato)
    y0, x0, y1, x1 = _riquadro(lat, lon, lato / 2)
    foto, nome_fonte = foto_dall_alto(y0, x0, y1, x1, lati, foto_da)
    foto = firma_fonte(foto, nome_fonte)
    return {
        "immagine": foto,
        "fonte_foto": nome_fonte,
        "metri_per_pixel": round(lato / lati, 4),
        "angoli": {"lat0": round(y0, 7), "lon0": round(x0, 7),
                   "lat1": round(y1, 7), "lon1": round(x1, 7)},
        "lato_m": round(lato, 2),
    }


def rilievo_da_disegnare(p, lato_m=None, motivo="", indirizzo="", cap="", foto_da=""):
    """Un rilievo senza misure: la foto giusta, e il giardino da segnare a dito."""
    f = solo_foto(p["lat"], p["lon"], lato_m, foto_da)
    avvisi = [motivo or SENZA_CATASTO]
    if p.get("avviso_comune"):
        avvisi.append(p["avviso_comune"])
    if not p["preciso"]:
        avvisi.append("L'indirizzo è stato trovato sulla via, non sul civico: "
                      "controlla che la foto sia della casa giusta.")
    return {
        "indirizzo": p["indirizzo"],
        "comune": "", "comune_nome": p["comune_nome"],
        "provincia": p.get("provincia", ""),
        "comune_verificato": bool(p.get("comune_verificato")),
        "foglio": "", "particella": "", "riferimento": "", "particelle": [],
        "suggerimenti": [] if p["preciso"] or not indirizzo
                        else _suggerimenti_calmi(indirizzo, cap),
        "lat": round(p["lat"], 7), "lon": round(p["lon"], 7),
        "punti": [[round(p["lat"], 7), round(p["lon"], 7)]],
        "da_disegnare": True,
        "ingombro": "",
        "metri_per_pixel": f["metri_per_pixel"],
        "angoli": f["angoli"],
        "lato_m": f["lato_m"],
        "fonte": "Foto dall'alto " + f["fonte_foto"] + " · confini segnati a mano",
        "fonte_foto": f["fonte_foto"],
        "fonti_foto": fonti_del_punto(p["lat"], p["lon"]),
        "preparato": time.strftime("%Y-%m-%d %H:%M"),
        "avvisi": avvisi,
        "preciso": p["preciso"],
        "foto": in_base64(f["immagine"]),
    }


# ------------------------------------------------- il rilievo, tutto insieme

def rilievo_senza_riferimenti(p, lato_m=None, indirizzo="", cap="", foto_da=""):
    """Il lotto misurato senza sapere come si chiama.

    Il catasto ha due sportelli, e si guastano separatamente: quello che dice
    "in questo punto c'e' la particella 485 del foglio 4" (GetFeatureInfo) e
    quello che disegna la mappa dei confini (GetMap). Il 17 settembre 2026 il
    primo rispondeva `InvalidFormat ERRX-2` a tutti mentre il secondo mandava le
    mappe regolarmente.

    Con le sole mappe i metri quadri si contano lo stesso: si riempie il lotto
    partendo dal punto dell'indirizzo e si contano i pixel, come sempre. Quello
    che manca e' il numero di foglio e particella, e il recinto che tiene il
    conto dentro l'ingombro dichiarato. Percio' il numero esce con un avviso.
    """
    m = misura([(p["lat"], p["lon"])], [None], lato_m=lato_m, foto_da=foto_da)
    # senza il recinto del catasto l'inquadratura parte da centodieci metri: se il
    # lotto arriva al bordo si riprova una volta sola, piu' larga, se no di un campo
    # si misurerebbe solo il pezzo inquadrato
    if m.get("tocca_il_bordo") and not lato_m:
        m = misura([(p["lat"], p["lon"])], [None], lato_m=320.0, foto_da=foto_da)
    avvisi = ["Il catasto non ha dato foglio e particella: i metri quadri sono contati "
              "sui confini disegnati sulla mappa. Guarda la figura e controllali."]
    if p.get("avviso_comune"):
        avvisi.append(p["avviso_comune"])
    if not p["preciso"]:
        avvisi.append("L'indirizzo è stato trovato sulla via, non sul civico: "
                      "controlla che il lotto acceso sia quello giusto.")
    avvisi += _avvisi_misura(m)
    return {
        "indirizzo": p["indirizzo"],
        "comune": "", "comune_nome": p["comune_nome"],
        "provincia": p.get("provincia", ""),
        "comune_verificato": bool(p.get("comune_verificato")),
        "foglio": "", "particella": "", "riferimento": "", "particelle": [],
        "senza_riferimenti": True,
        "suggerimenti": [] if p["preciso"] or not indirizzo
                        else _suggerimenti_calmi(indirizzo, cap),
        "lat": round(p["lat"], 7), "lon": round(p["lon"], 7),
        "punti": [[round(p["lat"], 7), round(p["lon"], 7)]],
        "lotto_mq": m["lotto_mq"],
        "scoperto_mq": m["scoperto_mq"],
        "coperto_mq": m["coperto_mq"],
        "verde_mq": m["verde_mq"],
        "ingombro": m["ingombro"],
        "metri_per_pixel": m["metri_per_pixel"],
        "angoli": m["angoli"],
        "lato_m": m["lato_m"],
        "fonte": "Cartografia catastale dell'Agenzia delle Entrate · foto dall'alto "
                 + m.get("fonte_foto", "Esri World Imagery"),
        "fonte_foto": m.get("fonte_foto", "Esri World Imagery"),
        "fonti_foto": fonti_del_punto(p["lat"], p["lon"]),
        # la foto senza i confini disegnati sopra, e il confine come punti
        # spostabili: i confini del catasto non combaciano col satellite, e cosi'
        # si ricalcano e si tirano al posto giusto
        "foto_pulita": in_base64(m["foto_pulita"]) if m.get("foto_pulita") else "",
        "contorno": m.get("contorno") or [],
        "contorni_verde": m.get("contorni_verde") or [],
        "preparato": time.strftime("%Y-%m-%d %H:%M"),
        "avvisi": avvisi,
        "preciso": p["preciso"],
        "foto": in_base64(m["immagine"]),
    }


def _suggerimenti_calmi(indirizzo, cap=""):
    """I suggerimenti, ma senza il rischio di far cadere la risposta: se anche
    questa ricerca non riesce, si torna una lista vuota."""
    try:
        return suggerimenti_indirizzo(indirizzo, cap)
    except Exception:                               # noqa: BLE001
        return []


def _col_punto(numero):
    """12345.6 -> "12.346", come si scrivono i numeri in italiano."""
    return "{:,}".format(int(round(numero))).replace(",", ".")


def rilievo(indirizzo, lato_m=None, cap="", foto_da=""):
    p = punto_dall_indirizzo(indirizzo, cap)
    try:
        part, lat, lon = cerca_la_particella(p["lat"], p["lon"])
        if not part:
            return rilievo_da_disegnare(p, lato_m,
                "L'indirizzo si trova, ma lì il catasto non ha una particella "
                "(succede su una strada, o a Trento e Bolzano, dove il catasto è "
                "delle Province autonome). Segna il giardino sulla foto: "
                "i metri quadri li conto io.", indirizzo, cap)
        avviso_strada = ""
        if e_una_strada(part.get("confine")):
            # Il civico e' caduto sull'asfalto: il punto dei servizi di indirizzi sta
            # davanti al cancello, e li' la particella e' la carreggiata. Ci si sposta
            # sulla casa piu' vicina e si riparte da quella.
            area, piu_lungo, _forma = forma_del_confine(part["confine"])
            casa = None
            try:
                casa = casa_piu_vicina(lat, lon)
            except CatastoOccupato:
                raise
            except Exception:                           # noqa: BLE001
                casa = None
            nuova = None
            if casa:
                nuova, lat2, lon2 = cerca_la_particella(casa["lat"], casa["lon"])
            if nuova and not e_una_strada(nuova.get("confine")):
                part, lat, lon = nuova, lat2, lon2
                avviso_strada = (
                    "Il civico cade sulla strada, non sulla casa: lì il catasto ha la "
                    "carreggiata (%s metri quadri). Sono partito dalla casa più vicina, "
                    "a %d metri, ma quella particella non è il giardino del cliente: "
                    "segna tu sulla foto quello che si lavora."
                    % (_col_punto(area), int(round(casa["distanza_m"]))))
            else:
                return rilievo_da_disegnare(
                    p, lato_m,
                    "Sotto questo civico il catasto non ha la casa: ha la strada coi "
                    "parcheggi, %s metri quadri lunghi %d metri. Segna il giardino sulla "
                    "foto: i metri quadri li conto io."
                    % (_col_punto(area), int(round(piu_lungo))), indirizzo, cap)
        semi, riquadri = _intorno_stessa_particella(part, lat, lon)
        m = misura(semi, riquadri, lato_m=lato_m,
                   confini=[part.get("confine")], foto_da=foto_da)
        if m["lotto_mq"] < 20:
            return rilievo_da_disegnare(
                p, lato_m,
                "Qui il catasto non ha dato una misura credibile. Segna il giardino "
                "a mano sulla foto: i metri quadri li conto io.", indirizzo, cap)
    except CatastoOccupato:
        # lo sportello che dice il nome della particella tace. Se quello che disegna
        # le mappe risponde ancora, il lotto si misura lo stesso; se no restano la
        # foto e il dito
        try:
            return rilievo_senza_riferimenti(p, lato_m, indirizzo, cap, foto_da)
        except Exception:                           # noqa: BLE001
            return rilievo_da_disegnare(p, lato_m, "", indirizzo, cap, foto_da)
    avvisi = [p["avviso_comune"]] if p.get("avviso_comune") else []
    if not p["preciso"]:
        civico = p.get("civico_scritto")
        if civico and p.get("civico_trovato"):
            avvisi.append("Hai scritto il civico %s, ma è stato trovato il %s: "
                          "controlla che la particella accesa sia quella giusta."
                          % (civico_da_leggere(civico),
                             civico_da_leggere(civico_scritto(", " + p["civico_trovato"])
                                               or p["civico_trovato"])))
        elif not civico:
            avvisi.append("Nell'indirizzo non c'è il numero civico: "
                          "controlla che la particella accesa sia quella giusta.")
        else:
            avvisi.append("L'indirizzo è stato trovato sulla via, non sul civico: "
                          "controlla che la particella accesa sia quella giusta.")
    if avviso_strada:
        avvisi.append(avviso_strada)
    avvisi += _avvisi_misura(m)
    # il civico non e' confermato: insieme al rilievo si mandano gli indirizzi
    # possibili, cosi' l'applicazione puo' farne scegliere un altro invece di
    # lasciare l'artigiano davanti a una casa che non e' quella del cliente
    altri = [] if p["preciso"] else _suggerimenti_calmi(indirizzo, cap)
    return {
        "indirizzo": p["indirizzo"],
        "suggerimenti": altri,
        "comune": part["comune"],
        "comune_nome": p["comune_nome"],
        "provincia": p.get("provincia", ""),
        "comune_verificato": bool(p.get("comune_verificato")),
        "foglio": part["foglio"],
        "particella": part["particella"],
        "riferimento": part["codice"],
        "particelle": [_particella_breve(part)],
        "lat": round(lat, 7), "lon": round(lon, 7),
        "punti": [[round(lat, 7), round(lon, 7)]],
        "lotto_mq": m["lotto_mq"],
        "scoperto_mq": m["scoperto_mq"],
        "coperto_mq": m["coperto_mq"],
        "verde_mq": m["verde_mq"],
        "ingombro": m["ingombro"],
        "metri_per_pixel": m["metri_per_pixel"],
        "angoli": m["angoli"],
        "lato_m": m["lato_m"],
        "fonte": "Cartografia catastale dell'Agenzia delle Entrate · foto dall'alto "
                 + m.get("fonte_foto", "Esri World Imagery"),
        "fonte_foto": m.get("fonte_foto", "Esri World Imagery"),
        "fonti_foto": fonti_del_punto(lat, lon),
        # la foto senza i confini disegnati sopra, e il confine come punti
        # spostabili: i confini del catasto non combaciano col satellite, e cosi'
        # si ricalcano e si tirano al posto giusto
        "foto_pulita": in_base64(m["foto_pulita"]) if m.get("foto_pulita") else "",
        "contorno": m.get("contorno") or [],
        "contorni_verde": m.get("contorni_verde") or [],
        "preparato": time.strftime("%Y-%m-%d %H:%M"),
        "avvisi": avvisi,
        "preciso": p["preciso"],
        # Quando il civico e' caduto sull'asfalto e la particella e' stata presa dalla
        # casa accanto, il contorno del catasto NON e' il lotto del cliente: in una
        # palazzina di ringhiera e' la particella comune di tutti. Disegnarlo sulla foto
        # mette davanti al giardiniere una forma grossa che non deve fidarsi (Andrea,
        # 24 settembre 2026, Via Torino 5: "tiene quel pezzo di catasto in vista").
        # Allora si consegna la foto pulita: il contorno resta nel campo `contorno` e si
        # riaccende dal foglio da disegno, quando lo vuole lui.
        "catasto_incerto": bool(avviso_strada),
        "foto": in_base64(firma_fonte(m["foto_pulita"], m.get("fonte_foto", ""))
                          if (avviso_strada and m.get("foto_pulita")) else m["immagine"]),
    }


def _intorno_stessa_particella(part, lat, lon, raggio_m=10.0):
    """Il punto dell'indirizzo cade spesso sul tetto, e li' il catasto risponde con
    l'ingombro del solo fabbricato: il giardino della stessa particella restava
    fuori dal conto (San Zenone, 13 settembre 2026: 229 mq invece di 1.092).
    Si guarda dieci metri intorno, in otto direzioni: i punti che cadono nella
    stessa particella allargano il recinto e diventano semi anche loro."""
    from concurrent.futures import ThreadPoolExecutor
    prove = []
    for grado in range(0, 360, 45):
        a = math.radians(grado)
        prove.append((lat + raggio_m * math.cos(a) / 111320.0,
                      lon + raggio_m * math.sin(a) / (111320.0 * math.cos(math.radians(lat)))))

    def guarda(pt):
        try:
            return pt, particella_nel_punto(*pt, atteso=part["codice"])
        except Exception:                                  # noqa: BLE001
            return pt, None

    # due domande per volta, non di piu': il catasto respinge chi chiede troppo
    semi, riquadri = [(lat, lon)], [part["riquadro"]]
    with ThreadPoolExecutor(max_workers=2) as ex:
        for pt, p in ex.map(guarda, prove):
            if p and p["codice"] == part["codice"] and p["riquadro"]:
                semi.append(pt)
                riquadri.append(p["riquadro"])
    return semi, riquadri


def _particella_breve(part):
    return {"codice": part["codice"], "comune": part["comune"],
            "foglio": part["foglio"], "particella": part["particella"]}


def _avvisi_misura(m):
    avvisi = []
    if m.get("tocca_il_bordo"):
        avvisi.append("Il lotto arriva al bordo della foto: fuori potrebbe continuare, "
                      "e allora questi metri quadri sono solo il pezzo che si vede. "
                      "Usa «Allarga la foto» per rifarlo più largo.")
    if m["senza_scoperto"]:
        avvisi.append("Nella particella trovata c'è solo il fabbricato: il giardino è "
                      "su un'altra particella. Toccala sulla foto per aggiungerla al lotto.")
    if not m.get("dal_confine"):
        avvisi.append("Il catasto non ha mandato il confine vero: questa misura è "
                      "stimata dalla mappa. Controllala oppure segna il giardino "
                      "a mano sulla foto.")
    if m["sospetto"]:
        avvisi.append("Il lotto misurato è molto più piccolo del suo ingombro: "
                      "forse un muro lo taglia in due e ne è stato contato solo "
                      "un pezzo. Controlla i metri quadri.")
    return avvisi


MAX_PUNTI = 8


def rilievo_da_punti(punti, indirizzo="", lato_m=None, foto_da=""):
    """Il lotto fatto di piu' particelle. Il primo punto e' quello del rilievo
    dall'indirizzo; gli altri li tocca il giardiniere sulla foto, sulle particelle
    che sono del cliente: il giardino accanto alla casa, il pezzo di prato dietro.
    Ogni punto diventa la sua particella, e si misurano tutte insieme."""
    trovate, semi, riquadri, avvisi, toccati = [], [], [], [], []
    for la, lo in punti[:MAX_PUNTI]:
        part, presa_lat, presa_lon = cerca_la_particella(la, lo, passo_m=3.0, giri=3)
        if not part:
            avvisi.append("In quel punto il catasto non trova terreno da misurare. "
                          "Segna il giardino a mano sulla foto.")
            continue
        if abs(presa_lat - la) > 1e-9 or abs(presa_lon - lo) > 1e-9:
            avvisi.append("Il punto era sul bordo: ho preso la particella più vicina.")
        toccati.append((la, lo))
        if not semi and part["riquadro"]:
            # il primo punto e' quello dell'indirizzo e cade spesso sul tetto: come nel
            # rilievo, si guarda intorno per riprendere il giardino della stessa
            # particella (San Zenone, 14 settembre 2026: 229 mq invece di 1.092)
            s, r = _intorno_stessa_particella(part, la, lo)
            semi += s
            riquadri += r
        else:
            semi.append((la, lo))
            riquadri.append(part["riquadro"])
        if part["codice"] not in [t["codice"] for t in trovate]:
            trovate.append(part)
    if not trovate:
        raise RuntimeError("Nessuno dei punti toccati cade su una particella del catasto.")
    confini = [t.get("confine") for t in trovate]
    m = misura(semi, riquadri, lato_m=lato_m, confini=confini, foto_da=foto_da)
    if m["lotto_mq"] < 20:
        raise RuntimeError("Qui non sono riuscito a misurare un lotto vero. "
                           "Segna il giardino a mano sulla foto.")
    avvisi += _avvisi_misura(m)
    fogli = list(dict.fromkeys(t["foglio"] for t in trovate))
    return {
        "indirizzo": indirizzo,
        "comune": trovate[0]["comune"],
        "foglio": ", ".join(fogli),
        "particella": ", ".join(t["particella"] for t in trovate),
        "riferimento": ", ".join(t["codice"] for t in trovate),
        "particelle": [_particella_breve(t) for t in trovate],
        "lat": round(toccati[0][0], 7), "lon": round(toccati[0][1], 7),
        "punti": [[round(la, 7), round(lo, 7)] for la, lo in toccati],
        "lotto_mq": m["lotto_mq"],
        "scoperto_mq": m["scoperto_mq"],
        "coperto_mq": m["coperto_mq"],
        "verde_mq": m["verde_mq"],
        "ingombro": m["ingombro"],
        "metri_per_pixel": m["metri_per_pixel"],
        "angoli": m["angoli"],
        "lato_m": m["lato_m"],
        "fonte": "Cartografia catastale dell'Agenzia delle Entrate · foto dall'alto "
                 + m.get("fonte_foto", "Esri World Imagery"),
        "fonte_foto": m.get("fonte_foto", "Esri World Imagery"),
        "fonti_foto": fonti_del_punto(toccati[0][0], toccati[0][1]),
        # la foto senza i confini disegnati sopra, e il confine come punti
        # spostabili: i confini del catasto non combaciano col satellite, e cosi'
        # si ricalcano e si tirano al posto giusto
        "foto_pulita": in_base64(m["foto_pulita"]) if m.get("foto_pulita") else "",
        "contorno": m.get("contorno") or [],
        "contorni_verde": m.get("contorni_verde") or [],
        "preparato": time.strftime("%Y-%m-%d %H:%M"),
        "avvisi": avvisi,
        "foto": in_base64(m["immagine"]),
    }


def leggi_punti(testo):
    """"45.3273,9.3542;45.3271,9.3544" -> [(45.3273, 9.3542), ...]. Solo punti in
    Italia, al massimo MAX_PUNTI: quello che non torna si scarta."""
    punti = []
    for pezzo in (testo or "").split(";"):
        try:
            la, lo = (float(v) for v in pezzo.split(","))
        except ValueError:
            continue
        if 35.0 <= la <= 48.0 and 6.0 <= lo <= 19.0:
            punti.append((la, lo))
    return punti[:MAX_PUNTI]


def nome_file(indirizzo):
    s = unicodedata.normalize("NFKD", indirizzo).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return (s[:60] or "rilievo")


# ------------------------------------------------- chi puo' entrare
#
# Andrea, 24 settembre 2026: «se trovassi dei nuovi giardinieri e volessi fargli
# provare l'app, poi gli rimane sul telefono? anche quando sara' a pagamento?».
# Oggi si': l'app si installa e nessuno sa nemmeno chi ce l'ha. Quindi serve un
# modo per sapere chi e' entrato e per chiudere la porta quando smette di pagare.
#
# NON e' un sistema di account. E' un CODICE D'INGRESSO per ogni giardiniere, che
# lui incolla una volta e che poi viaggia con ogni richiesta. Niente mail, niente
# password, nessun dato personale sul server: solo il codice. I nomi li tiene
# Andrea sul suo foglio.
#
# Il blocco vero sta QUI, non nell'app: anche se uno smonta l'app, senza un codice
# buono il server non gli manda ne' la foto ne' il catasto, e gli resta
# un'applicazione che non misura niente.
#
# L'elenco sta in `codici.json` accanto a questo file, e si cambia caricandolo:
# su Render il disco non e' permanente, quindi un file scritto dal server a un
# riavvio sparisce, mentre quello che arriva col caricamento resta sempre.
#
# INTERRUTTORE: finche' `attivo` e' false non cambia niente per nessuno, e l'app
# non chiede nessun codice. Si accende il giorno che si passa a pagamento.

# di solito e' il file qui accanto; si puo' puntare altrove con RILIEVO_CODICI,
# che serve a provare una ditta finta senza toccare l'elenco vero di Andrea
ELENCO_CODICI = pathlib.Path(os.environ.get("RILIEVO_CODICI") or (QUI / "codici.json"))
GIORNI_SENZA_RETE = 7          # quanto vale un via libera quando il telefono e' offline
_codici_letti = {"quando": 0, "roba": None}
# chi si e' fatto vivo, da quando e quante volte. Sta in memoria e riparte da zero
# a ogni riavvio del server: il dato che conta si ritrova nel registro stampato.
REGISTRO = {}


def codici():
    """L'elenco dei codici, riletto dal file quando cambia."""
    try:
        quando = ELENCO_CODICI.stat().st_mtime
    except OSError:
        return {"attivo": False, "codici": {}}
    if _codici_letti["roba"] is None or quando != _codici_letti["quando"]:
        try:
            _codici_letti["roba"] = json.loads(ELENCO_CODICI.read_text(encoding="utf-8"))
        except Exception:                       # noqa: BLE001
            _codici_letti["roba"] = {"attivo": False, "codici": {}}
        _codici_letti["quando"] = quando
    return _codici_letti["roba"]


def _pulisci_codice(c):
    return "".join(ch for ch in (c or "").upper() if ch.isalnum() or ch == "-")[:24]


def chi_entra(codice):
    """Questo codice puo' lavorare? Torna (si_o_no, cosa dirgli, per quanti giorni).

    Quando il controllo e' spento passano tutti, e l'app non chiede niente.
    """
    elenco = codici()
    if not elenco.get("attivo"):
        return True, "", 0
    c = _pulisci_codice(codice)
    riga = (elenco.get("codici") or {}).get(c)
    if not riga:
        return False, elenco.get("sconosciuto") or (
            "Questo codice non risulta. Controlla di averlo scritto giusto, "
            "oppure chiedine uno a chi ti ha dato l'applicazione."), 0
    if riga.get("bloccato"):
        return False, riga.get("messaggio") or (
            "Questo codice non e' piu' attivo. Scrivi a chi ti ha dato "
            "l'applicazione per riattivarlo."), 0
    oggi = time.strftime("%Y-%m-%d")
    r = REGISTRO.setdefault(c, {"dal": oggi, "quanti": 0})
    r["ultimo"] = oggi
    r["quanti"] = r.get("quanti", 0) + 1
    if r["quanti"] == 1:
        # la riga che resta nel registro del server anche dopo un riavvio
        print("  PRIMO INGRESSO del codice %s (%s)" % (c, riga.get("nome") or "senza nome"))
    return True, riga.get("avviso") or "", int(elenco.get("giorni") or GIORNI_SENZA_RETE)


# ------------------------------------------------- la ditta, e chi ci lavora dentro
#
# Andrea, 24 settembre 2026: l'elenco dei lavori visto da piu' persone «ci puo'
# servire per ampliare il target dell'app». Da qui in avanti un codice d'ingresso
# puo' dire due cose in piu':
#
#   "ditta": la ditta a cui appartiene. Piu' codici con la stessa ditta guardano
#            lo STESSO archivio. Un codice senza ditta e' un artigiano da solo, e
#            per lui non cambia assolutamente niente: i suoi lavori restano nella
#            memoria del suo telefono, come oggi.
#   "ruolo": cosa puo' fare dentro quell'archivio.
#            capo    -> mette dentro i lavori, li cambia, li cancella, e decide
#                       come si chiamano gli stati.
#            squadra -> sposta lo stato e scrive le note dei lavori che ci sono
#                       gia'. Non ne crea e non ne cancella. E' il valore di
#                       partenza per chi non ha scritto niente.
#            guarda  -> legge soltanto.
#
# ATTENZIONE, e' la scelta che tiene in piedi tutto il resto: l'archivio della
# ditta NON dipende dall'interruttore `attivo`. Quello serve a far pagare tutti,
# questo a condividere fra pochi. Se fossero lo stesso interruttore, per far
# provare l'archivio a una ditta si dovrebbe chiudere la porta a tutti gli altri.

RUOLI = {
    "capo":    {"legge": True, "sposta": True,  "mette": True,  "comanda": True},
    "squadra": {"legge": True, "sposta": True,  "mette": False, "comanda": False},
    "guarda":  {"legge": True, "sposta": False, "mette": False, "comanda": False},
}
RUOLO_DI_PARTENZA = "squadra"


def chi_e(codice):
    """Chi sta chiedendo, e per conto di quale ditta. None se non e' di nessuna.

    Torna None anche quando il codice esiste ma non nomina nessuna ditta: quello
    e' un artigiano da solo, e l'archivio condiviso non lo riguarda.
    """
    elenco = codici()
    c = _pulisci_codice(codice)
    if not c:
        return None
    riga = (elenco.get("codici") or {}).get(c)
    if not riga or riga.get("bloccato"):
        return None
    ditta = _pulisci_codice(riga.get("ditta") or "")
    if not ditta:
        return None
    dati_ditta = (elenco.get("ditte") or {}).get(ditta) or {}
    ruolo = str(riga.get("ruolo") or RUOLO_DI_PARTENZA).strip().lower()
    if ruolo not in RUOLI:
        ruolo = RUOLO_DI_PARTENZA
    return {"codice": c, "nome": riga.get("nome") or c, "ditta": ditta,
            "ditta_nome": dati_ditta.get("nome") or ditta, "ruolo": ruolo,
            "posso": dict(RUOLI[ruolo])}


def puo(chi, azione):
    """Questa persona puo' fare questa cosa? `chi` e' quello che torna da chi_e()."""
    if not chi:
        return False
    return bool(RUOLI.get(str(chi.get("ruolo") or ""), {}).get(azione))


def la_squadra_della_ditta(ditta):
    """Chi ha un codice per questa ditta, con che nome e che ruolo.

    Serve all'app per scrivere «spostato da Samir» invece di «spostato da CONTI-1».
    """
    elenco = codici()
    d = _pulisci_codice(ditta or "")
    fuori = []
    for c, riga in (elenco.get("codici") or {}).items():
        if _pulisci_codice(riga.get("ditta") or "") != d:
            continue
        ruolo = str(riga.get("ruolo") or RUOLO_DI_PARTENZA).strip().lower()
        fuori.append({"codice": _pulisci_codice(c), "nome": riga.get("nome") or c,
                      "ruolo": ruolo if ruolo in RUOLI else RUOLO_DI_PARTENZA,
                      "bloccato": bool(riga.get("bloccato"))})
    fuori.sort(key=lambda x: (x["ruolo"] != "capo", x["nome"].lower()))
    return fuori


def come_va_con_i_codici():
    """Chi si e' fatto vivo da quando il server e' acceso. Serve ad Andrea."""
    elenco = codici()
    fuori = []
    for c, riga in (elenco.get("codici") or {}).items():
        visto = REGISTRO.get(c, {})
        fuori.append({"codice": c, "nome": riga.get("nome") or "",
                      "bloccato": bool(riga.get("bloccato")),
                      "dal": visto.get("dal", ""), "ultimo": visto.get("ultimo", ""),
                      "quante_volte": visto.get("quanti", 0)})
    fuori.sort(key=lambda x: (not x["ultimo"], x["ultimo"]), reverse=True)
    return {"attivo": bool(elenco.get("attivo")),
            "giorni_senza_rete": int(elenco.get("giorni") or GIORNI_SENZA_RETE),
            "quanti": len(fuori), "giardinieri": fuori}


# ------------------------------------------------- il servizio, mentre lavori

# quale versione del codice sta girando: su Render la mette la piattaforma, in
# locale non c'e' e vale "locale". Serve a sapere in un colpo solo se il server
# ha davvero preso l'ultimo caricamento.
VERSIONE = (os.environ.get("RENDER_GIT_COMMIT") or "")[:10] or "locale"

# l'applicazione servita dal server stesso (cartella app/ accanto a questo file)
APP = QUI / "app"
STATICI = {
    "/":                     ("index.html", "text/html; charset=utf-8"),
    "/index.html":           ("index.html", "text/html; charset=utf-8"),
    "/sw.js":                ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icona-192.png":        ("icona-192.png", "image/png"),
    "/icona-512.png":        ("icona-512.png", "image/png"),
    "/icona-180.png":        ("icona-180.png", "image/png"),
}


def servizio(porta=8787, pubblico=False):
    import http.server, socketserver

    class Sportello(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _apri(self, codice=200, tipo="application/json; charset=utf-8"):
            self.send_response(codice)
            self.send_header("Content-Type", tipo)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def do_OPTIONS(self):
            self._apri(204, "text/plain")

        def _manda(self, codice, dati):
            self._apri(codice)
            self.wfile.write(json.dumps(dati, ensure_ascii=False).encode())

        def _corpo(self):
            """Quello che arriva nel corpo di una POST, gia' aperto. Al massimo 4 MB:
            un elenco di lavori di una ditta non arriva neanche vicino."""
            try:
                quanto = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if quanto <= 0 or quanto > 4 * 1024 * 1024:
                return None
            try:
                return json.loads(self.rfile.read(quanto).decode("utf-8"))
            except Exception:                   # noqa: BLE001
                return None

        def do_POST(self):
            """Si scrive solo nell'archivio della squadra. Tutto il resto del
            servizio e' in sola lettura e resta com'e'."""
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            codice = (q.get("codice") or [""])[0]
            if not u.path.startswith("/squadra/"):
                self._manda(404, {"errore": "Non c'e' nulla qui."})
                return
            chi = chi_e(codice)
            if not chi:
                self._manda(403, {"errore": "Questo codice non e' di nessuna ditta.",
                                  "senza_ditta": True})
                return
            dentro = self._corpo()
            if not isinstance(dentro, dict):
                self._manda(400, {"errore": "Non ho capito cosa mi stai mandando."})
                return

            if u.path == "/squadra/salva":
                if not puo(chi, "sposta"):
                    self._manda(403, {"errore": "Il tuo codice puo' solo guardare.",
                                      "solo_guarda": True})
                    return
                try:
                    r = archivio.salva(chi["ditta"], chi, dentro.get("lavori") or [])
                except Exception as e:          # noqa: BLE001
                    print("     archivio non salvato:", e)
                    self._manda(500, {"errore": "Non sono riuscito a salvare. Riprova."})
                    return
                print("  squadra %s: %d salvati, %d respinti (da %s)"
                      % (chi["ditta"], len(r["salvati"]), len(r["respinti"]), chi["codice"]))
                r["io"] = chi
                self._manda(200, r)
                return

            if u.path == "/squadra/stati":
                if not puo(chi, "comanda"):
                    self._manda(403, {"errore": "I nomi degli stati li decide il capo."})
                    return
                nuovi, motivo = archivio.scrivi_stati(chi["ditta"], dentro.get("stati"), chi)
                if not nuovi:
                    self._manda(400, {"errore": motivo})
                    return
                self._manda(200, {"stati": nuovi, "io": chi})
                return

            self._manda(404, {"errore": "Non c'e' nulla qui."})

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            # l'applicazione stessa, se c'e' la cartella app/: cosi' ha un indirizzo
            # fisso e si installa sul telefono, e si apre anche senza rete
            if u.path in STATICI and (APP / STATICI[u.path][0]).is_file():
                nome, tipo = STATICI[u.path]
                dati = (APP / nome).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(dati)))
                self.end_headers()
                self.wfile.write(dati)
                return
            def cap_chiesto():
                return (q.get("cap") or [""])[0].strip()[:5]

            def lato_chiesto():
                try:
                    return float((q.get("lato") or [""])[0])
                except ValueError:
                    return None

            def codice_chiesto():
                return (q.get("codice") or [""])[0]

            def foto_chiesta():
                # «Cambia la foto»: il giardiniere forza una fonte invece di
                # lasciar scegliere all'app. Vuoto: sceglie l'app.
                return (q.get("fonte") or q.get("foto") or [""])[0].strip().lower()[:30]

            if u.path in ("/", "/ci-sei"):
                self._manda(200, {"servizio": "rilievo", "pronto": True,
                                  "versione": VERSIONE,
                                  "codice_richiesto": bool(codici().get("attivo"))})
                return
            if u.path == "/entra":
                # l'app lo chiede all'apertura: questo codice vale ancora? e di
                # che ditta e'? Se non e' di nessuna, i campi della ditta tornano
                # vuoti e l'app resta esattamente quella di prima.
                ok, messaggio, giorni = chi_entra(codice_chiesto())
                elenco = codici()
                riga = (elenco.get("codici") or {}).get(_pulisci_codice(codice_chiesto())) or {}
                chi = chi_e(codice_chiesto())
                risposta = {"attivo": bool(elenco.get("attivo")), "valido": ok,
                            "nome": riga.get("nome") or "", "giorni": giorni,
                            "messaggio": messaggio,
                            "ditta": "", "ditta_nome": "", "ruolo": "", "posso": {},
                            "squadra": []}
                if chi:
                    risposta.update({"ditta": chi["ditta"], "ditta_nome": chi["ditta_nome"],
                                     "nome": chi["nome"], "ruolo": chi["ruolo"],
                                     "posso": chi["posso"],
                                     "squadra": la_squadra_della_ditta(chi["ditta"])})
                self._manda(200, risposta)
                return
            if u.path == "/chi-entra":
                # per Andrea: chi si e' fatto vivo, da quando, quante volte
                self._manda(200, come_va_con_i_codici())
                return

            # ---- l'archivio condiviso della ditta -------------------------------
            # Chi non ha un codice che nomina una ditta non arriva mai qui dentro:
            # per lui Rilievo resta l'app del suo telefono, identica a prima.
            if u.path.startswith("/squadra/"):
                chi = chi_e(codice_chiesto())
                if not chi:
                    self._manda(403, {"errore": "Questo codice non e' di nessuna ditta.",
                                      "senza_ditta": True})
                    return
                if u.path == "/squadra/lavori":
                    dalla = (q.get("dalla") or ["0"])[0]
                    r = archivio.lavori(chi["ditta"], dalla)
                    r["io"] = chi
                    self._manda(200, r)
                    return
                if u.path == "/squadra/come-va":
                    r = archivio.come_va(chi["ditta"])
                    r["io"] = chi
                    r["squadra"] = la_squadra_della_ditta(chi["ditta"])
                    self._manda(200, r)
                    return
                self._manda(404, {"errore": "Non c'e' nulla qui."})
                return

            # da qui in giu' si spende: foto dall'alto, catasto, particelle. Se il
            # controllo e' acceso, senza un codice buono non si passa.
            if u.path in ("/rilievo", "/foto", "/particelle", "/case-vicine", "/indirizzi"):
                ok, messaggio, _ = chi_entra(codice_chiesto())
                if not ok:
                    self._manda(402, {"errore": messaggio, "codice_non_valido": True})
                    return
            if u.path == "/catasto-risponde":
                # a che punto sta l'Agenzia delle Entrate, detto in chiaro: serve a
                # capire in dieci secondi se un rilievo mancato e' colpa loro
                self._manda(200, catasto_risponde())
                return
            if u.path == "/indirizzi":
                # gli indirizzi possibili per quello che e' stato scritto: la via
                # d'uscita quando l'indirizzo non si trova o cade nel posto sbagliato
                cerca = (q.get("cerca") or q.get("indirizzo") or [""])[0].strip()[:200]
                if not cerca:
                    self._manda(400, {"errore": "Manca l'indirizzo da cercare."})
                    return
                print("  indirizzi come:", cerca)
                try:
                    trovati = suggerimenti_indirizzo(cerca, cap_chiesto())
                    self._manda(200, {"cercato": cerca, "suggerimenti": trovati})
                except Exception as e:              # noqa: BLE001
                    print("     non riuscita:", e)
                    self._manda(502, {"errore": str(e), "suggerimenti": []})
                return
            if u.path == "/foto":
                # solo la foto dall'alto, da disegnare a mano: non passa dal catasto.
                # Due modi di dire dove: l'indirizzo scritto, oppure un punto gia'
                # noto. Il punto serve a spostare l'inquadratura mentre si disegna:
                # il giardino vero sta spesso fuori dal quadrato deciso al rilievo, e
                # ingrandire sullo schermo non ce lo porta dentro (Andrea, 21/9/2026).
                indirizzo = (q.get("indirizzo") or [""])[0].strip()
                punti = leggi_punti((q.get("punto") or q.get("punti") or [""])[0])
                if not indirizzo and not punti:
                    self._manda(400, {"errore": "Manca l'indirizzo o il punto."})
                    return
                print("  foto di:", punti[0] if punti else indirizzo)
                try:
                    if punti:
                        p = {"lat": punti[0][0], "lon": punti[0][1], "indirizzo": indirizzo,
                             "comune_nome": "", "preciso": True}
                        motivo = ("Foto dall'alto intorno al punto che hai scelto: "
                                  "segna il giardino e i metri quadri li conto io.")
                    else:
                        p = punto_dall_indirizzo(indirizzo, cap_chiesto())
                        motivo = ("Foto dall'alto di questo indirizzo: segna il giardino "
                                  "sulla foto e i metri quadri li conto io.")
                    r = rilievo_da_disegnare(p, lato_chiesto(), motivo, indirizzo,
                                             cap_chiesto(), foto_chiesta())
                    self._manda(200, r)
                except Exception as e:              # noqa: BLE001
                    print("     non riuscita:", e)
                    self._manda(502, {"errore": str(e),
                                      "suggerimenti": _suggerimenti_calmi(indirizzo, cap_chiesto())})
                return
            if u.path == "/case-vicine":
                # Le case intorno a un punto, per farne toccare una quando il civico
                # non si trova. Chi risponde e' la mappa dei fabbricati del catasto,
                # la stessa che serve a misurare il coperto.
                try:
                    la = float((q.get("lat") or [""])[0])
                    lo = float((q.get("lon") or [""])[0])
                except ValueError:
                    self._manda(400, {"errore": "Servono lat e lon."})
                    return
                try:
                    vicine = case_vicine(la, lo)
                    print("  case vicine:", len(vicine))
                    self._manda(200, {"case": vicine})
                except CatastoOccupato as e:
                    self._manda(503, {"errore": str(e)})
                except Exception as e:              # noqa: BLE001
                    self._manda(502, {"errore": str(e)})
                return
            if u.path == "/particelle":
                punti = leggi_punti((q.get("punti") or [""])[0])
                if not punti:
                    self._manda(400, {"errore": "Mancano i punti toccati sulla foto."})
                    return
                indirizzo = (q.get("indirizzo") or [""])[0].strip()[:200]
                print("  particelle:", len(punti), "punti")
                try:
                    r = rilievo_da_punti(punti, indirizzo, lato_chiesto(), foto_chiesta())
                    print("     %s mq di lotto, %s particelle" % (r["lotto_mq"], len(r["particelle"])))
                    self._manda(200, r)
                except Exception as e:              # noqa: BLE001
                    print("     non riuscito:", e)
                    self._manda(502, {"errore": str(e)})
                return
            if u.path != "/rilievo":
                self._apri(404)
                self.wfile.write(b'{"errore":"Non c\'e nulla qui."}')
                return
            indirizzo = (q.get("indirizzo") or [""])[0].strip()
            if not indirizzo:
                self._apri(400)
                self.wfile.write(json.dumps(
                    {"errore": "Manca l'indirizzo."}, ensure_ascii=False).encode())
                return
            print("  rilievo di:", indirizzo)
            try:
                r = rilievo(indirizzo, lato_chiesto(), cap_chiesto(), foto_chiesta())
                if r.get("da_disegnare"):
                    print("     senza catasto: foto da disegnare")
                else:
                    print("     %s mq di lotto, %s scoperti" % (r["lotto_mq"], r["scoperto_mq"]))
                self._apri()
                self.wfile.write(json.dumps(r, ensure_ascii=False).encode())
            except Exception as e:                  # noqa: BLE001
                # un indirizzo che non si trova non finisce in un vicolo cieco: si
                # mandano gli indirizzi possibili, e lui tocca quello giusto
                print("     non riuscito:", e)
                self._apri(502)
                self.wfile.write(json.dumps(
                    {"errore": str(e),
                     "suggerimenti": _suggerimenti_calmi(indirizzo, cap_chiesto())},
                    ensure_ascii=False).encode())

    # Sul portatile si ascolta solo su 127.0.0.1: nessuno da fuori puo' entrare.
    # Su un server serve --pubblico, e allora si ascolta su tutte le schede di
    # rete e si prende la porta che assegna chi ospita (variabile PORT).
    # E' un interruttore esplicito apposta: leggere PORT da soli e' pericoloso,
    # perche' PORT capita di trovarla gia' impostata per tutt altro motivo.
    if pubblico:
        indirizzo_ascolto = "0.0.0.0"
        porta = int(os.environ.get("PORT") or porta)
    else:
        indirizzo_ascolto = "127.0.0.1"

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((indirizzo_ascolto, porta), Sportello) as s:
        print("Il rilievo è acceso su http://%s:%d" % (indirizzo_ascolto, porta))
        print("Lascia questa finestra aperta e usa Rilievo. Ctrl-C per fermare.")
        try:
            s.serve_forever()
        except KeyboardInterrupt:
            print("\nFermato.")


# ------------------------------------------------- riga di comando

def main():
    a = argparse.ArgumentParser(description="Il rilievo dall'indirizzo.")
    a.add_argument("indirizzo", nargs="*", help="Via, civico, comune")
    a.add_argument("--servizio", action="store_true",
                   help="resta acceso e risponde all'applicazione")
    a.add_argument("--porta", type=int, default=8787)
    a.add_argument("--pubblico", action="store_true",
                   help="per un server: ascolta da fuori, sulla porta che assegna chi ospita")
    v = a.parse_args()

    if v.servizio:
        servizio(v.porta, v.pubblico)
        return

    if not v.indirizzo:
        a.print_help()
        return

    indirizzo = " ".join(v.indirizzo)
    r = rilievo(indirizzo)
    CARTELLA.mkdir(exist_ok=True)
    base = CARTELLA / nome_file(indirizzo)
    base.with_suffix(".json").write_text(
        json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    foto = base64.b64decode(r["foto"].split(",", 1)[1])
    base.with_suffix(".jpg").write_bytes(foto)

    print()
    print("  %s" % r["indirizzo"])
    print("  foglio %s, particella %s, comune %s" % (r["foglio"], r["particella"], r["comune"]))
    print("  lotto      %5d mq" % r["lotto_mq"])
    print("  scoperto   %5d mq   (terreno non costruito: anche cortili e vialetti)" % r["scoperto_mq"])
    print("  verde      %5d mq   <- stima dalla foto, da confermare sul posto" % r["verde_mq"])
    print("  coperto    %5d mq" % r["coperto_mq"])
    print("  ingombro   %s" % r["ingombro"])
    for x in r["avvisi"]:
        print("  attenzione: %s" % x)
    print()
    print("  salvato in %s" % base.with_suffix(".json"))
    print("  Caricalo dentro Rilievo dal tasto \"Carica un rilievo\".")


if __name__ == "__main__":
    main()
