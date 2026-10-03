#!/usr/bin/env python3
"""La posta che manda il server: per ora solo «scegli una password nuova».

Parte dalla casella di lavoro di Andrea (Gmail) con una «password per le app» di Google,
che sta nelle variabili d'ambiente di Render e in nessun file:
    RILIEVO_POSTA_UTENTE    la casella, per esempio andreamariani530a@gmail.com
    RILIEVO_POSTA_PASSWORD  la password per le app (16 lettere)
Senza le due variabili la mail NON parte: si scrive in `RILIEVO_ARCHIVIO/posta-in-uscita/`,
cosi' i collaudi la leggono, e nel registro del server resta scritto che la posta e' spenta.
"""
import json, os, pathlib, smtplib, ssl, time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import archivio


def accesa():
    return bool((os.environ.get("RILIEVO_POSTA_UTENTE") or "").strip()
                and (os.environ.get("RILIEVO_POSTA_PASSWORD") or "").strip())


def manda(a, oggetto, testo, html=""):
    """Torna True se la mail e' partita (o e' stata messa da parte nei collaudi)."""
    utente = (os.environ.get("RILIEVO_POSTA_UTENTE") or "").strip()
    chiave = (os.environ.get("RILIEVO_POSTA_PASSWORD") or "").replace(" ", "").strip()
    if not (utente and chiave):
        d = pathlib.Path(archivio.CARTELLA) / "posta-in-uscita"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%d-%s.json" % (int(time.time() * 1000), a.split("@")[0][:20]))).write_text(
            json.dumps({"a": a, "oggetto": oggetto, "testo": testo}, ensure_ascii=False), encoding="utf-8")
        print("  posta spenta (mancano RILIEVO_POSTA_UTENTE e RILIEVO_POSTA_PASSWORD): mail messa da parte")
        return True
    m = EmailMessage()
    m["From"] = formataddr(("Rilievo", utente))
    m["To"] = a
    m["Subject"] = oggetto
    m["Message-ID"] = make_msgid(domain=utente.split("@")[-1])
    m.set_content(testo)
    if html:
        m.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=20) as s:
            s.login(utente, chiave)
            s.send_message(m)
        return True
    except Exception as e:                  # noqa: BLE001
        print("  posta non partita:", type(e).__name__)
        return False
