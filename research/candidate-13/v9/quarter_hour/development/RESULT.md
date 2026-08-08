# Candidate 13 V9 — quarter-hour common-flow development

**REJECT_OR_REDESIGN**

V9 eliminated the original trade-frequency bottleneck but did not produce usable alpha. It treated the first completed five-minute interval of every UTC quarter-hour as a complete continuation signal, identified the largest ATR-standardized market as the information owner, and placed passive midpoint-retest orders only in confirmed followers.

This is exposed development evidence. It cannot support a success claim.

## Aggregate result

- observed calendar days: `42`
- completed intervals: `6 / 6`
- closed trades: `143`
- wins / losses: `53 / 90`
- win rate: `0.370629`
- payoff ratio: `1.640127`
- pooled independent-week NAV multiple: `0.4472241074`
- daily geometric growth: `-0.0189770405`
- maximum weekly closed-trade drawdown: `0.6754220862`
- submitted plans: `214`
- QH / preserved-core submissions: `203 / 11`
- safety audit: `failed in all six intervals`
- success claim: `false`

## Exact execution-failure removal

Each interval contained one child protective-stop rejection whose trigger was already in the market when the passive parent filled. Excluding only those exact six plan-time/symbol positions gives:

- QH trades: `126`
- wins / losses: `42 / 84`
- win rate: `0.333333`
- payoff ratio: `1.543725`
- realized PnL sum: `-64,792.63 USDT`

Therefore the rejected protective orders created invalid tail outcomes, but they did not cause the strategy failure. The protected subset remains materially negative.

## Mechanism diagnosis

The first five minutes after a quarter-hour boundary contain recurring cross-market activity, but simultaneous direction and taker flow do not establish that the next tradable leg has begun. The shortest-lived entries were the worst group:

- up to 5 minutes: `60` trades, `23.33%` win rate, `-80,824.14 USDT`
- 6–15 minutes: `43` trades, `37.21%` win rate, `-13,693.36 USDT`
- 16–60 minutes: `17` trades, `58.82%` win rate, `+33,424.79 USDT`

This supports a horizon/state mismatch rather than a frequency problem. V9 entered the synchronization burst itself. The next design must use the periodic burst only as an initiative source, wait for a separate completed auction leg, and fail-close any rejected protective child immediately.

## Interval evidence

- E01 `2021-07-12`: 24 trades, 7/17, daily geo `-2.4283%`, NAV `84,191.10`
- E02 `2022-05-09`: 79 trades, 34/45, daily geo `-6.7844%`, NAV `61,153.25`
- E03 `2022-07-25`: 14 trades, 4/10, daily geo `+9.5740%`, NAV `189,649.74`; dominated by one unprotected long-lived outlier
- E04 `2023-06-20`: 10 trades, 3/7, daily geo `-1.1629%`, NAV `92,138.00`
- E05 `2024-07-15`: 8 trades, 4/4, daily geo `+2.1576%`, NAV `116,116.61`; one unprotected outlier present
- E06 `2025-08-11`: 8 trades, 1/7, daily geo `-11.4140%`, NAV `42,810.88`

Authoritative machine-readable evidence is `aggregate.json`. Raw interval evidence remains attached to GitHub Actions run `31243941023`.
