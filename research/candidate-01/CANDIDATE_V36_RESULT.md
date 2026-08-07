# Candidate 01 v36 — Cross-market failed auction result

## Decision

**STOP after frozen BTC week 1.**

The candidate did not open week 2, week 3, a long evaluation, or any ETHUSDT/SOLUSDT/XRPUSDT evaluation.

Authoritative evidence:

- branch: `research/candidate-01`
- frozen week: `2024-01-01` through `2024-01-08` UTC
- workflow run containing both completed NautilusTrader engines: `31144472267`
- workflow head: `cbba753dbf734e7f59a85be3137f6db891485662`
- artifact: `candidate-01-v36-week1-cbba753dbf734e7f59a85be3137f6db891485662`
- artifact id: `8981153069`
- artifact digest: `sha256:89b2bf9af733bade513496716cd40bd153ea35d870b32721b258da8d2062420e`
- authoritative execution engine: NautilusTrader 1.230.0
- confirmation data: official checksum-verified Binance Vision BTCUSDT spot and USD-M futures aggTrades
- execution data: official USD-M aggregate trades represented one-for-one as TradeTicks
- current-NAV risk: 3%
- all-in execution cost: 7 bp per side
- global pending-entry plus position limit: 1

The immutable primary/control evidence files were byte-identical. Their SHA-256 hashes were:

| Evidence | SHA-256 |
|---|---|
| `joint_minute_bars.csv` | `f89d500f8278836841d291c1a8bf799bff750a56f247429f044d5507bddbefb1` |
| `cross_market_sweep_events.csv` | `b06a95627b2dc5fccb5fc45c50dbc9e6f0e2aa574d0f3332e1cb957945923a6a` |
| `cross_market_diagnostics.csv` | `b922e4fa118b2935f1853392e503470dcdd1461725173b982a2990b7e4da6c07` |
| `primary_plans.csv` | `aca74934eba0855060167d688bf2c7d9f1ae1942cc2c7d7107edec8664a66cdd` |
| `control_plans.csv` | `197307830a2ebb51f88f122f23b3839fd372f9bf610d5443d678ceaa7a2e03b0` |

## Frozen scenario

The v36 state sequence was:

1. both spot and futures form a two-sided 30-minute balance;
2. futures make a cost-resolved external-liquidity sweep with aligned aggressive flow;
3. within the next three later completed minutes, futures close back inside the frozen boundary with opposite aggressive flow;
4. primary: spot has not made the corresponding cost-resolved excursion before futures failure;
5. control: remove only spot non-confirmation;
6. enter on the first later official futures TradeTick;
7. invalidate beyond the complete observed futures sweep extreme plus one 7-bp cost buffer;
8. target the opposite frozen futures balance boundary.

No market was assumed to be the permanent price-discovery leader.

## Data and causality validation

All of the following passed:

- `smc4 doctor`;
- exact UTC completed-minute availability;
- 12,960 continuous synchronized spot/futures minutes: two context days plus seven evaluation days;
- zero futures-only or spot-only minutes;
- zero joint-time gaps;
- every archive checksum;
- 30-minute balance causality;
- later-minute failure confirmation;
- later spot-confirmation causality;
- primary subset of control events;
- seven daily NAV marks;
- ended flat;
- zero global-entry-gate violations;
- zero protective-order failures;
- zero liquidation markers.

## Event funnel

| Stage | Count |
|---|---:|
| Cost-resolved futures external sweeps | 189 |
| Completed futures failed auctions | 80 |
| Control plans | 80 |
| Spot-confirmed control-only events | 74 |
| Spot-unconfirmed primary plans | 6 |
| Executed primary trades | 0 |
| Executed control trades | 8 |

The spot non-confirmation variable was highly selective, retaining only 6 of the 80 completed futures failures.

## Authoritative performance

| Metric | Primary: spot unconfirmed | Control: all futures failures |
|---|---:|---:|
| Selected plans | 6 | 80 |
| Closed positions | 0 | 8 |
| Wins / losses | 0 / 0 | 2 / 6 |
| Win rate | 0.00% | 25.00% |
| Total return | 0.0000% | **−9.4626%** |
| Geometric daily return | 0.0000% | **−1.4101%** |
| Profit factor | undefined | **0.4510** |
| Maximum drawdown | 0.0000% | **−13.0635%** |
| Weekly classification | STOP | STOP |

Control rejection counts:

- `COST_DOMINATED`: 37
- `INSUFFICIENT_NET_REWARD_RISK`: 33
- `GLOBAL_POSITION_OCCUPIED`: 2

Primary rejection counts:

- `COST_DOMINATED`: 6

The six primary price-risk fractions were approximately 0.511–0.604, all below the unchanged execution-contract minimum of 0.65. Three of the events had potentially adequate gross destinations, but the full-sweep invalidation remained too close to the post-failure entry after paying 14 bp round-trip costs.

## Implementation errors separated from logic errors

### Implementation errors fixed without changing the frozen strategy

1. **Future expiry-index lookup in diagnostics**
   - The first implementation attempted to look up a not-yet-created future minute to record the scheduled response expiry.
   - Fix: compute expiry as `sweep event time + 3 exact minutes`.

2. **Expired state consumed the current completed minute**
   - After recording an expired response window, the same current minute could not arm a new independent sweep.
   - Fix: clear the expired state and allow the current completed minute to pass through the pattern detector.

3. **Two NautilusTrader engines in one Python interpreter**
   - Primary completed, but the second engine panicked because NautilusTrader 1.230.0 uses a process-global Rust logger that cannot be initialized twice.
   - Fix: launch primary and control in separate Python processes while rebuilding the same deterministic stream from the same checksum-verified cache.

4. **Optional diagnostic timestamp CSV parsing**
   - Pandas serialized nullable nanosecond timestamps in scientific notation; one gate assertion called `int()` directly.
   - Fix: use the existing nullable numeric parser consistently.

These were implementation defects. They were corrected under variable control and the same frozen week, conditions, stop, target, risk and cost were rerun.

### Logic errors

1. **The primary was too sparse.**
   - Spot non-confirmation retained only 6 plans in a full BTC week, below the minimum opportunity requirement before fills were considered.

2. **Every retained primary plan was structurally cost dominated.**
   - The failed-auction confirmation occurred after the sweep had already substantially retraced.
   - A logically correct full-sweep invalidation was then too near the entry to make price risk dominate entry-plus-stop execution cost.

3. **The control did not contain a positive failed-auction edge.**
   - Only 8 of 80 plans survived cost, reward-risk and global-position constraints.
   - Those trades lost 9.46% after costs with PF 0.451.

4. **Spot non-confirmation reduced exposure to a losing event population but did not identify tradable alpha.**
   - Its apparent improvement in return and drawdown came from taking no trades, not from selecting profitable independent events.
   - It did not improve win rate or profit factor and failed the pre-specified discrimination requirement.

## Research lesson carried forward

A cross-market divergence variable can be useful for scenario attribution, but divergence alone does not solve the economic geometry of the trade. A scenario must define, before execution:

- why the event creates directional continuation or reversal;
- a structurally valid invalidation far enough from entry that price risk dominates costs;
- a causally known liquidity destination far enough away to support positive post-cost expectancy;
- enough independent opportunities for weekly compounding.

The next candidate must not widen v36 stops or add filters to rescue this failed reversal. It should use a different causal source of order flow and define cost-resolvable geometry before any performance run.
