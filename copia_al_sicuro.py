#!/usr/bin/env python3
"""La copia al sicuro: il lavoro di un artigiano fuori dal suo telefono.

PERCHE' ESISTE
Fino al 3 ottobre 2026 tutto quello che un artigiano scrive in Rilievo stava solo
nella memoria del suo telefono. Telefono perso, rotto o cambiato: lavoro perso,
a meno di aver premuto «Esporta tutto in un file». Klivo tiene tutto sul server e
per un artigiano e' la cosa piu' ovvia del mondo; per noi era la mancanza piu'
grave (confronto del 3/10, `valutazioni/rilievo-contro-klivo-2026-10-03.pdf`).

COME FUNZIONA, IN DUE RIGHE
Il telefono si inventa una chiave lunga e casuale la prima volta, e con quella
manda al server una copia di tutto, da solo, poco dopo ogni salvataggio. Su un
altro telefono si scrive la stessa chiave e la copia torna indietro.

NIENTE ACCOUNT, NIENTE PASSWORD
La chiave non la sceglie la persona: la tira fuori il telefono, 80 bit a caso.
Nessuno la indovina, e non c'e' una password debole da rubare. Il server non la
conserva nemmeno: tiene solo la sua impronta (sha256), e il file ha quel nome.
Chi legge il disco non sa a chi appartiene una copia, e non puo' rifare la chiave.

DUE TELEFONI CHE SCRIVONO
Ogni copia ha un numero di versione. Il telefono dice «parto dalla versione 7».
Se sul server c'e' gia' la 8, vuol dire che un altro telefono ha salvato dopo, e
questa scrittura NON passa: il telefono deve chiedere alla persona cosa tenere.
Cosi' non si mangia mai il lavoro di un altro telefono senza dirlo.

DOVE STANNO I FILE
Nella stessa cartella dell'archivio della squadra (`RILIEVO_ARCHIVIO`, il disco
permanente su Render), sotto `copie/`. Un file per chiave, piu' la copia di prima
(`.prima`), per rimettere a posto un salvataggio andato storto.
"""
import hashlib, json, os, pathlib, re, threading, time

import archivio

MAX_COPIA = 12 * 1024 * 1024       # il telefono ne tiene al massimo 5-10 MB
_chiave = threading.RLock()

# la chiave come la scrive il telefono: 16 lettere/cifre (base32 senza 0, 1, O, I),
# a gruppi di quattro. Si accettano anche minuscole, spazi e trattini.
_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def pulisci_chiave(c):
    c = re.sub(r"[\s\-]", "", str(c or "")).upper()
    if len(c) != 16 or any(x not in _ALFABETO for x in c):
        return ""
    return c


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
        return "no", {"errore": "La chiave della copia non e' scritta bene."}
    if not isinstance(stato, dict) or not isinstance(stato.get("preventivi", []), list):
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
                 "quando": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
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
