# Candidate 08 flow-response research basis and falsification plan

## Status

This document is a research contract, not a profitability claim. `CAUSAL_FLOW_RESPONSE_EXTERNAL_AUCTION_V2`
remains unpromoted until its pinned NautilusTrader protocol produces valid evidence. The detector is frozen
before that evidence except for implementation errors which violate its stated causal or accounting contract.

The system is not intended to recognize a profitable candle shape. It asks whether unusually persistent
aggressive flow caused, failed to cause, or surrendered a price response at already-completed external
liquidity. Pattern inventory and trading scenarios remain separate.

## Evidence hierarchy

### Primary empirical and mathematical sources

1. Cont, Kukanov and Stoikov, *The Price Impact of Order Book Events*:
   <https://arxiv.org/abs/1011.6402>

   Short-horizon price changes are more robustly related to order-flow imbalance than to trade volume alone,
   and the impact coefficient varies inversely with available depth. The direct implication is that a large
   aggregate-trade bucket cannot be treated as initiative merely because its volume is large. Price progress
   relative to local noise and local impact must be observed.

2. Donier and Bonart, *A Million Metaorder Analysis of Market Impact on the Bitcoin*:
   <https://arxiv.org/abs/1412.4503>

   Bitcoin order flow exhibits concave impact and materially different decay for informed-like and
   uninformed-like flow. The project therefore separates immediate pressure from retained response rather
   than assuming that every aggressive burst has permanent directional information.

3. Bacry and Muzy, *Hawkes model for price and trades high-frequency dynamics*:
   <https://arxiv.org/abs/1301.1135>

   Trade arrivals are clustered, price changes mean-revert at high frequency, and impact relaxes. This makes a
   single ten-second bar an inadequate scenario. V2 requires a completed multi-bucket response and, for a
   reversal, a separate opposite response after absorption.

4. Jaisson, *Market impact as anticipation of the order flow imbalance*:
   <https://arxiv.org/abs/1402.1288>

   Persistent market-order signs and impact cannot be interpreted independently. V2 measures both pressure
   consistency and realized response surprise against a causal local impact estimate.

5. Aloud, Tsang, Olsen and Dupuis, *A directional-change event approach for studying financial time series*:
   <https://doi.org/10.5018/economics-ejournal.ja.2012-36>

   Physical time can over-sample quiet markets and under-represent bursts. This does not alter V2 after its
   evidence freeze, but it defines the successor path if a clean V2 failure shows that the fixed thirty-second
   clock is the structural weakness.

6. Petrov et al., *Instantaneous Volatility Seasonality of High-Frequency Markets in Directional-Change
   Intrinsic Time*:
   <https://doi.org/10.3390/jrfm12020054>

   Directional-change activity was examined across FX, Bitcoin and an equity index. It supports evaluating an
   intrinsic activity clock as a cross-market representation, not using a fitted session label as a trade
   filter.

### Official data and implementation sources

1. Binance public USD-M aggregate trades and websocket documentation:
   <https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Aggregate-Trade-Streams>

   An aggregate trade is organized around executed taker activity. It does not contain passive order
   submissions, cancellations or a reconstructable order-book state. Candidate 08 therefore uses the term
   **aggressive-flow price response**, not order-flow imbalance or proven passive absorption.

2. Binance diff-depth documentation:
   <https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Diff-Book-Depth-Streams>

   True passive-liquidity research requires a correctly synchronized local book. Historical aggregate trades
   cannot be silently promoted into that data contract. A future live depth version must start from snapshots,
   update IDs and gap detection; it is a separate candidate, not an extra V2 filter.

3. NautilusTrader remains the only order, fill, funding, margin, liquidation and shared-NAV engine in the
   performance protocol. The detector emits immutable signals; it never simulates a portfolio path.

### Practitioner and video material

Practitioner material is used to translate discretionary language into falsifiable states; it is not treated as
empirical proof.

- The Inner Circle Trader's public market-structure material repeatedly frames a market-structure shift as a
  sequence involving liquidity interaction, displacement and an imbalance/FVG rather than an isolated break
  label. V2 preserves the sequence principle but replaces discretionary visual interpretation with completed
  external levels, pressure, response, reclaim and separate confirmation.

- Bookmap's official educational material describes absorption as aggressive orders meeting passive
  liquidity and emphasizes confirmation rather than assuming an immediate reversal. Because the historical
  candidate lacks passive depth, V2 claims only *absorbed response*: large outward aggressive pressure with
  excursion but weak retained progress, followed by a distinct opposite initiative response.

These practitioner descriptions are useful semantic constraints. They cannot justify a trade when the actual
available data does not identify the claimed passive mechanism.

## Lessons carried forward from failed candidates

