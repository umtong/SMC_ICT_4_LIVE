# Frozen Slope sep2 MA-cross-only — corrected untouched evaluation

The workflow interval jobs succeeded, but its aggregate copy step failed. This audit was rebuilt from all nine interval artifacts. Each row is a fresh one-slot NautilusTrader account; interval NAVs are **not stitched** into a continuous account.

| interval | return | geo/day | MDD | valid trades | wins/losses | invalid fills | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| eval_2024_01 | -1.794% | -0.129% | 5.068% | 18 | 13/5 | 1 | 0.664 |
| eval_2024_08 | 5.100% | 0.356% | 5.343% | 33 | 30/3 | 3 | 2.257 |
| eval_2024_12 | 2.484% | 0.175% | 4.732% | 25 | 22/3 | 0 | 1.399 |
| eval_2025_01 | -4.095% | -0.298% | 6.995% | 14 | 10/4 | 0 | 0.427 |
| eval_2025_04 | -3.088% | -0.224% | 6.378% | 19 | 15/4 | 0 | 0.633 |
| eval_2025_07 | -3.231% | -0.234% | 5.780% | 13 | 9/4 | 0 | 0.433 |
| eval_2025_10 | -2.906% | -0.210% | 7.161% | 21 | 16/5 | 0 | 0.656 |
| eval_2026_02 | 10.523% | 0.717% | 3.863% | 48 | 45/3 | 0 | 4.328 |
| eval_2026_05 | -2.888% | -0.209% | 4.006% | 4 | 1/3 | 0 | 0.271 |

## Aggregate

- Positive/negative intervals: 3/6.
- Mean/median interval return: 0.012% / -2.888%.
- Mean/median geometric daily growth: -0.006% / -0.209%.
- Valid intended trades: 195 over 126 calendar days; fill invalidations: 4; global-position violations: 0.

## Exit-engine anatomy

- **PUBLIC_TRAILING_EXIT**: 154 trades, 154 wins, net 52,588.27 USDT, PF ∞.
- **PUBLIC_SOURCE_EXIT_SIGNAL**: 8 trades, 0 wins, net -16,832.74 USDT, PF 0.000.
- **PUBLIC_ROI_EXIT**: 22 trades, 7 wins, net -57.50 USDT, PF 0.927.
- **HARD_STOP**: 11 trades, 0 wins, net -33,289.76 USDT, PF 0.000.

## Decision

The frozen system failed as a complete system: six of nine untouched accounts were negative and median daily growth was negative. It is not discarded wholesale. The public trailing engine remained exceptionally strong—154 valid exits, 154 winners, net +52,588.27 USDT—while eight MA-thesis exits lost -16,832.74 USDT and eleven structural hard stops lost -33,289.76 USDT.

The next experiment therefore does not tune another price threshold. It treats no-progress as a causal-episode thesis failure using the public ROI schedule and public trailing activation, and blocks immediate re-entry into the same contiguous source condition after that failure.
