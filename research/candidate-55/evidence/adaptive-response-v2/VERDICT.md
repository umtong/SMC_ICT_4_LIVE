# Adaptive-response v2 exact verdict

## Decision

`REJECT_STANDALONE_STATE_TRANSITION`

The policy was evaluated only after repairing the runtime contracts which had prevented V1 from reaching economic falsification. The exact matrix completed in GitHub Actions run `31478339457` at head `9467c7d0a1308fb60111633d1d8591da6f89d67b` with NautilusTrader, four symbols, one global pending/open slot and 3% current-NAV planned loss sizing.

## What was repaired before judging alpha

1. The inherited response consumer required `target_net_r`, `directional_flow_15s`, `directional_depth`, `reference` and `target`; V1 omitted part of that contract.
2. Selected-scenario provenance was restored after the inherited second-touch controller relabelled it.
3. Exact Binance metrics boundary duplicates were collapsed only when identical. Conflicting values at the same timestamp were all quarantined rather than averaged or selected by file order. Only an earlier unambiguous observation inside the existing 600-second age limit could remain usable.
4. The workflow now fails when launch returns nonzero or exact metrics/diagnostics are absent.

Therefore the final negative result is not attributed to the previously observed implementation failures.

## Exact results

| Role | Interval | Daily geometric growth | Return | Trades | W-L | PF | MDD |
|---|---|---:|---:|---:|---:|---:|---:|
| fresh | 2024-06-03..09 | -0.3873% | -2.6796% | 1 | 0-1 | 0.000 | 2.6796% |
| preselected repair | 2024-10-07..13 | -0.3454% | -2.3932% | 1 | 0-1 | 0.000 | 4.3758% |
| fresh | 2025-02-10..16 | -1.1131% | -7.5361% | 3 | 0-3 | 0.000 | 7.5361% |
| preselected repair | 2025-08-04..10 | -1.5158% | -10.1401% | 4 | 0-4 | 0.000 | 12.0230% |
| development repair | 2025-11-03..09 | +1.4359% | +10.4947% | 3 | 2-1 | 5.335 | 4.2471% |
| preselected repair | 2026-02-02..08 | +0.4870% | +3.4595% | 2 | 1-1 | 2.136 | 5.1559% |
| development repair | 2026-04-13..19 | +0.5424% | +3.8592% | 2 | 1-1 | 2.464 | 3.7493% |
| fresh | 2026-06-08..14 | -0.7836% | -5.3582% | 2 | 0-2 | 0.000 | 5.3582% |

All three fresh intervals were negative. All six fresh trades lost. Density was only one to three completed trades per seven calendar days. Global position violations and order rejections were zero.

## Causal interpretation

The failed component is **confirmation/state transition**, not the risk engine or account simulator. The policy assumed that one completed minute of flow reversal plus decaying absorption/notional/trade rate and negative OI release meant that the liquidation leg had completed. Fresh trades show that this condition often occurred while the same causal leg was still active. Entries were then stopped quickly; development profitability depended on a few rare long-held winners and did not reproduce in fresh intervals.

The predicted loss removal did not occur. The loss group remained while opportunity density stayed far below the project requirement. Threshold retuning would therefore rescue a contradicted state model rather than test a still-supported hypothesis.

## Reusable components

Retain the conflict-quarantine/provenance contract, the complete response-record interface test and exact-run enforcement. The response family itself may only return later as a small specialist after an independent state owner has first established that the liquidation leg is over.

## Structural transition

The next hypothesis is not another adaptive-response threshold. It is a different state machine mined from public dump-reversal implementations and adapted to this project:

`capitulation -> arm -> update causal extreme while extension continues -> reclaim/stop-entry -> structural invalidation -> cost-aware management`

Price must first invalidate continuation of the active liquidation leg before entry. This directly targets the fresh failure anatomy above.
