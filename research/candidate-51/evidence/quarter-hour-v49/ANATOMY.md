# Quarter-hour causal episode anatomy

- source runs: 24
- events: 27648
- symbols: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- periods: development_2025_02, development_2025_06, forward_2026_02, forward_2026_06, post_2024_11, stress_2025_10
- signal: signed first-ten-second taker notional imbalance in minutes 00/15/30/45
- placebos: identical first-ten-second construction at minute offsets 03/07/11
- causal availability: quarter-hour minute close; d30 entries use state observed through minute 29
- figures below are directional gross basis points, not a NAV backtest

## 30-minute-delay to 8-hour-boundary ranking

| split | scenario | independent n | n/day | gross mean bp | after 10bp | hit | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| all | sc_reversal30_realign_price | 48 | 2.667 | 81.41 | 71.41 | 62.5 | 2.53 |
| all | sc_consensus_flow | 57 | 3.167 | 64.48 | 54.48 | 57.9 | 2.43 |
| all | sc_persistent_flow | 58 | 3.222 | 41.45 | 31.45 | 55.2 | 1.82 |
| all | sc_all | 60 | 3.333 | 39.78 | 29.78 | 50.0 | 1.42 |
| all | sc_nonfunding_extreme | 58 | 3.222 | 36.47 | 26.47 | 50.0 | 1.61 |
| all | sc_extreme_burst | 58 | 3.222 | 34.71 | 24.71 | 48.3 | 1.58 |
| all | sc_absorbed_1m | 55 | 3.056 | 27.32 | 17.32 | 43.6 | 1.32 |
| all | sc_nonfunding_abs50 | 60 | 3.333 | 23.13 | 13.13 | 55.0 | 1.31 |
| all | sc_reversal30 | 58 | 3.222 | 1.12 | -8.88 | 46.6 | 1.01 |
| all | sc_burst125 | 60 | 3.333 | 1.09 | -8.91 | 55.0 | 1.01 |
| all | sc_aligned_1m | 60 | 3.333 | -1.38 | -11.38 | 46.7 | 0.98 |
| all | sc_reversal30_5bps | 58 | 3.222 | -1.43 | -11.43 | 46.6 | 0.98 |
| all | sc_abs50 | 60 | 3.333 | -1.90 | -11.90 | 53.3 | 0.98 |
| all | sc_abs75 | 54 | 3.000 | -2.32 | -12.32 | 46.3 | 0.97 |
| all | sc_reversal30_full | 33 | 1.833 | -9.25 | -19.25 | 48.5 | 0.91 |
| all | sc_reversal30_realign | 55 | 3.056 | -30.15 | -40.15 | 47.3 | 0.70 |
| all | sc_reversal30_realign_burst | 47 | 2.611 | -58.45 | -68.45 | 40.4 | 0.50 |
| development | sc_all | 30 | 3.333 | 122.47 | 112.47 | 50.0 | 3.91 |
| development | sc_nonfunding_abs50 | 30 | 3.333 | 115.47 | 105.47 | 70.0 | 4.18 |
| development | sc_abs50 | 30 | 3.333 | 97.64 | 87.64 | 56.7 | 3.05 |
| development | sc_reversal30_realign_price | 25 | 2.778 | 77.04 | 67.04 | 48.0 | 2.24 |
| development | sc_consensus_flow | 29 | 3.222 | 72.40 | 62.40 | 58.6 | 3.21 |
| development | sc_aligned_1m | 30 | 3.333 | 71.07 | 61.07 | 46.7 | 2.53 |
| development | sc_nonfunding_extreme | 30 | 3.333 | 61.58 | 51.58 | 56.7 | 2.27 |
| development | sc_extreme_burst | 30 | 3.333 | 57.84 | 47.84 | 50.0 | 2.19 |
| development | sc_persistent_flow | 29 | 3.222 | 40.12 | 30.12 | 51.7 | 1.95 |
| development | sc_burst125 | 30 | 3.333 | 35.98 | 25.98 | 46.7 | 1.48 |
| development | sc_absorbed_1m | 28 | 3.111 | 25.57 | 15.57 | 39.3 | 1.29 |
| development | sc_abs75 | 27 | 3.000 | -7.49 | -17.49 | 37.0 | 0.89 |
| development | sc_reversal30 | 30 | 3.333 | -43.71 | -53.71 | 46.7 | 0.53 |
| development | sc_reversal30_5bps | 30 | 3.333 | -50.36 | -60.36 | 46.7 | 0.48 |
| development | sc_reversal30_realign | 28 | 3.111 | -66.68 | -76.68 | 39.3 | 0.37 |
| development | sc_reversal30_full | 18 | 2.000 | -76.71 | -86.71 | 27.8 | 0.48 |
| development | sc_reversal30_realign_burst | 25 | 2.778 | -112.67 | -122.67 | 28.0 | 0.16 |
| forward | sc_reversal30_realign_price | 15 | 2.500 | 147.89 | 137.89 | 86.7 | 10.14 |
| forward | sc_reversal30_full | 10 | 1.667 | 50.45 | 40.45 | 70.0 | 1.90 |
| forward | sc_reversal30_realign | 18 | 3.000 | 40.78 | 30.78 | 55.6 | 1.52 |
| forward | sc_consensus_flow | 19 | 3.167 | 39.16 | 29.16 | 52.6 | 1.60 |
| forward | sc_abs75 | 18 | 3.000 | 37.86 | 27.86 | 50.0 | 1.49 |
| forward | sc_reversal30_5bps | 19 | 3.167 | 33.93 | 23.93 | 52.6 | 1.53 |

