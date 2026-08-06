# Mark/Index-Confirmed Five-Minute Overshoot — Failure Record

## Classification

`LOGIC_ERROR / DISCARDED`

A timestamp precision implementation defect was isolated before the logic was
judged. Mixed pandas rows had converted nanosecond pivot timestamps through
`float64`, changing a handful of low-order nanoseconds and breaking the exact
mark/index pivot lookup. The correction reconstructed pivot and confirmation
times from the original `int64` timestamp column. No data, threshold, route,
stop, target or path rule changed, and the same frozen BTC Week-1 was rerun.

## Hypothesis

A traded-price breach of a confirmed five-minute pool was treated as a forced
futures overshoot only when:

- OI state was a completed release impulse;
- aggressor flow attacked through the pool;
- mark price penetrated the mapped mark pivot while the index did not transfer;
- mark/index premium expanded in the attack direction;
- trade and mark promptly reclaimed while premium contracted.

The detector created no orders or hypothetical NAV.

## Corrected frozen BTC Week-1 baseline

Period: `2025-12-22` through `2025-12-29` exclusive.

| Measure | Result |
|---|---:|
| OI-release contacts lacking attack flow | 35 |
| OI-release/attack contacts failing mark-index overshoot | 70 |
| Mark-confirmed overshoot scenarios | 0 |
| Entry-ready paths | 0 |

The corrected replay completed successfully. Mark-price agreement eliminated
every candidate event rather than revealing a hidden forced-liquidation class.

## Single controlled ablation

Removed exactly one core condition:

`MARK_PRICE_PENETRATION_AT_CONTACT`

Index non-transfer, OI release, aggressor flow, attack-direction premium
expansion, prompt mark reclaim, structural stop, liquidity targets, one-slot
blocking and exit-safe path accounting all remained unchanged.

| Measure | Baseline | Ablation |
|---|---:|---:|
| Entry-ready paths | 0 | 1 |
| Active days | 0 | 1 |
| Targets | 0 | 0 |
| Stops | 0 | 1 |
| Median MFE | — | -0.0019 R |
| Median MAE | — | 1.2418 R |

The sole surviving event was the losing one-minute last-price/index overshoot
already observed on `2025-12-27`. Mark did not penetrate the mapped pool, and
the path stopped without meaningful favorable excursion.

## Primary failure cause

The profitable-looking futures-only distinction from the one-minute predecessor
was not confirmed by the liquidation reference price. At the more frequent
five-minute pool scale, OI release and aggressive last-price flow produced many
contacts, but none combined mark transfer, index non-transfer and premium
expansion. Removing mark transfer admitted only a last-price-only wick which
failed immediately.

Therefore the missing density cannot be repaired by loosening the mark threshold
without discarding the economic reason for the candidate.

## Components retained

- checksum-verified exact-minute trade, mark and index archives;
- exact reference alignment and invalid-data state breaks;
- `int64`-safe pivot/confirmation timestamp reconstruction;
- same-pivot mapping of trade, mark and index liquidity levels;
- raw first-touch consumption and strict post-confirmation activation;
- mark/index premium expansion and contraction as useful diagnostics;
- failure evidence that last-price/index divergence alone is not forced
  liquidation.

## Next independent hypothesis

Use official one-second futures bars to measure **impact resilience**, not simply
whether a reference price crossed a pivot:

- first contact with a causal five-minute pool opens a short event window;
- attack-side signed quote flow must be extreme relative to past completed
  windows while OI already shows release;
- low directional price-path efficiency plus rapid pool reclaim and opposite
  terminal flow identifies absorption/exhaustion;
- stop is beyond the complete event extreme;
- target hierarchy is confirmed one-minute then five-minute liquidity;
- exact mark/index data remain diagnostic context, but are not allowed to erase
  all events before the impact-resilience test is observed.

## Evidence

- Initial implementation-error run: `31115474254`
- Corrected source commit: `5f529c22d025a09808f8dca6710e400167982015`
- Corrected run: `31115816388`
- Corrected artifact SHA-256: `3b818a9b5dc02d706f7048f6edfa3b7fbc22948936fb61313aec13c5b80e03ec`
- Ablation source commit: `a717a423c9b37cc9db503cccca867f90c9838532`
- Ablation run: `31116230546`
- Ablation artifact SHA-256: `d70d6a195762bcb5a0beefaa48c4ae81bc7c001e3be4e400db065ea459a3692a`
