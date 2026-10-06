#!/usr/bin/env python3
"""Prepara sw.js: elenca tutti i file dell'app e dei contenuti e calcola la VERSIONE
(impronta dei file). Da rilanciare dopo ogni modifica, prima di caricare l'app."""
import hashlib, json, pathlib

QUI = pathlib.Path(__file__).resolve().parent
FISSI = ["index.html", "app.js", "stile.css", "manifest.webmanifest", "icona-180.png", "icona-192.png", "icona-512.png"]
contenuti = sorted(str(p.relative_to(QUI)) for p in (QUI / "contenuti").rglob("*.json"))
tutti = FISSI + contenuti
h = hashlib.sha256()
for f in tutti:
    h.update(f.encode()); h.update((QUI / f).read_bytes())
versione = h.hexdigest()[:12]
modello = (QUI / "sw.js.modello").read_text(encoding="utf-8")
sw = modello.replace("__VERSIONE__", versione).replace("__FILE__", json.dumps(["./"] + tutti, indent=1))
(QUI / "sw.js").write_text(sw, encoding="utf-8")
print("sw.js pronto: versione", versione, "·", len(tutti), "file")
