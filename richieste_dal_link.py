#!/usr/bin/env python3
"""Le richieste che arrivano dal link della ditta (4 ottobre 2026).

PERCHE' ESISTE
Idea presa a Jobber, Deep Lawn, ServiceM8 e PrevAI, scelta da Andrea il 4/10: ogni ditta
ha un suo indirizzo, `/chiedi/<codice>`, da mettere sul sito, su WhatsApp, sul furgone.
Il cliente scrive nome, telefono, indirizzo e cosa gli serve; la richiesta arriva
nell'app dell'artigiano (in Richieste) e gli arriva una mail. Clienti nuovi mentre
lavora o dorme, senza telefonate perse nel furgone.

COME FUNZIONA
  - Il link si fa solo con l'accesso (mail e password): il server sa a chi portare
    la richiesta. Un codice per ditta; «Fai un link nuovo» spegne quello vecchio.
  - Le richieste aspettano in una cassetta per account (`richieste/<account>.json`),
    NON dentro la copia del lavoro: cosi' non litigano con i telefoni che salvano.
    L'app le prende, le mette in Richieste e dice al server che le ha viste.
  - La pagina del cliente non fa girare nessuno script (stessa gabbia del preventivo
    col link). Solo un modulo normale.

IL PREZZO INDICATIVO DALL'INDIRIZZO (passo 2, spento di base)
  Se l'artigiano lo accende e sceglie una voce del listino a metro quadro, il cliente
  scrive l'indirizzo e tocca «Vedi il prezzo indicativo»: il server fa il rilievo come per
  l'app (catasto e foto dall'alto) e mostra lo spazio scoperto del lotto e una forbice del
  15% intorno a «voce x metri», IVA compresa, con la frase fissa che lo conferma la ditta
  dopo il sopralluogo. Se il catasto non e' sicuro (civico sulla strada, niente
  particella) il prezzo non si mostra: meglio niente che un numero sbagliato. La stima
  viaggia con la richiesta, cosi' l'artigiano sa cosa ha visto il cliente.
  Massimo 15 prezzi al giorno per ditta e 4 l'ora per indirizzo di rete: ogni rilievo
  chiede al catasto e alle foto.

LE DIFESE
  - massimo 20 richieste al giorno per ditta e 6 l'ora dallo stesso indirizzo di rete;
  - un campo nascosto che le persone non vedono: chi lo riempie e' un programma, e la
    sua richiesta finisce nel nulla (ma gli si risponde «grazie», cosi' non riprova);
  - testi tagliati a misura, niente HTML che passi.
"""
import hashlib, html, json, os, pathlib, re, secrets, threading, time

import archivio

_chiave = threading.RLock()
_ALFABETO = "abcdefghijkmnpqrstuvwxyz23456789"
MAX_GIORNO = 20
MAX_ORA_IP = 6
MAX_IN_ATTESA = 200
_per_ip = {}                        # impronta dell'indirizzo -> [istanti]
_prezzi_ip = {}                     # lo stesso, per i prezzi indicativi
_stime = {}                         # (codice, indirizzo) -> la stima mostrata, per un'ora
MAX_PREZZI_GIORNO = 15
MAX_PREZZI_ORA_IP = 4


def _cartella():
    d = pathlib.Path(archivio.CARTELLA) / "richieste"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _leggi_json(f, vuoto):
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else vuoto
    except Exception:                       # noqa: BLE001
        return vuoto


def _scrivi_json(f, dati):
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def _codici():
    return _leggi_json(_cartella() / "codici.json", {})


def _codice_buono(c):
    return bool(re.fullmatch(r"[a-z2-9]{10}", str(c or "")))


def _cassetta(account):
    return _cartella() / (re.sub(r"[^a-f0-9]", "", account)[:64] + ".json")


def prezzo_buono(x):
    """La voce del listino per il prezzo indicativo: nome e prezzo al metro quadro, IVA compresa."""
    if not isinstance(x, dict):
        return None
    nome = re.sub(r"\s+", " ", str(x.get("nome") or "")).strip()[:80]
    try:
        al_mq = round(float(x.get("al_mq")), 4)
    except (TypeError, ValueError):
        return None
    return {"nome": nome, "al_mq": al_mq} if nome and 0 < al_mq < 1000 else None


