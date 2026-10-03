#!/usr/bin/env python3
"""I promemoria automatici: il cliente che non risponde viene risentito da solo.

PERCHE' ESISTE
Klivo risente da solo chi non risponde al preventivo e chi non paga. Rilievo fino al
4 ottobre 2026 lo faceva con un tocco su WhatsApp, cioe' solo se l'artigiano se ne
ricordava. Andrea: «ok ci sto con il piano» (punto 4 del confronto con Klivo).

COME FUNZIONA
Il telefono, quando manda la copia al sicuro (copia_al_sicuro.py), ci mette dentro
anche l'elenco gia' pronto di cosa si potrebbe ricordare (`daRicordare`): il conto
del totale, il nome del cliente e la sua mail li fa l'app, che e' l'unica che sa fare
i conti del preventivo. Qui si guarda solo la data e si decide se e' ora.

Ogni mezz'ora, nei giorni feriali dalle 9 alle 19 ora italiana:
  - preventivo consegnato senza risposta da `giorni` giorni (di base 5): una mail
    garbata al cliente, al massimo `volte` volte (di base 2), a `giorni` di distanza;
  - lavoro finito e non pagato da 7 giorni, SOLO se la ditta l'ha acceso: una mail
    per il pagamento, al massimo 2 volte, a 7 giorni di distanza.
La mail parte dalla casella di Rilievo col nome della ditta, e «Rispondi a» e' la
mail della ditta: il cliente risponde all'artigiano, non a noi.

COSA NON FA, APPOSTA
  - niente se la ditta non l'ha acceso (di base e' spento: si scrive ai clienti suoi);
  - niente la sera, la notte e nel fine settimana;
  - al massimo 20 mail al giorno per ditta e 300 in tutto (la casella Gmail ne regge 500);
  - se il telefono non ha ancora mandato la copia nuova (preventivo accettato ma senza
    rete), un promemoria puo' partire lo stesso: e' il limite di lavorare sulla copia.

Il registro di cosa e' partito sta in `RILIEVO_ARCHIVIO/promemoria/`, un file per
account, e l'app lo legge per scrivere sul preventivo «Risentito per mail il ...».
"""
import datetime, json, os, pathlib, re, threading, time

import archivio
import posta

try:
    import zoneinfo
    ROMA = zoneinfo.ZoneInfo("Europe/Rome")
except Exception:                           # noqa: BLE001
    ROMA = None

MAX_DITTA_GIORNO = 20
MAX_TUTTI_GIORNO = 300
GIORNI_PAGAMENTO = 7
_chiave = threading.RLock()


def _cartella():
    d = pathlib.Path(archivio.CARTELLA) / "promemoria"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _registro(nome):
    f = _cartella() / (nome + ".json")
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except Exception:                       # noqa: BLE001
        return {}


def _scrivi_registro(nome, dati):
    f = _cartella() / (nome + ".json")
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def registro(nome):
    """Per l'app: cosa e' partito per questo account, {chiave: [istanti]}."""
    return (_registro(nome) or {}).get("inviati", {})


def _ora_italiana(adesso):
    t = datetime.datetime.fromtimestamp(adesso, tz=datetime.timezone.utc)
    return t.astimezone(ROMA) if ROMA else t + datetime.timedelta(hours=2)


def ora_giusta(adesso=None):
    t = _ora_italiana(adesso or time.time())
    return t.weekday() < 5 and 9 <= t.hour < 19


def _giorni_da(data_iso, adesso):
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)", str(data_iso or ""))
    if not m:
        return None
    d = datetime.date(int(m[1]), int(m[2]), int(m[3]))
    return (_ora_italiana(adesso).date() - d).days


def _mail_buona(m):
    return bool(re.fullmatch(r"[^\s@<>\"']+@[^\s@<>\"']+\.[a-zA-Z]{2,24}", str(m or "").strip()))


def _soldi(n):
    try:
        s = f"{float(n):,.2f}"
    except (TypeError, ValueError):
        return ""
    return "€ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def testo(voce, ditta):
    """(oggetto, testo) della mail, nella stessa voce dei solleciti dell'app (del Lei)."""
    numero = voce.get("numero") or ""
    dove = (" per " + voce["luogo"]) if voce.get("luogo") else ""
    tot = _soldi(voce.get("totale"))
    firma = ditta.get("nome") or ""
    if ditta.get("tel"):
        firma += " · " + ditta["tel"]
    saluto = "Buongiorno" + ((" " + voce["nome"]) if voce.get("nome") else "") + ","
    if voce.get("tipo") == "pagamento":
        oggetto = "Lavoro" + dove + ": il pagamento"
        corpo = (f"{saluto}\n\nil lavoro{dove} è finito. Le ricordo il pagamento"
                 + (f" di {tot}" if tot else "") + (f" (preventivo n. {numero})" if numero else "")
                 + ".\nSe l'ha già fatto, non tenga conto di questo messaggio. Grazie.")
    else:
        oggetto = "Preventivo" + (f" n. {numero}" if numero else "") + dove
        corpo = (f"{saluto}\n\nle scrivo per il preventivo" + (f" n. {numero}" if numero else "") + dove
                 + (f" ({tot} IVA compresa)" if tot else "") + " che le ho mandato.\n"
                 "Ha avuto modo di guardarlo? Se c'è qualcosa da cambiare lo sistemiamo volentieri.")
        if voce.get("link"):
            corpo += "\n\nLo ritrova qui, e se le va bene lo accetta direttamente dalla pagina:\n" + voce["link"]
    corpo += "\n\n" + firma + "\n\n(Messaggio mandato da Rilievo per conto di " + (ditta.get("nome") or "la ditta") + \
             ": se risponde, la risposta arriva direttamente a loro.)"
    return oggetto, corpo


