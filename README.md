# MEXC Hourly Strong-Level Scanner

Scanner automatico dei perpetual Futures lineari USDT su MEXC, timeframe 1H.

## Funzionamento

- Recupera l'universo completo tramite gli endpoint pubblici ufficiali MEXC Futures.
- Applica i filtri di stato, età del listing, liquidità, spread, open interest, fair/index price e freschezza.
- Scarica fino a 760 candele 1H e utilizza esclusivamente candele completate.
- Classifica il regime di `BTC_USDT` con EMA, RSI, ATR, ADX/DMI, rendimenti e struttura swing.
- Cerca soltanto Strong Low LONG con BTC bullish o Strong High SHORT con BTC bearish.
- Applica veto di volatilità, conferma a candela chiusa, reward/risk minimo 2R e filtro di correlazione.
- Non invia ordini, non accede al conto e non utilizza API key.

## Pianificazione

GitHub Actions avvia lo scanner al minuto 7 di ogni ora. GitHub può ritardare leggermente i workflow pianificati nei momenti di carico.

È inoltre disponibile l'avvio manuale da **Actions → MEXC Hourly Scan → Run workflow**.

## Output

- `output/latest_report.json`: rapporto completo machine-readable.
- `output/latest_report.md`: rapporto leggibile in italiano.
- `output/status.json`: stato compatto per le automazioni.
- `output/previous_report.json`: scansione precedente, quando disponibile.

Un errore dei dati pubblici produce sempre `NO_TRADE` con valori di mercato mancanti impostati a `null`: lo scanner non inventa dati.

## Sicurezza

Il progetto usa soltanto richieste GET verso `https://api.mexc.com`. Non contiene credenziali, chiavi private, funzioni di trading o raccomandazioni sulla leva.

Il risultato è un'analisi tecnica automatizzata, non una garanzia di profitto né una consulenza finanziaria.
