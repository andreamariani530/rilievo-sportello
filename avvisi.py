#!/usr/bin/env python3
"""Gli avvisi ad Andrea: chi si iscrive a Rilievo e chi rientra da un telefono nuovo.

PERCHE' ESISTE
Il 7 ottobre 2026, prima di mandare l'app ai primi dieci giardinieri, Andrea ha chiesto
«voglio sapere chi entra nell'app». Due strade, tutte e due facoltative:
  - RILIEVO_NTFY_TOPIC: la notifica arriva sul telefono di Andrea (app ntfy);
  - la posta del server (posta.py): una mail alla casella di lavoro di Andrea.
Si manda solo la mail di chi entra e l'ora: niente password, niente lavoro.
L'avviso parte in un filo a parte: se ntfy o Gmail sono lenti, chi si iscrive non aspetta.
"""
import datetime, os, pathlib, threading, urllib.request
from zoneinfo import ZoneInfo

import posta

_chiave_benvenuti = threading.Lock()


def _ntfy(titolo, testo):
    topic = (os.environ.get("RILIEVO_NTFY_TOPIC") or "").strip()
    if not topic:
        return False
    try:
        r = urllib.request.Request("https://ntfy.sh/" + topic, data=testo.encode("utf-8"), method="POST",
                                   headers={"Title": titolo.encode("utf-8").decode("latin-1", "ignore"),
                                            "Tags": "bust_in_silhouette"})
        urllib.request.urlopen(r, timeout=15).read()
        return True
    except Exception as e:
        print("     avviso ntfy non partito:", e, flush=True)
        return False


def _mail(titolo, testo):
    a = (os.environ.get("RILIEVO_AVVISI_A") or os.environ.get("RILIEVO_POSTA_UTENTE") or "").strip()
    if not a or not posta.accesa():
        return False
    try:
        return posta.manda(a, titolo, testo, nome="Rilievo, avvisi")
    except Exception as e:
        print("     avviso per mail non partito:", e, flush=True)
        return False


def _manda(titolo, testo):
    _ntfy(titolo, testo)
    _mail(titolo, testo)


def avvisa(titolo, testo):
    """Un avviso qualsiasi ad Andrea, nel suo filo: chi lo chiama non aspetta.
    Lo usa visite.py: «Flora Giardini ha aperto Rilievo» (8/10/2026)."""
    print("  avviso ad Andrea:", titolo, flush=True)
    threading.Thread(target=_manda, args=(titolo, testo), daemon=True).start()


def chi_entra(mail, nuovo):
    """nuovo=True: si e' appena iscritto. nuovo=False: e' rientrato con mail e password."""
    ora = datetime.datetime.now(ZoneInfo("Europe/Rome")).strftime("%d/%m alle %H:%M")  # Render gira in UTC
    if nuovo:
        titolo = "Nuovo iscritto a Rilievo: " + mail
        testo = "%s si è iscritto a Rilievo il %s." % (mail, ora)
    else:
        titolo = "Rientrato in Rilievo: " + mail
        testo = "%s è rientrato con mail e password il %s (telefono nuovo, o era uscito)." % (mail, ora)
    print("  avviso ad Andrea:", titolo, flush=True)
    threading.Thread(target=_manda, args=(titolo, testo), daemon=True).start()



# ------------------------------------------------- la mail di benvenuto a chi si iscrive
# 10/11/2026, Andrea: «quando uno si iscrive mandiamo la mail di benvenuto», e «inviala
# subito, cosi' non sembra una truffa ma un'azienda seria». Parte appena l'iscrizione e'
# andata a buon fine, una volta sola per mail, da info@ con la firma di Andrea; le
# risposte tornano a info@. Non dice niente di cosa fa la persona nell'app: solo il
# grazie, come ritrovare il lavoro e a chi scrivere.
# Non parte per gli indirizzi di Andrea, per quelli dei collaudi, ne' per chi arriva
# con un codice di prova o di un amico (RILIEVO_BENVENUTO_NO, separati da virgole).
BENVENUTO_OGGETTO = "Ti diamo il benvenuto in Rilievo"
BENVENUTO_TESTO = """Ciao,

grazie per l'iscrizione a Rilievo.

Per ritrovare il tuo lavoro su un altro telefono o sul computer, apri https://rilievoapp.it ed entra con questa mail e la tua password.

Se qualcosa non ti torna o ti manca, rispondi pure a questa mail: la sto costruendo io, e i pareri di chi lavora davvero sono quelli che mi servono di più.

Buon lavoro!

Andrea
Rilievo · rilievoapp.it"""
_CODICI_SENZA_BENVENUTO = {"prova-andrea", "marco-472d"}
_MAIL_SENZA_BENVENUTO = ("andreamariani530", "@example.", "@rilievoapp.it", "collaud", "prova+")