def da_mandare(stato, registro_ditta, adesso):
    """Le voci di questa ditta per cui adesso e' ora di mandare un promemoria."""
    azienda = (stato or {}).get("azienda") or {}
    imp = azienda.get("promemoria") or {}
    if not imp.get("preventivi") and not imp.get("pagamenti"):
        return []
    try:
        giorni = max(2, min(30, int(imp.get("giorni") or 5)))
        volte = max(1, min(3, int(imp.get("volte") or 2)))
    except (TypeError, ValueError):
        giorni, volte = 5, 2
    fuori = []
    for v in (stato.get("daRicordare") or [])[:200]:
        if not isinstance(v, dict) or not _mail_buona(v.get("mail")) or not v.get("id"):
            continue
        tipo = "pagamento" if v.get("tipo") == "pagamento" else "preventivo"
        if tipo == "preventivo" and not imp.get("preventivi"):
            continue
        if tipo == "pagamento" and not imp.get("pagamenti"):
            continue
        attesa, quante = (GIORNI_PAGAMENTO, 2) if tipo == "pagamento" else (giorni, volte)
        g = _giorni_da(v.get("dal"), adesso)
        if g is None or g < attesa:
            continue
        chiave = tipo + ":" + str(v["id"])
        gia = registro_ditta.get(chiave) or []
        if len(gia) >= quante:
            continue
        if gia and _giorni_da(gia[-1][:10], adesso) is not None and _giorni_da(gia[-1][:10], adesso) < attesa:
            continue
        fuori.append((chiave, dict(v, tipo=tipo)))
    return fuori


def giro(adesso=None, forza_ora=False):
    """Un passaggio su tutte le copie. Torna quante mail sono partite."""
    import copia_al_sicuro
    adesso = adesso or time.time()
    if not forza_ora and not ora_giusta(adesso):
        return 0
    oggi = _ora_italiana(adesso).date().isoformat()
    partite = 0
    with _chiave:
        for f in sorted(copia_al_sicuro._cartella().glob("*.json")):
            nome = f.stem
            try:
                stato = json.loads(f.read_text(encoding="utf-8")).get("stato") or {}
            except Exception:               # noqa: BLE001
                continue
            reg = _registro(nome)
            inviati = reg.setdefault("inviati", {})
            oggi_ditta = sum(1 for xs in inviati.values() for x in xs if x.startswith(oggi))
            for chiave, voce in da_mandare(stato, inviati, adesso):
                if oggi_ditta >= MAX_DITTA_GIORNO or _partite_oggi(oggi) >= MAX_TUTTI_GIORNO:
                    break
                azienda = stato.get("azienda") or {}
                oggetto, corpo = testo(voce, azienda)
                rispondi = azienda.get("mail") if _mail_buona(azienda.get("mail")) else ""
                if posta.manda(voce["mail"].strip(), oggetto, corpo,
                               nome=azienda.get("nome") or "Rilievo", rispondi_a=rispondi):
                    quando = _ora_italiana(adesso).strftime("%Y-%m-%dT%H:%M")
                    inviati.setdefault(chiave, []).append(quando)
                    _conta(oggi)
                    oggi_ditta += 1
                    partite += 1
            if inviati:
                _scrivi_registro(nome, reg)
    if partite:
        print("  promemoria: %d mail partite" % partite)
    return partite


_contatore = {"giorno": "", "n": 0}


def _partite_oggi(oggi):
    return _contatore["n"] if _contatore["giorno"] == oggi else 0


def _conta(oggi):
    if _contatore["giorno"] != oggi:
        _contatore.update(giorno=oggi, n=0)
    _contatore["n"] += 1


def avvia():
    """Il giro ogni mezz'ora, in un filo a parte. Si spegne con RILIEVO_PROMEMORIA=spento."""
    if (os.environ.get("RILIEVO_PROMEMORIA") or "").strip().lower() == "spento":
        print("  promemoria spenti (RILIEVO_PROMEMORIA=spento)")
        return

    def gira():
        time.sleep(60)
        while True:
            try:
                giro()
            except Exception as e:          # noqa: BLE001
                print("  promemoria: giro non riuscito:", type(e).__name__)
            time.sleep(1800)

    threading.Thread(target=gira, name="promemoria", daemon=True).start()
