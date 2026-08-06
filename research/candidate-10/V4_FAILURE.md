# Candidate 10 v4 — Controlled Logic Failure

## Final classification

`DISCARDED_LOGIC_FAILURE`

The original v4 run contained a protective-order lifecycle defect. That implementation defect was isolated and corrected without changing the signal state machine, market data, selected week, fees, seed, entry, stop, target, or 3% current-NAV risk sizing:

- submit only the passive parent entry;
- cancel the unfilled parent remainder after first execution;
- protect each actual fill quantity with independent reduce-only exits;
- use raw last-trade triggering;
- exit immediately at market when a fill has already crossed stop or target;
- normalize execution-event evidence states.

The controlled rerun completed in NautilusTrader 1.230.0 with 36 tests passing, causal integrity passing, and zero order errors. The remaining loss is therefore a strategy-logic result, not the previously identified bracket-activation implementation error.

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

The order-flow requirement reduced the number of trades and damage, so executed-flow direction retained some discriminatory information. It did not create positive gross expectancy or a recoverable account path.

## Dominant failure event

The result was not 34 substantially independent opportunities.

During one compressed BTC event on `2023-10-16 05:16–05:17 UTC`:

- 13 trades were opened from the same market cascade;
- 11 lost;
- aggregate net PnL was `-48,557.4662 USDT`;
- aggregate commissions were `13,304.2806 USDT`;
- gross price PnL before commissions was `-35,253.1856 USDT`;
- the cluster contributed approximately 96.9% of the full system's total net loss.

The full and price-only ablation produced the same thirteen-trade dominant cluster. Therefore same-side aggressor-flow confirmation did not address the main failure mechanism.

## Why the state machine failed

### 1. Event-bar degeneration in high activity

The notional event threshold became small relative to individual aggregate trades. Many event bars then contained one effective trade, mechanically producing:

- `abs(delta_ratio) = 1`;
- `price_efficiency = 1`;
- empirical flow and efficiency thresholds equal to 1.

These values were not strong independent evidence of durable price acceptance. They were a consequence of the event-bar construction. The state machine repeatedly interpreted single-trade prints as newly confirmed auctions.

### 2. One liquidity event was counted as many scenarios

The fast dealing range and macro boundary changed after almost every print. Alternating upward and downward boundary breaks created new scenario IDs inside the same cascade. A single market event therefore generated repeated long and short entries instead of one auction identity with one terminal result.

This violates the project's independence requirement. A new numerical boundary does not necessarily mean a new liquidity cause.

### 3. The execution-loss distribution exceeded the modeled reserve

Several parent fills occurred while price was moving hundreds of dollars within milliseconds. Stops operated causally, but the two-tick execution reserve was not representative of stress-event gap risk. Examples include short entries whose stop was about 30 USDT away but whose next executable exit was hundreds of dollars above the stop.

This is not repaired by adding an arbitrary nominal cap. The scenario must estimate state-dependent impact before sizing and must not repeatedly enter a cascade whose liquidity is already impaired.

### 4. Negative signal expectancy existed before costs

Even excluding commissions, full gross price PnL was `-30,596.5885 USDT`. Costs amplified failure but were not its sole cause. Fee refinement cannot rescue v4.

## Useful components retained

The following observations remain useful but are not a complete strategy:

1. Same-side executed aggressor flow removed 21 trades and improved ending NAV by about 9.17 percentage points versus the exact price-only ablation.
2. A pre-existing larger-scale target is more coherent than an arbitrary fixed-R target.
3. Raw-trade stop and target observation plus current-NAV loss sizing can be implemented reproducibly in NautilusTrader.
4. Execution and evidence lifecycle must remain separated from scenario-state transitions.

## Structural path decision

v4 is not extended with more thresholds, cooldowns, or hand-selected exclusions. It is discarded.

The next generation changes the causal object rather than filtering v4:

- liquidity pools must pre-exist the event and carry identity;
- one consumed pool can create at most one scenario;
- a leverage shock is observed with open-interest state and executed flow;
- no first-event entry is allowed;
- a second completed bar must classify rejection versus acceptance;
- impact is state-dependent in the planned loss calculation;
- the exact ablation removes only open-interest state.

This is implemented as v20, `Liquidation Auction Rejection / Acceptance`.

## Reproduction evidence

- Controlled workflow run: `31105449233`
- Controlled job: `92629258226`
- Artifact ID: `8969930416`
- Branch source: `c10_flow_parent_execution.py`, `c10_flow_evidence_fix.py`, `c10_flow_v4.py`
