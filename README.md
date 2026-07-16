# MEXC 4-Hour Strong-Level Scanner

Scanner automatico dei perpetual Futures lineari USDT su MEXC, timeframe 1H.

## Funzionamento

- Recupera l'universo completo tramite gli endpoint pubblici ufficiali MEXC Futures.
- Applica i filtri di stato, età del listing, liquidità, spread, open interest, fair/index price e freschezza.
- Scarica fino a 760 candele 1H e utilizza esclusivamente candele completate.
- Classifica il regime di `BTC_USDT` con EMA, RSI, ATR, ADX/DMI, rendimenti e struttura swing.
- Replica la macchina a stati Strong/Weak High/Low del Pine Script open-source
  `Smart Money Concepts [LuxAlgo]`, con swing length 50 e sole candele 1H chiuse.
- Elenca separatamente i cinque Strong Low e i cinque Strong High più vicini.
- Usa il regime BTC soltanto per assegnare priorità: i livelli contrari al bias
  restano visibili e la decisione LONG/SHORT spetta sempre all'utente.
- Applica veto di volatilità, conferma a candela chiusa, reward/risk minimo 2R e filtro di correlazione.
- Non invia ordini, non accede al conto e non utilizza API key.

## Pianificazione

GitHub Actions avvia lo scanner ogni 4 ore, al minuto 37, nel fuso `Europe/Rome`. Il controllo automatico del rapporto avviene al minuto 58, lasciando tempo al workflow di completarsi.

È inoltre disponibile l'avvio manuale da **Actions → MEXC 4-Hour Scan → Run workflow**.

## Output

- `output/latest_report.json`: rapporto completo machine-readable.
- `output/latest_report.md`: rapporto leggibile in italiano.
- `output/status.json`: stato compatto per le automazioni.
- `output/previous_report.json`: scansione precedente, quando disponibile.

`nearby_strong_levels` contiene esclusivamente livelli Strong entro 1,50 ATR;
`nearest_strong_lows` e `nearest_strong_highs` restano distinti dai setup
qualificati. Un livello trovato non costituisce automaticamente un ingresso.

Un errore dei dati pubblici produce sempre `NO_TRADE` con valori di mercato mancanti impostati a `null`: lo scanner non inventa dati.

## Sicurezza

Il progetto usa soltanto richieste GET verso `https://api.mexc.com`. Non contiene credenziali, chiavi private, funzioni di trading o raccomandazioni sulla leva.

Il risultato è un'analisi tecnica automatizzata, non una garanzia di profitto né una consulenza finanziaria.

## Attribuzione Strong/Weak

La logica Strong/Weak High/Low è adattata da **Smart Money Concepts [LuxAlgo]**,
© LuxAlgo, distribuito con licenza
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
La porta Python mantiene attribuzione, uso non commerciale e condivisione con la
stessa licenza per la parte derivata.
