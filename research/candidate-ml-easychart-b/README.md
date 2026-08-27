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

`harvest_capped.py` reuses the candidate-1k causal episode generator and its cost model.
It declares the source-proximal entry, event invalidation and structural route before
future bars are inspected, then caps only a more distant route at an immutable 1.5 net-R
day-trading realization barrier.

`easychart_b.py` applies the shared policy across BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT,
chooses one plan per causal episode, arbitrates all symbols through one global pending or
position slot, and compounds one continuous account at 3% NAV risk.  It does not scale,
use symbol identity, impose daily caps, or use outcome fields in selection.

Round-one fresh windows are executed by
`.github/workflows/research-candidate-ml-easychart-b-round1.yml`.
