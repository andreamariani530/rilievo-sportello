#!/usr/bin/env python3
"""Le foto che manda il cliente (13 novembre 2026, approvato da Andrea alle 11:58).

PERCHE' ESISTE
Dal giro «foto e file» del 13/11: il cliente che chiede un preventivo dal link della ditta
(/chiedi/<codice>) o che risponde «Vorrei cambiare qualcosa» sul preventivo col link
(/p/<link>) non poteva far vedere niente. Una foto del posto spiega piu' di tre righe.

COME FUNZIONA
  - Fino a 3 foto. Le rimpicciolisce il telefono del cliente (lato 1400, JPEG), con un
    solo piccolo script permesso per impronta esatta (CSP sha256): tutto il resto della
    pagina resta senza script. Senza script il riquadro delle foto non compare.
  - Il server accetta solo JPEG e PNG veri (si guardano i primi byte e Pillow ne legge
    l'intestazione), al massimo MAX_FOTO byte l'una.
  - Si scrivono subito sul disco, in RILIEVO_ARCHIVIO/foto-clienti/ (su Render e' il disco
    /var/data/rilievo): il server ha 512 MB e non le tiene in memoria. Si servono a pezzi.
  - Ogni foto ha un nome casuale di 24 segni: lo conosce solo l'app dell'artigiano.
  - Dopo 30 giorni si cancellano da sole (pulisci(), all'avvio e ogni sei ore).
  - Nei registri solo numeri: quante foto e quanti KB.
"""
import base64, binascii, hashlib, io, os, pathlib, re, secrets, threading, time

import archivio

MAX_FOTO = 1536 * 1024            # una foto da 1400 px in JPEG pesa 150-600 KB
MAX_QUANTE = 3
GIORNI = 30
LATO_MAX = 4000                   # piu' grande di cosi' non arriva dal telefono
_NOME = re.compile(r"[A-Za-z0-9_-]{24}\.(jpg|png)")
_chiave = threading.RLock()


def cartella():
    d = pathlib.Path(archivio.CARTELLA) / "foto-clienti"
    d.mkdir(parents=True, exist_ok=True)
    return d


def nome_buono(n):
    return bool(_NOME.fullmatch(str(n or "")))


def _tipo(dati):
    if dati[:3] == b"\xff\xd8\xff":
        return "jpg"
    if dati[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


def _intestazione_buona(dati, tipo):
    """Pillow legge solo l'intestazione: dimensioni e formato, senza aprire tutta la foto."""
    try:
        from PIL import Image
    except Exception:                       # noqa: BLE001
        return True                         # senza Pillow bastano i primi byte
    try:
        with Image.open(io.BytesIO(dati)) as im:
            fmt = (im.format or "").upper()
            w, h = im.size
    except Exception:                       # noqa: BLE001
        return False
    if (tipo == "jpg" and fmt != "JPEG") or (tipo == "png" and fmt != "PNG"):
        return False
    return 0 < w <= LATO_MAX and 0 < h <= LATO_MAX


def leggi_dal_modulo(campi):
    """Dalle caselle foto1..foto3 del modulo. Torna (lista di (byte, tipo), errore)."""
    prese = []
    for k in ("foto1", "foto2", "foto3"):
        v = str(campi.get(k) or "").strip()
        if not v:
            continue
        m = re.fullmatch(r"data:image/(jpeg|png);base64,([A-Za-z0-9+/=\s]+)", v)
        if not m:
            return [], "Una foto non si legge. Scegline un'altra, in JPG o PNG."
        if len(m.group(2)) > MAX_FOTO * 4 // 3 + 16:
            return [], "Una foto è troppo grande. Scegline un'altra."
        try:
            dati = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=True)
        except (binascii.Error, ValueError):
            return [], "Una foto non si legge. Scegline un'altra, in JPG o PNG."
        tipo = _tipo(dati)
        if not tipo or len(dati) > MAX_FOTO or len(dati) < 200 or not _intestazione_buona(dati, tipo):
            return [], "Una foto non si legge. Scegline un'altra, in JPG o PNG."
        prese.append((dati, tipo))
    if len(prese) > MAX_QUANTE:
        return [], "Al massimo 3 foto."
    return prese, ""


