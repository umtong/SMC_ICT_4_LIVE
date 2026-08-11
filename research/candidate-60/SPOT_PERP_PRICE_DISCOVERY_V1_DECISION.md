# Candidate 60 — spot/perpetual price-discovery V1 decision

## Frozen development result

The diagnostic was reconstructed from verified 1 KB chunks and passed its
causal next-open, data-completeness and cost-accounting contracts. The
predeclared policy-fresh interval was not consumed.

### Spot-led continuation

| horizon | one-slot events | mean gross bp | mean net bp | positive symbols | positive days |
|---:|---:|---:|---:|---:|---:|
| 5m | 1,062 | -0.18 | -20.18 | 0/4 | 0/7 |
| 15m | 522 | -0.75 | -20.75 | 0/4 | 0/7 |
| 30m | 291 | -1.87 | -21.87 | 0/4 | 0/7 |
| 60m | 156 | +2.82 | -17.18 | 0/4 | 0/7 |

Spot price and executed spot flow dominating the same five-minute perpetual
response did not imply that the perpetual would subsequently follow. At the
primary 30-minute horizon the opposite direction was less bad even before a
tradeable edge existed. The exact spot-lead/follow state is closed.

### Unconfirmed perpetual-pressure fade

| horizon | one-slot events | mean gross bp | mean net bp | positive symbols | positive days |
|---:|---:|---:|---:|---:|---:|
| 5m | 26 | -0.44 | -20.44 | 0/4 | 0/7 |
| 15m | 25 | +0.00 | -20.00 | 0/4 | 0/7 |
| 30m | 23 | +7.88 | -12.12 | 0/4 | 3/7 |
| 60m | 20 | -2.28 | -22.28 | 1/4 | 1/7 |

The proposed fade direction was better than its opposite only at 30 minutes,
but the mean gross effect was 7.88 bp against the project's 20 bp round-trip
friction floor. Every asset remained negative after costs, the aggregate sum
was -278.81 bp, and removing the best trade worsened rather than rescued the
result. The isolated 30-minute bump did not persist at adjacent horizons.

## Market-model conclusion

Same-window return and executed-flow dominance does not establish price
leadership. It can arise because arbitrage has already transmitted the move,
because the observed leader is responding to a common latent shock, or because
aggressive volume is absorbed with little remaining impact.

The exact two-family policy is closed without searching magnitude thresholds,
clock subsets, asset exceptions, alternative primary horizons or cheaper fills
on the consumed July 27–August 2 interval. The untouched August 3–9 interval is
preserved.

The useful surviving insight is narrower:

> Perpetual-only pressure rejected by spot showed a small, transient 30-minute
> reversion before costs, but flow sign and relative return magnitude were not
> sufficient to identify economically exploitable inventory pressure.

Any successor must add a genuinely different state variable such as basis
innovation, depth-normalized price impact, refill/resiliency, or source-faithful
queue OFI. It must explain why the remaining price adjustment is large enough
to exceed execution costs rather than merely detecting statistical dependence.
