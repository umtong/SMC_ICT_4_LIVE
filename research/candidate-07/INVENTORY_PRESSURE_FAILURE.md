# Episode Inventory-Pressure Continuation — Failure Record

## Classification

`LOGIC_ERROR / DISCARDED`

The candidate was an independent redesign of the discarded post-liquidation
handoff model.  It removed the reversal branch, represented OI pressure
cumulatively from contact, placed invalidation beyond contact/confirmation
auction structure, and used a causal five-minute-internal to fifteen-minute-
external target ladder.  The implementation compiled and the frozen data/state
contracts passed; the failure was not a backtest or timestamp defect.

## Baseline hypothesis

At the first aggressive OI-release contact with a causally confirmed fifteen-
minute external pool:

1. open a continuation episode in the attack direction;
2. require a subsequent completed five-minute outside hold, same-direction body
   and aggressor flow while OI remains at or below contact OI;
3. size geometry from the contact-plus-confirmation auction structure;
4. target the nearest still-active confirmed five-minute pool, then a fifteen-
   minute pool.

No orders, fees, PnL or NAV were simulated at this diagnostic stage.

## Frozen BTC Week-1 baseline

Period: `2025-12-22` through `2025-12-29` exclusive.

| Measure | Result |
|---|---:|
| Pressure episodes | 29 |
| Broken-pool reclaims before entry | 26 |
| Entry-ready paths | 3 |
| Active days | 2 |
| Targets | 1 |
| Stops | 1 |
| Timeouts | 1 |
| Median exit-safe MFE | 0.8200 R |
| Median exit-safe MAE | 0.7532 R |

The state and target redesign reduced the severe adverse asymmetry of the parent
candidate, but the qualified opportunity set became too small and median
favorable excursion remained below the declared minimum structural reward.

## Single controlled ablation

Removed exactly one core stage:

`SEPARATE_POST_CONTACT_OUTSIDE_HOLD`

The same fifteen-minute contact pools, OI-release/attack-flow contact,
structural stop family, five-minute-to-fifteen-minute target hierarchy, frozen
data, one-slot blocking and exit-safe path accounting were retained.  The
contact bar itself had to close outside with a directional body.

| Measure | Baseline | Ablation |
|---|---:|---:|
| Entry-ready paths | 3 | 11 |
| Active days | 2 | 5 |
| Targets | 1 | 2 |
| Stops | 1 | 9 |
| Median MFE | 0.8200 R | 0.2714 R |
| Median MAE | 0.7532 R | 1.1487 R |

The ablation recovered density but destroyed path quality.  More entries did not
represent more independent positive-expectancy opportunities; they were mostly
first-contact continuation guesses which normal pool reclamation invalidated.

## Primary failure cause

An OI-release impulse and same-direction aggressor flow at a perpetual-futures
liquidity contact do not distinguish:

- forced-futures overshoot that should mean-revert;
- broad-market price discovery that should continue;
- temporary marking outside a public pool before auction repair.

Cumulative OI state and an internal target ladder improved geometry but could not
resolve that missing causal distinction.  Confirmation delay was not the sole
problem: removing it increased stops from one to nine.

## Components retained

- cumulative episode OI is preferable to requiring every later bar to be another
  rank-qualified release impulse;
- five-minute internal pools materially improve target availability relative to
  fifteen-minute targets alone;
- exit-safe first-event path accounting is mandatory;
- causal pool formation, first-contact consumption and invalid-positioning state
  breaks behaved correctly;
- baseline and ablation demonstrate that density must not be confused with alpha.

## Next independent hypothesis

Use the spot/index reference to distinguish futures-only overshoot from common
price discovery at the contact itself:

- perpetual breaches a confirmed pool, OI releases and aggressor flow attacks;
- **index non-confirmation / basis expansion** followed by immediate perpetual
  reclaim routes forced-futures overshoot reversal;
- **index confirmation / stable basis** with continued directional displacement
  routes broad-market continuation;
- no waiting for basis to contract all the way to fair value, because the prior
  MTF-index candidate showed that reversion was already complete by then.

## Evidence

- Baseline source commit: `4a1823483856c338b9d680f41fe1ac383d4ca438`
- Baseline run: `31111297029`
- Baseline artifact SHA-256: `dcecf3b467f74d4aabe289d6a301c43af84506b3bda3e4c29d81108616e724f0`
- Ablation source commit: `c054fdc68f2ec9407e831e37614d7699a4fcc8ba`
- Ablation run: `31111711155`
- Ablation artifact SHA-256: `8767072b69ad94f416ebebcaf4daa65558515d4f5871b110e9e9a148dabc0275`
