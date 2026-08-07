# Literature, Platform, Video, and Transcript Review V4

## Scope and decision rule

This review supports the **External-Liquidity Quote Resiliency** research family. It does not treat
microstructure papers, platform documentation, or discretionary education as proof that a trading
rule is profitable. Sources are separated by evidential role:

1. **Tier 1 — peer-reviewed or primary research:** establishes measurable market mechanisms and
   limitations.
2. **Tier 1 — official venue/platform contracts:** establishes what the data and execution engine
   actually represent.
3. **Tier 2 — practitioner videos, articles, and transcripts:** supplies hypotheses and qualitative
   sequencing only. Every such claim must be translated into causal observable states and tested.

The system is rejected or retained by frozen, cost-after NautilusTrader evidence, not by the
plausibility or popularity of the source.

## 1. Best-level order-flow imbalance is economically relevant, but not sufficient

### Primary evidence

Cont, Kukanov, and Stoikov, *The Price Impact of Order Book Events*:

- https://arxiv.org/abs/1011.6402
- https://doi.org/10.1093/jjfinec/nbt003

The paper defines best-level order-flow imbalance from limit orders, cancellations, and market
orders. Over short intervals it reports a substantially more stable relation between price change
and order-flow imbalance than between price change and trade volume, with impact inversely related
to market depth.

Huang, Lehalle, and Rosenbaum, *Simulating and analyzing order book data: The queue-reactive model*:

- https://arxiv.org/abs/1312.0563

The model treats the book as a queueing system whose order-arrival and removal intensities depend on
the current book state. Best-queue depletion or insertion inside the spread can change the reference
price. This supports treating queue response as a state transition rather than as a static threshold.

Gould and Bonart, *Queue Imbalance as a One-Tick-Ahead Price Predictor in a Limit Order Book*:

- https://arxiv.org/abs/1512.03492

Queue imbalance contains information about the next mid-price movement in the studied large-tick
stocks. It does **not** establish a portable, cost-after trading strategy, and its evidence is not
specific to Binance perpetual futures.

### Consequences for candidate-08

- Aggressive trade pressure alone cannot define acceptance or failure.
- Displayed bid/ask additions and removals must be observed after a completed external-liquidity
  interaction.
- Quote response is a **scenario-confirmation variable**, not a free-standing entry signal.
- A separate completed bucket must confirm price progress and aggressive-flow direction. This avoids
  interpreting a transient size update as a complete trade scenario.
- Normalization uses only prior completed data. The current response cannot set its own baseline.

## 2. Top-of-book data cannot prove participant identity, hidden liquidity, or full-depth support

### Primary evidence

Cont, Cucuringu, and Zhang, *Cross-Impact of Order Flow Imbalance in Equity Markets*:

- https://arxiv.org/abs/2112.13213

Multi-asset and multi-level order-flow information can explain contemporaneous price changes better
than a single best-level series, while lagged predictive effects decay quickly. The important design
lesson is not to convert contemporaneous association into a long-lived directional claim.

Xu, Gould, and Howison, *Multi-Level Order-Flow Imbalance in a Limit Order Book*:

- https://arxiv.org/abs/1907.06230

Adding deeper price levels improves out-of-sample explanatory fit in the studied Nasdaq stocks.
Candidate-08 only has historical best bid/ask updates for the frozen Binance Vision period, so it
cannot claim to observe the complete liquidity stack.

Frey and Sandås, *The impact of hidden liquidity in limit order books*:

- https://econpapers.repec.org/paper/zbwcfswop/200848.htm

Frey and Sandås, *The Impact of Iceberg Orders in Limit Order Books*:

- https://ideas.repec.org/a/wsi/qjfxxx/v07y2017i03ns2010139217500070.html

Hidden orders affect displayed volume and price-impact interpretation. Repeated replenishment at the
best price is compatible with hidden liquidity, but it is not proof of a specific iceberg order or a
single institutional participant.

### Consequences for candidate-08

The implementation uses deliberately conservative vocabulary:

- `displayed opposing liquidity replenished`
- `displayed opposing liquidity withdrew`
- `same-side displayed support replaced`

It does **not** emit `institutional order`, `smart money detected`, or `iceberg detected`.
Replenishment/withdrawal is accepted only when followed by a causal reclaim/hold and a separate
price-plus-flow confirmation. Portability beyond liquid Binance perpetuals remains an empirical
question.

