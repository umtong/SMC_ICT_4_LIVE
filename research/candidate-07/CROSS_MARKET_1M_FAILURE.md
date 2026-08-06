# One-Minute Cross-Market Liquidation Transfer — Failure Record

## Classification

`LOGIC_ERROR / BASELINE DISCARDED; OVERSHOOT COMPONENT RETAINED FOR REDESIGN`

The candidate preserved completed one-minute perpetual/index geometry while
joining the latest completed five-minute OI state backward-as-of.  Fifteen-
minute pools and mapped index pivot levels activated strictly after causal
confirmation, and every raw first touch consumed the pool.

## Frozen BTC Week-1 baseline

Period: `2025-12-22` through `2025-12-29` exclusive.

| Measure | Result |
|---|---:|
| Entry-ready paths | 6 |
| Active days | 4 |
| Targets | 1 |
| Stops | 5 |
| Median exit-safe MFE | 0.3164 R |
| Median exit-safe MAE | 1.1772 R |

Route decomposition:

- `COMMON_PRICE_DISCOVERY`: four entries, four stops.
- `FUTURES_ONLY_OVERSHOOT`: two entries on one day, one target and one stop.

The winning overshoot reached a confirmed one-minute target at approximately
`3.50 R` and achieved `5.04 R` maximum favorable excursion before exit.  The
losing overshoot stopped with no meaningful favorable excursion.

## Single controlled ablation

Removed exactly one route:

`COMMON_PRICE_DISCOVERY_CONTINUATION`

Data, OI state, transfer ratios, basis rank, pool formation, confirmation,
structural stops, target hierarchy, one-slot blocking and exit-safe accounting
were unchanged.

| Measure | Baseline | Ablation |
|---|---:|---:|
| Entry-ready paths | 6 | 2 |
| Active days | 4 | 1 |
| Targets | 1 | 1 |
| Stops | 5 | 1 |
| Median MFE | 0.3164 R | 2.5199 R |
| Median MAE | 1.1772 R | 0.7298 R |

The ablation isolated a materially better path distribution, but only two events
from one date remained and targets did not outnumber stops.  It cannot support a
weekly or long-horizon success claim.

## Primary failure causes

1. Joint one-minute index confirmation did not imply sustainable common price
   discovery.  All four continuation paths were stopped.
2. The futures-only overshoot distinction appeared meaningful, but fifteen-
   minute external pools produced insufficient independent opportunities.
3. Last-trade/index separation alone does not prove a forced liquidation.  A
   transient traded-price wick can occur without the mark price—the liquidation
   trigger reference—transferring the move.

## Components retained

- one-minute contact resolution recovered a futures-only class hidden by
  five-minute aggregation;
- the overshoot-only ablation improved MFE/MAE materially;
- backward-as-of completed OI state, exact perpetual/index alignment, strict
  post-confirmation activation and first-touch pool consumption behaved as
  intended;
- one-minute internal target pools supplied realistic structural targets.

## Redesign path

The successor uses more frequent confirmed five-minute pools but requires mark
price to agree with the derivatives move while the index does not.  This seeks
more opportunities without treating last-price-only wicks as forced liquidation.

## Evidence

- Baseline source commit: `e8bc66f7cc0a844ce51542fc9a456a586bfc454d`
- Baseline run: `31114018015`
- Baseline artifact SHA-256: `6fb4c0329243c1766239ccb79b1063b51afc00f667784fff075f54ce588f4c6e`
- Ablation source commit: `0a31d5b4dce695ea871356d2ecc5d4563abe8f40`
- Ablation run: `31114488423`
- Ablation artifact SHA-256: `ede159202e79ed8111a96a9d233cd68393dc5e28f1b8657c2075191599d2dc50`
