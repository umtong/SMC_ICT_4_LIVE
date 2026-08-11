# Candidate 60 — real micro-auction state forensic V1 decision

## Status

**The exact continuation and absorption trade families are retired. The absorption observation is preserved only as a weak, non-tradable state descriptor. No fresh interval is consumed.**

This is not a binary rejection because the old continuous accounts lost money. The consumed-development forensic separated:

1. directional state information;
2. actual next-open objective space;
3. cost-aware geometry;
4. source bracket and management.

Evidence:

- workflow run: `31495345063`
- immutable source commit: `f7787095f98b27f31fa3766bda13a94ae350269d`
- interval: `2026-04-13` through `2026-04-19` UTC
- exact rising edges: 47
- global three-minute episodes: 46
- exact absorption edges: 34 across all four symbols
- exact continuation edges: 13, all BTC
- round-trip diagnostic cost: 20 bp

## Continuation: state and geometry both fail

The exact efficient-flow balance-break state did not identify a useful continuation distribution.

| horizon | gross mean | gross median | gross positive rate | cost-after mean |
|---:|---:|---:|---:|---:|
| 1m | -0.06 bp | +0.77 bp | 61.5% | -20.06 bp |
| 5m | +0.50 bp | -3.02 bp | 46.2% | -19.50 bp |
| 15m | +0.47 bp | -2.27 bp | 46.2% | -19.53 bp |
| 30m | +1.95 bp | -0.87 bp | 46.2% | -18.05 bp |
| 60m | -0.83 bp | -4.39 bp | 38.5% | -20.83 bp |
| 120m | -7.48 bp | -14.38 bp | 46.2% | -27.48 bp |

The state occurred only in BTC. Removing its best 15-minute observation made the gross mean negative. There is therefore no cross-asset or distributional evidence of a continuing participant constraint.

The source event-close reward/risk also did not survive actual observability. At the next-open entry:

- all 13 events had formally ordered stop/entry/objective geometry;
- only 1 of 13 retained an objective more than 20 bp away;
- mean objective after costs was `-6.62 bp`;
- median objective after costs was `-7.04 bp`;
- the sole positive cost-aware reward/risk was only `0.040R`.

Thus the family was not merely harmed by an imperfect stop or timeout. The classifier described contemporaneous liquidity consumption after most of the exploitable price move had already occurred.

## Absorption: a small directional observation survives, but not an alpha

The exact absorption/reclaim state was directionally better than continuation:

| horizon | gross mean | gross median | gross positive rate | cost-after mean |
|---:|---:|---:|---:|---:|
| 1m | +2.29 bp | +0.85 bp | 55.9% | -17.71 bp |
| 5m | +7.54 bp | +4.08 bp | 61.8% | -12.46 bp |
| 15m | +5.29 bp | +8.92 bp | 61.8% | -14.71 bp |
| 30m | -3.45 bp | +5.57 bp | 55.9% | -23.45 bp |
| 60m | -6.79 bp | -5.26 bp | 44.1% | -26.79 bp |
| 120m | +11.01 bp | +1.51 bp | 50.0% | -8.99 bp |

At 15 minutes, the trimmed-best gross mean remained positive (`+3.76 bp`), so the sign was not created solely by the largest winner. This is useful information: a completed sweep/reclaim with aggressive-flow inefficiency has a weak short-horizon tendency toward the defending side.

It is not a tradable edge under the project contract:

- every horizon remained negative after 20 bp costs;
- the largest 15-minute loss was about `-98.62 bp` gross;
- the state did not create monotonic continuation toward the midpoint objective;
- `ABSORPTION_NO_LIQUIDITY_SWEEP`, a first-failure control, still averaged about `+3.28 bp` gross at 15 minutes, so the full state added only a few basis points over a less-complete observation;
- the 27 events rejected for exhausted source reward space averaged `-9.63 bp` gross, confirming that geometry mattered, but accepting the 34 source events still did not leave enough movement.

At the actual next-open:

- all 34 events retained formally valid stop/entry/objective ordering;
- only 7 of 34 had an objective farther than the 20 bp round-trip cost;
- mean objective after costs was `-4.58 bp`;
- median objective after costs was `-8.44 bp`;
- the seven positive cost-aware reward/risk observations averaged only `0.42R` and were selected by geometry already visible in the consumed interval.

The source bracket therefore attempted to monetize a state whose expected remaining move was smaller than the cost of entering and exiting. Widening the target after observing these paths would no longer belong to the same auction objective and would convert a short failed-auction state into an outcome-fitted hold.

## Unified and one-slot implications

Across the 46 global three-minute episodes:

- 15-minute gross mean was `+3.86 bp` and median `+3.38 bp`;
- cost-after mean was `-16.14 bp` and median `-16.62 bp`;
- only 19.6% were cost-positive;
- only 17.4% had a next-open objective beyond costs.

The 15-minute non-overlapping slot path retained 45 trades and remained `-15.47 bp` per trade after costs. Extending the diagnostic slot to 120 minutes improved the gross mean to `+13.94 bp`, but the median was only `+2.68 bp`, the cost-after mean remained `-6.06 bp`, and the result depended materially on a `+201.72 bp` observation. This does not support changing the holding period after the fact.

## Market-model correction

The useful conclusion is narrower than “order flow does not work”:

```text
aggressive flow + price inefficiency + sweep/reclaim
→ weak evidence that passive liquidity temporarily controlled the next few minutes
→ typical remaining move only a few basis points
→ source midpoint objective often already inside the round-trip cost
→ no durable day-trading alpha
```

Likewise:

```text
persistent aggressive flow + efficient displacement + depth transition + balance break
→ contemporaneous description of completed price discovery
→ no evidence that an unconsumed metaorder remains
```

Static one-minute flow, depth change and completed price geometry cannot identify whether the initiating order is an active informed metaorder with remaining quantity or a finished inventory transfer. The missing information is the **lifecycle** of the participant constraint: order-flow run age, marginal-impact evolution, book refill/resiliency and a causal change point separating active execution from completion.

## Preserved

- explicit distinction between efficient liquidity consumption and absorption;
- actual aggressor flow and book-depth data contracts;
- temporal separation of completed observation and next-open execution;
- balance/sweep geometry as diagnostics;
- the weak absorption sign as a possible context variable, never as an entry rule;
- first-failure controls and cost-aware next-open objective accounting.

## Retired

Do not tune or fresh-test the exact:

- continuation and absorption thresholds;
- one-minute state direction;
- balance midpoint or measured-move objective;
- ATR buffer;
- holding period;
- mode arbitration.

The next microstructure family must model an economically persistent order-flow lifecycle rather than add thresholds to this static snapshot.