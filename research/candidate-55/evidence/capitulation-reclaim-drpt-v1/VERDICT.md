# Candidate-55 DRPT v1 exact fresh verdict

## Decision

**Reject as a strategy and reject as a source-faithful DRPT replication. Do not run medium or long evaluation.**

## Exact four-period account evidence

| Period | Daily growth | Trades | W-L | PF | Net PnL | MDD |
|---|---:|---:|---:|---:|---:|---:|
| 2024-03-04~2024-03-10 | -5.109% | 33 | 5-28 | 0.122 | -30,723.37 | 32.15% |
| 2024-12-02~2024-12-08 | -0.886% | 32 | 10-22 | 0.740 | -6,036.89 | 13.37% |
| 2025-05-12~2025-05-18 | -3.812% | 34 | 7-27 | 0.387 | -23,817.41 | 32.72% |
| 2026-01-12~2026-01-18 | -2.189% | 30 | 9-21 | 0.449 | -14,354.65 | 16.69% |

Combined: 129 trades, 31 wins, PF 0.391, net -74,932.32 USDT, mean -0.229R.

## Causal anatomy

| Exit family | Trades | Wins | Net PnL | PF | Mean R |
|---|---:|---:|---:|---:|---:|
| forced daytrade | 30 | 15 | +6,766.92 | 1.483 | +0.074 |
| other bracket/account | 7 | 1 | -3,292.13 | 0.024 | -0.153 |
| peak retrace | 14 | 14 | +22,094.23 | infinite | +0.588 |
| structural stop | 22 | 0 | -55,129.13 | 0.000 | -0.988 |
| target | 1 | 1 | +5,228.88 | infinite | +2.651 |
| time in loss | 55 | 0 | -50,601.08 | 0.000 | -0.361 |

- The peak-retrace subset was profitable, but it was too small to offset structural-stop and time-in-loss losses.
- The short side produced 84 of 129 trades and -59,107.67 USDT even though the public source is long-only.
- All four fresh periods were negative; the result is not a one-period anomaly.
- Arms older than 120 minutes produced 11 losses and no wins, but this is a diagnostic consequence, not permission to optimize the TTL after seeing results.
- Execution integrity passed: one global slot, no order rejections, no global-position violations, and current-NAV risk accounting.

## Source parity audit

- Public source: long-only, 1-2% dump, prior seven completed daily-low break, BTC 4h-up and resistance filters, default ATRStop trigger.
- V1: symmetric long/short, six-hour local-extreme ATR event, different confirmation and management.
- Therefore V1 does not test whether the public source selector works; it tests a materially different local reversal policy.

## Market-logic verdict

The completed-minute reclaim did not establish ownership of the next auction leg. Most losses came from either immediate structural invalidation or ninety minutes spent below entry. The profitable peak-retrace episodes prove that some capitulation interactions can produce rebounds, but the V1 context and transition model could not distinguish those episodes before entry.

## Next action

Test the public source selector once, preserving its long-only seven-day liquidity context and natural filters while adapting only execution and risk validity. Do not tune V1 thresholds or run longer periods to rescue it.
