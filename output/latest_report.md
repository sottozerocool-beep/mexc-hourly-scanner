# MEXC Futures Scan

**Scan timestamp:** 2026-08-10T21:00:24Z
**Primary timeframe:** 1H
**Contracts retrieved:** 794
**Contracts eligible:** 327
**Contracts analyzed:** 324
**Contracts skipped:** 470
**Data freshness:** Valida

## BTC Regime

**BTC_USDT price:** 64,126.50
**BTC regime:** BEARISH
**BTC Regime Score:** -70
**EMA 20 / EMA 50 / EMA 200:** 64,575.57 / 64,782.45 / 64,524.54
**ADX / +DI / -DI:** 35.5751 / 11.06969 / 28.79002
**6H return:** -0.80%
**24H return:** -1.74%
**ATR volatility:** 0.407767458% | veto=False
**Permitted direction:** SHORT

## Livelli Strong/Weak LuxAlgo

Strong Low rilevati: 210 | Strong High rilevati: 113 | Entro 1.50 ATR: 11

BTC determina soltanto la priorità (`PREFERRED`/`COUNTER_BIAS`): non elimina i livelli della direzione opposta.

| Symbol | Tipo | Livello | Prezzo | Distanza | ATR | BTC | Cancellazione |
|---|---|---:|---:|---:|---:|---|---|
| MANA_USDT | STRONG_HIGH | 0.06743 | 0.06717 | 0.39% | 0.63 | PREFERRED | Chiusura 1H sopra 0.06743 |
| DATA_USDT | STRONG_HIGH | 0.2135 | 0.2124 | 0.52% | 0.73 | PREFERRED | Chiusura 1H sopra 0.2135 |
| XPD_USDT | STRONG_HIGH | 1,396.38 | 1,387.84 | 0.62% | 1.19 | PREFERRED | Chiusura 1H sopra 1396.38 |
| DEEP_USDT | STRONG_LOW | 0.01494 | 0.01511 | 1.13% | 0.74 | COUNTER_BIAS | Chiusura 1H sotto 0.01494 |
| VIRTUAL_USDT | STRONG_LOW | 0.5537 | 0.5579 | 0.75% | 0.97 | COUNTER_BIAS | Chiusura 1H sotto 0.5537 |
| RUNE_USDT | STRONG_LOW | 0.4256 | 0.4279 | 0.54% | 1.00 | COUNTER_BIAS | Chiusura 1H sotto 0.4256 |
| FLOKI_USDT | STRONG_LOW | 0.00002074 | 0.00002093 | 0.91% | 1.16 | COUNTER_BIAS | Chiusura 1H sotto 2.074e-05 |
| ZKSYNC_USDT | STRONG_LOW | 0.00776 | 0.00788 | 1.52% | 1.32 | COUNTER_BIAS | Chiusura 1H sotto 0.00776 |
| PIEVERSE_USDT | STRONG_LOW | 0.7623 | 0.7775 | 1.95% | 1.32 | COUNTER_BIAS | Chiusura 1H sotto 0.7623 |
| KAIA_USDT | STRONG_LOW | 0.02629 | 0.02654 | 0.94% | 1.34 | COUNTER_BIAS | Chiusura 1H sotto 0.02629 |
| BB_USDT | STRONG_LOW | 0.01334 | 0.01373 | 2.84% | 1.44 | COUNTER_BIAS | Chiusura 1H sotto 0.01334 |

**Strong Low più vicini:**
DEEP_USDT 0.01494 (1.13%, 0.74 ATR, COUNTER_BIAS); VIRTUAL_USDT 0.5537 (0.75%, 0.97 ATR, COUNTER_BIAS); RUNE_USDT 0.4256 (0.54%, 1.00 ATR, COUNTER_BIAS); FLOKI_USDT 0.00002074 (0.91%, 1.16 ATR, COUNTER_BIAS); ZKSYNC_USDT 0.00776 (1.52%, 1.32 ATR, COUNTER_BIAS)

**Strong High più vicini:**
MANA_USDT 0.06743 (0.39%, 0.63 ATR, PREFERRED); DATA_USDT 0.2135 (0.52%, 0.73 ATR, PREFERRED); XPD_USDT 1,396.38 (0.62%, 1.19 ATR, PREFERRED); LINK_USDT 8.396 (1.32%, 1.76 ATR, PREFERRED); AIO_USDT 0.04774 (4.88%, 1.77 ATR, PREFERRED)

## Best Available Opportunity

**XPD_USDT — SHORT STRONG_HIGH — 81/100**

- Prezzo MEXC: 1,387.84
- Conferma: strong_upper_wick_rejection
- Entry-reference: 1,387.40 – 1,388.84
- Invalidazione: 1,393.86 (0.41%, 0.80 ATR)
- TP1 / TP2: 1,373.09 / 1,373.09
- R:R TP2: 2.62
- Turnover 24H: 3,127,883 USDT
- Spread: 0.0367%
- Open interest: 4,424,244.00
- Funding: 0.0311%
- Score: {'A_btc_alignment': 20, 'B_candidate_structure': 10, 'C_zone_quality': 20, 'D_closed_candle_confirmation': 12, 'E_momentum_divergence': 7, 'F_volume_open_interest': 6, 'G_relative_performance': 3, 'H_execution_quality': 3}
- Motivi: Livello LuxAlgo Strong High a 1396.38, distanza 1.15 ATR; Zona con 4 confluenze: fib_0.618, high_volume_reaction, repeated_level, swing_high; Conferma 1H chiusa: strong_upper_wick_rejection
- Rischi: Allineamento EMA non completo; Una chiusura 1H oltre l'invalidazione strutturale cancella il setup
- Cancellazione: Chiusura 1H sopra 1393.860356 oppure ingresso oltre 0,75 ATR dalla conferma

## Ranked Qualified Opportunities

| Rank | Symbol | Side | Type | Score | Entry Zone | Invalidation | TP1 | TP2 | R:R | Turnover | Spread | Funding | Status |
|---:|---|---|---|---:|---|---|---|---|---:|---:|---:|---:|---|
| 1 | XPD_USDT | SHORT | STRONG_HIGH | 81 | 1,387.40–1,388.84 | 1,393.86 | 1,373.09 | 1,373.09 | 2.62 | 3,127,883 | 0.0367% | 0.0311% | PRIMARY |

Spiegazione punteggi:
- **XPD_USDT 81/100:** {'A_btc_alignment': 20, 'B_candidate_structure': 10, 'C_zone_quality': 20, 'D_closed_candle_confirmation': 12, 'E_momentum_divergence': 7, 'F_volume_open_interest': 6, 'G_relative_performance': 3, 'H_execution_quality': 3}

## Watchlist

Nessun candidato utile in watchlist.

## Final Decision

**QUALIFIED SHORT STRONG HIGH OPPORTUNITY FOUND.**

> Analisi tecnica automatizzata, non consulenza finanziaria e non istruzione di esecuzione. Nessun ordine viene preparato o inviato.
