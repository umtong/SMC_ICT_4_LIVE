# Candidate 08 — Mechanism-First Literature and Transcript Review for V3

This note distinguishes three evidence classes:

1. **market-microstructure research** for observable mechanisms;
2. **official exchange/engine documentation** for data and execution semantics; and
3. **SMC/ICT and order-flow teaching transcripts** for the discretionary sequence that must be
   converted into testable states.

Teaching material is not treated as proof of profitability.  It is used only to recover the intended
order of concepts before defining a falsifiable algorithm.

## 1. Aggressive order flow does not have constant price impact

### Sources

- Taranto, Bormetti and Lillo, *The adaptive nature of liquidity taking in limit order books*,
  https://arxiv.org/abs/1403.0842
- Bouchaud, Gefen, Potters and Wyart, *Fluctuations and response in financial markets: the subtle
  nature of random price changes*, https://arxiv.org/abs/cond-mat/0307332
- Toth et al., *Anomalous price impact and the critical nature of liquidity in financial markets*,
  https://arxiv.org/abs/1105.1694
- Cont, Kukanov and Stoikov, *The Price Impact of Order Book Events*,
  https://arxiv.org/abs/1011.6402

### Mechanism retained

Order signs can remain directionally persistent while the probability and magnitude of a price move
per order change with available and adaptive liquidity.  Therefore:

```text
large aggressive flow != guaranteed continuation
small final progress after large pressure != automatically passive absorption
```

The V3 state must compare pressure with **realized price progress, excursion retention, and the
causal local impact expected from prior completed observations**.  A large candle or volume ratio by
itself is not displacement.

## 2. Fixed physical time can mix different amounts of market activity

### Sources

- Petrov, Golub and Olsen, *Instantaneous Volatility Seasonality of High-Frequency Markets in
  Directional-Change Intrinsic Time*, https://ssrn.com/abstract=3243797
- Bae, Kyle, Lee and Obizhaeva, *Invariance of Buy-Sell Switching Points*,
  https://ssrn.com/abstract=2730770
- Bowsher, *Modelling Security Market Events in Continuous Time: Intensity Based, Multivariate Point
  Process Models*, https://ssrn.com/abstract=934530
- Skouras and Axioglou, *Markets Change Every Day: Evidence from the Memory of Trade Direction*,
  https://ssrn.com/abstract=1735352

### Mechanism retained

A fixed thirty seconds can contain very different activity and information across sessions and
regimes.  V3 keeps thirty seconds frozen for a clean falsification.  An intrinsic-activity clock is a
separate successor, not a parameter variation, and becomes eligible only if V3 path evidence shows
physical-time truncation or dilution.

The successor foundation therefore freezes an activity budget using only prior completed windows
and closes the event when that budget is reached, subject to a fixed timeout.  It is not wired to
trading before V3 produces the required failure mechanism.

## 3. What aggregate trades can and cannot establish

### Sources

- Binance official developer documentation, https://developers.binance.com/en/docs/introduction
- Binance official aggregate-trade and depth-stream documentation under the USD-M and stream API
  references, https://developers.binance.com/en/docs/catalog
- Bookmap, *Absorption Indicator*, https://bookmap.com/absorption/

### Data boundary

Aggregate trades identify executed transactions and the aggressor side.  They do not reveal the full
sequence of passive limit-order additions, cancellations, queue changes, or hidden liquidity.
Bookmap's own absorption description explicitly relies on passive limit transactions and order-book
context.

Consequently candidate 08 uses the name **absorbed price response**, meaning only:

```text
unusually persistent outward aggressive flow
+ observable directional excursion
+ weak retained final progress relative to prior impact
```

It does not claim to have observed passive absorption.  A future passive-liquidity candidate must use
synchronized order-book deltas with sequence continuity, not infer the mechanism from `aggTrades`.

## 4. ICT sequence recovered from transcripts

### Sources

