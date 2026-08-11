# Candidate 60 — confirmed seesaw-rotation decision

## Status

**The exact V1 policy is closed. The policy-fresh interval remains untouched.**

This is not a binary rejection based only on negative net profit. The result was
decomposed to determine whether the economic mechanism, direction, event
magnitude, or tail path failed.

Evidence:

- GitHub Actions run: `31482627772`
- development: `2026-07-06` through `2026-07-12` UTC
- valid one-slot 15-minute events: `29`
- primary gross mean: `-3.121266 bp/event`
- primary net mean after 20 bp: `-23.121266 bp/event`
- primary cumulative net: `-670.516703 bp`
- all four target assets were represented
- policy-fresh `2026-08-03` through `2026-08-09`: not consumed

## Why the policy is not accepted

The 15-minute gross result contains one `-104.295700 bp` event. Removing that
single worst event changes the remaining cumulative gross result to only
`+13.778997 bp` across 28 events, or approximately `+0.492107 bp/event`.
Therefore the family is not rejected merely because one tail event made the
mean negative. Even without that event, the residual movement is far below the
20 bp round-trip friction floor.

At five minutes the reported gross mean is slightly positive at
`+0.858821 bp/event`, but removing the best event makes cumulative gross
negative. That positive headline is therefore not evidence of a usable edge.

The exact opposite-direction placebo has positive gross mean at the primary
horizon only because the proposed direction is slightly negative. Its mean
after the same 20 bp cost is still negative. Neither direction expresses an
economic movement large enough to trade.

## Trade-level state diagnosis

### Strong peer counter-initiative contradicted the rotation interpretation

Six events had both the peer confirmation return ratio and peer confirmation
flow ratio above their strictly prior daily medians. These were the observations
that most closely resembled a genuine independent peer initiative.

For the proposed capital-rotation direction at 15 minutes:

- events: `6`
- gross wins / losses: `0 / 6`
- mean gross: `-26.260832 bp`
- cumulative gross: `-157.564993 bp`

The exact fade of those six bursts has positive mean net after 20 bp, but it is
not accepted:

- the positive mean is dominated by one approximately `+84.30 bp` net event;
- removing that best fade leaves approximately `-46.73 bp` cumulative net;
- only two of six fades exceed the 20 bp friction floor;
- the sample is too small and payoff concentration is too high.

This is precisely why a positive aggregate result is not treated as proof.

### Weak opposite signs were not a tradable rotation either

The remaining 23 events, where the peer's opposite price/flow signs were not
both stronger than the prior daily medians, had approximately `+2.915143 bp`
mean gross at 15 minutes. This is directionally better but still economically
irrelevant after realistic cost.

### Direction and symbol asymmetries are diagnostic, not filters

- hot-leader events: approximately `+1.466643 bp` mean gross;
- cold-leader events: approximately `-10.628753 bp` mean gross;
- XRP as leader: approximately `+4.646305 bp` mean gross;
- SOL as leader: approximately `-15.114606 bp` mean gross.

No side or symbol subset is promoted. Every apparently better subset remains
below the cost floor, has small samples, and was observed only after the result.
Using these observations as filters would be outcome fitting.

## Market-model correction

The V1 interpretation was:

```text
strong hot/cold leader
→ leader persists
→ peer develops opposite price-and-aggressor initiative
→ capital is rotating
→ follow the peer
```

The evidence says the final implication is not identified by raw signs.
Among four highly correlated large crypto assets, an opposite peer block can be:

- a temporary countertrend retracement inside a dominant common-factor move;
- local inventory relief rather than durable capital reallocation;
- aggressive flow that fails to retain price impact;
- a beta difference or idiosyncratic shock unrelated to the selected leader;
- a genuine relative rotation whose absolute return is still overwhelmed by the
  market factor.

The strongest peer bursts were the least persistent in the proposed direction.
Thus stronger price-and-flow initiative is not a threshold that should be added
to rescue V1. It changes the latent state: the burst may be an initiative that
must first prove acceptance or failure.

## Preserved information

The following ideas remain useful:

1. relative asset selection should be separated from absolute market direction;
2. a crypto common factor must be distinguished from idiosyncratic rotation;
3. leader state, peer initiative, initiative failure/acceptance, and entry must
   be temporally distinct observations;
4. strong peer counter-initiative is a candidate *state transition to diagnose*,
   not an automatic continuation signal;
5. one-slot effects, cost, payoff concentration, days, symbols, and exact
   placebos must continue to be measured together.

## Next independent hypothesis

Do not tune V1 thresholds, symbols, directions, or horizons.

The next causal hypothesis is structurally different:

```text
broad common-factor initiative with a persistent leader
→ one peer launches a strong opposite price-and-flow burst
→ that burst fails to retain price impact on a later completed observation
→ peer flow and price turn back toward the still-dominant common factor
→ enter toward the common factor at the next open
```

This is a failed counter-initiative / trapped temporary-liquidity hypothesis,
not a revised seesaw-continuation rule. It requires a later failure observation
and must be compared with a peer-only failed-initiative control to determine
whether the cross-asset leader contributes information.

Its first test uses a new development interval. The reserved August policy-fresh
interval remains untouched unless a fully frozen causal policy shows substantial
cost-after movement, broad attribution, and non-concentrated payoff.
