# Delayed Boundary Reacceptance V1 — Final Decision

## Decision

`candidate-08-delayed-reacceptance-btc-nautilus-v1` is discarded.

The final reproducible staged protocol completed with status zero after all implementation and
evidence defects were separated from economic behavior.  The fixed first BTC week failed in the
base form, and the one predeclared diagnostic ablation also failed.  No threshold, stop, target,
expiry, risk rate, leverage, cost assumption or week was changed after observing outcomes.

## Frozen scenario

The candidate required the following causal sequence:

1. interaction with an already completed external-liquidity level;
2. a strictly post-interaction outward response, with no entry;
3. a later close back through the interacted boundary;
4. a separate post-reclaim outward initiative response;
5. a break of the frozen counter-auction extreme;
6. a native NautilusTrader market OUO bracket;
7. stop beyond the observed counter-auction structure; and
8. target at the active completed external-liquidity level selected before entry.

The only ablation removed the requirement that the first outward response itself be classified as
`INITIATIVE_RESPONSE`.  It retained outward direction, outside-boundary close, reclaim, separate
initiative reacceptance, counter-auction break, stop, target, costs, funding, current shared NAV and
the three-percent risk contract.  The ablation was diagnostic and never promotable.

## Implementation failures isolated before economic judgment

### 1. Missing observable event transition

The detector actually passed through
`INTERACTION_ARMED -> INITIAL_OUTWARD_RESPONSE`, but the serialized research evidence contained
only the armed, reclaim and confirmed events.  Event-log validation correctly rejected the broken
state chain.  V3 restored the omitted transition and generalized the merger to preserve complete
variable-length event chains.

This was an implementation/evidence error.  It did not justify changing the market scenario.

### 2. Fill-adjusted risk calculation omitted the causal stop-slippage reserve

The signal-time quantity used a shifted, causal stop-slippage reserve.  The post-fill recomputation
incorrectly fell back to one tick.  The risk revision retained the same signal and position size but
required the fill-adjusted expected loss to include the original causal reserve.

This was a calculation error.  It did not justify relabeling an over-budget trade as valid.

### 3. Forced-risk exit violated native callback and bar ordering

The first repair requested a forced exit inside `OrderFilled`, before the native `PositionOpened`
callback.  The second requested the exit at the same timestamp as the entry.  Both were rejected by
the event-chain and same-bar causality contracts.

The final revision records `PositionOpened`, cancels contingent children, then requests the forced
exit only on the first separately completed ten-second bar.  This preserves both the three-percent
risk response and causal event ordering.

These were implementation errors.  After they were repaired, the staged runner and evidence
contracts completed cleanly.

## Base economic result

Fixed BTC week: 2024-04-08 through 2024-04-15 UTC.

| Metric | Result |
|---|---:|
| Signals | 1 |
| Closed trades | 1 |
| Wins | 0 |
| Realized PnL | -2,808.14165604 USDT |
| Combined daily geometric growth | -0.4060766972% |
| Close reason | Structural stop |
| Structural first touch | Stop |
| Target reached after close | No |
| Target reached after invalidation | No |
| Complete post-run paths | 1 / 1 |

The trade did not represent a correct direction with merely premature invalidation.  Within the
complete 240-minute horizon the external target was not reached after the stop or after structural
invalidation.  Therefore widening the stop would not repair the scenario logic.

## Single ablation economic and execution-risk result

| Metric | Result |
|---|---:|
| Signals | 5 |
| Closed trades | 5 |
| Wins | 0 |
| Realized PnL | -6,885.32152498 USDT |
| Combined daily geometric growth | -1.0139438698% |
| Structural-stop closes | 2 |
| Fill-adjusted risk forced exits | 3 |
| Complete post-run paths | 5 / 5 |
| Target reached after actual close | 0 |
| Target reached after invalidation | 0 |

Three market entries caused fill-adjusted expected loss to exceed the exact shared-NAV
three-percent budget by small but real amounts:

| Scenario | Fill-adjusted loss / budget |
|---|---:|
| `000009` | 1.0000661247 |
| `000041` | 1.0000096298 |
| `000044` | 1.0000893860 |

They were closed on the next completed ten-second bar and classified as
`ENTRY_FILL_SLIPPAGE_RISK_CONTRACT_FAILURE`, not as Python or NautilusTrader failures.  The two
remaining trades reached structural stops.  None of the five trades reached its frozen external
target after close or invalidation.

The ablation therefore did not reveal a useful but over-filtered base scenario.  It increased
frequency by admitting weaker first responses, while producing five independent losses and no
completed target path.

## Logic conclusion

The failure is not explained by an overly strong first-response state requirement.  A boundary
reclaim followed by a later outward initiative response and counter-auction break is still
insufficient evidence that the external-liquidity auction will continue to the next completed
objective.

The missing information is closer to liquidity supply than to another candle-sequence condition.
Aggressive trades and price response show that pressure occurred, but do not establish whether the
opposing top-of-book was depleted, replenished, withdrawn or resilient.  Adding more OHLC or flow
thresholds to this candidate would fit the observed week without repairing that causal gap.

## Permanent research-method updates

1. Every scenario state that can alter eligibility must have an explicit research event.  Serializers
   may not assume a fixed number of detector states.
2. A native replay can finish economically yet still fail its evidence contract; no PnL judgment is
   valid until both complete.
3. An evidence-complete execution-risk breach is a candidate failure, not an implementation failure.
4. Fill-adjusted expected loss must preserve every causal reserve used to size the original order.
5. Risk exits triggered by an entry fill must occur after native position-open confirmation and on a
   strictly later completed data event.
6. A positive direction excursion is not a successful scenario.  The frozen target, invalidation and
   complete post-run path decide whether the market thesis occurred.
7. A diagnostic ablation that only adds losing trades is evidence against rebuilding the base family.
8. No further parameter relaxation, stop widening or target shortening is permitted for this family.

## Next research direction

The next candidate will separate aggressive execution from liquidity-supply response by combining
checksum-verified Binance USD-M `aggTrades` with top-of-book `bookTicker` updates around completed
external-liquidity interactions.  The intended causal sequence is:

```text
completed external-liquidity interaction
-> aggressive flow and observable top-of-book depletion
-> opposing quote replenishment or withdrawal response
-> boundary reclaim/hold
-> separate quote-and-trade reacceptance
-> next completed external-liquidity objective
```

This is a new scenario family, not a parameterized continuation of Delayed Reacceptance V1.  It will
use NautilusTrader native backtesting and custom data/features rather than a custom backtest engine.
