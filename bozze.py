#!/usr/bin/env python3
"""Il promemoria all'artigiano: il preventivo fatto e mai mandato al cliente.

PERCHE' ESISTE
11/11/2026: cinque persone hanno fatto un preventivo con Rilievo, nessuna l'ha mandato
al suo cliente. Quasi nessuno riapre l'app il giorno dopo. I promemoria di
promemoria.py scrivono ai CLIENTI della ditta; qui si scrive alla DITTA, una volta,
la mattina dopo. Andrea: «si a tutti e due».

COSA GUARDA
Solo la copia al sicuro di chi si e' iscritto con mail e password (copia_al_sicuro.py),
cioe' il lavoro che la persona ha salvato. MAI la traccia dei passi: la mail dice solo
quello che c'e' nel suo lavoro, uguale per tutti (regola «mai far capire cosa vediamo»).

QUANDO PARTE
  - preventivo in bozza, con almeno una voce, fatto da 1 a 6 giorni;
  - dal lunedi' al sabato, fra le 7:30 e le 9:30 ora italiana (prima del cantiere);
  - UNA mail per preventivo, per sempre; al massimo una mail al giorno per persona
    (se le bozze sono piu' di una, stanno nella stessa mail);
  - mai per la ditta di esempio, mai per gli indirizzi di Andrea e dei collaudi
    (stessa regola della mail di benvenuto), mai per chi e' in RILIEVO_BOZZE_NO.
Si spegne tutto con RILIEVO_BOZZE=spento su Render.

Il registro di cosa e' partito sta in RILIEVO_ARCHIVIO/bozze-ricordate.json.
"""
import datetime, json, os, pathlib, re, threading, time

import archivio
import posta

try:
    import zoneinfo
    ROMA = zoneinfo.ZoneInfo("Europe/Rome")
except Exception:                           # noqa: BLE001
    ROMA = None

DITTA_DI_ESEMPIO = "Giardini Conti"
MAX_GIORNO = 30
GIORNI_MIN, GIORNI_MAX = 1, 6
SITO = "https://rilievoapp.it"
_chiave = threading.RLock()


def _ora_italiana(adesso):
    t = datetime.datetime.fromtimestamp(adesso, tz=datetime.timezone.utc)
    return t.astimezone(ROMA) if ROMA else t + datetime.timedelta(hours=1)


def ora_giusta(adesso=None):
    t = _ora_italiana(adesso or time.time())
    minuti = t.hour * 60 + t.minute
    return t.weekday() < 6 and 7 * 60 + 30 <= minuti < 9 * 60 + 30


def _giorni_da(data_iso, adesso):
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)", str(data_iso or ""))
    if not m:
        return None
    try:
        d = datetime.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None
    return (_ora_italiana(adesso).date() - d).days


def _registro_file():
    return pathlib.Path(archivio.CARTELLA) / "bozze-ricordate.json"


def _registro():
    f = _registro_file()
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except Exception:                       # noqa: BLE001
        return {}


def _scrivi_registro(dati):
    f = _registro_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def escluso(mail):
    import avvisi
    m = str(mail or "").strip().lower()
    no = {x.strip().lower() for x in (os.environ.get("RILIEVO_BOZZE_NO") or "").split(",") if x.strip()}
    return avvisi.niente_benvenuto(m) or m in no


def bozze_da_ricordare(stato, gia, adesso):
    """Le bozze di questa copia per cui e' ora di scrivere. gia = {id_preventivo: data}."""
    stato = stato or {}
    if ((stato.get("azienda") or {}).get("nome") or "").strip() == DITTA_DI_ESEMPIO:
        return []
    fuori = []
    for p in (stato.get("preventivi") or [])[:500]:
        if not isinstance(p, dict) or p.get("stato") != "bozza" or not p.get("id"):
            continue
        if p.get("dal_documento") or not p.get("righe"):
            continue
        if str(p["id"]) in gia:
            continue
        g = _giorni_da(p.get("data"), adesso)
        if g is None or not (GIORNI_MIN <= g <= GIORNI_MAX):
            continue
        fuori.append(p)
    return fuori


def _per_chi(p, stato):
    """«il signor Rossi, via Roma 3» con quello che c'e'; mai inventato."""
    nome = ""
    cid = p.get("cliente")
    for c in (stato.get("clienti") or []):
        if isinstance(c, dict) and cid and c.get("id") == cid:
            nome = (c.get("nome") or "").strip()
            break
    nome = nome or (p.get("nome") or "").strip()
    luogo = (p.get("luogo") or "").strip()
    pezzi = [x for x in (nome, luogo) if x]
    return ", ".join(pezzi)


