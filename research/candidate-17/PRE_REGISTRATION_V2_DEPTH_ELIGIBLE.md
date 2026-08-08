# Candidate 17 v2 — depth-eligible untouched BTC week

The first deterministic holdout selected 2022-02-07, but every required daily `bookDepth` archive was absent and no feature row became ready. This replacement is selected before reading its features, scenarios, trades, or PnL. It changes only data eligibility, not strategy logic or parameters.

## Frozen strategy

- implementation commit: `3bfc9a45f1fa29aef7479b2deb723944d13b87d0`
- frozen blob identities: unchanged from `candidate-17-v2-holdout.yml`
- config, risk, costs, routing, entry, invalidation, target, and management: unchanged

## Eligible universe

- every Monday from 2023-01-02 through 2025-12-29 inclusive
- exclude development weeks 2023-12-25 and 2024-09-16
- candidate Mondays: 155
- eligibility reason: the strategy requires displayed-liquidity `bookDepth`; the first holdout proved that the earlier selected week had no such archive

## Deterministic selection

- seed: `candidate17-v2-depth-eligible|3bfc9a45f1fa29aef7479b2deb723944d13b87d0|untouched-week-2`
- mapping: integer value of SHA-256(seed), modulo 155
- expected index: 99
- evaluation start: 2024-12-09 UTC
- evaluation end: 2024-12-15 UTC
- build/bootstrap start: 2024-12-06 UTC
- build/bootstrap end: 2024-12-15 UTC

## Authoritative decision

The unchanged gate in `research/candidate-17/config.json` applies. The run fails if mandatory L2 feature readiness is absent, execution integrity fails, or the weekly performance/opportunity gate fails. A positive result with too few independent trades is not success.
