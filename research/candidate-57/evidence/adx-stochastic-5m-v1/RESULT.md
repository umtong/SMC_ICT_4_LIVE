# Public ADXStochastic five-minute tournament

Every row is the four-symbol, one-slot, after-cost account. Case JSON files contain every completed trade and entry-state diagnostics.

## development

| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | ROI exits | source exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| literal_source | 12 | 4/8 | 0.21763777916770613 | -0.007113770267765052 | -0.09511617423589991 | 0.09981490366488366 | -792.6347852991667 | 6 | 0 |
| corrected_exit | 12 | 4/8 | 0.21763777916770613 | -0.007113770267765052 | -0.09511617423589991 | 0.09981490366488366 | -792.6347852991667 | 6 | 0 |
| roi_stop_only | 12 | 4/8 | 0.21763777916770613 | -0.007113770267765052 | -0.09511617423589991 | 0.09981490366488366 | -792.6347852991667 | 6 | 0 |
| structural_literal | 11 | 3/8 | 0.253786694999586 | -0.007512107698250503 | -0.1001853844735 | 0.10176015271567929 | -910.7762224863636 | 4 | 1 |
| structural_corrected | 11 | 3/8 | 0.253786694999586 | -0.007512107698250503 | -0.1001853844735 | 0.10176015271567929 | -910.7762224863636 | 4 | 1 |

## reserved

| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | ROI exits | source exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| corrected_exit | 5 | 5/0 | None | 0.0023400588728650362 | 0.016495855032599982 | 0.02074410901609136 | 329.917100652 | 5 | 0 |
| literal_source | 5 | 5/0 | None | 0.0023400588728650362 | 0.016495855032599982 | 0.02074410901609136 | 329.917100652 | 5 | 0 |

## continuous_30d

| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | ROI exits | source exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|

## Allocation

- development survivors: ['corrected_exit', 'literal_source']
- positive reserved survivors: []
- continuous winner: None
- strict project pass: False
