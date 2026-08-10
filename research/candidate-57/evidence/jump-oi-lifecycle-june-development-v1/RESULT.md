# Jump OI lifecycle development anatomy

This is a development-only causal diagnostic. Falling target open interest is defined by the sign of the change across the same completed four-hour impulse; no magnitude threshold was fitted.

| group | rows | win rate | mean R | PF(R) | sum R |
|---|---:|---:|---:|---:|---:|
| all | 23 | 0.391304347826087 | -0.02519450772989175 | 0.9349701708609484 | -0.5794736777875102 |
| target_oi_unwind | 11 | 0.45454545454545453 | 0.026423661883605332 | 1.0804929263755672 | 0.29066028071965866 |
| target_oi_build_or_flat | 12 | 0.3333333333333333 | -0.07251116320893074 | 0.8358202606295528 | -0.8701339585071689 |
| taker_3of4 | 4 | 0.5 | 0.8180079318036382 | 4.831309244894376 | 3.2720317272145527 |
| oi_unwind_and_taker_3of4 | 1 | 0.0 | -0.5541380955383474 | 0.0 | -0.5541380955383474 |
| oi_build_or_flat_and_taker_3of4 | 3 | 0.6666666666666666 | 1.2753899409176332 | 13.758733134989482 | 3.8261698227528997 |

## One candidate per independent boundary (shadow only)

| policy | boundaries | win rate | mean R | PF(R) | sum R |
|---|---:|---:|---:|---:|---:|
| source_max_z | 11 | 0.45454545454545453 | 0.1695238273524902 | 1.4094654272131713 | 1.864762100877392 |
| least_z | 11 | 0.36363636363636365 | 0.3742572821338675 | 2.0526580193459814 | 4.116830103472543 |
| oi_unwind_max_z | 7 | 0.42857142857142855 | -0.03732772357142343 | 0.8976977535175564 | -0.261294064999964 |
| oi_unwind_least_z | 7 | 0.42857142857142855 | 0.18438004010280756 | 1.4943157003430059 | 1.290660280719653 |
| oi_unwind_taker_least_z | 1 | 0.0 | -0.5541380955383474 | 0.0 | -0.5541380955383474 |
