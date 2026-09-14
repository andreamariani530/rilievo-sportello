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
from PIL import Image, ImageDraw, ImageFilter

QUI          = pathlib.Path(__file__).parent
CARTELLA     = QUI / "rilievi"
NOMINATIM    = "https://nominatim.openstreetmap.org/search"
INDIRIZZI_ESRI = ("https://geocode.arcgis.com/arcgis/rest/services/World/"
                  "GeocodeServer/findAddressCandidates")
CATASTO      = "https://wms.cartografia.agenziaentrate.gov.it/inspire/wms/ows01.php"
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

# i tipi di risultato di OpenStreetMap che sono una casa. Non "place": e' il nome di
# una localita' o di una cascina, un punto che puo' stare lontano dalla casa.
CASE_OSM = ("house", "building", "yes")


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


def _punto_da_esri(indirizzo, civico):
    """Il servizio indirizzi di Esri conosce i numeri civici italiani; OpenStreetMap
    spesso no, e allora mette il punto a meta' della via e si misura la casa sbagliata
    (successo il 10 settembre 2026: Via 4 Novembre a Quartiano, il 105/B e il 3 davano
    lo stesso punto).

    Torna due cose: il punto esatto, se Esri ha trovato proprio il civico scritto, e
    il punto vicino, se ha trovato una casa vera della stessa via ma con un altro
    numero. Il vicino non e' mai "preciso": e' la casa accanto."""
    try:
        d = json.loads(prendi(INDIRIZZI_ESRI, {
            "f": "json", "SingleLine": indirizzo, "countryCode": "ITA", "maxLocations": 3,
            "outFields": "Addr_type,Match_addr,City,AddNum",
        }, tentativi=1, tempo=12).decode("utf-8"))
    except Exception:                                   # noqa: BLE001
        return None, None
    vicino = None
    for c in d.get("candidates", []):
        a = c.get("attributes", {})
        if a.get("Addr_type") not in ("PointAddress", "Subaddress") or c.get("score", 0) < 90:
            continue
        punto = {
            "lat": float(c["location"]["y"]), "lon": float(c["location"]["x"]),
            "indirizzo": a.get("Match_addr") or c.get("address") or indirizzo,
            "comune_nome": a.get("City") or "",
            "civico_trovato": str(a.get("AddNum") or ""),
        }
        if _stesso_civico(civico, a.get("AddNum")):
            return dict(punto, preciso=True), None
        vicino = vicino or dict(punto, preciso=False)
    return None, vicino


def _punto_da_osm(t, preciso):
    a = t.get("address", {})
    comune = (a.get("village") or a.get("town") or a.get("city")
              or a.get("municipality") or a.get("hamlet") or "")
    return {
        "lat": float(t["lat"]), "lon": float(t["lon"]),
        "indirizzo": t.get("display_name", ""),
        "comune_nome": comune,
        "civico_trovato": str(a.get("house_number") or ""),
        "preciso": preciso,
    }


def punto_dall_indirizzo(indirizzo):
    """Indirizzo scritto a mano -> latitudine, longitudine, indirizzo per esteso.

    "preciso" vuol dire una cosa sola: e' stato trovato proprio il civico scritto.
    Una casa qualunque della via, una localita', un civico diverso: non e' preciso,
    e l'applicazione avvisa di controllare la particella."""
    civico = civico_scritto(indirizzo)
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
        if (t.get("addresstype") in CASE_OSM
                and _stesso_civico(civico, t.get("address", {}).get("house_number"))):
            return _punto_da_osm(t, True)
    # niente civico giusto: meglio una casa vera della via (Esri) che il centro della via
    if vicino:
        return vicino
    # meglio una casa che una via intera: la via è una riga, la casa è un punto
    trovati.sort(key=lambda t: 0 if t.get("addresstype") in CASE_OSM else 1)
    return _punto_da_osm(trovati[0], False)


# ------------------------------------------------- dal punto alla particella