## Quarter-hour minus median placebo-phase spread (d30 to h480)

| split | scenario | qh independent n | placebo median n | gross spread bp | net10 spread bp |
|---|---:|---:|---:|---:|---:|
| all | sc_reversal30_realign_price | 48 | 49 | 83.15 | 83.15 |
| all | sc_reversal30_full | 33 | 42 | 38.18 | 38.18 |
| all | sc_reversal30_5bps | 58 | 58 | 35.24 | 35.24 |
| all | sc_all | 60 | 60 | 30.70 | 30.70 |
| all | sc_abs75 | 54 | 56 | 30.48 | 30.48 |
| all | sc_absorbed_1m | 55 | 57 | 21.06 | 21.06 |
| all | sc_nonfunding_abs50 | 60 | 60 | 20.26 | 20.26 |
| all | sc_persistent_flow | 58 | 59 | 11.83 | 11.83 |
| all | sc_reversal30 | 58 | 59 | 11.05 | 11.05 |
| all | sc_consensus_flow | 57 | 58 | -3.46 | -3.46 |
| all | sc_abs50 | 60 | 60 | -4.76 | -4.76 |
| all | sc_nonfunding_extreme | 58 | 58 | -8.03 | -8.03 |
| all | sc_extreme_burst | 58 | 58 | -9.80 | -9.80 |
| all | sc_reversal30_realign_burst | 47 | 46 | -17.24 | -17.24 |
| all | sc_burst125 | 60 | 60 | -23.02 | -23.02 |
| all | sc_reversal30_realign | 55 | 54 | -31.35 | -31.35 |
| all | sc_aligned_1m | 60 | 60 | -55.43 | -55.43 |
| development | sc_nonfunding_abs50 | 30 | 30 | 144.02 | 144.02 |
| development | sc_all | 30 | 30 | 141.26 | 141.26 |
| development | sc_abs50 | 30 | 30 | 126.19 | 126.19 |
| development | sc_absorbed_1m | 28 | 29 | 48.44 | 48.44 |
| development | sc_reversal30_realign_price | 25 | 25 | 30.99 | 30.99 |
| development | sc_abs75 | 27 | 29 | 26.30 | 26.30 |
| development | sc_consensus_flow | 29 | 30 | 23.51 | 23.51 |
| development | sc_nonfunding_extreme | 30 | 30 | 16.36 | 16.36 |
| development | sc_extreme_burst | 30 | 30 | 12.62 | 12.62 |
| development | sc_reversal30_full | 18 | 20 | -1.24 | -1.24 |
| development | sc_aligned_1m | 30 | 30 | -6.08 | -6.08 |
| development | sc_persistent_flow | 29 | 30 | -10.12 | -10.12 |
| development | sc_burst125 | 30 | 30 | -33.03 | -33.03 |
| development | sc_reversal30_realign_burst | 25 | 23 | -39.44 | -39.44 |
| development | sc_reversal30_5bps | 30 | 29 | -44.12 | -44.12 |
| development | sc_reversal30 | 30 | 30 | -46.19 | -46.19 |
| development | sc_reversal30_realign | 28 | 27 | -102.76 | -102.76 |
| forward | sc_reversal30_realign_price | 15 | 16 | 152.28 | 152.28 |
| forward | sc_reversal30_full | 10 | 13 | 93.58 | 93.58 |
| forward | sc_reversal30_realign | 18 | 18 | 76.42 | 76.42 |
| forward | sc_reversal30 | 19 | 20 | 67.71 | 67.71 |
| forward | sc_reversal30_5bps | 19 | 20 | 55.07 | 55.07 |
| forward | sc_all | 20 | 20 | 47.47 | 47.47 |

## Interpretation contract

A positive conditional mean is only a mechanism clue. Promotion requires a NautilusTrader strategy with executable entry timing, fees, slippage, market impact, funding, one global position, risk-sized quantity, and continuous NAV. Overlapping quarter-hour observations are not counted as independent trades; the ranking uses a greedy non-overlap view for the stated horizon.
