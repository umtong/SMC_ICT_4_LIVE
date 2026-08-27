# ML EasyChart-B — causal liquidity control

This candidate treats OB, FVG, trend/channel boundaries and BPR as parts of one
liquidity episode rather than independent indicator strategies.

The executable question is: **has control transferred at an important inherited
liquidity source strongly enough that the first credible opposing auction frontier is
reachable before structural invalidation?**

The common policy admits four observable forms of the same decision:

- relative control after a failed auction,
- low-impact absorption at a repeatedly defended source,
- push-pull absorption on the first accepted return,
- passive defended residual control.

All four share one anti-chase condition: during the 10–15 minutes before the accepted
departure, direction-signed return may not exceed 5 bps. The plan must therefore emerge
from contraction or an adverse liquidity sweep and reclaim, rather than enter after an
already extended move. This is the coded counterpart of Fakeout/Trap confirmation.

`harvest_capped.py` reuses the candidate-1k causal episode generator and its cost model.
It declares the source-proximal entry, event invalidation and structural route before
future bars are inspected, then realizes a more distant structural route at the first
immutable cost-adjusted 1.1 net-R day-trading destination.

`easychart_b.py` applies the shared policy across BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT,
chooses one plan per causal episode, arbitrates all symbols through one global pending or
position slot, and compounds one continuous account at 3% NAV risk. It does not scale,
use symbol identity, impose daily caps, or use outcome fields in selection.

Round-one fresh windows are executed by
`.github/workflows/research-candidate-ml-easychart-b-round1.yml`.
