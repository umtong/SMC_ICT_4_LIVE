# Candidate 17 v2 — untouched BTC week pre-registration

This period was selected before its Candidate 17 features, trades, or PnL were read.
The strategy is frozen at commit `3bfc9a45f1fa29aef7479b2deb723944d13b87d0`.
The two previously inspected weeks (2023-12-25 and 2024-09-16) are development data only.

## Deterministic selection

- universe: every Monday from 2022-01-03 through 2025-12-29 inclusive
- number of candidate Mondays: 209
- seed: `candidate17-v2-first-retest-cost-geometry|3bfc9a45f1fa29aef7479b2deb723944d13b87d0|untouched-week-1`
- mapping: integer value of SHA-256(seed), modulo 209
- expected index: 5
- selected evaluation start: 2022-02-07 UTC
- selected evaluation end: 2022-02-13 UTC
- build/bootstrap start: 2022-02-04 UTC
- build/bootstrap end: 2022-02-13 UTC

## Frozen system

- instrument: BTCUSDT perpetual
- engine: existing NautilusTrader BacktestNode path
- one continuous account; no daily reset
- current-NAV planned loss at maximum 3% per trade
- registered fee, adverse slippage, funding, and execution assumptions unchanged
- maximum one entry intent or open position globally
- no PnL-trained parameter change after the two development weeks
- failed-auction initiative arms the first retest; it is not an entry
- only a held first retest may enter
- structural invalidation distance must be at least the expected complete execution-cost component
- repeated-defense depletion logic remains unchanged

## Decision

The unchanged project weekly gate in `research/candidate-17/config.json` is authoritative.
A positive week with too few independent trades is not a pass. Any implementation or execution-integrity failure is also a fail.
