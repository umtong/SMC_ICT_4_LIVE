# Frozen quarter-hour replication

- source runs: 64
- untouched calendar days: 224
- symbols: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- periods: rep_2024_11, rep_2024_12, rep_2025_01, rep_2025_03, rep_2025_04, rep_2025_05, rep_2025_07, rep_2025_08, rep_2025_09, rep_2025_10b, rep_2025_11, rep_2025_12, rep_2026_01, rep_2026_03, rep_2026_04, rep_2026_07
- primary scenario: `sc_reversal30_realign_price`
- frozen entry/exit: +30m open to +480m open
- arbitration: largest initial absolute first-ten-second taker imbalance at a timestamp
- account approximation: one global non-overlapping 8-hour slot
- primary cost screen: 15 bp round trip
- this is mechanism replication, not a NautilusTrader NAV backtest

## Global one-slot quarter-hour results

| scenario | n | n/day | gross bp | net15 bp | net15 hit | net15 PF | bootstrap 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| sc_reversal30_realign_price | 603 | 2.692 | 0.69 | -14.31 | 43.3 | 0.81 | [-31.44, 4.25] |
| sc_consensus_flow | 701 | 3.129 | -7.75 | -22.75 | 42.8 | 0.72 | [-39.60, -6.22] |
| sc_abs50 | 718 | 3.205 | -11.80 | -26.80 | 44.7 | 0.66 | [-41.39, -12.26] |
| sc_all | 720 | 3.214 | -4.49 | -19.49 | 43.2 | 0.75 | [-35.01, -4.57] |

## Primary scenario by period

| period | n | gross bp | net15 bp | net15 hit | PF |
|---|---:|---:|---:|---:|---:|
| rep_2024_11 | 38 | 21.48 | 6.48 | 47.4 | 1.07 |
| rep_2024_12 | 38 | -0.39 | -15.39 | 52.6 | 0.81 |
| rep_2025_01 | 39 | -28.00 | -43.00 | 46.2 | 0.55 |
| rep_2025_03 | 36 | 132.54 | 117.54 | 50.0 | 2.86 |
| rep_2025_04 | 36 | -35.99 | -50.99 | 44.4 | 0.61 |
| rep_2025_05 | 39 | -0.62 | -15.62 | 43.6 | 0.77 |
| rep_2025_07 | 38 | -14.62 | -29.62 | 39.5 | 0.53 |
| rep_2025_08 | 37 | -13.56 | -28.56 | 40.5 | 0.60 |
| rep_2025_09 | 38 | -5.90 | -20.90 | 39.5 | 0.61 |
| rep_2025_10b | 39 | -21.16 | -36.16 | 46.2 | 0.56 |
| rep_2025_11 | 36 | 10.17 | -4.83 | 44.4 | 0.93 |
| rep_2025_12 | 35 | 24.09 | 9.09 | 60.0 | 1.17 |
| rep_2026_01 | 39 | -15.16 | -30.16 | 35.9 | 0.52 |
| rep_2026_03 | 36 | -32.62 | -47.62 | 36.1 | 0.42 |
| rep_2026_04 | 39 | 24.06 | 9.06 | 43.6 | 1.21 |
| rep_2026_07 | 40 | -25.52 | -40.52 | 25.0 | 0.40 |

## Quarter-hour phase specificity

| scenario | qh n | placebo median n | gross spread bp | net15 spread bp |
|---|---:|---:|---:|---:|
| sc_reversal30_realign_price | 603 | 614 | -2.98 | -2.98 |
| sc_consensus_flow | 701 | 702 | -11.82 | -11.82 |
| sc_abs50 | 718 | 720 | -2.89 | -2.89 |
| sc_all | 720 | 720 | -3.74 | -3.74 |

## Interpretation contract

This untouched replication freezes the discovery scenario, timing, arbitration and 8-hour slot before observing these periods. A positive mean is still not a deployment result. Promotion requires a NautilusTrader continuous four-asset account with actual order lifecycle, risk sizing from current NAV, fees, adverse slippage, market impact, funding and restart-safe state handling.