def testo(bozze, stato):
    """(oggetto, testo). Del tu, come la mail di benvenuto, firma di Andrea."""
    if len(bozze) == 1:
        p = bozze[0]
        chi = _per_chi(p, stato)
        oggetto = "Il preventivo" + ((" per " + chi) if chi else "") + " è pronto da mandare"
        corpo = ("Ciao,\n\nin Rilievo hai un preventivo pronto"
                 + ((" per " + chi) if chi else "")
                 + ". È ancora in bozza, quindi il cliente non l'ha ricevuto.\n\n"
                 "Per mandarglielo apri il preventivo e tocca «Consegnalo al cliente»: "
                 "parte su WhatsApp o per mail, con il link per accettarlo.\n"
                 + SITO + "/?entra=1#preventivo/" + str(p["id"]))
    else:
        oggetto = "Hai %d preventivi pronti da mandare" % len(bozze)
        righe = []
        for p in bozze[:5]:
            chi = _per_chi(p, stato) or (("n. " + str(p["numero"])) if p.get("numero") else "preventivo senza nome")
            righe.append("- " + chi + ": " + SITO + "/?entra=1#preventivo/" + str(p["id"]))
        corpo = ("Ciao,\n\nin Rilievo hai %d preventivi pronti, ancora in bozza: "
                 "i clienti non li hanno ricevuti.\n\n" % len(bozze)
                 + "\n".join(righe)
                 + "\n\nApri quello che vuoi mandare e tocca «Consegnalo al cliente»: "
                 "parte su WhatsApp o per mail, con il link per accettarlo.")
    corpo += ("\n\nSe era solo una prova, non serve fare niente: questa mail non torna."
              "\nSe invece qualcosa ti ha fermato, rispondi pure qui: lo sistemo io."
              "\n\nBuon lavoro!\n\nAndrea\nRilievo · rilievoapp.it")
    return oggetto, corpo


def _account():
    """[(mail, file della copia)] di chi si e' iscritto."""
    import accessi, copia_al_sicuro
    fuori = []
    for f in sorted(accessi._cartella().glob("*.json")):
        try:
            mail = accessi.mail_pulita(json.loads(f.read_text(encoding="utf-8")).get("mail"))
        except Exception:                   # noqa: BLE001
            continue
        if mail:
            fuori.append((mail, copia_al_sicuro._file(accessi.id_account(mail))))
    return fuori


def giro(adesso=None, forza_ora=False, prova=False):
    """Un passaggio su tutti gli iscritti. Torna [(mail, oggetto)] delle mail partite
    (o che partirebbero, con prova=True: niente posta e niente registro)."""
    adesso = adesso or time.time()
    if not forza_ora and not ora_giusta(adesso):
        return []
    oggi = _ora_italiana(adesso).date().isoformat()
    partite = []
    with _chiave:
        reg = _registro()
        fatti = reg.setdefault("preventivi", {})       # account -> {id: data}
        giorni = reg.setdefault("giorni", {})          # account -> ultimo giorno scritto
        oggi_tutti = sum(1 for g in giorni.values() if g == oggi)
        for mail, f in _account():
            if oggi_tutti >= MAX_GIORNO:
                break
            if escluso(mail) or not f.is_file():
                continue
            conto = f.stem
            if giorni.get(conto) == oggi:
                continue
            try:
                stato = json.loads(f.read_text(encoding="utf-8")).get("stato") or {}
            except Exception:               # noqa: BLE001
                continue
            gia = fatti.get(conto) or {}
            bozze = bozze_da_ricordare(stato, gia, adesso)
            if not bozze:
                continue
            oggetto, corpo = testo(bozze, stato)
            if prova:
                partite.append((mail, oggetto))
                continue
            if posta.manda(mail, oggetto, corpo, nome="Andrea di Rilievo", rispondi_a=posta.mittente()):
                fatti.setdefault(conto, {}).update({str(p["id"]): oggi for p in bozze})
                giorni[conto] = oggi
                oggi_tutti += 1
                partite.append((mail, oggetto))
                print("  bozze: promemoria partito (%d preventivi)" % len(bozze), flush=True)
        if partite and not prova:
            _scrivi_registro(reg)
    return partite


def avvia():
    """Ogni mezz'ora, in un filo a parte. Si spegne con RILIEVO_BOZZE=spento."""
    if (os.environ.get("RILIEVO_BOZZE") or "").strip().lower() == "spento":
        print("  promemoria delle bozze spenti (RILIEVO_BOZZE=spento)")
        return

    def gira():
        time.sleep(90)
        while True:
            try:
                giro()
            except Exception as e:          # noqa: BLE001
                print("  bozze: giro non riuscito:", type(e).__name__, flush=True)
            time.sleep(1800)

    threading.Thread(target=gira, name="bozze", daemon=True).start()
