# Candidate 13 V17 — missed passive retrace rearm diagnostic

**EXPOSED_DEVELOPMENT_DIAGNOSTIC_ONLY — success_claim: false**

The original V16 FAR is never chased or rewritten.  This diagnostic begins only after a real unfilled parent expiry and requires a new pullback-break-retest leg.

## Summary

- expired FAR parents: `9`
- feasible rearm confirmations: `3`
- filled rearm plans: `3`
- wins / losses: `0 / 2`
- resolved win rate: `0.0`
- sum realized R: `-2.224799`

## Per expired FAR

| Week | Symbol | Role | State | Outcome | Planned R | Realized R |
|---|---|---|---|---|---:|---:|
| W10 | XRPUSDT | SEMANTIC_FAR_EXHAUSTION_UNANIMOUS | REARM_GEOMETRY_REJECTED | — | 0.040 | — |
| W11 | XRPUSDT | SEMANTIC_FAR_EXHAUSTION_UNANIMOUS | ORIGINAL_TARGET_CONSUMED | — | — | — |
| W14 | SOLUSDT | SEMANTIC_FAR_EXHAUSTION_UNANIMOUS | REARM_CONFIRMATION_FOUND | EVALUATION_END_MARKET_FLATTEN | 2.187 | -0.225 |
| W18 | SOLUSDT | SEMANTIC_FAR_NASCENT_TREND_RESUMPTION | ORIGINAL_TARGET_CONSUMED | — | — | — |
| W18 | BTCUSDT | SEMANTIC_FAR_CAPITULATION_SYNCHRONIZED | REARM_CONFIRMATION_FOUND | LOSS_STOP | 7.802 | -1.000 |
| W18 | SOLUSDT | SEMANTIC_FAR_EXHAUSTION_UNANIMOUS | REARM_GEOMETRY_REJECTED | — | 0.640 | — |
| W23 | SOLUSDT | SEMANTIC_FAR_CAPITULATION_SYNCHRONIZED | ORIGINAL_STRUCTURAL_STOP_TOUCHED | — | — | — |
| W27 | BTCUSDT | SEMANTIC_FAR_ROTATION_TRANSFER_UNANIMOUS | ORIGINAL_TARGET_CONSUMED | — | — | — |
| W28 | ETHUSDT | SEMANTIC_FAR_CAPITULATION_SYNCHRONIZED | REARM_CONFIRMATION_FOUND | LOSS_STOP | 6.840 | -1.000 |

## Causal contract

- Signal search begins strictly after the Nautilus parent expiry event.
- The breakout bar is not included in the prior pullback boundary.
- The retest limit cannot fill on the confirmation bar.
- Original target/stop consumption invalidates rearming.
- Same-bar entry/target ambiguity is not credited as a win.
- Portfolio overlap and final fills must be verified in NautilusTrader before adoption.
