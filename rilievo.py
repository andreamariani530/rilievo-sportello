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


# ---------------------------------------------------------------- rete

def prendi(url, dati=None, tentativi=3, attesa=1.5):
    """Una chiamata in rete, con due secondi tentativi se la prima non va."""
    if dati:
        url = url + "?" + urllib.parse.urlencode(dati)
    ultimo = None
    for n in range(tentativi):
        try:
            richiesta = urllib.request.Request(url, headers={"User-Agent": AGENTE})
            with urllib.request.urlopen(richiesta, timeout=60) as r:
                return r.read()
        except Exception as e:                      # noqa: BLE001
            ultimo = e
            time.sleep(attesa * (n + 1))
    raise RuntimeError("Non risponde: %s (%s)" % (url.split("?")[0], ultimo))


# ------------------------------------------------- dall'indirizzo al punto

def punto_dall_indirizzo(indirizzo):
    """Indirizzo scritto a mano -> latitudine, longitudine, indirizzo per esteso."""
    grezzo = prendi(NOMINATIM, {
        "q": indirizzo, "format": "jsonv2", "limit": 5,
        "countrycodes": "it", "addressdetails": 1,
    })
    trovati = json.loads(grezzo.decode("utf-8"))
    if not trovati:
        raise RuntimeError("Questo indirizzo non si trova sulla mappa: %s" % indirizzo)
    # meglio un civico che una via intera: la via è una riga, il civico è un punto
    trovati.sort(key=lambda t: 0 if t.get("addresstype") in ("house", "building",
                                                            "place", "yes") else 1)
    t = trovati[0]
    a = t.get("address", {})
    comune = (a.get("village") or a.get("town") or a.get("city")
              or a.get("municipality") or a.get("hamlet") or "")
    return {
        "lat": float(t["lat"]), "lon": float(t["lon"]),
        "indirizzo": t.get("display_name", indirizzo),
        "comune_nome": comune,
        "preciso": t.get("addresstype") in ("house", "building", "place", "yes"),
    }


# ------------------------------------------------- dal punto alla particella

