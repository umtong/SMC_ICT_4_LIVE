# Candidate 01 v34 — Impact-Saturation Reversal

## Frozen question

Does a completed outside-flow initiative become a more reliable failed-auction
reversal when aggressive-flow effort remains high but its marginal price impact
decays, and the first accepted counterflow pullback then produces greater
marginal price response?

V34 froze v31's outside initiative, first accepted counterflow pullback,
immediate reversal timing, pullback-swing invalidation, opposite completed
20-event structure target, 7-bps-per-side cost contract and current-NAV 3% risk.
The only primary variable was causal impact-saturation asymmetry. The single
control removed only that asymmetry.

## Authoritative first frozen BTC week

- Evaluation: `2025-05-12T00:00:00Z` to `2025-05-19T00:00:00Z`
- Engine: NautilusTrader `1.230.0`
- Execution: official Binance Vision USD-M aggregate trades as one-for-one
  `TradeTick`
- Custom fill, PnL or NAV simulator: none

### Primary — impact-saturation confirmation

| Diagnostic | Result |
|---|---:|
| initiative profiles | 25 |
| saturated initiative profiles | 12 |
| accepted pullback decisions | 13 |
| impact-saturation-confirmed decisions | 7 |
| evaluation plans | 3 |
| Nautilus submissions | 0 |
| closed positions | 0 |
| cost-dominated rejections | 3 |
| total return | 0.0000% |
| geometric daily return | 0.0000% |

All three evaluation plans had positive gross destination distance but their
structural pullback stops left less than the frozen minimum share of planned
loss attributable to price risk after fees. Nautilus therefore rejected all
three before submission. This is a real execution-geometry failure, not missing
fills or a backtest-engine issue.

### Single ablation — remove impact-saturation asymmetry

| Diagnostic | Result |
|---|---:|
| evaluation plans | 7 |
| cost-dominated rejections | 6 |
| Nautilus submissions | 1 |
| closed positions | 1 |
| wins | 0 |
| total return | **-3.0008%** |
| geometric daily return | **-0.4343%** |
| profit factor | **0.0000** |
| maximum drawdown | **-3.0008%** |

Removing the saturation variable admitted four additional plans but did not
repair the structural problem: six of seven plans remained cost dominated and
the only executable trade stopped for the planned account loss.

## Interpretation

The result is not evidence that impact saturation never matters. It shows that
this aggregate-trade-only implementation did not create a viable path toward
the project objective:

- the primary selected no executable trades;
- the ablation did not uncover a missed profitable population;
- the sole executable control trade lost;
- the selected pullback swing was usually too narrow relative to the fixed
  14-bps round-trip cost;
- widening that stop without a newly observed structural invalidation would be
  an arbitrary risk change rather than a scenario improvement.

The useful surviving conclusion is that price progress per aggressive-flow
unit is not enough to identify whether passive liquidity actually absorbed,
withdrew or replenished. The next independent hypothesis must observe a new
market-state variable rather than add another candle, session or score filter.

## Decision

`STOP` — discard v34. Do not open its second or third frozen weeks. Preserve the
Nautilus execution contract and move to independently sourced position or
passive-liquidity state.
