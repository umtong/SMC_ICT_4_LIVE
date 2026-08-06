# Candidate 08 executed results

## Status

**No success claim.** Candidate 08 has a verified native NautilusTrader execution foundation and a
fully causal shared-account research protocol, but no promotable system has yet demonstrated the
project target.  The current successor is `candidate-08-auction-router-nautilus-v1`; its first fixed
week is staged in GitHub Actions, and promotion remains blocked until fresh NautilusTrader evidence
exists.

This file distinguishes implementation failures from logic failures.  A run is performance evidence
only when the pinned environment, official-data adapters, causal contracts, shared-account risk
checks, and residual-exposure checks all pass.

## Execution foundation — verified

Commit `02978fca21260fbeabf566ba9d155c7a6e94ef63` completed the native PyO3 production adapter for
NautilusTrader 1.230.0.  The verified foundation contains:

- one Binance margin venue and one shared USDT account;
- BTCUSDT, ETHUSDT, SOLUSDT, and XRPUSDT in one event stream;
- at most one global entry order or open position;
- current full account NAV as the sizing base;
- a fixed three-percent planned-loss budget per trade;
- entry and stop fees, entry tick reserve, causal stop-slippage reserve, and causal funding reserve;
- official funding-rate and completed mark-price updates;
- native liquidation, contingent-order, and account handling;
- no arbitrary maximum notional, leverage cap, or score multiplier;
- strict entry/exit timestamp causality and zero residual exposure after replay.

The production adapter was accepted only after the same deterministic native smoke and all causal,
risk, funding, mark-price, and reporting contracts passed.

## Earlier range/FVG research — rejected

The first fixed 2024-04-08 through 2024-04-15 range/FVG run closed four trades, won one, and reduced
NAV from 100,000.00 to 92,550.21513436 USDT.  Cost-after total return was -7.449785%, daily geometric
NAV growth was -1.099890%, and maximum realized-equity drawdown was 7.449785%.  All four executed
trades belonged to the acceptance family; the rejection family produced almost no usable
opportunity.  This candidate was not promoted.

A broader lower-timeframe multi-asset probe also showed that high nominal reward-risk did not protect
BTC, SOL, or XRP continuation signals from repeated stops.  ETH was less negative and sometimes
positive, but its contribution was too concentrated to justify an asset-specific rule.  The useful
part retained from this work is the completed 4-hour/day/week external-liquidity inventory and the
separation of pattern detection from execution.

## Original acceptance baseline — rejected

GitHub Actions run `31078218914` replayed the same fixed first BTC week with official
checksum-verified Binance Vision data, six basis points per fill, one adverse tick reserve, and
NAV-based three-percent planned-loss sizing.

| metric | result |
|---|---:|
| closed trades | 11 |
| winning trades | 2 |
| win rate | 18.1818% |
| final NAV | 82,611.57175032 USDT |
| total return | -17.388428% |
| daily geometric NAV growth | -2.691966% |
| maximum realized-equity drawdown | 26.4543% |
| profit factor | 0.19048 |
| execution failures / residual exposure | 0 / 0 |

All eleven trades were acceptance continuations.  The baseline therefore failed logically before a
second screen week.

## Controlled sequence revision — rejected

Commit `28f469a6ad0a9e854fdb943fe992f2fabcc09f19` corrected the original same-candle entry sequence by
requiring established liquidity, a held contracted retest, and a later separate continuation
bar.  Risk, fees, stop, target, dates, and the cost-after payoff gate were held constant.

The revised first week still failed: four trades, zero wins, NAV 100,000.00 to 77,993.28 USDT, and
daily geometric growth -3.48838%.  Three rejection trades lost 19,279.51 USDT and the remaining
acceptance trade lost 2,727.21 USDT.  A stale event-state reporting bug was later fixed without
changing trading behavior, but it did not invalidate the already observed economic failure.  A
generic `sweep -> reverse` rule is therefore not carried into the successor.

## Native ten-second acceptance base — clean logic failure

After all native adapter problems were removed, the exact fixed first week was rerun as
`evidence/aggtrade-acceptance-nautilus/first-base-v3`.

| metric | result |
|---|---:|
| closed trades | 3 |
| wins | 0 |
| final NAV | 93,949.88433884 USDT |
| total return | -6.050116% |
| daily geometric NAV growth | -0.887590% |
| maximum realized-equity drawdown | 6.050116% |
| execution failures | 0 |
| unexpected/liquidation closes | 0 |
| residual orders / positions | 0 / 0 |

All three positions hit their observed structural stop.  Every causality, funding, risk-budget, and
account contract passed.  This is a logic failure rather than an implementation failure.

The two BTC continuations were invalidated approximately 90 seconds and 50 seconds after entry.
Their confirmation closes were outside the completed boundary, but their outward displacement was
only about 0.52 and 0.61 of the already-computed causal 60-minute ten-second true-range Q99 reserve.
The detector had classified ordinary within-noise excursions as initiative acceptance.

## Single-variable ablation — useful but not promotable