def _riquadro(lat, lon, meta_lato_m):
    """Riquadro quadrato sul terreno, in gradi, attorno a un punto."""
    dlat = meta_lato_m / 111320.0
    dlon = dlat / max(0.2, math.cos(math.radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def _chiedi_al_catasto(lat, lon, strato, formato, lato_m=180.0, lati=700):
    y0, x0, y1, x1 = _riquadro(lat, lon, lato_m / 2)
    return prendi(CATASTO, {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo",
        "LAYERS": strato, "QUERY_LAYERS": strato, "CRS": "EPSG:6706",
        "BBOX": "%f,%f,%f,%f" % (y0, x0, y1, x1),
        "WIDTH": lati, "HEIGHT": lati, "I": lati // 2, "J": lati // 2,
        "INFO_FORMAT": formato, "FEATURE_COUNT": 1,
    }).decode("utf-8", "replace")


def particella_nel_punto(lat, lon):
    """Il punto -> foglio, particella, comune, ingombro. None se lì non c'è nulla."""
    html = _chiedi_al_catasto(lat, lon, "CP.CadastralParcel", "text/html")
    rif = re.search(r"NationalCadastralReference</th><td>([^<]+)<", html)
    if not rif:
        return None
    # forma: F801_001300.416  ->  comune F801, foglio 0013, particella 416
    codice = rif.group(1).strip()
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


def _maschera(immagine, prova):
    return bytearray(1 if prova(p) else 0 for p in immagine.getdata())


def misura(lat, lon, riquadro, lati=1100):
    """Conta i metri quadri della particella dove cade il punto.

    Il conto si fa sui pixel della mappa che disegna l'Agenzia delle Entrate.
    Si chiede la mappa di un pezzo di terreno di misura nota, quindi si sa
    quanti metri quadri vale un pixel. Poi si riempie la particella partendo
    dal punto e si contano i pixel: prima il terreno scoperto, poi i fabbricati
    che gli stanno attaccati. Sommati fanno il lotto.
    """
    # il riquadro da disegnare: quello della particella, con un po' di aria
    if riquadro:
        clat = (riquadro["lat0"] + riquadro["lat1"]) / 2
        clon = (riquadro["lon0"] + riquadro["lon1"]) / 2
        alto_m  = (riquadro["lat1"] - riquadro["lat0"]) * 111320.0
        largo_m = (riquadro["lon1"] - riquadro["lon0"]) * 111320.0 * math.cos(math.radians(clat))
        meta = max(alto_m, largo_m, 34.0) * 0.85
    else:
        clat, clon, meta = lat, lon, 55.0
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

    px, py = a_pixel(lat, lon)

    # sulla mappa dell'Agenzia i fabbricati sono arancioni pieni
    edifici = _maschera(fabb, lambda p: p[0] > 150 and p[1] < 190 and p[2] < 120
                                        and p[0] - p[2] > 60)
    # sulla mappa delle particelle il terreno è color carta, i confini sono neri,
    # e quello che particella non è (strade, acqua) resta vuoto
    terreno = _maschera(mappa, lambda p: p[0] > 200 and p[1] > 185 and p[2] > 130
                                         and p[0] - p[2] > 25)

    # il recinto: l'ingombro che il catasto dichiara per questa particella.
    # Fuori di lì la particella non può stare, quindi non ci si guarda nemmeno.
    # È la rete di sicurezza che impedisce al conto di sbordare sul vicino.
    if riquadro:
        rx0, ry0 = a_pixel(riquadro["lat1"], riquadro["lon0"])
        rx1, ry1 = a_pixel(riquadro["lat0"], riquadro["lon1"])
        rx0 -= 3; ry0 -= 3; rx1 += 3; ry1 += 3
    else:
        rx0 = ry0 = 0; rx1 = ry1 = lati - 1
    for y in range(lati):
        dentro_y = ry0 <= y <= ry1
        riga = y * lati
        for x in range(lati):
            if not (dentro_y and rx0 <= x <= rx1):
                terreno[riga + x] = 0
                edifici[riga + x] = 0

    # 1. lo scoperto: si riempie il terreno libero a partire dal punto.
    #    Le linee nere del catasto lo fermano: confini del lotto e muri di casa.
    seme = _vicino(terreno, lati, px, py)
    if not seme:
        raise RuntimeError("Nel punto trovato non c'è terreno di particella da misurare.")
    aperto, aperto_px, est_a = _riempi(terreno, lati, [seme])

    # 2. il coperto: i fabbricati che toccano quel terreno sono di questo lotto.
    #    Si allarga di poco lo scoperto, quel tanto che basta a scavalcare la
    #    linea del muro, e da lì si riempiono le sagome piene dei fabbricati.
    bordo = _allarga(aperto, lati, 7)
    semi = [(i % lati, i // lati) for i in range(lati * lati)
            if edifici[i] and bordo[i]]
    coperti, coperto_px, est_c = _riempi(edifici, lati, semi) if semi else \
             (bytearray(lati * lati), 0, est_a)

    dentro = bytearray(1 if (aperto[i] or coperti[i]) else 0 for i in range(lati * lati))
    estremi = (min(est_a[0], est_c[0] if coperto_px else est_a[0]),
               min(est_a[1], est_c[1] if coperto_px else est_a[1]),
               max(est_a[2], est_c[2] if coperto_px else est_a[2]),
               max(est_a[3], est_c[3] if coperto_px else est_a[3]))

    lotto_mq    = (aperto_px + coperto_px) * mq_px
    coperto_mq  = coperto_px * mq_px
    scoperto_mq = aperto_px * mq_px

    largo = (estremi[2] - estremi[0] + 1) * m_per_px
    alto  = (estremi[3] - estremi[1] + 1) * m_per_px

    # il conto è da guardare due volte se copre molto meno dell'ingombro che il
    # catasto dichiara: vuol dire che un muro ha tagliato il lotto in due pezzi
    sospetto = False
    if riquadro:
        recinto_mq = largo_m * alto_m
        if recinto_mq > 0 and lotto_mq < recinto_mq * 0.30:
            sospetto = True

    disegno = componi(foto, mappa, dentro, lati)
    return {
        "lotto_mq": int(round(lotto_mq)),
        "coperto_mq": int(round(coperto_mq)),
        "scoperto_mq": int(round(scoperto_mq)),
        "ingombro": "%d × %d m" % (round(largo), round(alto)),
        "sospetto": sospetto,
        "metri_per_pixel": round(m_per_px, 3),
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
    m = misura(lat, lon, part["riquadro"])
    avvisi = []
    if not p["preciso"]:
        avvisi.append("L'indirizzo è stato trovato sulla via, non sul civico: "
                      "controlla che la particella accesa sia quella giusta.")
    if m["sospetto"]:
        avvisi.append("Il lotto misurato è molto più piccolo del suo ingombro: "
                      "forse un muro lo taglia in due e ne è stato contato solo "
                      "un pezzo. Controlla i metri quadri.")
    return {
        "indirizzo": p["indirizzo"],
        "comune": part["comune"],
        "comune_nome": p["comune_nome"],
        "foglio": part["foglio"],
        "particella": part["particella"],
        "riferimento": part["codice"],
        "lat": round(lat, 7), "lon": round(lon, 7),
        "lotto_mq": m["lotto_mq"],
        "scoperto_mq": m["scoperto_mq"],
        "coperto_mq": m["coperto_mq"],
        "ingombro": m["ingombro"],
        "metri_per_pixel": m["metri_per_pixel"],
        "fonte": FONTE,
        "preparato": time.strftime("%Y-%m-%d %H:%M"),
        "avvisi": avvisi,
        "foto": in_base64(m["immagine"]),
    }


def nome_file(indirizzo):
    s = unicodedata.normalize("NFKD", indirizzo).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return (s[:60] or "rilievo")


# ------------------------------------------------- il servizio, mentre lavori

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

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path in ("/", "/ci-sei"):
                self._apri()
                self.wfile.write(json.dumps({"servizio": "rilievo", "pronto": True}).encode())
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
    print("  scoperto   %5d mq   <- questo è quello da trattare" % r["scoperto_mq"])
    print("  coperto    %5d mq" % r["coperto_mq"])
    print("  ingombro   %s" % r["ingombro"])
    for x in r["avvisi"]:
        print("  attenzione: %s" % x)
    print()
    print("  salvato in %s" % base.with_suffix(".json"))
    print("  Caricalo dentro Rilievo dal tasto \"Carica un rilievo\".")


if __name__ == "__main__":
    main()
