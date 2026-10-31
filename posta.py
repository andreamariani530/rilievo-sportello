#!/usr/bin/env python3
"""La posta che manda il server: per ora solo «scegli una password nuova».

Dal 31/10/2026 parte prima dalla casella del dominio, info@rilievoapp.it su Aruba:
    RILIEVO_INFO_UTENTE     info@rilievoapp.it
    RILIEVO_INFO_PASSWORD   la password della casella
Se Aruba non la prende (o le due variabili mancano), parte dalla casella di lavoro di
Andrea (Gmail) con una «password per le app» di Google:
    RILIEVO_POSTA_UTENTE    la casella, per esempio andreamariani530a@gmail.com
    RILIEVO_POSTA_PASSWORD  la password per le app (16 lettere)
Stanno nelle variabili d'ambiente di Render e in nessun file.
Senza nessuna delle due coppie la mail NON parte: si scrive in `RILIEVO_ARCHIVIO/posta-in-uscita/`,
cosi' i collaudi la leggono, e nel registro del server resta scritto che la posta e' spenta.
"""
import json, os, pathlib, smtplib, ssl, time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import archivio


def _caselle():
    """Le caselle da provare, nell'ordine: prima info@ del dominio, poi la Gmail."""
    c = []
    for pre, server in (("RILIEVO_INFO", "smtps.aruba.it"), ("RILIEVO_POSTA", "smtp.gmail.com")):
        utente = (os.environ.get(pre + "_UTENTE") or "").strip()
        chiave = (os.environ.get(pre + "_PASSWORD") or "").strip()
        if pre == "RILIEVO_POSTA":
            chiave = chiave.replace(" ", "")
        if utente and chiave:
            c.append((utente, chiave, (os.environ.get(pre + "_SERVER") or "").strip() or server))
    return c


def mittente():
    """L'indirizzo da cui parte la posta (e a cui il cliente risponde)."""
    c = _caselle()
    return c[0][0] if c else ""


def accesa():
    return bool(_caselle())


def manda(a, oggetto, testo, html="", nome="Rilievo", rispondi_a=""):
    """Torna True se la mail e' partita (o e' stata messa da parte nei collaudi)."""
    caselle = _caselle()
    if not caselle:
        d = pathlib.Path(archivio.CARTELLA) / "posta-in-uscita"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%d-%s.json" % (int(time.time() * 1000), a.split("@")[0][:20]))).write_text(
            json.dumps({"a": a, "oggetto": oggetto, "testo": testo, "nome": nome, "rispondi_a": rispondi_a}, ensure_ascii=False), encoding="utf-8")
        print("  posta spenta (mancano RILIEVO_POSTA_UTENTE e RILIEVO_POSTA_PASSWORD): mail messa da parte")
        return True
    for utente, chiave, server in caselle:
        m = EmailMessage()
        m["From"] = formataddr((str(nome or "Rilievo")[:80], utente))
        if rispondi_a:
            m["Reply-To"] = rispondi_a
        m["To"] = a
        m["Subject"] = oggetto
        m["Message-ID"] = make_msgid(domain=utente.split("@")[-1])
        m.set_content(testo)
        if html:
            m.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP_SSL(server, 465, context=ssl.create_default_context(), timeout=20) as s:
                s.login(utente, chiave)
                s.send_message(m)
            return True
        except Exception as e:              # noqa: BLE001
            print("  posta non partita da %s: %s" % (server, type(e).__name__))
    return False