The required diagnostic ablation removed only the retest activity-contraction condition.  It kept the
same week, data, boundary inventory, reacceleration, structural stop, external target, fees, funding,
shared account, and three-percent risk budget.

| metric | result |
|---|---:|
| closed trades | 3 |
| wins | 1 |
| final NAV | 104,182.48422935 USDT |
| total return | +4.182484% |
| daily geometric NAV growth | +0.587057% |
| maximum realized-equity drawdown | 3.899378% |

The positive result came entirely from one ETH trade (+8,409.79 USDT); the two BTC continuations
still lost -4,227.31 USDT in aggregate.  Positive PnL concentration was therefore 100%, the daily
geometric result was below one percent, and the run was predeclared diagnostic-only.  It cannot open
a promotion path.

The useful mechanism finding is narrower: retest contraction is not a necessary definition of good
acceptance.  The ETH winner's confirmation close displaced about 1.14 causal-noise reserves beyond
the boundary, whereas both BTC losers remained below one reserve.  This observation motivated a
market-state distinction rather than a fitted threshold search.

## Successor: causal auction router v1

The successor separates two economically different scenarios after a completed external-liquidity
interaction.

### 1. Initiative acceptance continuation

1. outward aggressive-flow acceptance beyond a completed 4-hour/day/week boundary;
2. a separate retest which touches and holds the boundary, without imposing artificial volume
   contraction;
3. a later same-direction aggressive-flow bar which breaks the observed retest extreme;
4. the confirmation close must have displaced at least one already-known causal Q99 noise reserve
   beyond the boundary;
5. stop behind the observed retest extreme and target at the nearest active completed external
   liquidity in the continuation direction.

The one-reserve condition is not fitted to a profit table.  It reuses the causal execution-noise
scale that already existed before the signals were examined and asks whether aggressive flow caused
meaningful price progress rather than merely crossing a line.

### 2. Failed-auction reversal

1. an outward aggressive-flow interaction crosses a completed external boundary;
2. price subsequently closes back through that same boundary;
3. a separate inward aggressive-flow bar must break the reclaim-bar extreme;
4. stop beyond the actually observed sweep extreme;
5. target at the nearest active completed external liquidity in the reversal direction.

This is deliberately narrower than the failed historical range/FVG rejection family.  A wick or a
single sweep candle is insufficient.  Reversal requires a state transition from outward auction to
reclaim and then a separate opposite displacement.  It remains unproven until fresh NautilusTrader
results attribute PnL by scenario family.

## Source-stable implementation

The current implementation files are:

- `aggtrade_auction_router_signals.py` — future-free detector and explicit state machine;
- `test_aggtrade_auction_router.py` — initiative, shallow-displacement rejection, failed-auction,
  future-row invariance, and no-outcome-proxy contracts;
- `config_auction_router_nautilus_v1.json` — fixed costs, risk, assets, dates, and gates;
- `run_aggtrade_auction_router_nautilus.py` — verified runner wrapper and scenario attribution;
- `.github/workflows/candidate-08-auction-router-nautilus-v3.yml` — staged first-week then
  three-week validation.

Commit `0e030726949cc76793e905cb4071ae5db2c40dd8` removed a fragile workflow-time strategy rewrite.
The wrapper now normalizes scenario metadata only after Nautilus execution, so execution, sizing,
funding, liquidation, and orders remain identical to the verified production adapter.  The
source-stable staged workflow was added in commit
`764740aadb3547b2ec3d96d9331d9bf4e9c345da` and triggered by commit
`8fd348c8b4ed11d92777973c3762e54f477daeec`.

`evidence/auction-router-nautilus-v1/frozen-signal-transition-diagnostic.json` applies the new
initiative rule to the three already frozen ablation signals.  It keeps the ETH winner and rejects
the two BTC losers, but is explicitly labelled a logic-transition diagnostic rather than new
performance evidence.  It does not contain new failed-auction signals and cannot support promotion.

## Promotion and ablation protocol

1. Run the fixed 2024-04-08 through 2024-04-15 NautilusTrader week.
2. If an implementation contract fails, change no strategy variable and rerun the same week.
3. If the implementation is valid but the first-week gate fails, attribute PnL and opportunity by
   `INITIATIVE_ACCEPTANCE_CONTINUATION` and `FAILED_AUCTION_REVERSAL`.
4. Perform exactly one economic-family ablation: remove the family whose independent contribution
   invalidates the router, while holding all other logic and execution assumptions fixed.
5. If no structural improvement path remains, discard auction-router v1.
6. Only a passed first week may execute the other two fixed weeks.
7. Only all three passed fixed weeks may open a predeclared long evaluation.

No numeric parameter sweep, asset-specific optimization, risk reduction, leverage cap, or
performance-target fitting is permitted in this protocol.

## Current blocker

GitHub-hosted workflow run `31119226020` is still queued without a job start.  Other workflows in the
repository are running, so this is recorded as an external runner-queue blocker rather than a
candidate result.  No claim is made about auction-router v1 performance until committed
`suite_metrics.json`, account reports, fills, positions, and stage decision evidence exist.
