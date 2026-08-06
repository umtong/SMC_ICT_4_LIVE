# Candidate 08 auction-router research note

## Research question

The system is not trying to discover a candle pattern which happened to earn money.  It asks how a
machine can distinguish two market states after price interacts with already-completed external
liquidity:

1. **initiative auction** — aggressive orders cause and retain meaningful price progress beyond the
   boundary, so the next active external liquidity in the same direction remains a rational target;
2. **failed auction** — aggressive orders cross the boundary but liquidity provision absorbs the
   pressure, price loses acceptance, and a separate opposite displacement confirms that inventory is
   being unwound toward the opposite active liquidity.

The pattern detector owns only observable state transitions.  NautilusTrader owns account state,
orders, fills, fees, funding, liquidation, and portfolio accounting.

## Empirical motivation from candidate 08

### What repeatedly failed

- The first range/FVG week lost 7.45% with three losses and one win.  All executed positions were
  acceptance continuations.
- The original acceptance system lost 17.39% over eleven trades.  A breakout and immediate retest
  did not establish sustained acceptance.
- A controlled established-liquidity/retest/reacceleration revision still lost 22.01% over four
  trades.  Three generic rejection trades accounted for most of the loss.
- The clean native ten-second base lost 6.05%; all three signals stopped despite high nominal
  reward-risk.
- Removing only retest contraction produced +4.18%, but one ETH winner supplied all positive PnL and
  two BTC trades remained losses.
- The earlier ten-second `SWEEP_ABSORPTION_REVERSAL` probe produced four BTC scenarios: three stops
  and one positive timeout.  Its same-bar sweep-and-reclaim definition is rejected as a production
  scenario.
- The second fixed BTC diagnostic week contained only one acceptance continuation and it stopped.

These failures show that nominal reward-risk, activity, imbalance, a completed level, and a separate
confirmation bar are not sufficient.  The missing distinction is whether aggressive flow actually
creates price progress which is large enough to survive the market's already-observed noise and
liquidity response.

### What survived

- Completed 4-hour/day/week high and low inventory is causal and economically interpretable.
- The nearest still-active completed external level is a more defensible target than a fitted fixed-R
  projection.
- Ten-second aggregate trades provide signed aggressive-flow evidence, even though they do not
  expose passive limit submissions or cancellations.
- A shifted rolling true-range reserve is observable before the signal and gives a common price-scale
  unit across assets and volatility regimes.
- The ETH winner displaced 1.14 such reserves beyond its boundary; both clean BTC losers displaced
  only 0.52 and 0.61.
- Retest contraction is not a necessary condition.  Price progress and retention are more directly
  connected to the auction hypothesis.

## Market-microstructure basis

The following papers are used as structural guidance, not as parameter sources.

1. Rama Cont, Arseniy Kukanov, Sasha Stoikov, *The Price Impact of Order Book Events*, arXiv:1011.6402.
   Short-horizon price changes are more robustly related to order-flow imbalance than to trade
   volume alone, and the response coefficient varies inversely with market depth.
2. Zoltan Eisler, Jean-Philippe Bouchaud, Julien Kockelkoren, *Models for the Impact of All Order Book
   Events*, arXiv:1107.3364.  Impact depends on event history; equally signed aggressive events need
   not have equal marginal impact.
3. J. Doyne Farmer, Austin Gerig, Fabrizio Lillo, Szabolcs Mike, *Market Efficiency and the
   Long-Memory of Supply and Demand*, arXiv:physics/0602015.  Persistent transaction-sign pressure
   can be offset by an adaptive liquidity imbalance, making same-direction orders progressively less
   price-effective.
4. Bence Toth et al., *How Does the Market React to Your Order Flow?*, arXiv:1104.0587.  Market-order
   impact reflects competition between liquidity takers and liquidity providers rather than the
   taker stream in isolation.
5. Jonathan Donier, Julius Bonart, *A Million Metaorder Analysis of Market Impact on the Bitcoin*,
   arXiv:1412.4503.  Bitcoin impact is nonlinear and uninformed impact can substantially decay.
6. Carol Alexander, Daniel F. Heck, Andreas Kaeck, Ryan Riordan, *Order Flow Impact and Price
   Formation in Centralized Crypto Exchanges*, SSRN 4867599.  Limit submissions and cancellations
   materially contribute to price discovery on Binance and Coinbase, so trades alone cannot be
   treated as the full order-flow state.

The implication is deliberately modest: Binance `aggTrades` support a signed aggressive-flow proxy,
not true order-flow imbalance.  Without a reliable historical order-book reconstruction for the
fixed April 2024 week, the system must judge passive absorption through the observed price response
and retention, and must not label its trade-only quantity as L1 OFI.

Official Binance public archives provide checksum-verified aggregate trades and klines used by this
candidate.  A historical book feed is not added to the fixed protocol because the required April
2024 USD-M coverage has not been established as complete and reliable in the same source contract.
Adding an inconsistent data regime after observing results would also invalidate comparability.

