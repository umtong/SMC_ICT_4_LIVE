# Jump + Ichi N→1 fresh one-account comparison

Scored entry window: `2024-02-01` through `2024-02-29` UTC (29 days). Startup and runoff cannot open entries.

- mechanically valid: True
- decision: `JUMP_ICHI_N1_COMPOSITION_REJECTED_NO_RETUNING`
- strict project target: False
- composition causal support: False
- thresholds searched: False

| mode | trades | W/L | win rate | PF | expectancy | signal geo/day | return | MDD | jump | ichi |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| jump_only | 37 | 15/22 | 0.40540540540540543 | 0.3344562272277531 | -641.8364436397296 | -0.009305600163050043 | -0.23747948414669995 | 0.2629147334289553 | 37 | 0 |
| ichi_only | 37 | 11/26 | 0.2972972972972973 | 0.5397561835234905 | -181.2931923537838 | -0.0023914187045713797 | -0.0670784811709001 | 0.08466915937965214 | 0 | 37 |
| integrated | 69 | 26/43 | 0.37681159420289856 | 0.4417066957614086 | -344.1436907998551 | -0.009304689029496771 | -0.23745914665190004 | 0.264930026567172 | 34 | 35 |

## Causal composition

- Jump winner preservation: `{"best_jump_key": ["jump", "XRPUSDT", 1709063999999000000], "best_jump_preserved_positive": true, "best_jump_r": 0.945167085058143, "positive_winner_preservation_share": 1.0, "preserved_positive_jump_winners": 15, "source_jump_trades": 37, "source_jump_winners": 15}`
- Integrated minus jump: `{"ending_nav": 2.0337494799896376, "expectancy_usdt": 297.6927528398745, "geometric_daily_growth_signal_window": 9.111335532718812e-07, "largest_winner_share": -0.06698635795119429, "losses": 21.0, "max_drawdown": 0.0020152931382166894, "profit_factor": 0.1072504685336555, "total_return": 2.0337494799904654e-05, "trades": 32.0, "win_rate": -0.02859381120250687, "wins": 11.0}`
- Integrated minus ichi: `{"ending_nav": -17038.066548100003, "expectancy_usdt": -162.8504984460713, "geometric_daily_growth_signal_window": -0.006913270324925391, "largest_winner_share": -0.09420444320970747, "losses": 17.0, "max_drawdown": 0.18026086718751988, "profit_factor": -0.09804948776208194, "total_return": -0.17038066548099995, "trades": 32.0, "win_rate": 0.07951429690560124, "wins": 15.0}`
- Episode comparison: `{"ichi_standalone_count": 37, "integrated_added_ichi": [], "integrated_added_jump": [], "integrated_ichi_count": 35, "integrated_jump_count": 34, "integrated_omitted_ichi": [["ichi", "SOLUSDT", 1708566299999000000], ["ichi", "SOLUSDT", 1709151299999000000]], "integrated_omitted_jump": [["jump", "BTCUSDT", 1708444799999000000], ["jump", "SOLUSDT", 1707148799999000000], ["jump", "SOLUSDT", 1709049599999000000]], "jump_standalone_count": 37}`

Positive return alone is insufficient: the integrated account must preserve jump winners, add independent ichi opportunities, and improve both density and geometric growth.
