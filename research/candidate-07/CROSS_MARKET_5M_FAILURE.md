# Five-Minute Cross-Market Liquidation Transfer — Failure Record

## Classification

`LOGIC_ERROR / DISCARDED`

The candidate paired every causally confirmed fifteen-minute perpetual swing
pool with the index high/low of the same completed pivot bar.  At first contact
it compared ATR-normalized perpetual and index penetration, OI release,
aggressor flow and a causal basis-change rank.  Raw crossings consumed the pool
immediately, including contacts which failed later classification.

## Hypothesis

- low index transfer plus attack-direction basis expansion -> futures-only
  overshoot, then prompt reclaim reversal;
- high index transfer plus limited basis distortion -> common price discovery,
  then joint outside-hold continuation.

The diagnostic created no orders or hypothetical NAV.

## Frozen BTC Week-1 baseline

Period: `2025-12-22` through `2025-12-29` exclusive.

| Measure | Result |
|---|---:|
| Futures-only overshoot contacts | 0 |
| Common-price-discovery contacts | 13 |
| Common routes invalidated before entry | 11 |
| Entry-ready paths | 2 |
| Active days | 2 |
| Targets | 0 |
| Stops | 0 |
| Timeouts | 2 |
| Median exit-safe MFE | 0.5910 R |
| Median exit-safe MAE | 0.8684 R |

Both entry-ready paths were lower-pool short continuations.  Their structural
stops were more than three ATR wide and neither reached the nearest confirmed
five-minute target during the two-hour diagnostic horizon.

## Single controlled ablation

Removed exactly one core route:

`COMMON_PRICE_DISCOVERY_CONTINUATION`

All data, pool mapping, OI/flow contact requirements, cross-market transfer
classification and first-touch consumption were unchanged.

| Measure | Baseline | Ablation |
|---|---:|---:|
| Entry-ready paths | 2 | 0 |
| Active days | 2 | 0 |
| Futures-only overshoot scenarios | 0 | 0 |

The ablation left no scenario.  The five-minute representation did not expose a
futures-only overshoot edge, and the only available common-discovery route was
not economically strong enough.

## Primary failure cause

Five-minute high/low aggregation largely erased the timing distinction the
hypothesis was intended to capture.  Perpetual and index both registered the
same broad five-minute swing penetration even when the perpetual may have led
for seconds or individual minutes.  Once both markets were classified as common
price discovery, an additional joint hold arrived too late or required a very
wide structural stop.

This is distinct from the earlier MTF valuation failure, where entry waited
until basis contraction had already reached fair value.  Here the problem is
that contact classification itself was too coarse.

## Components retained

- a perpetual pool and index reference must share an already completed pivot
  timestamp;
- cross-market movement should be normalized by each market's own past-only ATR;
- basis change should be ranked causally, not compared with a fixed BTC-specific
  number;
- ambiguous transfer ratios should not be forced into a route;
- raw first touch consumes a pool;
- internal-five-minute then external-fifteen-minute target hierarchy remains
  preferable to remote external targets alone.

## Next independent candidate

Use one-minute perpetual/index contact geometry while retaining only the latest
completed five-minute OI state:

- fifteen-minute pool formation remains unchanged;
- contact and reclaim/acceptance are observed on completed one-minute bars;
- one-minute basis impulse and transfer ratio classify futures-only overshoot
  before it disappears inside a five-minute aggregate;
- OI is backward-as-of from the latest completed public five-minute snapshot and
  must already show release at contact;
- continuation remains available only if index confirms the break promptly;
- stop and target remain causal structural levels.

## Evidence

- Baseline source commit: `59600b1fed6f847fdab075f0fe550b0280e426f5`
- Baseline run: `31112863418`
- Baseline artifact SHA-256: `e9e7001b933173f7cead1b3b75a52e3dd9671d17b71fafb7f393fbcf90aa0f34`
- Ablation source commit: `1e9f8012ab77221fe775b096e6fe81ee5394330b`
- Ablation run: `31113198829`
- Ablation artifact SHA-256: `7468ce2811b9b1c7ba4b48bd6384c4be8a8c2e298d5cb7fa33ee16f7a148db6c`
