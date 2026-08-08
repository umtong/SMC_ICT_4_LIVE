# Candidate 15 V4 failure evidence

V4 was an exposed mechanism-development test of a new scenario family, not a
holdout and never a success claim. It quarantined the rejected V1--V3 SCDAM
family and evaluated only persistent cross-market initiative followed by fresh
five-minute MSS/displacement/FVG continuation.

## Frozen result

- protocol: `candidate-15-persistent-cross-market-initiative-v4`
- exposed intervals: E01--E06
- weekly-reset NAV multiple: `0.4449088394`
- daily geometric growth: `-0.019104985267216213`
- closed trades: `104`
- wins / losses: `17 / 87`
- win rate: `0.16346153846153846`
- payoff ratio: `4.225811812418611`
- active intervals: `6 / 6`
- closed-trade path maximum drawdown: `0.7205772733454886`
- initiative activations: `1015`
- submitted continuation plans: `177`
- classification: `CANDIDATE15_V4_DEVELOPMENT_REJECTED`

The result rejected both the alpha hypothesis and the state router. A high payoff
ratio could not compensate for an 83.65% loss rate, and positive growth was
concentrated in a minority of intervals.

## Structural failure decomposition

### 1. Timeframe units were inconsistent

The router aggregated five one-minute bars into a five-minute impulse but divided
that body by ATR computed from one-minute true ranges. A normal five-minute move
therefore appeared several times larger than its comparison scale. Across 42
evaluation days the router observed thousands of three-market common-flow events
and activated 1,015 initiatives, roughly 24 per day. The supposed persistent
information state was ordinary synchronized beta movement.

### 2. Repetition did not prove persistence

Two same-direction common-flow observations were sufficient even when common
markets had not advanced between the first and second events. A second bullish
bar after intervening retracement could activate a bullish state; the flow label
was repeated, but price had not demonstrated continuing information delivery.

### 3. The fixed refresh horizon made state nearly continuous

Every same-direction event refreshed expiry by four hours. Given the excessive
event rate, the state was frequently active without a clearly bounded causal
information episode. Later five-minute structures inherited a broad regime label
rather than the response of one identifiable impulse sequence.

### 4. Flat post-only rejection was classified as an engine failure

In E03 a post-only parent was rejected because its limit would have crossed and
become a taker. The account remained flat; contingent-child rejections followed
because the parent was already closed. This is expected passive non-execution,
not proof of naked exposure. A rejection after a fill remains a safety failure
and must force flattening, but the two cases must be distinguished.

## V5 structural correction

V5 preserves the independent post-activation entry engine but replaces the state
layer:

1. compare each five-minute impulse only with ATR from prior completed,
   non-overlapping five-minute bars;
2. append the current five-minute range after the decision, avoiding
   self-normalization;
3. require at least three markets common to both events;
4. require positive median direction-adjusted log progress between event closes,
   with a majority both advancing and holding beyond the first event origins;
5. set the active horizon to the observed separation between confirming events,
   rather than automatically refreshing four hours;
6. treat an unfilled post-only parent rejection as terminal passive
   non-execution, while any rejection after a fill remains an engine error and
   immediately fail-closes exposure.

E01--E06 remain exposed controlled-development periods. V5 results on them can
only reject or justify a later source freeze and newly predeclared confirmation;
they cannot support a success claim.