def link_di(account, mail, ditta="", tel="", rifai=False, prezzo=False):
    """Il codice della ditta: lo stesso di sempre, oppure uno nuovo che spegne il vecchio.
    prezzo=False: non si tocca; None: spento; un dict: la voce per il prezzo indicativo."""
    with _chiave:
        tutti = _codici()
        vecchio = next((c for c, v in tutti.items() if v.get("account") == account), None)
        if vecchio and rifai:
            prezzo = tutti[vecchio].get("prezzo") if prezzo is False else prezzo
        if vecchio and not rifai:
            if prezzo is not False:
                tutti[vecchio]["prezzo"] = prezzo_buono(prezzo)
            # nome e telefono si aggiornano solo se arrivano: una chiamata senza non li cancella
            if str(ditta or "").strip():
                tutti[vecchio]["ditta"] = str(ditta)[:120]
            if re.sub(r"\D", "", str(tel or "")):
                tutti[vecchio]["tel"] = re.sub(r"[^\d+ ]", "", str(tel))[:20]
            tutti[vecchio]["mail"] = mail
            _scrivi_json(_cartella() / "codici.json", tutti)
            return vecchio
        if vecchio:
            del tutti[vecchio]
        nuovo = "".join(secrets.choice(_ALFABETO) for _ in range(10))
        while nuovo in tutti:
            nuovo = "".join(secrets.choice(_ALFABETO) for _ in range(10))
        tutti[nuovo] = {"account": account, "mail": mail, "ditta": str(ditta or "")[:120],
                        "tel": re.sub(r"[^\d+ ]", "", str(tel or ""))[:20], "creato": int(time.time()), "giorni": {},
                        "prezzo": prezzo_buono(prezzo) if prezzo is not False else None}
        _scrivi_json(_cartella() / "codici.json", tutti)
        return nuovo


def di_chi(codice):
    if not _codice_buono(codice):
        return None
    return _codici().get(codice)


def _pulito(x, n):
    return re.sub(r"\s+", " ", str(x or "")).strip()[:n]


def ricevi(codice, campi, ip=""):
    """Una richiesta dal modulo. Torna (stato, messaggio, chi) con stato 'ok', 'errore' o 'finto'."""
    ditta = di_chi(codice)
    if not ditta:
        return "errore", "Questo link non funziona piu'. Chiedi alla ditta quello nuovo.", None
    if _pulito(campi.get("sito"), 200):
        return "finto", "", None              # il campo nascosto: solo un programma lo riempie
    nome, tel = _pulito(campi.get("nome"), 80), _pulito(campi.get("tel"), 30)
    indirizzo, cosa = _pulito(campi.get("indirizzo"), 200), str(campi.get("cosa") or "").strip()[:1000]
    if len(nome) < 2:
        return "errore", "Scrivi il tuo nome.", None
    if len(re.sub(r"\D", "", tel)) < 6:
        return "errore", "Scrivi un numero di telefono: la ditta ti richiama lì.", None
    if len(cosa) < 3:
        return "errore", "Scrivi in due parole cosa ti serve.", None
    adesso = time.time()
    chi_ip = hashlib.sha256(("ip:" + str(ip or "")).encode()).hexdigest()[:16]
    with _chiave:
        recenti = [t for t in _per_ip.get(chi_ip, []) if adesso - t < 3600]
        if len(recenti) >= MAX_ORA_IP:
            return "errore", "Hai mandato tante richieste in poco tempo. Riprova fra un'ora, o chiama la ditta.", None
        tutti = _codici()
        d = tutti.get(codice)
        oggi = time.strftime("%Y-%m-%d", time.gmtime(adesso))
        giorni = {k: v for k, v in (d.get("giorni") or {}).items() if k == oggi}
        if giorni.get(oggi, 0) >= MAX_GIORNO:
            return "errore", "Oggi la ditta ha ricevuto tante richieste. Riprova domani, o chiamala.", None
        giorni[oggi] = giorni.get(oggi, 0) + 1
        d["giorni"] = giorni
        _scrivi_json(_cartella() / "codici.json", tutti)
        _per_ip[chi_ip] = recenti + [adesso]
        f = _cassetta(d["account"])
        attesa = _leggi_json(f, [])[-(MAX_IN_ATTESA - 1):]
        nuova = {"id": "rl" + secrets.token_hex(6), "quando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(adesso)),
                 "nome": nome, "tel": tel, "indirizzo": indirizzo, "cosa": cosa}
        st = _stime.get((codice, _chiave_ind(indirizzo)))
        if st and adesso - st["quando"] < 3600:
            nuova["stima"] = {k: st[k] for k in ("mq", "da", "a", "voce")}
        attesa.append(nuova)
        _scrivi_json(f, attesa)
    return "ok", "", {"ditta": d, "richiesta": nuova}


