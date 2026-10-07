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
import datetime, os, threading, urllib.request
from zoneinfo import ZoneInfo

import posta


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


def chi_entra(mail, nuovo):
    """nuovo=True: si e' appena iscritto. nuovo=False: e' rientrato con mail e password."""
    ora = datetime.datetime.now(ZoneInfo("Europe/Rome")).strftime("%d/%m alle %H:%M")  # Render gira in UTC
    if nuovo:
        titolo = "Nuovo iscritto a Rilievo: " + mail
        testo = "%s si è iscritto a Rilievo il %s. Da oggi partono i suoi 30 giorni di prova." % (mail, ora)
    else:
        titolo = "Rientrato in Rilievo: " + mail
        testo = "%s è rientrato con mail e password il %s (telefono nuovo, o era uscito)." % (mail, ora)
    print("  avviso ad Andrea:", titolo, flush=True)
    threading.Thread(target=_manda, args=(titolo, testo), daemon=True).start()
