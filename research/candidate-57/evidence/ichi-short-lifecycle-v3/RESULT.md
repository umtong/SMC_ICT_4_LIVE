# Public ichiV2 short lifecycle forensic v3

The actual account is behaviour-identical; post-exit shadows never trade.

- baseline identical: True
- trades: 32
- wins/losses: 15/17
- PF: 1.4384645193414491
- geometric daily growth: 0.001626941656724945

## Actual lifecycle

| exit family | trades | W/L | mean R | median MFE R | median MAE R | median active-source ratio | state loss before +0.25R |
|---|---:|---:|---:|---:|---:|---:|---:|
| roi_exit | 13 | 13/0 | 0.39265283693121317 | 0.48319769541185376 | 0.09637142395345023 | None | 0.6153846153846154 |
| source_exit | 19 | 2/17 | -0.18062352136653984 | 0.11495016059823808 | 0.21677467442390178 | None | 0.8947368421052632 |

## Non-trading post-exit shadow

- source-exit losses evaluated: 17
- recovered to original ROI: 5
- recovery rate: 0.29411764705882354
- median shadow minus actual R: 0.06926521026914448
- resolutions: {'ORIGINAL_ROI': 7, 'ORIGINAL_STOP': 3, 'ORIGINAL_HORIZON': 9, 'EVALUATION_END': 0}

## Predeclared interpretation

- source-exit-validity hypothesis supported: False
- reason: source-exit loss recovery rate=0.294; median shadow-minus-actual=0.069R; source-exit median pre-exit MFE=0.115R

If the source exit is validated, the next minimal investigation moves to entry-state and cross-asset arbitration. If it is falsified, only an exit-confirmation state is tested, with the recovered trades predeclared before any untouched run.