def _chiave_ind(x):
    return re.sub(r"[^a-z0-9]", "", str(x or "").lower())[:120]


def _a_cinque(x):
    return int(round(x / 5.0)) * 5 or 5


def puo_fare_prezzo(codice, ip=""):
    """(True, '') se si puo' calcolare un altro prezzo, se no (False, cosa dire)."""
    d = di_chi(codice)
    if not d or not d.get("prezzo"):
        return False, ""
    adesso = time.time()
    chi_ip = hashlib.sha256(("ip:" + str(ip or "")).encode()).hexdigest()[:16]
    with _chiave:
        recenti = [t for t in _prezzi_ip.get(chi_ip, []) if adesso - t < 3600]
        if len(recenti) >= MAX_PREZZI_ORA_IP:
            return False, "Hai chiesto tanti prezzi in poco tempo. Manda la richiesta: la ditta ti fa sapere."
        tutti = _codici()
        x = tutti.get(codice)
        oggi = time.strftime("%Y-%m-%d", time.gmtime(adesso))
        conta = {k: v for k, v in (x.get("prezzi_giorni") or {}).items() if k == oggi}
        if conta.get(oggi, 0) >= MAX_PREZZI_GIORNO:
            return False, "Per oggi i prezzi indicativi sono finiti. Manda la richiesta: la ditta ti fa sapere."
        conta[oggi] = conta.get(oggi, 0) + 1
        x["prezzi_giorni"] = conta
        _scrivi_json(_cartella() / "codici.json", tutti)
        _prezzi_ip[chi_ip] = recenti + [adesso]
    return True, ""


def stima_dal_rilievo(codice, indirizzo, r):
    """Dal rilievo alla forbice di prezzo. None se il rilievo non e' sicuro."""
    d = di_chi(codice)
    if not d or not d.get("prezzo") or not isinstance(r, dict):
        return None
    if r.get("da_disegnare") or r.get("catasto_incerto"):
        return None
    try:
        mq = int(round(float(r.get("scoperto_mq") or 0)))
    except (TypeError, ValueError):
        return None
    if mq < 20 or mq > 20000:
        return None
    centro = mq * d["prezzo"]["al_mq"]
    st = {"mq": mq, "da": _a_cinque(centro * 0.85), "a": _a_cinque(centro * 1.15), "voce": d["prezzo"]["nome"],
          "quando": time.time()}
    with _chiave:
        if len(_stime) > 2000:
            _stime.clear()
        _stime[(codice, _chiave_ind(indirizzo))] = st
    return st


def in_attesa(account):
    with _chiave:
        return _leggi_json(_cassetta(account), [])


def viste(account, ids):
    ids = {str(x) for x in (ids or [])[:500]}
    with _chiave:
        f = _cassetta(account)
        resta = [r for r in _leggi_json(f, []) if r.get("id") not in ids]
        _scrivi_json(f, resta)
        return len(resta)