## Auction-router v1 state machines

### Initiative acceptance continuation

```text
IDLE
  -> EXTERNAL_LEVEL_ACCEPTED
  -> ACCEPTANCE_RETEST_HELD
  -> INITIATIVE_REACCELERATION_CONFIRMED
  -> MARKET_OUO_BRACKET_SUBMITTED
```

Observable requirements:

- boundary existed before interaction and is completed external liquidity;
- high activity and directional aggressive flow close outside the boundary;
- a later bar touches and holds the boundary;
- another later bar breaks the retest extreme with directional body, close location, and aggressive
  flow;
- its close is at least one shifted causal 60-minute ten-second true-range Q99 reserve beyond the
  boundary;
- rounded cost-after geometry remains at least 1.20 before the order is submitted.

Invalidation is the observed retest extreme with a structural buffer.  Target is the nearest active
completed external level in the same direction.

The one-reserve condition is a market-state unit, not a fitted reward score.  The reserve was already
required for causal stop-execution risk and is independent of trade direction and outcome.

### Failed-auction reversal

```text
IDLE
  -> EXTERNAL_LEVEL_ACCEPTED
  -> FAILED_AUCTION_RECLAIMED
  -> INWARD_DISPLACEMENT_CONFIRMED
  -> MARKET_OUO_BRACKET_SUBMITTED
```

Observable requirements:

- the initial interaction first satisfies outward acceptance; a same-bar wick rejection does not
  qualify;
- while acceptance remains pending, every later outward high/low updates the observable sweep
  extreme;
- a later close returns through the completed boundary;
- another later bar breaks the reclaim-bar extreme with opposite directional body, close location,
  aggressive flow, and normal-or-higher activity;
- rounded cost-after geometry remains at least 1.20.

The stop is beyond the complete observed sweep extreme, not merely beyond the reclaim candle.  The
target is the nearest active completed external level in the reversal direction.

This differs from the discarded `SWEEP_ABSORPTION_REVERSAL`: the discarded family armed when the
first crossing candle already closed inside.  The new family requires genuine outside acceptance to
exist and subsequently fail.  The distinction is economic: a failed initiative auction, not a wick
shape.

## Why no additional numeric filters are added now

The research has enough evidence to reject several mechanisms but not enough independent trades to
estimate new numeric thresholds safely.  The following tempting changes are therefore prohibited
before family-attributed fresh evidence:

- optimize the one-reserve multiplier;
- add asset-specific thresholds because ETH produced the only winner;
- rank trades by model score or scale risk by confidence;
- add arbitrary session windows;
- tighten reward-risk until historical losers disappear;
- reverse every rejected continuation;
- reduce the three-percent risk budget to make a weak system appear safer.

## Staged validation and one permitted ablation

1. The fixed first week must produce a complete native NautilusTrader run with no implementation,
   causal, risk, funding, liquidation, or residual-exposure failure.
2. PnL, wins, losses, skipped opportunities, targets, stops, and drawdown must be attributed to
   `INITIATIVE_ACCEPTANCE_CONTINUATION` and `FAILED_AUCTION_REVERSAL` independently.
3. If the gate fails, exactly one family-level ablation is allowed:
   - remove initiative if initiative is independently destructive; or
   - remove failed auction if failed auction merely repeats the historical rejection failure.
4. The ablation is diagnostic.  A remaining family becomes a new base candidate only when its
   mechanism and opportunity rate still make sense; the ablated result itself is not promoted.
5. No second variable is changed in the same comparison.
6. A passed first week opens only the two remaining fixed weeks.  Three passed fixed weeks open only
   a predeclared long evaluation.

## Impact-response successor path if v1 is rejected

If both v1 families fail structurally, the next candidate will not add more SMC labels.  It will use
an explicit causal flow-response mismatch:

```text
signed aggressive flow pressure
versus
observable outward price progress and retention
```

A potential initiative state requires large positive price response per unit of signed aggressive
pressure.  A potential absorption state requires strong outward pressure with weak or decaying
outward progress, followed by reclaim and separate inward progress.  All normalizers must be shifted
rolling distributions available before the decision.  This successor remains a research direction,
not a trading rule, until opportunity and family diagnostics justify implementation.

## Reproducibility anchors

- Verified native adapter: `02978fca21260fbeabf566ba9d155c7a6e94ef63`
- Source-stable scenario attribution: `0e030726949cc76793e905cb4071ae5db2c40dd8`
- Staged workflow definition: `764740aadb3547b2ec3d96d9331d9bf4e9c345da`
- Staged workflow trigger: `8fd348c8b4ed11d92777973c3762e54f477daeec`
- Frozen transition diagnostic: `ca19139e911245deac7515a97864abc1a3972865`
- Current queued staged run: `31119226020`