## 3. Spoofing and cancellation ambiguity require persistence and separate confirmation

### Research evidence

*Learning the Spoofability of Limit Order Books With Interpretable Probabilistic Neural Networks*:

- https://arxiv.org/abs/2504.15908

The work emphasizes that spoofing vulnerability depends on where orders are posted relative to the
book, and that top-level-only descriptions cannot identify all manipulative behavior. It is a recent
preprint and is used here only as a limitation warning.

### Consequences for candidate-08

- A single best-quote size increase never triggers entry.
- Equal-millisecond venue updates are retained in stable update-id order rather than collapsed.
- The detector aggregates a bounded response sequence over separate completed ten-second buckets.
- Reversal requires: external interaction -> opposing displayed replenishment -> boundary reclaim ->
  separate opposite aggressive-flow and quote-OFI confirmation -> frozen reclaim-extreme break.
- Continuation requires: external interaction -> opposing displayed withdrawal plus same-side
  support -> boundary hold -> weaker separate retest -> separate same-direction aggressive-flow and
  quote-OFI confirmation -> frozen retest-extreme break.
- These contracts reduce ambiguity; they do not make spoofing impossible to misclassify.

## 4. Crypto evidence supports studying order flow, not assuming directional alpha

Lensky and Hao, *Learning to Predict Short-Term Volatility with Order Flow Image Representation*:

- https://arxiv.org/abs/2304.02472

The Bitcoin study reports that combining trade and order-book information can improve short-term
volatility prediction. Its target is volatility, not cost-after directional NAV growth. It therefore
supports the informational value of microstructure data but not candidate profitability.

*Explainable Patterns in Cryptocurrency Microstructure*:

- https://arxiv.org/abs/2602.00776

This recent preprint investigates explainable cross-asset cryptocurrency microstructure patterns. It
is treated as exploratory evidence only because it is recent and not a substitute for the project's
frozen-period, native-execution validation.

### Consequences for candidate-08

- The first experiment remains BTC-only and uses pre-frozen weeks.
- No threshold is fitted separately to BTC, ETH, SOL, or XRP.
- Cross-asset evaluation occurs only after the BTC scenario shows repeated, cost-after structural
  merit.
- Volatility prediction, directional accuracy, and profitable execution are kept as separate claims.

## 5. Official data and NautilusTrader contracts

### Binance Vision / bookTicker

Official historical catalog prefix used by the implementation:

- https://data.binance.vision/?prefix=data/futures/um/daily/bookTicker/BTCUSDT/

The archived rows expose update id, best bid price/quantity, best ask price/quantity, transaction
time, and event time. The project independently checksum-verifies every archive and fails closed on
malformed numerics, crossed quotes, nonpositive prices or quantities, transaction-time regression,
or ambiguous equal-time update-id regression.

The `bookTicker` stream semantics are best-bid/best-ask price or quantity updates, not full depth.
The research therefore derives displayed L1 response features but does not reconstruct L2/L3.

### NautilusTrader stable documentation

- https://nautilustrader.io/docs/latest/concepts/backtesting/
- https://nautilustrader.io/docs/latest/concepts/order_book/
- https://nautilustrader.io/docs/latest/concepts/data/

NautilusTrader supports L1, L2, and L3 data with different matching fidelity. L1 quote, trade, or bar
inputs maintain only top-of-book state; they cannot synthesize unavailable deeper liquidity. The
project continues to use the existing verified candidate-08 native execution path with explicit
fees, adverse-tick reserves, official funding and mark prices, liquidation modeling, native orders,
and shared-account risk sizing. The new research code does not implement another backtest engine.

### Execution limitation retained in the decision contract

The current first-week experiment uses quote data to construct causal market-state features while the
existing conservative bar replay remains authoritative for fills. It is not a full historical-depth
walk. A candidate that barely survives only under this L1/bar approximation cannot be promoted;
execution sensitivity must be investigated after strong alpha is first demonstrated.

## 6. Practitioner videos and transcript material: hypothesis generation only

### Jigsaw / Axia Futures

- https://www.jigsawtrading.com/video-resource-library/
- https://www.jigsawtrading.com/blog/order-flow-absorption-market-reversals/

The material describes order flow as information not visible on price-only charts and illustrates
push/pause, reversal, breakout, and absorption. It also warns that absorption need not always be an
iceberg and suggests waiting for other traders to join the reversal rather than buying the apparent
low immediately.