# ---- la pagina che vede il cliente -------------------------------------------
_STILE = """
<style>
/* nomi con rl-: lo stile dell'app viaggia con la pagina, e lì «.chiedi» e' il tasto della chat */
html,body{background:#F4EFE6;margin:0}
.rl-pagina{max-width:620px;margin:0 auto;padding:26px 20px 60px;font-family:Georgia,serif;color:#2B2822}
.rl-pagina .chi{font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#6E7F66}
.rl-pagina h1{font-family:'Cormorant',Georgia,serif;font-weight:400;font-size:32px;margin:4px 0 6px}
.rl-pagina p{line-height:1.55;color:#4A463F;margin:6px 0 16px}
.rl-pagina label{display:block;margin:14px 0 4px;font-size:15px;color:#4A463F}
.rl-pagina input,.rl-pagina textarea{width:100%;box-sizing:border-box;font-size:18px;padding:13px;border:1px solid #BDB4A3;background:#fff;font-family:inherit;color:#2B2822}
.rl-pagina textarea{min-height:110px}
.rl-pagina button{margin-top:20px;font-size:18px;padding:15px 28px;border:0;background:#2B2822;color:#F4EFE6;font-family:inherit;letter-spacing:.04em}
.rl-pagina .errore{color:#9A4B3A}
.rl-pagina .fatto{font-family:'Cormorant',Georgia,serif;font-size:26px;color:#4F6B4A}
.rl-pagina .nascosto{position:absolute;left:-5000px;width:1px;height:1px;overflow:hidden}
.rl-pagina .nota{font-size:13px;color:#7A746A;margin-top:18px}
.rl-stima{margin:14px 0 4px;padding:14px 16px;border:1px solid #D9D1C2;background:#FBF8F2}
.rl-stima img{display:block;width:100%;height:auto;margin-bottom:10px}
.rl-stima .rl-mq{margin:0 0 4px}
.rl-nw{white-space:nowrap}
.rl-stima .rl-forbice{font-family:'Cormorant',Georgia,serif;font-size:24px;line-height:1.25;color:#2B2822;margin:0 0 8px}
.rl-stima .rl-nota-prezzo{font-size:14px;color:#7A746A;margin:6px 0 0}
.rl-pagina button.rl-secondario{margin-top:0;background:#fff;color:#2B2822;border:1px solid #2B2822;font-size:16px;padding:12px 20px}
.rl-pagina .contatti a{color:#2B2822;margin-right:18px}
</style>"""


def _euro_tondo(x):
    return "€ " + f"{int(x):,}".replace(",", ".")


def pagina(codice, stili_app, errore="", fatto=False, valori=None, stima=None, foto="", nota_prezzo=""):
    d = di_chi(codice)
    e = html.escape
    if not d:
        corpo = ('<div class="rl-pagina"><h1>Questo link non funziona più</h1>'
                 '<p>La ditta ne ha fatto uno nuovo. Chiedi a chi te l’ha dato quello giusto.</p></div>')
        return _intera("Richiesta", stili_app, corpo), 404
    v = valori or {}
    ditta = e(d.get("ditta") or "La ditta")
    tel = d.get("tel") or ""
    contatti = (f'<p class="contatti">Preferisci chiamare? <a href="tel:{e(re.sub(r"[^\d+]", "", tel))}">{e(tel)}</a></p>') if tel else ""
    if fatto:
        corpo = (f'<div class="rl-pagina"><div class="chi">{ditta}</div><p class="fatto">Richiesta mandata. Grazie!</p>'
                 f'<p>{ditta} l’ha già ricevuta e ti richiama al numero che hai scritto.</p>{contatti}</div>')
        return _intera("Richiesta mandata", stili_app, corpo), 200
    corpo = (f'<div class="rl-pagina"><div class="chi">{ditta}</div><h1>Chiedi un preventivo</h1>'
             f'<p>Scrivi chi sei, dove e cosa ti serve: {ditta} ti richiama per fissare un sopralluogo.</p>'
             + (f'<p class="errore">{e(errore)}</p>' if errore else '') +
             f'<form method="post" action="/chiedi/{e(codice)}">'
             f'<label for="nome">Nome e cognome</label><input id="nome" name="nome" autocomplete="name" required minlength="2" value="{e(v.get("nome", ""))}">'
             f'<label for="tel">Telefono</label><input id="tel" name="tel" type="tel" autocomplete="tel" required value="{e(v.get("tel", ""))}">'
             f'<label for="indirizzo">Indirizzo del lavoro</label><input id="indirizzo" name="indirizzo" autocomplete="street-address" '
             f'placeholder="Via, numero e paese" value="{e(v.get("indirizzo", ""))}">'
             + (_riquadro_prezzo(d, e, stima, foto, nota_prezzo) if d.get("prezzo") else "") +
             f'<label for="cosa">Cosa ti serve</label><textarea id="cosa" name="cosa" required minlength="3" '
             f'placeholder="Per esempio: tagliare il prato e la siepe, circa 300 metri">{e(v.get("cosa", ""))}</textarea>'
             f'<div class="nascosto" aria-hidden="true"><label for="sito">Lascia vuoto</label><input id="sito" name="sito" tabindex="-1" autocomplete="off"></div>'
             f'<button type="submit">Manda la richiesta</button></form>{contatti}'
             f'<p class="nota">I tuoi dati vanno solo a {ditta}, per richiamarti. Non li usiamo per altro.</p></div>')
    return _intera("Chiedi un preventivo a " + (d.get("ditta") or "la ditta"), stili_app, corpo), 200


