#!/usr/bin/env python3
"""Il server dell'app inglese.

Fa tre cose:
  1. da' i file dell'app (la cartella sopra a questa: index.html, app.js, contenuti/...);
  2. accesso con mail e password, e salvataggio dei progressi per persona;
  3. l'insegnante (Claude Haiku), con il tetto di spesa del mese.

Per provarlo sul computer:   python3 server.py            (http://127.0.0.1:8090)
Su Render:                   python3 server/server.py --servizio   (porta da PORT)

Tutta la logica sta in `gestisci()`, che non tocca la rete: i collaudi la chiamano
direttamente. La classe `Sportello` la collega soltanto a HTTP.
"""
import argparse, http.server, json, mimetypes, os, pathlib, sys, urllib.parse

QUI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(QUI))

import accessi, insegnante, progressi        # noqa: E402

APP = QUI.parent
MAX_CORPO = 3 * 1024 * 1024
TIPI = {".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".webmanifest": "application/manifest+json", ".png": "image/png", ".svg": "image/svg+xml"}
SEGRETI = ("server", "collaudi", "node_modules")


def _json(codice, dati):
    return codice, "application/json; charset=utf-8", json.dumps(dati, ensure_ascii=False).encode("utf-8")


def file_statico(percorso):
    """Il file dell'app che corrisponde all'indirizzo, oppure None. Mai fuori dalla cartella
    dell'app, mai il codice del server o i collaudi."""
    p = urllib.parse.unquote(percorso or "/").split("?")[0]
    if p in ("", "/"):
        p = "/index.html"
    rel = pathlib.PurePosixPath(p.lstrip("/"))
    if any(x in ("..", "") or x.startswith(".") for x in rel.parts) or not rel.parts:
        return None
    if rel.parts[0] in SEGRETI or rel.suffix in (".py", ".yaml", ".modello", ".md", ".txt"):
        return None
    f = (APP / rel).resolve()
    try:
        f.relative_to(APP)
    except ValueError:
        return None
    return f if f.is_file() else None


def _chi(intestazioni):
    h = {k.lower(): v for k, v in (intestazioni or {}).items()}
    return accessi.chi_e(h.get("x-mail", ""), h.get("x-gettone", ""))


def gestisci(metodo, percorso, corpo=None, intestazioni=None, ip=""):
    """Torna (codice, tipo, byte). `corpo` e' gia' il JSON aperto (o None)."""
    u = urllib.parse.urlparse(percorso)
    via = u.path
    corpo = corpo if isinstance(corpo, dict) else {}

    if metodo == "GET" and via == "/ci-sei":
        return _json(200, {"ok": True})

    if via.startswith("/api/"):
        if metodo == "POST" and via == "/api/registra":
            g, no, c = accessi.registra(corpo.get("mail"), corpo.get("password"))
            return _json(c, {"gettone": g, "mail": accessi.mail_pulita(corpo.get("mail"))} if g else {"errore": no})
        if metodo == "POST" and via == "/api/entra":
            g, no, c = accessi.entra(corpo.get("mail"), corpo.get("password"), ip)
            if not g:
                if no == "NESSUN_ACCOUNT":
                    return _json(c, {"errore": "Con questa mail non c'è un accesso.", "nessun_account": True})
                return _json(c, {"errore": no})
            m = accessi.mail_pulita(corpo.get("mail"))
            return _json(200, {"gettone": g, "mail": m, "progressi": progressi.leggi(m)})
        chi = _chi(intestazioni)
        if not chi:
            return _json(401, {"errore": "Devi entrare di nuovo con mail e password."})
        if metodo == "POST" and via == "/api/esci":
            h = {k.lower(): v for k, v in (intestazioni or {}).items()}
            accessi.esci(chi, h.get("x-gettone", ""))
            return _json(200, {"ok": True})
        if via == "/api/progressi":
            if metodo == "GET":
                return _json(200, {"progressi": progressi.leggi(chi)})
            if metodo == "POST":
                c, d = progressi.salva(chi, corpo.get("progressi"), bool(corpo.get("forza")))
                return _json(c, d)
        if via == "/api/insegnante":
            if metodo == "GET":
                return _json(200, insegnante.stato())
            if metodo == "POST":
                c, d = insegnante.rispondi(corpo.get("modo"), corpo.get("testo"),
                                           corpo.get("livello"), corpo.get("storia"))
                return _json(c, d)
        return _json(404, {"errore": "Non c'è."})

    if metodo == "GET":
        f = file_statico(via)
        if not f:
            return 404, "text/plain; charset=utf-8", "Non c'è.".encode("utf-8")
        tipo = TIPI.get(f.suffix) or mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        return 200, tipo, f.read_bytes()
    return _json(405, {"errore": "Non si può."})


class Sportello(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _rispondi(self, codice, tipo, dati):
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dati)))
        if tipo.startswith("text/html") or self.path.split("?")[0].endswith("sw.js"):
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(dati)

    def _ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def do_GET(self):
        self._rispondi(*gestisci("GET", self.path, None, dict(self.headers), self._ip()))

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        corpo = None
        if 0 < n <= MAX_CORPO:
            try:
                corpo = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:                       # noqa: BLE001
                corpo = None
        if corpo is None:
            self._rispondi(*_json(400, {"errore": "Richiesta non leggibile."}))
            return
        self._rispondi(*gestisci("POST", self.path, corpo, dict(self.headers), self._ip()))


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--porta", type=int, default=8090)
    a.add_argument("--servizio", action="store_true",
                   help="su Render: ascolta su tutte le reti e prende la porta da PORT")
    o = a.parse_args()
    dove, porta = "127.0.0.1", o.porta
    if o.servizio:
        dove, porta = "0.0.0.0", int(os.environ.get("PORT") or porta)
    s = http.server.ThreadingHTTPServer((dove, porta), Sportello)
    print("App inglese su http://%s:%d" % (dove, porta))
    s.serve_forever()


if __name__ == "__main__":
    main()
