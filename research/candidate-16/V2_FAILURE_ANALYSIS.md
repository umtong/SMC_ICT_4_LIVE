# Candidate 16 v2 failure analysis

## Evidence identity

- Frozen source: `0d43da0256af7d4d2a1aa81dcdb98fec8f625cda`
- GitHub Actions run: `31247106893`
- Artifact: `candidate-16-v2-screen-0d43da0256af7d4d2a1aa81dcdb98fec8f625cda`
- Evaluation: `2024-09-16` through `2024-09-22` UTC
- Engine: NautilusTrader 1.230.0 `BacktestNode`
- Account: one continuous 100,000 USDT margin account
- Planned risk: current whole-account NAV × 3% per entry

The interval was selected deterministically and frozen before v2 output. It is development data from this point onward and must not be reused as untouched evidence.

## Result

**CANDIDATE16_V2_REJECTED**

- ending NAV: `85,576.22883101`
- total return: `-14.423771%`
- geometric daily growth: `-2.200606%`
- closed trades: `21`
- wins / losses: `7 / 14`
- win rate: `33.3333%`
- profit factor: `0.553153`
- expectancy: `-686.846246 USDT/trade`
- realized maximum drawdown: `17.770526%`
- active days: `7 / 7`
- largest winner share: `21.7279%`
- liquidations: `0`
- Nautilus order rejections: `0`
- maximum simultaneous entry intents / positions: `1 / 1`

The integrity checks passed. The result is therefore a strategy/data-representation failure rather than an account-engine or overlap failure.

## Causal funnel

| Stage | Count |
|---|---:|
| parent external-liquidity interactions | 1,384 |
| v1 effort/result failed-auction labels | 427 |
| rejected for no coarse displayed-liquidity defense | 198 |
| failure frozen with no order | 229 |
| parent extreme re-accessed before initiative | 118 |
| failure expired without initiative | 90 |
| strictly later price/flow/book initiative | 20 |
| acceptance coarse-liquidity confirmed | 31 |
| acceptance coarse-liquidity rejected | 54 |
| submitted entries | 21 |

The state sequence worked as designed: the failure bar did not enter, 208 of 229 frozen failures were invalidated or expired, and only 20 later initiatives reached reversal entry.

## Branch attribution

| Branch | Trades | Wins | Win rate | Net PnL |
|---|---:|---:|---:|---:|
| REJECTION | 20 | 7 | 35.00% | `-11,855.280930 USDT` |
| ACCEPTANCE | 1 | 0 | 0.00% | `-2,568.490239 USDT` |

The temporal repair and displayed-depth sign agreement greatly reduced v1's 167 entries, but the surviving reversal branch remained negative with insufficient accuracy.

## What v2 actually measured

The inherited Binance public `bookDepth` files are not top-of-book order-event data. Candidate 05 materializes the last snapshot in each minute of aggregate notional lying within ±1% and ±2% bands, then computes:

```text
depth imbalance = (bid-band notional - ask-band notional) / total band notional
one-minute depth change = percentage change in band notional
```

The v2 rules therefore measured changes in coarse distance-band depth, not:

- best-bid/best-ask queue survival;
- quote-by-quote replenishment after consumption;
- spread recovery in the first seconds;
- repeated refill at the attacked price;
- add/cancel intensity;
- actual top-of-book liquidity withdrawal ahead of the new leg.

A positive one-minute change in the ±1% band can arise because the market moved, orders migrated between distance bands, or far-from-touch liquidity changed. It is not equivalent to defending liquidity replenishing at the boundary.

## Structural conclusion

The v2 hypothesis was not that any positive depth statistic should filter losses. It was specifically that defending displayed liquidity would absorb an attack and that later opposite initiative would own the reversal. The available coarse `bookDepth` representation cannot identify that mechanism with sufficient precision.

The following policy is retired:

```text
minute-level ±1% depth-band sign
+ effort/result reclaim
+ later price/flow/depth-band initiative
→ reversal
```

No adjustment to v1/v2 effort, progress, efficiency, session, direction, holding time, or reward/risk thresholds is justified. Loosening signs to increase trade count or strengthening them using this outcome would be outcome fitting.

## What is preserved

- one parent liquidity episode and one terminal decision;
- explicit `UNRESOLVED / NO TRADE`;
- failure completion without an order;
- a strictly later initiative that owns entry;
- parent excursion as invalidation;
- unconsumed natural-liquidity objective after costs;
- actual-fill protective fail-close;
- NautilusTrader account/execution ownership;
- current-NAV 3% planned-loss sizing;
- one global pending entry or position;
- checksum-verified aggregate-trade data.

## Next information-value step

Move from coarse distance-band snapshots to actual top-of-book dynamics already implemented elsewhere in the project.

Candidate 03 contains checksum-verified Binance `bookTicker` ingestion and quote-preserving compression. The next independent hypothesis must measure resilience after an aggressive shock using event-time or second-time observations:

```text
aggressive attack at an external boundary
→ best quote is consumed or displaced
→ spread and midpoint response
→ defending best-quote size/price reappears and persists
→ attacking flow fails to retain price impact
→ boundary reclaim completes; no order
→ strictly later opposite trade flow + midpoint progress
   + quote support/withdrawal confirms a new leg
→ entry
```

Static pre-shock imbalance alone is insufficient. Resilience is a recovery process after the shock. The next implementation must distinguish spread recovery from depth recovery and must not infer add/cancel behavior from minute distance bands.
