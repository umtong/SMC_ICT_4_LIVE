# Candidate 06 v1.9 — Failed-Acceptance Trap Resiliency (FATR) Terminal Report

## Terminal decision

`DISCARDED BEFORE MARKET CAMPAIGN — STRUCTURAL OPPORTUNITY UPPER BOUND`

FATR was not discarded because the passive-depth detector failed a backtest. It was discarded because trade-level decomposition of its unchanged parent scenario proves that no filter-only variant can satisfy the frozen first-week opportunity gate.

## Implementation / logic separation

The first workflow attempt stopped before any market run at an implementation-contract assertion. The workflow imported the mixin under the wrong class name and searched the generic execution method for a configuration literal that is intentionally supplied at runtime. The fixed Nautilus environment, source registration, and causal detector unit tests had passed before that assertion.

That defect was an implementation-verification error. It changed neither the FATR hypothesis nor any market, cost, risk, or execution rule.

Before rerunning, the parent `trap_reclaim_body_flow` trade ledger was decomposed by `family`. This exposed a structural impossibility independent of the implementation defect, making a data-heavy rerun unnecessary.

## Parent first-week trade decomposition

Frozen BTC week: `2024-02-26` through `2024-03-04` UTC.

The unchanged parent produced seven closed trades:

| sequence | resolved family | direction | cost-after PnL (USDT) | outcome |
|---:|---|---|---:|---|
| 1 | SAC | LONG | +7,056.5410 | win |
| 2 | FAT | SHORT | -3,211.3725 | stop |
| 3 | SAC | LONG | -3,115.2022 | stop |
| 4 | SAC | LONG | +9,348.2403 | win |
| 5 | SAC | SHORT | -3,301.9461 | stop |
| 6 | FAT | SHORT | -3,203.6743 | stop |
| 7 | SAC | LONG | -3,107.8587 | stop |

Therefore:

- actual failed-acceptance reversal trades: `2`;
- FAT wins: `0`;
- FAT cost-after PnL: approximately `-6,415.05 USDT`;
- parent total: `7 trades`, `2 wins`, approximately `+464.73 USDT`;
- all positive contribution came from the five SAC continuation trades, not the FAT branch.

Removing the two FAT losses leaves the SAC subset at approximately:

- `5 trades`;
- `2 wins`;
- `+6,879.77 USDT`;
- profit factor approximately `1.72`.

## Why the full FATR candidate cannot pass

FATR is a confirmation gate. It can reject an already completed FAT trade, but it cannot create a new FAT event or a new winning trade. Consequently its first-week theoretical maxima are bounded by the parent ledger:

```text
maximum total trades <= 7
maximum positive trades <= 2
```

The frozen project gate requires:

```text
trades >= 10
positive trades >= 5
```

Even an oracle depth detector that rejected both FAT losses and retained every profitable parent trade would end with only five trades and two wins. The full FATR candidate is therefore mathematically unable to pass the fixed first-week gate. Running the normalized passive-depth archive would consume resources without changing this decision.

This is a logic/opportunity-set failure, not a parameter failure. Threshold tuning, an extra session filter, a looser depth condition, or a different depth aggregation cannot repair the missing independent opportunities while preserving FATR as the declared one-variable filter.

## Controlled ablation conclusion

The parent price-and-flow branch already supplies the required ablation:

- `depth confirmation removed`: seven trades, of which only two are FAT and both lose;
- `depth confirmation enabled`: can only select a subset of those two FAT trades while leaving the SAC branch unchanged.

The largest possible causal benefit of depth is removal of approximately `6.42%` of starting NAV in FAT losses. This would improve the parent result but still leave the candidate structurally below the fixed trade and positive-trade gates. No further ablation is justified.

## Useful components retained

Although the candidate is discarded, the following implementation components remain valid research infrastructure:

1. normalized official `bookDepth` observations indexed by event time;
2. strict decision-time causality: no observation after the completed decision bar;
3. missing/stale-depth abstention rather than imputation;
4. source-side resiliency and target-path asymmetry diagnostics;
5. explicit separation of the passive-depth detector from the price/flow trading scenario.

They are not evidence that passive depth has trading alpha in this candidate.

## Most important successful component in the failed parent

The profitable component was the SAC continuation subset:

- five trades;
- two wins;
- approximately `+6.88%` cost-after PnL on starting NAV;
- profit factor approximately `1.72`.

The parent diagnostics also show:

- `19` SAC confirmations passed;
- `13` were rejected because the favorable move had already been consumed;
- `5` were rejected because delayed cost-after reward/risk had eroded;
- only `7` entries were submitted across SAC and FAT.

This identifies the next structural research question: the extra completed-bar entry confirmation delays an already accepted auction until much of its favorable path has been consumed. The next candidate must change entry timing/placement rather than append another directional filter.

## Next candidate boundary

The next experiment must preserve the completed-auction acceptance/retest logic and structural stop/objective while testing a causally earlier execution contract. It must first compare against all previously executed auction-entry variants to avoid duplicating a failed no-delay or structural-entry design.

FATR is not eligible for holdout or long evaluation.
