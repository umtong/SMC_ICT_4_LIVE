# Candidate 01 v32 — Pullback MSS to Calendar Liquidity Result

## Frozen question

V31's immediate accepted-pullback reversal was weakly positive, while a later
one-tick boundary trigger selected five losing whipsaws. V32 therefore asked
whether replacing the tick trigger with a completed structural reversal and a
causal external-liquidity destination could isolate the valid reversals.

The frozen sequence was:

1. cost-resolved outside-flow initiative beyond completed 20-event structure;
2. first completed opposite-flow pullback whose close preserves outside value;
3. later completed equal-notional event closes through both the accepted
   boundary and the frozen pullback internal swing;
4. primary additionally requires aggressive flow on that MSS event to agree
   with the reversal;
5. market entry on the first official venue TradeTick after the completed MSS;
6. stop beyond the frozen pullback outside extreme plus one 7-bp buffer;
7. target the nearest active unconsumed completed-day/week level strictly
   beyond the opposite pre-initiative structure edge.

The single ablation removed only MSS aggressive-flow agreement.

## Authoritative first BTC week

- Evaluation: `2024-02-26T00:00:00Z` to `2024-03-04T00:00:00Z`
- Engine: NautilusTrader `1.230.0`
- Data: official Binance Vision BTCUSDT USD-M aggregate trades represented
  one-for-one as NautilusTrader `TradeTick`
- Costs: 7 bps per side
- Planned risk: current Nautilus account NAV × 3%
- Maximum hold: four hours
- Custom fill, PnL or NAV simulator: none

### Primary — completed MSS plus aligned aggressive flow

| Diagnostic | Result |
|---|---:|
| full-context outside initiatives | 67 |
| accepted counterflow pullbacks | 43 |
| pullback swing invalidated before MSS | 19 |
| response-window expiries | 21 |
| structural MSS without active calendar target | 4 |
| MSS decisions with causal target | 4 |
| flow-aligned MSS decisions | 4 |
| selected plans | 4 |
| Nautilus submissions | 3 |
| closed positions | 3 |
| wins | 1 |
| win rate | **33.33%** |
| total return | **-4.7086%** |
| geometric daily return | **-0.6866%** |
| profit factor | **0.2035** |
| maximum drawdown | **-6.1104%** |
| failed confirmation hold | 1 |
| target exits | 0 |
| stop exits | 2 |
| maximum-hold exits | 1 |

The selected calendar destinations were all completed-day levels. The minimum
cost-after reward/risk at submission was approximately `3.38`, so target
distance was not the binding rejection variable. Two trades reversed promptly
through the frozen pullback stop. The third reached the four-hour exit with a
small gain. No calendar target was reached.

### Single ablation — completed MSS close without flow agreement

Every one of the four structural MSS decisions already had aligned aggressive
flow. The ablation therefore produced the identical four plans, identical three
submissions, identical fills, identical exits and identical portfolio metrics:

| Metric | Primary | Ablation |
|---|---:|---:|
| trades | 3 | 3 |
| wins | 1 | 1 |
| total return | -4.7086% | -4.7086% |
| geometric daily return | -0.6866% | -0.6866% |
| profit factor | 0.2035 | 0.2035 |
| maximum drawdown | -6.1104% | -6.1104% |

Both paths ended flat with zero global-entry-gate violations, zero protective
order failures and zero liquidation markers.

## Diagnosis

This is a logical failure, not an implementation error.

The candidate produced frequent initiatives and pullbacks, selected only causal
unconsumed calendar levels, retained strong cost-after reward/risk, submitted
orders through NautilusTrader, and closed cleanly. The failure came after all of
those conditions: a completed reversal close through the accepted boundary and
pullback swing still did not establish directional follow-through.

The useful components are:

- the initiative/pullback detector continued to produce many independent
  opportunities;
- pullback-swing invalidation created an executable risk unit;
- calendar targets repaired reward distance without PnL selection;
- completed-event MSS was materially more causal than a one-tick trigger;
- the execution and risk pipeline remained operationally sound.

The dominant failed assumption was still one-sided direction selection. V30
forced continuation, v31/v32 forced reversal. The same accepted pullback can
resolve either way. Aggressive-flow agreement was not discriminating in this
week because every completed structural reversal already had it.

The long calendar destination was secondary rather than the first-order cause:
two losses hit their structural stop before target horizon mattered. Extending
hold time or shortening targets would not repair the wrong directional branch.

## Decision

`STOP` — do not run v32 weeks 2 and 3, and do not tune the MSS distance, flow
threshold, calendar level age or four-hour hold.

The next independent candidate removes the direction prior. It maintains
continuation and reversal branches after the same accepted pullback, invalidates
each branch independently at the opposite pullback extreme, and trades only the
first completed structural resolution which belongs to a still-valid branch.
The single control removes only resolution-event aggressive-flow agreement.
