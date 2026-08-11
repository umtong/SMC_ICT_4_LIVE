# Candidate 60 — ZaratustraV5 × RAHTF clean-state forensic decision

## Account result

The mechanically valid development account does not support the proposed state
model.

| cell | trades | W/L | PF | return | MDD | expectancy USDT |
|---|---:|---:|---:|---:|---:|---:|
| rising-edge source control | 73 | 50/23 | 0.8093 | -5.6873% | 11.5236% | -77.91 |
| rising-edge RAHTF clean state | 11 | 6/5 | 0.2317 | -7.2515% | 7.9754% | -659.22 |

The clean-state policy lowered drawdown only by reducing exposure. It worsened
return, profit factor, geometric growth and expectancy. The predeclared
policy-fresh interval was not consumed.

## Trade and slot-path effect

This was not a harmless filter applied to an otherwise identical ledger. Under
the global one-slot account, rejecting early opportunities changed later slot
availability and created a different trade path.

- only 2 actual trade keys were shared;
- 71 source-control trades disappeared: 49 positive and 22 negative;
- the removed set summed to approximately -1.018R because a smaller number of
  large losses outweighed many small winners;
- 9 trades appeared only in the RAHTF account because its slot path differed;
- those added trades summed to approximately -1.669R;
- the source control's best positive trade, about +0.352R, was not preserved;
- the added account path concentrated in ETH longs and included two nearly full
  -0.98R losses.

Thus a lower trade count or lower MDD cannot be interpreted as better state
classification. The policy discarded most of the source's high-frequency small
winner engine while failing to prevent its destructive loss geometry.

## Market-model conclusion

A slow, clean higher-timeframe directional label does not answer the relevant
question for this entry engine.

The missing state is not simply whether a trend exists. It is whether the
source signal occurs early enough in a still-funded price-discovery leg, or late
inside crowded inventory, weakening marginal impact, target-space exhaustion,
or an impending transition. A strong slow drift can coexist with poor entry
geometry and can even select mature, crowded continuation states.

The exact RAHTF gate is therefore closed. Its thresholds, horizon and dates are
not retuned. Useful lessons retained:

1. state components must predict which actual losses disappear and which
   winners remain before implementation;
2. any rejection policy must be evaluated through the changed one-slot path,
   including trades it enables later;
3. continuation state needs information about flow persistence, inventory
   construction, impact efficiency and remaining auction space—not a directional
   trend label alone.

No fresh evaluation, long validation or integration is authorized for this
policy.
