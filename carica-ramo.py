#!/usr/bin/env python3
"""Carica questo servizio sul ramo whatsapp-jarvis di rilievo-sportello. Il ramo non ha storia in comune con main:
un commit qui riavvia solo whatsapp-jarvis, e un commit su main non lo tocca. L'archivio e' pubblico: mai segreti qui."""
import base64, json, os, sys, urllib.error, urllib.request

REPO = "andreamariani530/rilievo-sportello"
RAMO = "whatsapp-jarvis"
QUI = os.path.dirname(os.path.abspath(__file__))
FILE = [".gitignore", "package.json", "package-lock.json", "cerotti.mjs", "motore.js", "servizio.js", "server.js",
        "collauda-servizio.mjs", "carica-ramo.py"]
CHIAVE = open(os.path.expanduser("~/.knowlee/segreti/rilievo-chiave-github"), encoding="utf-8").read().strip()


def chiama(percorso, dati=None, metodo=None):
    corpo = json.dumps(dati).encode() if dati is not None else None
    r = urllib.request.Request("https://api.github.com/repos/%s/%s" % (REPO, percorso), data=corpo, method=metodo or ("POST" if dati else "GET"),
                               headers={"Authorization": "Bearer " + CHIAVE, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as x:
        return json.loads(x.read().decode() or "{}")


try:
    testa = chiama("git/ref/heads/" + RAMO)["object"]["sha"]
except urllib.error.HTTPError as e:
    if e.code != 404:
        raise
    testa = None
voci = []
for n in FILE:
    with open(os.path.join(QUI, n), "rb") as f:
        b = chiama("git/blobs", {"content": base64.b64encode(f.read()).decode(), "encoding": "base64"})
    voci.append({"path": n, "mode": "100644", "type": "blob", "sha": b["sha"]})
albero = chiama("git/trees", {"tree": voci})   # senza base_tree: il ramo contiene solo questi file
nuovo = chiama("git/commits", {"message": sys.argv[1] if len(sys.argv) > 1 else "Aggiornamento whatsapp-jarvis", "tree": albero["sha"], "parents": [testa] if testa else []})
if testa:
    chiama("git/refs/heads/" + RAMO, {"sha": nuovo["sha"], "force": False}, metodo="PATCH")
else:
    chiama("git/refs", {"ref": "refs/heads/" + RAMO, "sha": nuovo["sha"]})
print("ramo", RAMO, "commit", nuovo["sha"][:10])