def _riquadro(lat, lon, meta_lato_m):
    """Riquadro quadrato sul terreno, in gradi, attorno a un punto."""
    dlat = meta_lato_m / 111320.0
    dlon = dlat / max(0.2, math.cos(math.radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


class CatastoOccupato(RuntimeError):
    """Il catasto risponde, ma rifiuta la domanda (ServiceException ERRX-2).
    Succede quando da uno stesso indirizzo di rete arrivano troppe domande:
    il 13 settembre 2026 la macchina delle prove e' stata respinta per ore,
    mentre il server su Render riceveva risposte normali."""


def _chiedi_al_catasto(lat, lon, strato, formato, lato_m=180.0, lati=700):
    y0, x0, y1, x1 = _riquadro(lat, lon, lato_m / 2)
    domanda = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo",
        "LAYERS": strato, "QUERY_LAYERS": strato, "CRS": "EPSG:6706",
        "BBOX": "%f,%f,%f,%f" % (y0, x0, y1, x1),
        "WIDTH": lati, "HEIGHT": lati, "I": lati // 2, "J": lati // 2,
        "INFO_FORMAT": formato, "FEATURE_COUNT": 1,
    }
    for attesa in (2.0, None):
        testo = prendi(CATASTO, domanda).decode("utf-8", "replace")
        if "ServiceException" not in testo:
            return testo
        if attesa:
            time.sleep(attesa)
    raise CatastoOccupato(
        "Il catasto dell'Agenzia delle Entrate in questo momento non accetta "
        "domande. Riprova tra qualche minuto.")


def particella_nel_punto(lat, lon, atteso=None):
    """Il punto -> foglio, particella, comune, ingombro. None se lì non c'è nulla.
    Con `atteso`, se il punto cade su un'altra particella si risponde col solo
    codice, senza chiedere anche l'ingombro: una domanda al catasto in meno."""
    html = _chiedi_al_catasto(lat, lon, "CP.CadastralParcel", "text/html")
    rif = re.search(r"NationalCadastralReference</th><td>([^<]+)<", html)
    if not rif:
        return None
    # forma: F801_001300.416  ->  comune F801, foglio 0013, particella 416
    codice = rif.group(1).strip()
    if atteso and codice != atteso:
        return {"codice": codice, "riquadro": None}
    m = re.match(r"^([A-Z0-9]{4})_(\d{4})(\w*)\.(.+)$", codice)
    if m:
        comune, foglio, coda, part = m.group(1), str(int(m.group(2))), m.group(3), m.group(4)
    else:
        comune, foglio, part = "", "", codice

    gml = _chiedi_al_catasto(lat, lon, "CP.CadastralParcel",
                             "application/vnd.ogc.gml")
    box = re.search(r"<gml:coordinates>([\d.,\s-]+)</gml:coordinates>", gml)
    riquadro = None
    if box:
        n = [float(v) for v in re.split(r"[,\s]+", box.group(1).strip())]
        riquadro = {"lon0": n[0], "lat0": n[1], "lon1": n[2], "lat1": n[3]}
    return {"codice": codice, "comune": comune, "foglio": foglio,
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

def _mappa_catastale(y0, x0, y1, x1, strati, lati, trasparente="TRUE"):
    dati = prendi(CATASTO, {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
        "LAYERS": strati, "STYLES": "", "CRS": "EPSG:6706",
        "BBOX": "%f,%f,%f,%f" % (y0, x0, y1, x1),
        "WIDTH": lati, "HEIGHT": lati, "FORMAT": "image/png",
        "TRANSPARENT": trasparente,
    })
    return Image.open(io.BytesIO(dati)).convert("RGB")


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


def misura(semi, riquadri, lati=1100):
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
    if buoni:
        la0 = min(q["lat0"] for q in buoni); la1 = max(q["lat1"] for q in buoni)
        lo0 = min(q["lon0"] for q in buoni); lo1 = max(q["lon1"] for q in buoni)
        clat, clon = (la0 + la1) / 2, (lo0 + lo1) / 2
        alto_m  = (la1 - la0) * 111320.0
        largo_m = (lo1 - lo0) * 111320.0 * math.cos(math.radians(clat))
        meta = max(alto_m, largo_m, 34.0) * 0.85
    else:
        clat = sum(s[0] for s in semi) / len(semi)
        clon = sum(s[1] for s in semi) / len(semi)
        meta = 55.0
    meta = min(meta, 320.0)
    y0, x0, y1, x1 = _riquadro(clat, clon, meta)

    mappa = _mappa_catastale(y0, x0, y1, x1, "CP.CadastralParcel", lati)
    fabb  = _mappa_catastale(y0, x0, y1, x1, "fabbricati", lati)
    foto  = _foto_dall_alto(y0, x0, y1, x1, lati)

    # quanto vale un pixel, in metri e in metri quadri
    m_per_px = (2 * meta) / lati
    mq_px    = m_per_px * m_per_px

    def a_pixel(la, lo):
        return (max(0, min(lati - 1, int(round((lo - x0) / (x1 - x0) * (lati - 1))))),
                max(0, min(lati - 1, int(round((y1 - la) / (y1 - y0) * (lati - 1))))))

    # sulla mappa dell'Agenzia i fabbricati sono arancioni pieni
    edifici = _maschera(fabb, lambda p: p[0] > 150 and p[1] < 190 and p[2] < 120
                                        and p[0] - p[2] > 60)
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

    disegno = componi(foto, mappa, dentro, lati)
    return {
        "lotto_mq": int(round(lotto_mq)),
        "coperto_mq": int(round(coperto_mq)),
        "scoperto_mq": int(round(scoperto_mq)),
        "verde_mq": min(int(round(verde_mq)), int(round(scoperto_mq))),
        "ingombro": "%d × %d m" % (round(largo), round(alto)),
        "sospetto": sospetto,
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


def componi(foto, mappa, dentro, lati):
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
    d.text((11, lati - 18), "Agenzia delle Entrate  ·  Esri World Imagery",
           fill=(247, 241, 229, 235))
    return fondo


def in_base64(immagine, lato=1000):
    im = immagine.copy()
    if im.width > lato:
        im = im.resize((lato, lato), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------- il rilievo, tutto insieme

def rilievo(indirizzo):
    p = punto_dall_indirizzo(indirizzo)
    part, lat, lon = cerca_la_particella(p["lat"], p["lon"])
    if not part:
        raise RuntimeError(
            "L'indirizzo si trova, ma lì il catasto non ha una particella "
            "(può capitare su una strada o in una zona di Trento e Bolzano, "
            "dove il catasto è delle Province autonome).")
    semi, riquadri = _intorno_stessa_particella(part, lat, lon)
    m = misura(semi, riquadri)
    avvisi = []
    if not p["preciso"]:
        civico = civico_scritto(indirizzo)
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
    avvisi += _avvisi_misura(m)
    return {
        "indirizzo": p["indirizzo"],
        "comune": part["comune"],
        "comune_nome": p["comune_nome"],
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
        "fonte": FONTE,
        "preparato": time.strftime("%Y-%m-%d %H:%M"),
        "avvisi": avvisi,
        "preciso": p["preciso"],
        "foto": in_base64(m["immagine"]),
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
    if m["senza_scoperto"]:
        avvisi.append("Nella particella trovata c'è solo il fabbricato: il giardino è "
                      "su un'altra particella. Toccala sulla foto per aggiungerla al lotto.")
    if m["sospetto"]:
        avvisi.append("Il lotto misurato è molto più piccolo del suo ingombro: "
                      "forse un muro lo taglia in due e ne è stato contato solo "
                      "un pezzo. Controlla i metri quadri.")
    return avvisi


MAX_PUNTI = 8


def rilievo_da_punti(punti, indirizzo=""):
    """Il lotto fatto di piu' particelle. Il primo punto e' quello del rilievo
    dall'indirizzo; gli altri li tocca il giardiniere sulla foto, sulle particelle
    che sono del cliente: il giardino accanto alla casa, il pezzo di prato dietro.
    Ogni punto diventa la sua particella, e si misurano tutte insieme."""
    trovate, semi, riquadri, avvisi, toccati = [], [], [], [], []
    for la, lo in punti[:MAX_PUNTI]:
        part = particella_nel_punto(la, lo)
        if not part:
            avvisi.append("Un punto toccato non cade su nessuna particella (forse una "
                          "strada): l'ho lasciato fuori.")
            continue
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
    m = misura(semi, riquadri)
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
        "fonte": FONTE,
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


# ------------------------------------------------- il servizio, mentre lavori

# l'applicazione servita dal server stesso (cartella app/ accanto a questo file)
APP = QUI / "app"
STATICI = {
    "/":                     ("index.html", "text/html; charset=utf-8"),
    "/index.html":           ("index.html", "text/html; charset=utf-8"),
    "/sw.js":                ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icona-192.png":        ("icona-192.png", "image/png"),
    "/icona-512.png":        ("icona-512.png", "image/png"),
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
            if u.path in ("/", "/ci-sei"):
                self._manda(200, {"servizio": "rilievo", "pronto": True})
                return
            if u.path == "/particelle":
                punti = leggi_punti((q.get("punti") or [""])[0])
                if not punti:
                    self._manda(400, {"errore": "Mancano i punti toccati sulla foto."})
                    return
                indirizzo = (q.get("indirizzo") or [""])[0].strip()[:200]
                print("  particelle:", len(punti), "punti")
                try:
                    r = rilievo_da_punti(punti, indirizzo)
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
                r = rilievo(indirizzo)
                print("     %s mq di lotto, %s scoperti" % (r["lotto_mq"], r["scoperto_mq"]))
                self._apri()
                self.wfile.write(json.dumps(r, ensure_ascii=False).encode())
            except Exception as e:                  # noqa: BLE001
                print("     non riuscito:", e)
                self._apri(502)
                self.wfile.write(json.dumps({"errore": str(e)}, ensure_ascii=False).encode())

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
