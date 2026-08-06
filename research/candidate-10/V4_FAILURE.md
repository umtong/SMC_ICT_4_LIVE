# Candidate 10 v4 — Controlled Logic Failure

## Final classification

`DISCARDED_LOGIC_FAILURE`

The original v4 result contained a protective-order lifecycle defect. That implementation defect was isolated and corrected without changing the signal state machine, market data, selected week, fees, seed, entry, stop, target, or 3% current-NAV sizing:

- submit only the passive parent entry;
- cancel the unfilled parent remainder after first execution;
- protect each actual fill quantity with independent reduce-only exits;
- trigger protection from raw last trades;
- exit immediately at market if a fill has already crossed stop or target;
- normalize execution-event evidence states.

The controlled rerun completed in NautilusTrader 1.230.0 with 36 tests passing, causal integrity passing, and zero order errors. The remaining loss is therefore a strategy-logic result, not the earlier bracket-activation implementation error.

## Controlled first-week result

Preselected BTC week: `2023-10-16`

| Metric | Full same-side flow | Exact price-only ablation |
|---|---:|---:|
| Acceptance plans | 118 | 166 |
| Parent orders | 50 | 74 |
| Closed trades | 34 | 55 |
| Wins / losses | 6 / 28 | 10 / 45 |
| Ending NAV | 49,891.2577 | 40,722.6126 |
| Net return | -50.1087% | -59.2774% |
| Daily geometric growth | -9.4558% | -12.0447% |
| Intraday maximum drawdown | 52.9533% | 59.2801% |
| Gross price PnL before commissions | -30,596.5885 | -35,789.5340 |
| Reported commissions | 19,512.1537 | 23,487.8534 |
| Order errors | 0 | 0 |

The flow requirement removed trades and damage, so executed-flow direction retained limited discriminatory information. It did not create positive gross expectancy or a recoverable account path.

## Dominant failure event

The result was not 34 independent opportunities. During one compressed event on `2023-10-16 05:16–05:17 UTC`:

- 13 trades opened from the same cascade;
- 11 lost;
- aggregate net PnL was `-48,557.4662 USDT`;
- aggregate commissions were `13,304.2806 USDT`;
- gross price PnL before commissions was `-35,253.1856 USDT`;
- the cluster contributed about 96.9% of the full system's net loss.

The full and price-only ablation produced the same dominant 13-trade cluster. Same-side flow did not address the main failure mechanism.

## Failure mechanisms

### Event-bar degeneration

The notional threshold became small relative to individual aggregate trades. Many event bars contained one effective trade, mechanically producing `abs(delta_ratio)=1` and `price_efficiency=1`; empirical thresholds also converged to 1. These were not independent evidence of durable acceptance.

### One liquidity cause became many scenario IDs

Fast and macro boundaries changed after almost every print. Alternating boundary breaks created repeated long and short entries inside one cascade. A new numerical boundary did not represent a new liquidity event.

### Stress impact exceeded the declared reserve

Several fills occurred while price moved hundreds of dollars within milliseconds. Stops operated causally, but the two-tick reserve was not representative of stress-event gap risk. This cannot be repaired by an arbitrary nominal cap; state-dependent impact must enter planned loss before sizing.

### Expectancy was negative before costs

Full gross price PnL before commissions was `-30,596.5885 USDT`. Fee refinement cannot rescue v4.

## Useful components retained

1. Same-side aggressor flow removed 21 trades and improved ending NAV by about 9.17 percentage points versus the exact price-only ablation.
2. A pre-existing larger-scale liquidity target is more coherent than a fixed-R target.
3. Raw-trade stop/target observation and whole-NAV planned-loss sizing are reproducible in NautilusTrader.
4. Execution lifecycle and scenario-state transitions must remain separate.

## Structural decision

v4 is not extended with more thresholds, cooldowns, or hand-selected exclusions. It is discarded. v20 changes the causal object:

- liquidity pools pre-exist the event and carry identity;
- one consumed pool creates at most one scenario;
- OI and executed flow describe leverage state at the pool;
- the first event is never traded;
- a second completed bar classifies rejection versus acceptance;
- state-dependent impact enters planned loss;
- the exact ablation removes only OI state.

## Reproduction evidence

- Controlled workflow run: `31105449233`
- Controlled job: `92629258226`
- Artifact ID: `8969930416`
- Source: `c10_flow_parent_execution.py`, `c10_flow_evidence_fix.py`, `c10_flow_v4.py`
