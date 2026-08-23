# EasyChart v5 decision design

## State machine

```text
STRUCTURE AVAILABLE
    |
    +-- simple touch ----------------------> BOUNCE / ROTATION candidate
    |
    +-- breach + close back inside --------> REJECTION candidate
    |
    +-- breach, ambiguous close -----------> WAITING_RECLAIM
    |
    +-- body closes outside ---------------> WAITING_ACCEPTANCE_HOLD

REJECTION / BOUNCE / ROTATION
    -> same-side event-local OB/FVG formed at structure
    -> first later retest reacts
    -> immutable plan or terminal no-trade

ACCEPTANCE
    -> next decision bar opens and closes outside current projected boundary
    -> first retest holds outside
    -> immutable plan or terminal no-trade
```

## Structure objects

### Horizontal liquidity

A high/low is not available when it occurs.  It becomes available only after the configured right-side bars have closed.  The original event timestamp and the later observation timestamp are both retained.

### Trend line

Two same-span wick pivots are connected only when they form a rising-low or falling-high sequence and no intervening wick violates the candidate line beyond one tick.  A newer compatible line supersedes its immediate predecessor.

### Channel

A line plus the strongest confirmed opposite pivot between its anchors defines an exact parallel edge.  The historical path between anchors must remain inside the candidate channel.  Only later interactions are eligible, satisfying the three-points-before-fourth-interaction rule.

## Interaction clustering

Same-side structures within one tick are one causal cluster.  The cluster keeps every member for audit; it is not converted into a score.  A decision bar that spans selected support and resistance is unresolved because its OHLC cannot establish a trustworthy intrabar sequence. Macro and micro plans are also collapsed when their decision-bar intervals and structure price bands overlap, so the same cascade cannot become two independent trades merely because two scales named it.

## Target selection

- Channel rejection/rotation: opposite edge, projected until entry and then frozen.
- Other scenarios: nearest pre-existing, unspent opposite pivot on the same or larger pivot scale.
- No target: terminal no-trade.
- Target reached before entry: terminal no-trade.

## Execution footprint

For rejection, rotation and bounce, the footprint must:

- be created after the state confirmation;
- have the trade side;
- form while touching the current projected structure;
- be an engulfing OB, or a source-valid FVG with the required displacement;
- receive one later retest.

The OB 2× body ratio is treated as a confidence note, not a mandatory rule, when the OB is already event-local at meaningful structure.  FVG strength remains enforced by the detector because the source definition requires a conspicuous middle candle.

## Explicit terminal outcomes

`INVALIDATED`, `TARGET_SPENT`, `NO_TARGET`, `NO_TRADE_GEOMETRY`, `UNRESOLVED` and `DUPLICATE_EPISODE` are first-class results.  They are not silently discarded; each transition is written to the scenario event log.
