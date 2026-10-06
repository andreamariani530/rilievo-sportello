# Formato dei contenuti

Tutti i file sono JSON in UTF-8. L'app li legge così come sono: un campo sbagliato
rompe una schermata. `collaudi/controlla-contenuti.mjs` li verifica tutti.

## Unità: `a1/unita-NN.json` (NN = 01..12)

```json
{
  "id": "a1-01",
  "livello": "A1",
  "numero": 1,
  "titolo": "Piacere, sono Andrea",
  "tema": "Salutare e presentarsi",
  "obiettivo": "Alla fine sai dire chi sei, salutare e chiedere il nome.",
  "grammatica": {
    "titolo": "Il verbo to be: I am, you are",
    "spiegazione": ["Paragrafo corto in italiano semplice.", "Altro paragrafo."],
    "tabella": [["I am", "io sono"], ["you are", "tu sei"]],
    "esempi": [{"en": "I am Andrea.", "it": "Sono Andrea."}],
    "attenzione": "L'errore tipico degli italiani, in una frase."
  },
  "parole": [
    {"en": "hello", "it": "ciao", "esempio": {"en": "Hello, I'm Tom.", "it": "Ciao, sono Tom."}}
  ],
  "dialogo": {
    "titolo": "Al bar sotto casa",
    "situazione": "Andrea incontra una collega nuova.",
    "battute": [{"nome": "Anna", "voce": "donna", "en": "Hi! I'm Anna.", "it": "Ciao! Sono Anna."}],
    "domande": [{"domanda": "Come si chiama la collega?", "scelte": ["Anna", "Sara", "Laura"], "giusta": 0}]
  },
  "esercizi": [
    {"tipo": "scelta", "domanda": "Come si dice «sono stanco»?", "scelte": ["I am tired.", "I is tired.", "I are tired."], "giusta": 0, "spiega": "Con I si usa sempre am."},
    {"tipo": "completa", "frase": "She ___ a teacher.", "risposte": ["is"], "it": "Lei è un'insegnante.", "spiega": "Con she si usa is."},
    {"tipo": "riordina", "parole": ["name", "My", "is", "Andrea"], "risposte": ["My name is Andrea."], "it": "Mi chiamo Andrea."},
    {"tipo": "traduci", "it": "Io sono italiano.", "risposte": ["I am Italian.", "I'm Italian."]},
    {"tipo": "voce", "en": "Nice to meet you.", "it": "Piacere di conoscerti."}
  ],
  "parlato": [
    {"domanda": "What's your name?", "it": "Come ti chiami?", "modello": "My name is Andrea."}
  ],
  "esame": {
    "soglia": 0.7,
    "domande": [ "10-12 esercizi dei tipi scelta, completa, riordina, traduci (niente voce)" ]
  }
}
```

Regole:
- `parole`: da 15 a 20. `esercizi`: da 12 a 16, con almeno 2 di ogni tipo.
- `parlato`: da 3 a 4 domande per i due minuti a voce.
- `esame.domande`: da 10 a 12, frasi DIVERSE da quelle degli esercizi.
- `completa`: un solo `___` nella frase; `risposte` sono le parole che vanno al posto del vuoto.
- `riordina`: `parole` sono i pezzi mescolati (la punteggiatura finale va attaccata all'ultima
  parola nella risposta, non nei pezzi); `risposte` le frasi giuste intere.
- `traduci` e `riordina`: in `risposte` tutte le forme corrette ragionevoli (con e senza
  forma contratta). L'app ignora maiuscole, punteggiatura, e scioglie da sola le forme
  contratte comuni (I'm = I am, don't = do not, ...).
- `voce` del dialogo: "uomo" o "donna" (sceglie la voce del telefono).

## Test di livello: `test-livello.json`

```json
{"domande": [
  {"id": "a1-1", "livello": "A1", "tipo": "scelta", "domanda": "...", "scelte": ["..."], "giusta": 0},
  {"id": "a1-7", "livello": "A1", "tipo": "ascolto", "en": "Frase letta dalla voce", "domanda": "Che cosa hai sentito?", "scelte": ["..."], "giusta": 2}
]}
```
Almeno 8 domande per livello (A1, A2, B1, B2), di cui 2 di ascolto.

## Percorso: `percorso.json`

```json
{"livelli": [
  {"livello": "A1", "titolo": "Primi passi", "alla_fine": "...", "unita": [
    {"numero": 1, "titolo": "...", "tema": "...", "grammatica": "...", "obiettivo": "..."}
  ]}
]}
```
A1 12 unità (gli stessi titoli dei file), A2 12, B1 14, B2 14.
