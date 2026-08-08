# Why random weeks looked good and longer evaluation failed

**RANDOM_WEEK_EVIDENCE_CONTAMINATED_AND_UNDERPOWERED**

## Finding

The archive disproves the premise that random weeks were always good: multiple preselected first-week tests were negative. The apparent pattern is survivorship across generations. Calendar randomization also did not create independent validation. The same opened weeks were reused across source revisions, sparse trades were treated as five weekly samples, and the development trades concentrated in one short-side latent regime. Fresh data therefore revealed the base rate and domain shift hidden by the adaptively selected point estimate.

## Direct evidence

- The archive contains `4` explicit negative short-week generations, so the premise that random weeks always worked is false.
- The same W10-W14 calendar set was evaluated across `4` source generations.
- Candidate 13's final 7/7 point win rate has an exact 95% lower bound of only `0.590384`.
- Multi-session development was `1.183094%` daily, while the untouched holdout was `-0.077670%` daily.
- Development short share was `90.91%` versus `33.33%` in holdout.
- Combined continuity evidence was `0.708454%` daily, below the project target of `1.00%`.
- Combined observed trade density was `0.250000` per day. Holding its realized average log return per trade fixed would require `0.352371` trades per day to reach 1% daily growth.

## Failure modes

- `SURVIVORSHIP_AND_RESEARCH_MEMORY_BIAS`
- `ADAPTIVE_REUSE_OF_OPENED_RANDOM_WEEKS`
- `UNDERPOWERED_TRADE_SAMPLE`
- `POINT_WIN_RATE_NOT_STATISTICALLY_SECURE`
- `DIRECTION_AND_LATENT_REGIME_CONCENTRATION`
- `DEVELOPMENT_HOLDOUT_DOMAIN_SHIFT`
- `COMBINED_EVIDENCE_BELOW_PROJECT_GROWTH_TARGET`
- `OPPORTUNITY_DENSITY_SHORTFALL`
- `FRESH_CAUSAL_SCREEN_FOUND_NO_EXECUTABLE_EVENTS`

## Binding decision

Permanently classify W10-W14 as development-only and H1-H3 as consumed holdout. No future source may claim validation from either set. Spend another unseen interval only after the new contiguous-block protocol's development gate is met.

This audit is an evidence-integrity result, not an alpha claim.
