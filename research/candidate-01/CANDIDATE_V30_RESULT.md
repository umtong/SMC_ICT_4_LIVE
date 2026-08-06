# Candidate 01 v30 — Accepted-Pullback Expansion Result

## Frozen question

After v29 showed that waiting for a completed resumption event was too late, v30
asked whether the counterflow pullback itself could define a new continuation
auction leg:

1. keep the v29 cost-resolved outside-flow initiative and first completed
   counterflow pullback which closes outside the accepted boundary;
2. place a NautilusTrader `STOP_LIMIT` one BTC tick beyond the completed
   pullback extreme, with a 7-bp worst-fill cap;
3. invalidate beyond the opposite pullback extreme plus one 7-bp buffer;
4. primary target the second completed structure-width expansion node;
5. single ablation target the first expansion node with every other variable
   unchanged.

## Authoritative first BTC week

- Evaluation: `2025-11-24T00:00:00Z` to `2025-12-01T00:00:00Z`
- Engine: NautilusTrader `1.230.0`
- Execution data: official Binance Vision BTCUSDT USD-M aggregate trades
  represented one-for-one as NautilusTrader `TradeTick`
- Costs: 7 bps per side
- Planned risk: current Nautilus account NAV × 3%
- Maximum hold: four hours
- Custom fill, PnL or NAV simulator: none

### Primary — second expansion node

| Diagnostic | Result |
|---|---:|
| outside initiatives | 47 |
| accepted counterflow pullbacks | 28 |
| evaluation instructions | 10 |
| Nautilus submissions | 6 |
| closed positions | 4 |
| wins | 1 |
| win rate | 25.00% |
| total return | **-7.1757%** |
| geometric daily return | **-1.0581%** |
| profit factor | **0.0502** |
| maximum drawdown | **-7.1757%** |
| maximum-hold exits | 1 |
| stop exits | 3 |
| target exits | 0 |

The positive trade was the four-hour exit and was small after cost. All three
completed stop outcomes lost approximately one planned risk unit. Two submitted
entries invalidated before fill. Three additional plans were cost dominated and
one lacked the frozen 1.35 cost-after reward/risk.

### Single ablation — first expansion node

| Diagnostic | Result |
|---|---:|
| evaluation instructions | 10 |
| Nautilus submissions | 1 |
| closed positions | 1 |
| wins | 0 |
| total return | **-2.5752%** |
| geometric daily return | **-0.3720%** |
| profit factor | **0.0000** |
| maximum drawdown | **-2.5752%** |
| cost-dominated rejections | 3 |
| insufficient net-RR rejections | 6 |

Moving the target to the first node reduced opportunity geometry from six
submitted plans to one and that trade stopped. It did not produce a target hit.

Both variants ended flat with no pending entry, no global-entry-gate violation,
no protective-order failure and no liquidation marker.

## Diagnosis

This is a logical failure, not an implementation failure.

The STOP_LIMIT lifecycle, worst-limit-price risk sizing, pullback-swing
invalidation, contingent protection, fees and NAV accounting all behaved as
specified. The target-distance ablation reduced loss magnitude only by rejecting
most opportunities; it did not reverse the sign of expectancy.

The surviving useful components are:

- frequent cost-resolved outside initiatives;
- frequent completed counterflow pullbacks while the close preserves outside
  value;
- causal conditional entry at a completed swing;
- naturally cost-resolved pullback invalidation for a meaningful subset;
- operationally sound NautilusTrader STOP_LIMIT execution.

The dominant failed assumption is directional: one counterflow event whose
close remains outside does **not** establish that the next auction leg will
continue. The same state can be temporary impact followed by replenishment and
failed acceptance. The first- and second-expansion targets therefore failed for
the same reason; target distance was secondary.

## Decision

`STOP` — do not run v30 week 2 or week 3 and do not tune the expansion multiple.

The next independent candidate preserves the initiative and pullback detector
but removes the continuation prior. It waits for a later loss of the accepted
boundary and then trades a failed-auction reversal toward the opposite edge of
the completed pre-initiative structure. The single control removes only that
boundary-loss confirmation and enters the same reversal immediately after the
pullback.
