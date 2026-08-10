# Candidate 51 retrospective profit/loss mining

This audit deliberately does **not** classify a strategy by final PnL alone. It ranks the already-observed winning engine, opportunity density, loss burden, and whether a simple pre-entry separation preserves winners while removing losses on the later half of the same run.

Explicitly invalid actual-fill/economic trades are excluded from logic analysis.

## Highest-priority historical runs

| rank | evidence | trades/day | gross profit/day NAV | gross loss/day NAV | net/day NAV | PF | validated simple repair |
|---:|---|---:|---:|---:|---:|---:|:---:|
| 1 | `evidence/development-2026-07-22_2026-07-28/metrics.json` | 32.714 | 2.248% | 16.120% | -13.872% | 0.139 | yes |
| 2 | `evidence/v12-jump-reversion/development/h1_z2/metrics.json` | 3.143 | 0.586% | 4.585% | -3.999% | 0.128 | yes |
| 3 | `evidence/v12-jump-reversion/development/h4_z2/metrics.json` | 1.000 | 2.880% | 2.197% | 0.684% | 1.311 | no |
| 4 | `evidence/v12-jump-reversion/development/h2_z2/metrics.json` | 1.643 | 0.774% | 2.814% | -2.040% | 0.275 | no |
| 5 | `evidence/v7-zaratustra/development/dense_di20/metrics.json` | 2.857 | 0.761% | 1.400% | -0.639% | 0.544 | yes |
| 6 | `evidence/v8-mbe2/development/source_cap10_30_70/metrics.json` | 4.286 | 0.258% | 1.255% | -0.997% | 0.206 | yes |
| 7 | `evidence/v8-mbe2/development/dense_35_65/metrics.json` | 4.286 | 0.458% | 0.985% | -0.526% | 0.465 | yes |
| 8 | `evidence/v7-zaratustra/development/source_di25/metrics.json` | 2.857 | 0.794% | 0.859% | -0.065% | 0.924 | yes |
| 9 | `evidence/v3-runway25-development-2026-07-22_2026-07-28/metrics.json` | 1.143 | 0.833% | 1.585% | -0.751% | 0.526 | no |
| 10 | `evidence/v12-jump-reversion/development/h1_z3/metrics.json` | 1.286 | 0.341% | 2.386% | -2.045% | 0.143 | yes |
| 11 | `evidence/v11-winner15m/development/dense_impulse/metrics.json` | 1.714 | 0.569% | 1.124% | -0.555% | 0.506 | no |
| 12 | `evidence/v11-winner15m/holdout/metrics.json` | 1.429 | 0.295% | 1.595% | -1.300% | 0.185 | no |
| 13 | `evidence/v11-winner15m/development/strict_impulse/metrics.json` | 1.429 | 1.017% | 0.696% | 0.321% | 1.461 | no |
| 14 | `evidence/v7-zaratustra/development/strict_di30/metrics.json` | 2.286 | 0.577% | 0.729% | -0.153% | 0.791 | yes |
| 15 | `evidence/v11-winner15m/development/source_exact/metrics.json` | 1.571 | 0.925% | 0.642% | 0.283% | 1.441 | no |
| 16 | `evidence/v8-mbe2/development/claim_avg_30_70/metrics.json` | 3.000 | 0.199% | 0.929% | -0.730% | 0.214 | no |
| 17 | `evidence/v10-el/development/dense_current/metrics.json` | 2.714 | 0.227% | 0.828% | -0.600% | 0.275 | yes |
| 18 | `evidence/v11-winner15m/development/source_short_only/metrics.json` | 1.286 | 0.850% | 0.677% | 0.173% | 1.256 | no |
| 19 | `evidence/v3-runway90-development-2026-07-22_2026-07-28/metrics.json` | 0.714 | 0.267% | 1.338% | -1.071% | 0.200 | no |
| 20 | `evidence/v11-winner15m/development/source_long_only/metrics.json` | 1.143 | 0.516% | 0.690% | -0.174% | 0.747 | no |
| 21 | `evidence/v12-jump-reversion/development/h2_z3/metrics.json` | 0.714 | 0.226% | 1.307% | -1.081% | 0.173 | no |
| 22 | `evidence/v10-el/development/balanced_current/metrics.json` | 1.571 | 0.116% | 0.520% | -0.404% | 0.223 | no |
| 23 | `evidence/v10-el/development/source_exact/metrics.json` | 1.571 | 0.181% | 0.435% | -0.254% | 0.417 | no |
| 24 | `evidence/v10-el/development/source_short_only/metrics.json` | 1.571 | 0.181% | 0.435% | -0.254% | 0.417 | no |
| 25 | `evidence/v5-sma-offset-timestamp-repair-development-2026-07-22_2026-07-28/shallow0990/metrics.json` | 0.429 | 0.087% | 0.796% | -0.709% | 0.109 | no |
| 26 | `evidence/v2-development-2026-07-22_2026-07-28/metrics.json` | 0.286 | 0.032% | 0.269% | -0.238% | 0.118 | no |
| 27 | `evidence/v5-sma-offset-timestamp-repair-development-2026-07-22_2026-07-28/moderate0980/metrics.json` | 0.143 | 0.040% | 0.000% | 0.040% | 0.000 | no |
| 28 | `evidence/v10-bbrpb/development/cofi/metrics.json` | 0.000 | 0.000% | 0.000% | 0.000% | 0.000 | no |
| 29 | `evidence/v10-bbrpb/development/local_dip/metrics.json` | 0.000 | 0.000% | 0.000% | 0.000% | 0.000 | no |
| 30 | `evidence/v10-bbrpb/development/nfi32/metrics.json` | 0.000 | 0.000% | 0.000% | 0.000% | 0.000 | no |

## Highest-gross-profit states

| state | trades | gross profit | gross loss | PF | sources |
|---|---:|---:|---:|---:|---:|
| `ACADEMIC_INTRADAY_JUMP_REVERSION` | 109 | 67305.42 | 186058.08 | 0.362 | 5 |
| `PUBLIC_WINNER15M_TREND_IMPULSE` | 60 | 29199.53 | 37966.28 | 0.769 | 6 |
| `PUBLIC_ZARATUSTRA_V5_MTF_TREND` | 56 | 14923.25 | 20919.97 | 0.713 | 3 |
| `ICHI_FAN_ACCELERATION_CONTINUATION` | 220 | 14911.33 | 110729.37 | 0.135 | 1 |
| `ICHI_V25_FAN_ACCELERATION_LONG` | 13 | 7703.12 | 20461.40 | 0.376 | 2 |
| `PUBLIC_MYSHORTING_MBE2_RSI_TEMA_REVERSAL` | 81 | 6407.31 | 22183.50 | 0.289 | 3 |
| `PUBLIC_EL_SMAOFFSET_EWO` | 52 | 4942.56 | 15519.42 | 0.318 | 4 |
| `NR7_RANGE_EXPANSION` | 9 | 824.10 | 2113.48 | 0.390 | 1 |