def salva(prese):
    """Scrive le foto sul disco e torna i loro nomi. Le foto non restano in memoria."""
    nomi = []
    d = cartella()
    for dati, tipo in prese[:MAX_QUANTE]:
        nome = secrets.token_urlsafe(18)[:24] + "." + tipo
        while not nome_buono(nome) or (d / nome).exists():
            nome = secrets.token_urlsafe(18)[:24] + "." + tipo
        tmp = d / (nome + ".tmp")
        with open(tmp, "wb") as f:
            f.write(dati)
        os.replace(tmp, d / nome)
        nomi.append(nome)
    if nomi:
        print("  foto dal cliente: %d (%d KB)" % (len(nomi), sum(len(x[0]) for x in prese) // 1024))
    return nomi


def togli(nomi):
    for n in nomi or []:
        if nome_buono(n):
            try:
                (cartella() / n).unlink()
            except OSError:
                pass


def percorso(nome):
    """Il file da servire, se c'e' e non e' scaduto. None altrimenti."""
    if not nome_buono(nome):
        return None
    f = cartella() / nome
    try:
        st = f.stat()
    except OSError:
        return None
    if time.time() - st.st_mtime > GIORNI * 86400:
        return None
    return f


def nomi_buoni(lista):
    return [str(n) for n in (lista if isinstance(lista, list) else [])[:MAX_QUANTE] if nome_buono(n)]


def pulisci(adesso=None):
    """Cancella le foto piu' vecchie di 30 giorni (e i .tmp rimasti a meta'). Torna quante."""
    adesso = adesso or time.time()
    via = 0
    with _chiave:
        try:
            elenco = list(cartella().iterdir())
        except OSError:
            return 0
        for f in elenco:
            try:
                vecchio = adesso - f.stat().st_mtime
                if (f.suffix == ".tmp" and vecchio > 3600) or vecchio > GIORNI * 86400:
                    f.unlink()
                    via += 1
            except OSError:
                pass
    if via:
        print("  foto dal cliente cancellate dopo %d giorni: %d" % (GIORNI, via))
    return via


def pulizia_continua(ogni=6 * 3600):
    """All'avvio e poi ogni sei ore, in un filo suo."""
    def gira():
        while True:
            try:
                pulisci()
            except Exception as e:          # noqa: BLE001
                print("  pulizia delle foto dal cliente non riuscita:", type(e).__name__)
            time.sleep(ogni)
    threading.Thread(target=gira, daemon=True).start()


# ---- il riquadro sulla pagina del cliente -----------------------------------
# Lo script e' l'unico permesso sulla pagina, per impronta esatta: se cambia anche una
# virgola, cambia l'impronta in REGOLE_CON_FOTO e il browser lo blocca. Non legge niente
# fuori dal modulo, non chiama nessuno: rimpicciolisce le foto scelte e le mette nelle
# caselle nascoste foto1..foto3.
SCRIPT = r"""
(function(){
  var box = document.getElementById('rl-foto'); if(!box) return;
  box.hidden = false;
  var scegli = document.getElementById('rl-scegli'), viste = document.getElementById('rl-viste'),
      detto = document.getElementById('rl-detto'), caselle = [1, 2, 3].map(function(i){ return document.getElementById('rl-f' + i); });
  function disegna(){
    viste.innerHTML = '';
    caselle.forEach(function(c, i){
      if(!c.value) return;
      var f = document.createElement('figure'), im = document.createElement('img'), b = document.createElement('button');
      im.src = c.value; im.alt = 'Foto ' + (i + 1);
      b.type = 'button'; b.textContent = 'Togli'; b.className = 'rl-togli';
      b.addEventListener('click', function(){ c.value = ''; detto.textContent = ''; compatta(); disegna(); });
      f.appendChild(im); f.appendChild(b); viste.appendChild(f);
    });
    var n = caselle.filter(function(c){ return c.value; }).length;
    scegli.disabled = n >= 3;
    document.getElementById('rl-tasto').textContent = n ? (n >= 3 ? 'Hai messo 3 foto' : 'Aggiungi un\'altra foto') : 'Aggiungi una foto';
  }
  function compatta(){
    var v = caselle.map(function(c){ return c.value; }).filter(Boolean);
    caselle.forEach(function(c, i){ c.value = v[i] || ''; });
  }
  function piccola(file){
    return new Promise(function(ok){
      if(!file || !/^image\//.test(file.type || '')){ ok(null); return; }
      var r = new FileReader();
      r.onerror = function(){ ok(null); };
      r.onload = function(){
        var im = new Image();
        im.onerror = function(){ ok(null); };
        im.onload = function(){
          var k = Math.min(1, 1400 / Math.max(im.width, im.height)), c = document.createElement('canvas');
          c.width = Math.max(1, Math.round(im.width * k)); c.height = Math.max(1, Math.round(im.height * k));
          var g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height);
          g.drawImage(im, 0, 0, c.width, c.height);
          ok(c.toDataURL('image/jpeg', 0.72));
        };
        im.src = r.result;
      };
      r.readAsDataURL(file);
    });
  }
  scegli.addEventListener('change', function(){
    var files = Array.prototype.slice.call(scegli.files || []); scegli.value = '';
    var liberi = caselle.filter(function(c){ return !c.value; }).length;
    detto.textContent = files.length > liberi ? 'Al massimo 3 foto: ho preso le prime.' : (files.length ? 'Preparo le foto…' : '');
    var fuori = 0;
    files.slice(0, liberi).reduce(function(p, f){
      return p.then(function(){ return piccola(f).then(function(s){
        if(!s){ fuori++; return; }
        var c = caselle.filter(function(x){ return !x.value; })[0]; if(c) c.value = s;
      }); });
    }, Promise.resolve()).then(function(){
      disegna();
      if(fuori) detto.textContent = fuori === 1 ? 'Una foto non si apre: scegline un\'altra, in JPG.' : fuori + ' foto non si aprono: scegline altre, in JPG.';
      else if(detto.textContent === 'Preparo le foto…') detto.textContent = '';
    });
  });
  disegna();
})();
""".strip()

IMPRONTA_SCRIPT = "sha256-" + base64.b64encode(hashlib.sha256(SCRIPT.encode("utf-8")).digest()).decode()

STILE = """
<style>
.rl-foto{margin:16px 0 4px}
.rl-foto[hidden]{display:none}
.rl-foto .rl-scegli{position:relative;display:inline-flex;align-items:center;min-height:48px;padding:0 20px;border:1px solid #2B2822;background:#fff;color:#2B2822;font-size:16px;cursor:pointer}
.rl-foto .rl-scegli input{position:absolute;inset:0;opacity:0;width:100%;height:100%;cursor:pointer}
.rl-foto .rl-scegli:has(input:disabled){opacity:.5}
.rl-viste{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
.rl-viste figure{margin:0;width:96px}
.rl-viste img{display:block;width:96px;height:96px;object-fit:cover;border:1px solid #BDB4A3}
.rl-viste .rl-togli{margin:2px 0 0;padding:0;min-height:44px;width:100%;font-size:14px;background:none;border:0;color:#9A4B3A;text-decoration:underline}
.rl-foto .rl-detto{min-height:1em;color:#9A4B3A;margin:8px 0 0;font-size:15px}
.rl-foto .rl-tenute{font-size:13px;color:#7A746A;margin:8px 0 0}
</style>"""


def riquadro(valori=None, testo="Se vuoi, aggiungi fino a 3 foto: aiutano a capire il lavoro."):
    """Il riquadro delle foto per un modulo. valori: le caselle gia' riempite (dopo un errore)."""
    v = valori or {}
    caselle = ""
    for i in (1, 2, 3):
        x = str(v.get("foto%d" % i) or "")
        x = x if re.fullmatch(r"data:image/(jpeg|png);base64,[A-Za-z0-9+/=]+", x) else ""
        caselle += f'<input type="hidden" name="foto{i}" id="rl-f{i}" value="{x}">'
    return (f'{STILE}<div class="rl-foto" id="rl-foto" hidden><p style="margin:0 0 10px">{testo}</p>'
            f'<label class="rl-scegli"><span id="rl-tasto">Aggiungi una foto</span>'
            f'<input type="file" id="rl-scegli" accept="image/jpeg,image/png,image/*" multiple></label>'
            f'<div class="rl-viste" id="rl-viste"></div><p class="rl-detto" id="rl-detto" aria-live="polite"></p>'
            f'<p class="rl-tenute">Le foto restano 30 giorni, poi si cancellano.</p>{caselle}</div>'
            f'<script id="rl-foto-script">{SCRIPT}</script>')