def _gia_salutati():
    import archivio
    return pathlib.Path(archivio.CARTELLA) / "benvenuti.txt"


def niente_benvenuto(mail, da=""):
    m = str(mail or "").lower()
    d = str(da or "").strip().lower()
    no = set(_CODICI_SENZA_BENVENUTO) | {x.strip().lower() for x in
                                         (os.environ.get("RILIEVO_BENVENUTO_NO") or "").split(",") if x.strip()}
    return (not m) or d in no or any(x in m for x in _MAIL_SENZA_BENVENUTO)


def _manda_benvenuto(mail):
    try:
        f = _gia_salutati()
        with _chiave_benvenuti:
            fatti = set(f.read_text(encoding="utf-8").split()) if f.exists() else set()
            if mail in fatti:
                return False
            ok = posta.manda(mail, BENVENUTO_OGGETTO, BENVENUTO_TESTO, nome="Andrea di Rilievo",
                             rispondi_a=posta.mittente())
            if ok:
                f.parent.mkdir(parents=True, exist_ok=True)
                with open(f, "a", encoding="utf-8") as x:
                    x.write(mail + "\n")
        print("  benvenuto a", mail, "partito" if ok else "NON partito", flush=True)
        return ok
    except Exception as e:                 # noqa: BLE001
        print("     benvenuto non partito:", type(e).__name__, e, flush=True)
        return False


def benvenuto(mail, da=""):
    """Subito dopo l'iscrizione, nel suo filo: chi si iscrive non aspetta."""
    if niente_benvenuto(mail, da):
        print("  benvenuto saltato (prova o indirizzo di Andrea):", mail, flush=True)
        return None
    t = threading.Thread(target=_manda_benvenuto, args=(mail,), daemon=True)
    t.start()
    return t


# ------------------------------------------------- le mail di Andrea, coi link puliti
# Il 7/10/2026 il connettore Gmail dello spazio riscriveva ogni link delle mail in
# uscita in https://www.google.com/url?q=... : chi lo apre vede l'«Avviso di
# reindirizzamento» di Google, che sembra una truffa. La posta del server (SMTP con la
# password per le app) non tocca i link. Questa porta la usa solo chi ha la chiave
# RILIEVO_CHIAVE_POSTA (sta su Render e nel deposito di Andrea), con un tetto al giorno.
import hashlib, hmac, re

_TETTO_GIORNO = 20
_inviate = {}


def manda_per_andrea(chiave, a, oggetto, testo):
    """Torna (codice, risposta)."""
    giusta = (os.environ.get("RILIEVO_CHIAVE_POSTA") or "").strip()
    if not giusta or not hmac.compare_digest(hashlib.sha256(str(chiave or "").encode()).digest(),
                                             hashlib.sha256(giusta.encode()).digest()):
        return 403, {"errore": "Chiave sbagliata."}
    a = str(a or "").strip()
    if not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[a-z]{2,}", a, re.I):
        return 400, {"errore": "Destinatario non valido."}
    if not oggetto or not testo or len(testo) > 8000:
        return 400, {"errore": "Manca l'oggetto o il testo."}
    oggi = datetime.date.today().isoformat()
    if _inviate.get(oggi, 0) >= _TETTO_GIORNO:
        return 429, {"errore": "Tetto di oggi raggiunto."}
    if not posta.accesa():
        return 503, {"errore": "La posta del server e' spenta."}
    ok = posta.manda(a, str(oggetto)[:200], str(testo), nome="Andrea Mariani", rispondi_a=posta.mittente())
    if not ok:
        return 502, {"errore": "Gmail non ha preso la mail."}
    _inviate[oggi] = _inviate.get(oggi, 0) + 1
    print("  mail di Andrea partita a", a, flush=True)
    return 200, {"partita": True, "a": a}
