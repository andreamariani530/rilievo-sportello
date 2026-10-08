#!/usr/bin/env python3
"""Chi apre il link: un link per ogni ditta, e un segno quando lo apre.

PERCHE' ESISTE
L'8 ottobre 2026 Andrea ha chiesto «riesci a capire se qualcuno ha cliccato il link?».
La risposta era no: il server non segnava le visite, Render su questo piano non tiene
i log delle richieste, e il link mandato ai giardinieri era uguale per tutti.
Da qui in avanti ogni ditta ha il suo link: https://rilievo-sportello.onrender.com/?da=<codice>.

COSA SI SEGNA (e cosa no)
Una riga per volta in `RILIEVO_ARCHIVIO/visite/visite.jsonl`, sullo stesso disco
dell'archivio: il codice, l'ora di Roma, la pagina, cosa ha fatto (aperto il link o
chiesto un rilievo) e «telefono» o «computer». Niente indirizzo IP, niente user-agent,
niente indirizzo del giardino. Il codice e' corto e non contiene dati personali.

Il nome della ditta viene da `da-chi.json` (accanto a questo file), fatto dal registro
dei contatti. Alla prima apertura di un codice dell'elenco, e al suo primo rilievo,
parte un avviso ad Andrea (come per chi si iscrive). Un codice che non e' nell'elenco
si segna lo stesso ma non fa partire avvisi: cosi' nessuno puo' riempire di notifiche
il telefono di Andrea inventando codici.
"""
import datetime, hashlib, hmac, json, os, pathlib, re, threading
from zoneinfo import ZoneInfo

import archivio
import avvisi

QUI = pathlib.Path(__file__).resolve().parent
CODICE_BUONO = re.compile(r"[a-z0-9-]{2,30}")
MASSIMO_FILE = 5 * 1024 * 1024      # oltre i 5 MB non si scrive piu': non deve mai riempire il disco
MASSIMO_SCONOSCIUTI = 200           # codici fuori elenco tenuti, al massimo

_lucchetto = threading.Lock()
_visti = None                       # {(codice, cosa)} gia' visti: per l'avviso «solo la prima volta»


def _file():
    return pathlib.Path(archivio.CARTELLA) / "visite" / "visite.jsonl"


def codice_pulito(c):
    """Il codice se e' fatto bene, altrimenti None (e la visita non si segna)."""
    c = str(c or "").strip()
    return c if CODICE_BUONO.fullmatch(c) else None


def da_chi():
    try:
        d = json.loads((QUI / "da-chi.json").read_text(encoding="utf-8"))
        return d.get("codici") or {}
    except Exception:                   # noqa: BLE001
        return {}


def _righe():
    f = _file()
    if not f.is_file():
        return []
    fuori = []
    for r in f.read_text(encoding="utf-8").splitlines():
        try:
            fuori.append(json.loads(r))
        except ValueError:
            pass
    return fuori


def _carica_visti():
    global _visti
    if _visti is None:
        _visti = {(r.get("codice"), r.get("cosa")) for r in _righe()}
    return _visti


def dispositivo(ua):
    return "telefono" if re.search(r"Mobile|Android|iPhone|iPad", ua or "") else "computer"


def segna(codice, pagina, cosa, ua=""):
    """cosa: "apertura" (ha aperto il link) o "rilievo" (ha chiesto un rilievo).
    Torna True se la visita e' stata segnata. Non alza mai: l'app non deve accorgersene."""
    try:
        c = codice_pulito(codice)
        if not c or cosa not in ("apertura", "rilievo"):
            return False
        elenco = da_chi()
        ora = datetime.datetime.now(ZoneInfo("Europe/Rome"))
        riga = {"codice": c, "ora": ora.strftime("%Y-%m-%d %H:%M:%S"), "pagina": str(pagina)[:30],
                "cosa": cosa, "da": dispositivo(ua)}
        with _lucchetto:
            visti = _carica_visti()
            if c not in elenco and not any(k == c for k, _ in visti) and \
                    len({k for k, _ in visti if k not in elenco}) >= MASSIMO_SCONOSCIUTI:
                return False
            f = _file()
            f.parent.mkdir(parents=True, exist_ok=True)
            if f.is_file() and f.stat().st_size > MASSIMO_FILE:
                return False
            prima_volta = (c, cosa) not in visti
            with f.open("a", encoding="utf-8") as w:
                w.write(json.dumps(riga, ensure_ascii=False) + "\n")
            visti.add((c, cosa))
        print("  visita: %s %s %s" % (c, cosa, pagina), flush=True)
        if prima_volta and c in elenco:
            _avvisa(c, elenco[c], cosa, ora, riga["da"])
        return True
    except Exception as e:              # noqa: BLE001
        print("     visita non segnata:", e, flush=True)
        return False


def _avvisa(c, chi, cosa, ora, da):
    ditta = (chi.get("ditta") if isinstance(chi, dict) else str(chi)) or c
    prova = isinstance(chi, dict) and chi.get("prova")
    quando = ora.strftime("%d/%m alle %H:%M")
    if cosa == "apertura":
        titolo = "%s ha aperto Rilievo" % ditta
        testo = "%s ha aperto il suo link di Rilievo il %s, dal %s." % (ditta, quando, da)
    else:
        titolo = "%s ha fatto un rilievo" % ditta
        testo = "%s ha chiesto il suo primo rilievo il %s, dal %s." % (ditta, quando, da)
    if prova:
        titolo = "Prova: " + titolo
    avvisi.avvisa(titolo, testo)


def riassunto():
    """Per ogni codice: prima apertura, ultima, quante volte, rilievi fatti."""
    elenco = da_chi()
    per = {}
    for r in _righe():
        c = r.get("codice")
        x = per.setdefault(c, {"codice": c, "prima_apertura": "", "ultima_apertura": "", "aperture": 0,
                               "primo_rilievo": "", "ultimo_rilievo": "", "rilievi": 0, "da": []})
        if r.get("cosa") == "apertura":
            x["aperture"] += 1
            x["prima_apertura"] = x["prima_apertura"] or r.get("ora", "")
            x["ultima_apertura"] = r.get("ora", "")
        elif r.get("cosa") == "rilievo":
            x["rilievi"] += 1
            x["primo_rilievo"] = x["primo_rilievo"] or r.get("ora", "")
            x["ultimo_rilievo"] = r.get("ora", "")
        if r.get("da") and r["da"] not in x["da"]:
            x["da"].append(r["da"])
    fuori = []
    for c, x in per.items():
        chi = elenco.get(c)
        x["ditta"] = (chi.get("ditta") if isinstance(chi, dict) else chi) or ""
        x["prova"] = bool(isinstance(chi, dict) and chi.get("prova"))
        x["in_elenco"] = chi is not None
        fuori.append(x)
    fuori.sort(key=lambda x: max(x["ultima_apertura"], x["ultimo_rilievo"]), reverse=True)
    return {"quanti": len(fuori), "codici": fuori}


def chiave_giusta(chiave):
    giusta = (os.environ.get("RILIEVO_CHIAVE_POSTA") or "").strip()
    return bool(giusta) and hmac.compare_digest(hashlib.sha256(str(chiave or "").encode()).digest(),
                                                hashlib.sha256(giusta.encode()).digest())