def _riquadro_prezzo(d, e, stima, foto, nota):
    ditta = e(d.get("ditta") or "La ditta")
    voce = e(d["prezzo"]["nome"])
    if stima:
        return (f'<div class="rl-stima" id="stima">'
                + (f'<img src="{e(foto)}" alt="Il lotto visto dall\'alto">' if foto else '') +
                f'<p class="rl-mq">Spazio all’aperto del lotto: circa <b>{stima["mq"]:,} m²</b></p>'.replace(",", ".") +
                f'<p class="rl-forbice">{voce}: da circa <b class="rl-nw">{_euro_tondo(stima["da"])}</b> a <b class="rl-nw">{_euro_tondo(stima["a"])}</b>, IVA compresa.</p>'
                f'<p class="rl-nota-prezzo">Prezzo indicativo, misurato sul catasto e sulla foto dall’alto: lo conferma {ditta} dopo un sopralluogo. '
                f'Se va bene, manda la richiesta qui sotto.</p></div>')
    return (f'<div class="rl-stima">'
            + (f'<p class="rl-nota-prezzo">{e(nota)}</p>' if nota else '') +
            f'<button type="submit" name="fai" value="prezzo" formnovalidate class="rl-secondario">Vedi il prezzo indicativo</button>'
            f'<p class="rl-nota-prezzo">Per {voce.lower()}: scrivi l’indirizzo e tocca qui. Ci vuole fino a mezzo minuto: misuriamo il lotto sul catasto.</p></div>')


def _intera(titolo, stili_app, corpo):
    return ('<!doctype html><html lang="it"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow">'
            f'<title>{html.escape(titolo)}</title>{stili_app}{_STILE}</head><body>{corpo}</body></html>')


def mail_alla_ditta(d, r):
    """Oggetto e testo della mail che avvisa l'artigiano."""
    paese = (r.get("indirizzo") or "").split(",")[-1].strip()
    oggetto = "Nuova richiesta da " + r["nome"] + (", " + paese if paese else "")
    testo = ("Ti ha scritto " + r["nome"] + " dal tuo link per le richieste.\n\n"
             "Telefono: " + r["tel"] + "\n" + ("Indirizzo: " + r["indirizzo"] + "\n" if r.get("indirizzo") else "") +
             "Cosa gli serve: " + r["cosa"] + "\n" +
             ("Ha visto il prezzo indicativo: %s, da %s a %s euro (%s m2).\n" % (r["stima"]["voce"], r["stima"]["da"], r["stima"]["a"], r["stima"]["mq"])
              if r.get("stima") else "") + "\n"
             "La trovi anche in Rilievo, in Richieste, appena apri l'app.\n\nRilievo")
    return oggetto, testo
