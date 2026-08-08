# Candidate 13 V13 — causal OI-state diagnostic

This is an **exposed-development diagnostic**, not OOS evidence.
Official Binance USD-M five-minute metrics are checksum verified and become observable only five minutes after `create_time`.

## Coverage

- trades: 24
- post-sweep causal captures: 17
- unresolved: 7

## Outcome by OI state

| state | trades | wins | losses | win rate | net PnL USDT | payoff |
|---|---:|---:|---:|---:|---:|---:|
| DELEVERAGING_RESET | 7 | 7 | 0 | 100.00% | 32799.34 | inf |
| FRESH_INVENTORY_SPONSORSHIP | 4 | 4 | 0 | 100.00% | 33924.94 | inf |
| MIXED_POSITIONING | 1 | 1 | 0 | 100.00% | 7117.16 | inf |
| NONEXPANDING_CHOCH_AFTER_FRESH_EVENT | 5 | 5 | 0 | 100.00% | 39601.83 | inf |
| UNRESOLVED_NO_POST_SWEEP_METRIC | 7 | 4 | 3 | 57.14% | 7842.26 | 1.323 |

## Pre-existing Candidate 05 reset predicate

- retained: 14 trades, 14 wins, 0 losses
- retained net PnL: 86771.49 USDT
- rejected: 9 trades, net 37882.60 USDT

No threshold was fitted to Candidate 13 outcomes. The 0.10% threshold is reused unchanged from Candidate 05's positioning-reset predicate.

## Official archive availability

- missing archives: 14
- policy: no synthetic fill; affected observations remain UNRESOLVED
