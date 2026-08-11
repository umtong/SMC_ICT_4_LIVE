# Winner15m lifecycle thesis forensic v3

- parity pass: True
- mechanically valid: True
- decision: `WINNER_LIFECYCLE_THESIS_HYPOTHESIS_REJECTED_NO_RETUNING`
- thresholds searched: False
- policy fresh authorized: False

The tested transition is fixed: before trailing activation, the completed 15-minute public source side no longer matches the entry side while direction-adjusted close return is non-positive.

| period | trades | trailing winners | hard-stop-like losses | winner preservation | loss capture | prediction supported |
|---|---:|---:|---:|---:|---:|---:|
| march_2025 | 44 | 26 | 17 | 0.4230769230769231 | 0.8823529411764706 | False |
| september_2024 | 15 | 8 | 4 | 0.25 | 1.0 | False |

No source entry, stop, target, trailing, ROI, score, risk, cost, fill or holding rule was changed. If the same categorical transition does not satisfy the predeclared separation in both consumed periods, the Winner lifecycle repair is rejected without retuning.