1. **High activity is not direction.** Previous acceptance candidates entered after dramatic activity but
   repeatedly stopped. A volume ratio or imbalance threshold without price response only measures urgency.

2. **A retest label is not a state transition.** Entering on the same bucket which first touched and held a
   boundary collapsed the intended sequence into a candle pattern. Every response state must be completed
   before entry, and a reversal must use a later opposite response.

3. **Invalidation must include everything already observed.** Failed-auction V1 froze the sweep too early.
   V2 stops use the complete observed response or sweep through the completed confirmation bucket.

4. **Earlier events cannot acquire later facts.** The absorption event freezes the absorption-time sweep.
   Confirmation-time extensions are stored only in the final event and final stop geometry.

5. **Configuration text is not configuration.** Every numeric response contract is machine-readable, loaded by
   the production wrapper, revision-checked and compared with the detector dataclass defaults before replay.

6. **A family ablation is not model selection.** One diagnostic removal is permitted only when both families
   traded independently and one was cost-after positive while the other was negative. Both-negative evidence
   discards the candidate; it does not retain the less negative family.

## V2 economic hypothesis

### Initiative continuation

A first interaction with completed 4-hour/day/week external liquidity is followed by a complete response
window which begins strictly after the interaction. Persistent tail aggressive pressure must cause at least one
causal-noise unit of progress, retain at least half of its directional excursion and meet or exceed the causal
local-impact expectation. The close must remain beyond the interacted level.

This is falsified if the first fixed week has fewer than three executable opportunities, non-positive cost-after
NAV return, or repeated structural-stop exits despite valid response attribution. A clean failure is a logic
failure, not a reason to lower the response threshold after inspecting outcomes.

### Absorption reversal

Outward tail pressure must create an excursion but retain less than half of it, finish with less than half a
noise unit of progress, underperform the causal local-impact expectation and reclaim the completed boundary.
A separate response window, beginning strictly after that absorption bucket, must then show opposite
initiative response and break the reclaim extreme. The stop is beyond the complete sweep through that final
confirmation.

This is falsified when apparent absorbed responses continue outward, when opposite responses do not reach
causal targets after costs, or when the family has no independent executed sample. No passive-order claim is
made from aggregate trades.

## Mandatory diagnostic decomposition

Every valid replay must report, by economic family and by symbol:

- armed interactions, response states, geometry rejections and emitted signals;
- closed trades, wins, losses, realized PnL and close reasons;
- entry and exit causality, fill-adjusted and realized loss-budget checks;
- target, stop and timeout counts;
- signal attribution and unclassified counts;
- concentration of positive PnL by trade and by asset;
- residual orders/positions, funding state and liquidation/unexpected closes.

A first-week positive return generated by one isolated trade is not sufficient for the three-week promotion
gate. A clean no-trade result is a logic failure of opportunity generation, not a successful risk result.

## Frozen decision tree

1. Run the fixed BTC first week with both families and the V2 revision.
2. Fix only implementation/evidence-contract errors and rerun the same week.
3. If the first-week economic gate fails:
   - one positive and one negative independently traded family: run one diagnostic removal;
   - both negative, both positive, either untraded, or attribution incomplete: no family ablation.
4. A diagnostic can only support a newly rebuilt base. It can never be promoted directly.
5. Only a both-family base which passes the first week may run the two remaining fixed BTC weeks.
6. Only a three-week base pass may proceed to a predeclared long evaluation or multiasset shared-account
   evaluation.

## Successor trigger: intrinsic response clock

V2 deliberately keeps one fixed thirty-second response window so its hypothesis remains simple and falsifiable.
If V2 fails cleanly and the failure records show that response windows systematically truncate high-activity
moves or dilute low-activity moves, the next independent candidate should replace physical duration with a
causal intrinsic clock:

- start only after the completed interaction;
- accumulate normalized absolute aggressive activity until a predeclared causal activity budget is reached;
- preserve a maximum physical timeout so an inactive interaction cannot remain armed indefinitely;
- measure signed consistency, directional progress, excursion retention and local-impact surprise over that
  variable-length completed event;
- use a causal directional-change/noise threshold as a second completion condition, not as a fitted entry
  filter;
- keep external-level inventory, target selection, risk sizing and Nautilus execution unchanged.

The successor is justified only by timing failure evidence. It must not be introduced merely because V2 PnL is
negative, and its activity budget must be frozen before its own performance replay.

## Explicit non-paths

- no parameter sweep over pressure quantiles, retention, response duration or target RR;
- no outcome-derived regime label;
- no fixed-R target when completed external liquidity is absent;
- no claim of hidden orders or passive absorption from aggregate trades;
- no direct promotion from a single-family diagnostic;
- no change to the current-NAV three-percent planned-loss sizing contract;
- no alternate backtest engine.
