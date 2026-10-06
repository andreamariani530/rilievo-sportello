#!/usr/bin/env python3
"""I progressi di ogni persona: un file per account in `archivio/progressi/`.

Il telefono manda tutto il suo stato (livello, unita' superate, ripasso, frasi
salvate) e il server lo tiene. Su un telefono nuovo si entra e si riprende.
Vince la copia con `aggiornato` piu' recente: se arriva una copia piu' vecchia di
quella che c'e', il server la rifiuta e rimanda la sua (codice 409), cosi' un
telefono rimasto spento non cancella il lavoro fatto su un altro.
"""
import threading, time

import accessi
import archivio

MAX_BYTE = 2 * 1024 * 1024
_chiave = threading.RLock()


def _file(mail):
    return archivio.cartella("progressi") / (accessi.id_account(mail) + ".json")


def leggi(mail):
    return archivio.leggi_json(_file(mail))


def salva(mail, dati, forza=False):
    """Torna (codice, risposta)."""
    if not isinstance(dati, dict):
        return 400, {"errore": "Progressi non leggibili."}
    try:
        quando = float(dati.get("aggiornato") or 0)
    except (TypeError, ValueError):
        quando = 0
    with _chiave:
        ora = leggi(mail)
        if ora and not forza and float(ora.get("aggiornato") or 0) > quando:
            return 409, {"errore": "Sul server c'è una copia più recente.", "progressi": ora}
        dati = dict(dati)
        dati["salvato_dal_server"] = int(time.time())
        archivio.scrivi_json(_file(mail), dati)
    return 200, {"ok": True, "salvato": dati["salvato_dal_server"]}
