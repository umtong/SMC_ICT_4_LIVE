# EasyChart v5 source and translation contract

This contract prevents a profitable-looking implementation detail from silently becoming “what EasyChart meant.”  A rule may enter the decision path only with one of the provenance classes below.

## 1. Source-explicit rules

Derived from the supplied files `00_시작하며`, `01_오더블럭`, `02_FVG`, `03_추세선`, `04_채널`, `05_거짓돌파_Fakeout_함정_Trap` and the supplied Korean VTT transcripts.

1. Market structure supplies direction and range; OB/FVG/Fakeout are institutional-footprint observations, not complete strategies by themselves.
2. An OB is meaningful when it occurs at liquidity or another meaningful structure.  The body is the trading zone; formation-wick extremes are invalidation candidates.
3. An FVG is a three-candle imbalance with a conspicuously large middle candle.  Sweep-linked and OB-linked FVGs are emphasized; stale FVGs can lose function.
4. Trend lines connect meaningful wick lows or highs and are used for direction, bounce and breakout/retest.
5. A channel consists of exactly parallel lines.  At least three points establish it; the next interaction is the first trade candidate.  The opposite edge is the natural rotation objective.
6. A fakeout/trap requires a pre-existing visible structure, a breach/sweep and a return.  A conservative entry waits for recovery/retest.
7. Channel acceptance requires a body close outside and the next bar to remain outside; re-entry means the breakout premise failed.
8. A planned area that is not reached is not chased.
9. Stop placement must invalidate the causal idea.  Targets are prior highs/lows, the opposite channel edge or another pre-existing opposing structure.
10. Source examples sometimes use partial exits, break-even moves and discretionary volume-based exits.  The project operating contract deliberately translates these into one predeclared full target; this is a project constraint, not a claim that the source never managed positions dynamically.

## 2. Deterministic translations of source ambiguity

These are necessary to run the ideas online.  They are not attributed to the source as exact numeric rules.

| Ambiguity | v5 translation | Why it is necessary |
|---|---|---|
| A visual line has zero width | one tradable tick on each side forms the interaction band | real prices and stops require discrete levels |
| A pivot is obvious to a human only after price turns | a pivot becomes observable only after its right-side span has closed | prevents future information |
| “Meaningful” local and larger swings | spans 2 and 6 coexist; no PnL-based span selection | preserves multiple auction scales while making the hypothesis explicit |
| A diagonal line moves while a setup develops | reclaim, hold and retest use the line/channel value at that later timestamp | matches an extended chart line instead of freezing it at the breach |
| Channel target moves with the channel | recalculate until entry and freeze the exact price before order submission | satisfies both channel geometry and the single predeclared target contract |
| A wick breaches but the close is neither clearly in nor out | remain `UNRESOLVED`; do not infer direction | avoids outcome-based labels |
| Several structures are touched by one bar | overlapping same-side structures form one causal cluster; a bar spanning both sides is unresolved | prevents ID-splitting one liquidity event into many trades |
| Macro and micro plans describe the same event | overlapping decision-bar intervals plus overlapping price bands are one episode | suppresses cross-scale trade-count inflation without an arbitrary clock window |
| A first retest fails | consume that retest and terminate the setup | prevents repeated hindsight entries into the same event |
| Trigger bars touch a higher-timeframe level before its decision bar closes | the decision bar owns and classifies that intrabar interaction | lower bars must not delete an event before the designated state bar can observe it |

## 3. Research hypotheses under test

These are falsifiable and may be replaced without rewriting the source history.

- Confirmed wick pivots are a workable machine proxy for meaningful horizontal liquidity.
- `60/15/5` and `15/5/1` are useful macro and micro decision stacks for intraday crypto.
- Pivot spans 2 and 6 represent local and larger auction legs adequately enough for initial diagnosis.
- Two same-side pivots plus the strongest intervening opposite pivot are a workable causal channel construction.
- For rejection, bounce and rotation, a later same-side OB or FVG whose formation touches the structure is an independent event-local displacement footprint.
- For accepted breakouts, outside hold plus first retest is sufficient confirmation without requiring an OB/FVG.

## 4. External methods researched but not inserted as filters

The research reviewed online change-point/state inference, robust line fitting, order-flow imbalance/depth and Critical Decision Method expert elicitation.  Their current role is limited:

- Critical-decision decomposition informs the source case ledger.
- Sequential state reasoning informs the explicit state machine and avoidance of future outcome labels.
- RANSAC-style robust fitting, BOCPD, order-flow imbalance, OI/liquidation and cross-venue features are **not** active trading rules in this version.

They may be introduced only when a diagnosed trade-level failure identifies the exact decision they improve and a simpler observable rule cannot resolve it.

## 5. Invariants not open to parameter search

- Future bars cannot establish current pivots, lines, channels, states or targets.
- One causal event cannot be counted as multiple independent trades.
- Entry, stop and target must belong to one coherent auction leg.
- A pre-existing unspent target must exist before entry.
- Gross RR must be at least 1.0 before entry.
- Risk is always derived from current total NAV and estimated per-unit loss, never from a score.
- Final performance is one four-symbol, one-slot, continuous Nautilus account.
