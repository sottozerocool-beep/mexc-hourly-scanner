# Core PCE Live Watcher → BTC/USDT

Watcher a bassa latenza per il rilascio **Personal Income and Outlays** del Bureau of Economic Analysis (BEA).

## Cosa fa

- parte circa due ore prima del rilascio;
- controlla la pagina ufficiale BEA ogni **2 secondi** dal momento previsto;
- usa facoltativamente la BEA Data API come secondo canale ufficiale;
- estrae il **Core PCE mensile**, evitando di confonderlo con il dato annuale;
- invia in italiano Actual, Forecast, Previous e scenario BTC;
- registra BTC/USDT prima e subito dopo il dato;
- verifica prezzo e volume a **+5 e +15 minuti**;
- crea un pull request GitHub `[PCE LIVE]`, utilizzabile come trigger per ChatGPT Work;
- non apre ordini e non usa credenziali exchange.

## Regole configurate

| Core PCE m/m Actual | BTC rialzista | BTC ribassista |
|---|---:|---:|
| 0,1% o inferiore | 65% | 35% |
| 0,2% | 52% | 48% |
| 0,3% | 30–32% | 68–70% |
| 0,4% o superiore | 20–25% | 75–80% |

Sono regole di scenario trasparenti, non probabilità garantite e non segnali di ingresso.

## Configurazione del 26 agosto 2026

`config.json` contiene già:

- rilascio: **26 agosto 2026, 12:30 UTC / 14:30 Italia**;
- forecast: **0,2%**;
- previous: **0,1%**;
- polling: **2 secondi**;
- follow-up: **+5 e +15 minuti**.

## Telegram

1. In Telegram crea un bot con `@BotFather`.
2. Apri la chat del nuovo bot e invia `/start`.
3. Da un terminale esegui `python pce_watcher/setup_telegram.py` per leggere il chat ID senza mostrare il token.
4. In GitHub apri **Settings → Secrets and variables → Actions** e crea:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

Non inserire mai il token in commit, issue o chat pubbliche.

## Permessi GitHub Actions

Apri **Settings → Actions → General → Workflow permissions** e abilita:

- **Read and write permissions**
- **Allow GitHub Actions to create and approve pull requests**

## Collegamento con ChatGPT Work

Dopo aver collegato GitHub in ChatGPT, crea una task event-triggered con questa istruzione:

> Quando nel repository `sottozerocool-beep/mexc-hourly-scanner` viene aperto o aggiornato un pull request il cui titolo contiene `[PCE LIVE]`, leggi il corpo e i commenti. Rispondi in italiano in questa chat con Actual, Forecast, Previous, probabilità BTC rialzista/ribassista, variazione BTC dal pre-release, rapporto volume e indicazione CONFERMA, INDECISA oppure SMENTITA/WHIPSAW. Non inventare dati mancanti e ricorda che non è consulenza finanziaria.

Telegram resta il canale più rapido; la task Work aggiunge la risposta ChatGPT contestualizzata.

## BEA API opzionale

Per un secondo controllo ufficiale, salva una chiave BEA come secret `BEA_API_KEY`. Il watcher usa la tabella NIPA `T20807` soltanto nella finestra del rilascio.

## Test

```bash
python -m unittest discover -s pce_watcher -p "test_*.py" -v
```

Simulazione manuale:

```bash
python pce_watcher/watcher.py live \
  --release-at "2026-08-26T10:00:00Z" \
  --test-actual 0.3
```

## Limiti

- “Ogni 2 secondi” non garantisce una notifica esattamente entro 2 secondi: incidono pubblicazione BEA, CDN, rete GitHub e Telegram.
- Il forecast non è un dato ufficiale BEA e va aggiornato prima di ogni rilascio.
- La prima candela può essere invertita; per questo il watcher invia le verifiche a +5 e +15 minuti.
