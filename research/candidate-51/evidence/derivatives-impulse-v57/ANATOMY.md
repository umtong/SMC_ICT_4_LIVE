# Derivatives sponsorship anatomy for hourly impulse episodes

- source periods: 10
- events: 377
- assets: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- cost screen: 19 bp round trip
- no optimized thresholds; state boundaries are directional signs plus a trailing liquidity-normalized OI materiality test
- direct entry and +15m transition entry are separate causal policies
- mechanism diagnostic, not NautilusTrader NAV

## State results at 4h horizon

| state | n | direct continuation bp | PF | direct reversal bp | delayed continuation bp | delayed reversal bp |
|---|---:|---:|---:|---:|---:|---:|
| FLOW_ALIGNED_PRICE_ONLY | 8 | 6.31 | 1.42 | -44.31 | 10.18 | -48.18 |
| FORCED_UNWIND_ACCEPTED | 160 | 50.64 | 1.84 | -88.64 | 23.23 | -61.23 |
| FORCED_UNWIND_REJECTED | 30 | 5.72 | 1.08 | -43.72 | -25.57 | -12.43 |
| MIXED_SPONSORSHIP | 81 | 91.48 | 3.78 | -129.48 | 27.74 | -65.74 |
| SPONSORED_BUILD_BROAD | 78 | -65.65 | 0.39 | 27.65 | -53.94 | 15.94 |
| SPONSORED_BUILD_IDIOSYNCRATIC | 2 | -113.35 | 0.00 | 75.35 | -30.61 | -7.39 |
| UNSPONSORED_CONFLICT | 18 | 95.60 | 2.97 | -133.60 | 3.04 | -41.04 |

## Global one-slot source-family results

| family | entry | horizon | trades | trades/day | continuation bp | PF | reversal bp |
|---|---|---:|---:|---:|---:|---:|---:|
| public_vectorized_no_ema | direct | 120m | 77 | 1.069 | 25.52 | 1.38 | -63.52 |
| public_vectorized_no_ema | delayed | 120m | 77 | 1.069 | -13.51 | 0.82 | -24.49 |
| public_vectorized_no_ema | direct | 240m | 74 | 1.028 | 22.02 | 1.29 | -60.02 |
| public_vectorized_no_ema | delayed | 240m | 74 | 1.028 | -19.15 | 0.77 | -18.85 |
| public_vectorized_no_ema | direct | 480m | 66 | 0.917 | 31.90 | 1.32 | -69.90 |
| public_vectorized_no_ema | delayed | 480m | 66 | 0.917 | -1.49 | 0.99 | -36.51 |
| public_vectorized_no_ema | direct | 720m | 61 | 0.847 | 19.06 | 1.14 | -57.06 |
| public_vectorized_no_ema | delayed | 720m | 61 | 0.847 | -2.84 | 0.98 | -35.16 |
| impulse_only_2atr | direct | 120m | 107 | 1.486 | 17.82 | 1.28 | -55.82 |
| impulse_only_2atr | delayed | 120m | 107 | 1.486 | -7.14 | 0.89 | -30.86 |
| impulse_only_2atr | direct | 240m | 98 | 1.361 | 9.58 | 1.12 | -47.58 |
| impulse_only_2atr | delayed | 240m | 98 | 1.361 | -14.84 | 0.81 | -23.16 |
| impulse_only_2atr | direct | 480m | 84 | 1.167 | 14.44 | 1.13 | -52.44 |
| impulse_only_2atr | delayed | 480m | 84 | 1.167 | -13.47 | 0.89 | -24.53 |
| impulse_only_2atr | direct | 720m | 77 | 1.069 | -6.90 | 0.95 | -31.10 |
| impulse_only_2atr | delayed | 720m | 77 | 1.069 | -19.98 | 0.87 | -18.02 |

## Interpretation contract

A state is reusable only if the predicted continuation/reversal relationship appears in development, confirmation, untouched, and post-publication partitions without relying on one extreme episode. A higher aggregate total with the predicted loss group unchanged is not confirmation.

If no state is stable, the hourly impulse family remains a statistical clue but is not promoted. If one state is stable, the next step is an executable scenario with entry, same-leg invalidation, target, one-slot arbitration, and NautilusTrader accounting; the other states become no-trade or a distinct reversal family rather than filters stacked onto one entry.
