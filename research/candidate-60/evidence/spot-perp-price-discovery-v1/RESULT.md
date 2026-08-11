# Candidate 60 spot/perpetual price-discovery result

Both families use completed five-minute same-asset price and executed-flow dominance. The primary 30-minute result subtracts the project's 20 bp round-trip friction floor.

## Development — 2026-07-27 to 2026-08-02

| family | one-slot trades | mean gross bp | mean net bp | sum net bp | positive symbols | positive days | opposite mean net bp | eligible |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| spot_lead_follow | 291 | -1.868223556838087 | -21.868223556838085 | -6363.653055039883 | 0 | 0 | -18.13177644316191 | False |
| perp_lead_fade | 23 | 7.877982373802408 | -12.122017626197591 | -278.8064054025446 | 0 | 3 | -27.87798237380241 | False |

## Policy-fresh

Not consumed because no family passed the frozen 30-minute cost-after and robustness conditions.