- The Inner Circle Trader, *How to Identify Market Structure Shifts* (2022 mentorship), transcript:
  https://glasp.co/youtube/nQfHZ2DEJ8c
- ICT 2022 Mentorship Episode 6 subtitle archive:
  https://info.quagmyre.com/xwiki/bin/view/Forex/The-Inner-Circle-Trader/ICT-Youtube-Series-2022/ICT-YT-2022-02-04-ICT-Mentorship-2022-Episode-6-srt/
- ICT 2022 Mentorship Episode 22 subtitle archive:
  https://info.quagmyre.com/xwiki/bin/view/Forex/The-Inner-Circle-Trader/ICT-Youtube-Series-2022/ICT-YT-2022-04-30-ICT-Mentorship-2022-Episode-22-srt/
- The Inner Circle Trader, *How Does ICT Liquidity Framing Set Trade Entries and Exits?*, transcript:
  https://glasp.co/youtube/npL3ZXJ5zOU

### Sequence retained

The teaching sequence is not “an old high or low was touched, therefore reverse.”  It is closer to:

```text
external liquidity interaction
-> displacement / market-structure response
-> inefficiency or dealing-range context
-> retracement or later confirmation
-> opposing or next external liquidity objective
```

Episode 6 explicitly describes a run through old lows followed by a move through a short-term high
and a fair value gap.  The liquidity-framing transcript distinguishes internal range liquidity from
external range liquidity.  Episode 22 discusses a structural stop outside the price structure that
created the gap rather than choosing the narrowest stop for maximum nominal return.

### Algorithmic translation

- **Liquidity**: only already-completed 4-hour/day/week external levels in V3.
- **Interaction**: first causal crossing of an active completed level; every crossing consumes the
  level even if no trade follows.
- **Displacement**: pressure-conditioned retained price response, not a candle label.
- **FVG**: optional retracement/dealing-range context, not an independent signal in V3.
- **MSS/BOS**: a state change confirmed after interaction, never a same-bar hindsight label.
- **Target**: nearest active completed external liquidity in the scenario direction; no fitted R
  projection.
- **Invalidation**: the observed response or full sweep structure that contradicts the scenario.

## 5. Execution engine and derivatives accounting

### Sources

- NautilusTrader, *Backtesting*,
  https://nautilustrader.io/docs/latest/concepts/backtesting/
- NautilusTrader, *Backtest accounts and margin*,
  https://nautilustrader.io/docs/nightly/concepts/backtesting/accounts-and-margin/
- NautilusTrader, *Binance integration*,
  https://nautilustrader.io/docs/latest/integrations/binance/

### Contract retained

NautilusTrader settles perpetual funding from `FundingRateUpdate` at funding boundaries and adjusts
account balance and realized PnL.  Candidate 08 therefore replays official funding and completed mark
prices through Nautilus rather than subtracting a post-hoc scalar estimate.  The causal reserve used
for sizing is still computed only from the latest observation available at the signal.

The detector and scenario wrappers are prohibited from constructing their own engine, venue,
account, risk sizing, or order simulation.  They delegate to the verified shared-margin native
runner.

## 6. Implications for the next decision

### V3 passes the fixed BTC first week

Run the remaining two predeclared BTC weeks unchanged.  Do not expand to other assets before the
three-week gate.

### V3 is a clean logic failure with one positive and one negative family

Run the one frozen family-removal diagnostic.  It can support rebuilding a new single-family base,
but cannot be promoted directly.

### V3 has both families negative or no independent family opportunity

Discard V3.  Use the post-run continuous path evidence to distinguish:

- wrong direction/auction classification;
- correct direction after invalidation;
- target too remote despite favorable movement;
- physical-time response window truncation or dilution;
- insufficient independent opportunities.

Only physical-time failure supports the intrinsic-clock successor.  Evidence that aggressive flow
cannot distinguish true passive absorption instead supports a new order-book-delta candidate, which
must use complete synchronized depth rather than reinterpret aggregate trades.
