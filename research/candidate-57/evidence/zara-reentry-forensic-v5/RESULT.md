# ZaratustraV5 continuous-episode re-entry forensic v5

The source account is unchanged; every actual trade is tagged by causal state episode.

- baseline identical: True
- trades: 214
- tagged trades: 214
- independent source episodes represented by account trades: 149
- repeated trades collapsed by causal episode: 65

| entry group | trades | episodes | W/L | mean R | PF(R) | median MFE R | median MAE R | median episode age | trailing exits | stop/bracket exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| first_entry | 149 | 149 | 102/47 | -0.03557090122566115 | 0.7694559388566317 | 0.26354516719578974 | 0.22914391832114986 | 5.0 | 102 | 0 |
| reentry | 65 | 35 | 42/23 | -0.12343409954581513 | 0.4880747476095601 | 0.2736755453410319 | 0.17166469463536382 | 35.0 | 43 | 0 |
| second_entry | 35 | 35 | 24/11 | -0.09052565739023306 | 0.5711780952028548 | 0.2785632347676142 | 0.16496031906673236 | 20.0 | 24 | 0 |
| third_plus | 30 | 15 | 18/12 | -0.16182728206066088 | 0.413953959221625 | 0.27027651431751043 | 0.2581446482939845 | 55.0 | 19 | 0 |

## Predeclared interpretation

- re-entry exhaustion hypothesis supported: False
- reason: reentries=65; first mean=-0.036R; reentry mean=-0.123R; first/reentry median MFE=0.264/0.274R; first/reentry median MAE=0.229/0.172R

A one-entry-per-continuous-state policy is tested on untouched data only if re-entry deterioration is broad in R, MFE/MAE and exit mix. Otherwise repeated entry remains part of the source renewal mechanism and is merely collapsed for independent-opportunity reporting.