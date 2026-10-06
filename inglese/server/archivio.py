#!/usr/bin/env python3
"""Dove l'app inglese tiene le sue cose sul disco.

Una cartella sola: `INGLESE_ARCHIVIO` se c'e' (su Render, il disco), se no
`server/archivio` accanto al codice. Dentro:
  accessi/      un file per account (password cifrate, mai in chiaro)
  progressi/    un file per account (unita', ripasso, frasi salvate)
  insegnante/   la spesa del mese (solo numeri)
"""
import json, os, pathlib

QUI = pathlib.Path(__file__).resolve().parent
CARTELLA = pathlib.Path((os.environ.get("INGLESE_ARCHIVIO") or "").strip() or (QUI / "archivio"))


def cartella(nome):
    d = pathlib.Path(CARTELLA) / nome
    d.mkdir(parents=True, exist_ok=True)
    return d


def leggi_json(f, se_manca=None):
    try:
        return json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return se_manca


def scrivi_json(f, dati):
    """Si scrive accanto e poi si sostituisce: un file a meta' non esiste mai."""
    f = pathlib.Path(f)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)