**Translated research hypothesis:** high aggressive selling with repeated bid replenishment is not a
long signal by itself. A boundary reclaim and separate positive aggressive/quote response must occur.

### Bookmap absorption/exhaustion training

- https://bookmap.com/learning-center/en/supply-demand-setups/supply-demand-setups/absorption-exhaustion

The training distinguishes passive absorption from a drop-off in aggressive continuation and
suggests confirmation through failed pushes, aggressive-flow reversal, or cumulative-volume-delta
change.

**Translated research hypothesis:** separate displayed-liquidity response from the later confirmation
bucket. Do not merge interaction, absorption, and entry into one candle.

### DOM education

- https://orderflw.com/education/dom

The material emphasizes that trapped sellers or buyers alone are insufficient and that initiation,
pulling/stacking, response, and persistence matter.

**Translated research hypothesis:** require both opposing-liquidity behavior and same-direction
support/initiative rather than assigning direction from one side of the tape.

### ICT liquidity transcript material

- https://glasp.co/youtube/U8xH2dEgH5A

The transcript frames old and equal highs/lows as buy-side/sell-side liquidity references. This is
not scientific validation and is used only to retain the project's external-liquidity interaction
vocabulary. External levels are mechanically formed from completed four-hour/day/week periods; the
machine does not visually reinterpret them after the fact.

## 7. Frozen scenario decisions derived from the review

### Base family A — quote-replenished failed-auction reversal

1. A completed external high/low is crossed with normalized outward aggressive pressure.
2. Over no more than three subsequent completed ten-second buckets, displayed opposing liquidity
   replenishment exceeds removal by at least the frozen ratio.
3. Cumulative quote OFI opposes the sweep and price reclaims the external boundary.
4. A separate completed bucket shows opposite aggressive pressure and normalized quote OFI.
5. Price closes through the frozen reclaim-response extreme.
6. Stop is beyond the complete sweep-response extreme plus causal structural buffer.
7. Target is the nearest still-active completed external liquidity in the reversal direction.

### Base family B — quote-withdrawal acceptance continuation

1. A completed external high/low is crossed with normalized outward aggressive pressure.
2. Displayed opposing liquidity removal exceeds replenishment, while same-side support is added
   behind the move and spread returns within the frozen causal bound.
3. Price holds beyond the external boundary.
4. A separate retest touches the boundary with weaker normalized aggressive pressure.
5. A separate completed confirmation bucket restores same-direction aggressive pressure and quote
   OFI and closes through the frozen retest extreme.
6. Stop is beyond the frozen retest extreme plus causal structural buffer.
7. Target is the nearest still-active completed external liquidity in the continuation direction.

### Single predeclared ablation

Remove only the **confirmation normalized quote-OFI direction gate**. All other interaction,
response, reclaim/hold, retest, structure, stop, target, cost, funding, shared-NAV risk, and native
execution contracts remain fixed. The ablation is diagnostic and cannot be promoted directly.

## 8. Known falsification conditions

The family should be discarded rather than threshold-tuned when a clean frozen-week execution shows
one or more of the following without a structural improvement path:

- quote response classifications occur but do not lead to frozen external targets;
- reversal stops occur and the original target remains unreached over the complete maximum-hold
  horizon;
- continuation repeatedly reclaims the accepted boundary after entry;
- the predeclared OFI ablation merely increases independent losses;
- profitability is concentrated in one event, one day, or one family;
- edge disappears after existing fees, stop reserve, funding, and native fills;
- fill-adjusted expected loss repeatedly exceeds the shared NAV three-percent budget;
- the L1 response is too sensitive to a small number of size updates or parser boundaries.

## 9. Research-method learning retained

- Data semantics are part of the hypothesis. Duplicate exchange timestamps and chunk boundaries are
  causal-order contracts, not parser conveniences.
- A plausible discretionary label is never evidence. Every label must map to observable transitions,
  a frozen invalidation, a frozen target, and a complete post-run path diagnostic.
- Implementation failures are repaired and rerun on the same data. Economic failures receive only
  the one predeclared ablation before discard.
- More signals are not progress when they only add independent losses.
- The strongest next step is not a larger parameter search. It is a clean first frozen-week native
  execution that can falsify the two economic scenarios.
