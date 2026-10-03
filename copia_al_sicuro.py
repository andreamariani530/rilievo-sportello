#!/usr/bin/env python3
"""La copia al sicuro: il lavoro di un artigiano fuori dal suo telefono.

PERCHE' ESISTE
Fino al 3 ottobre 2026 tutto quello che un artigiano scrive in Rilievo stava solo
nella memoria del suo telefono. Telefono perso, rotto o cambiato: lavoro perso.
Klivo tiene tutto sul server; per noi era la mancanza piu' grave (confronto del 3/10,
`valutazioni/rilievo-contro-klivo-2026-10-03.pdf`).

COME FUNZIONA
Il telefono, entrato con mail e password (vedi accessi.py), manda una copia di tutto
poco dopo ogni salvataggio. Su un altro telefono si entra con la stessa mail e la copia
torna indietro. La copia e' legata all'account: il nome del file e' l'impronta della
mail, non la mail.

PRIMA ERA CHIUSA A CHIAVE SUL TELEFONO, ADESSO NO
La prima versione (3/10, 20:20) cifrava la copia sul telefono con una chiave di 16
segni che noi non vedevamo mai. Andrea ha scelto mail e password «come tutti», cioe'
anche la password dimenticata che si rimette con una mail: per poterlo fare la copia
deve essere leggibile dal server. Sta sul disco permanente di Render, in Europa, e non
la guarda nessuno. L'informativa lo dice cosi', senza promettere di piu'.

DUE TELEFONI CHE SCRIVONO
Ogni copia ha un numero di versione. Il telefono dice «parto dalla versione 7».
Se sul server c'e' gia' la 8, un altro telefono ha salvato dopo, e questa scrittura
NON passa: il telefono chiede alla persona cosa tenere.

DOVE STANNO I FILE
`RILIEVO_ARCHIVIO/copie/`, un file per account, piu' la copia di prima (`.prima`).
"""
import hashlib, json, os, pathlib, re, threading, time

import archivio

MAX_COPIA = 12 * 1024 * 1024       # il telefono ne tiene al massimo 5-10 MB
_chiave = threading.RLock()

def pulisci_chiave(c):
    """Il nome della copia che manda il telefono: 64 cifre esadecimali."""
    c = str(c or "").strip().lower()
    return c if re.fullmatch(r"[0-9a-f]{64}", c) else ""


def _cartella():
    d = pathlib.Path(archivio.CARTELLA) / "copie"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(chiave):
    impronta = hashlib.sha256(("rilievo-copia:" + chiave).encode()).hexdigest()
    return _cartella() / (impronta + ".json")


def leggi(chiave):
    """La copia per questa chiave, o None se non c'e'."""
    chiave = pulisci_chiave(chiave)
    if not chiave:
        return None
    f = _file(chiave)
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:                       # noqa: BLE001
        prima = f.with_suffix(".prima")
        if prima.is_file():
            return json.loads(prima.read_text(encoding="utf-8"))
        return None


def versione(chiave):
    c = leggi(chiave)
    return {"c_e": bool(c), "versione": (c or {}).get("versione", 0),
            "quando": (c or {}).get("quando", ""), "da": (c or {}).get("da", "")}


def salva(chiave, stato, partenza, da=""):
    """Scrive la copia. Torna (esito, dati):
       ("ok", {"versione": n})         scritta
       ("dopo", {"versione": n, ...})  un altro telefono ha salvato dopo: non scritta
       ("no", {"errore": ...})         chiave o contenuto sbagliati
    """
    chiave = pulisci_chiave(chiave)
    if not chiave:
        return "no", {"errore": "Il nome della copia non e' scritto bene."}
    if (not isinstance(stato, dict) or not isinstance(stato.get("azienda"), dict)
            or not isinstance(stato.get("preventivi", []), list)):
        return "no", {"errore": "Quello che mi mandi non sembra il lavoro di Rilievo."}
    try:
        partenza = int(partenza or 0)
    except (TypeError, ValueError):
        partenza = 0
    testo_stato = json.dumps(stato, ensure_ascii=False, separators=(",", ":"))
    if len(testo_stato.encode()) > MAX_COPIA:
        return "no", {"errore": "Il lavoro e' troppo grande per la copia (oltre 12 MB)."}
    with _chiave:
        vecchia = leggi(chiave)
        ora = (vecchia or {}).get("versione", 0)
        if vecchia and partenza != ora:
            return "dopo", {"versione": ora, "quando": vecchia.get("quando", ""),
                            "da": vecchia.get("da", "")}
        nuova = {"versione": ora + 1,
                 "quando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "da": str(da or "")[:60],
                 "stato": stato}
        f = _file(chiave)
        if f.is_file():
            os.replace(f, f.with_suffix(".prima"))
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(nuova, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        os.replace(tmp, f)
        return "ok", {"versione": nuova["versione"], "quando": nuova["quando"]}


def quante():
    """Per Andrea: quante copie ci sono e quanto pesano (senza dire di chi)."""
    d = _cartella()
    tutte = list(d.glob("*.json"))
    return {"copie": len(tutte), "mb": round(sum(f.stat().st_size for f in tutte) / 1e6, 2)}
