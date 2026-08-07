# Local 15-second sweep → MSS → broken-level retest — W1 decision

## Classification

`STRUCTURAL EDGE PRESENT / W1 GATE NOT YET PASSED / SUCCESSOR REQUIRED`

The initial workflow failure was implementation-only: two NautilusTrader engines
were initialized sequentially in one Python process and the process-global Rust
logger rejected the second initialization. Baseline and ablation were rerun in
separate spawned processes without changing any market rule, threshold, period,
risk, cost, target, stop or fill behavior. All 121 causal/data/state tests passed.

## Frozen BTC Week-1

Period: `2025-12-22` through `2025-12-29` exclusive.

| Measure | Broken-level retest | Ablation: MSS close |
|---|---:|---:|
| Entry-ready / trades | 4 / 4 | 8 / 8 |
| Active days | 2 | 4 |
| Wins / losses | 3 / 1 | 4 / 4 |
| Win rate | 75.00% | 50.00% |
| Net return | +6.2513% | +0.4336% |
| Daily geometric growth | +0.8700% | +0.0618% |
| Profit factor | 3.3450 | 1.0383 |
| Maximum drawdown | 3.8476% | 9.4731% |
| Largest winner share | 47.41% | 29.56% |

The retest variant failed only the minimum trade count, minimum active days and
1% daily-growth gate. It passed NAV positivity, one-slot execution, drawdown and
winner-concentration checks. The ablation met opportunity requirements but lost
almost all of the edge. Therefore the retest is not decorative ICT terminology;
it is a materially useful state transition and is retained.

## Trade-level attribution

The retest route produced three wins with structural targets between about
1.66R and 2.14R and one loss whose declared target was about 2.49R. The sole
losing setup used the same physical fifteen-second pivot bar as both:

- the swept lower source-liquidity pool; and
- the opposing upper swing whose break was called an MSS.

That is not an independent structure shift. One outside bar cannot supply both
sides of the claimed transition and then prove that its opposite side is a
separate protected swing. The successor therefore requires distinct source and
MSS pivot identities for every timeframe. This is a scenario-definition repair,
not a fitted loss filter.

The four MSS-close trades which never produced the required retest yielded only
one win and three losses. Strong displacement alone was therefore insufficient;
the successor does not add a no-retest continuation route.

## Structural improvement path

The exact 15-second-only source population was too sparse after the useful
retest requirement: four trades on two days. The next implementation keeps all
successful components frozen and changes only the liquidity sampling scale:

```text
causally confirmed 15S / 30S / 1M source liquidity
-> literal first touch, one episode per touch bar
-> finite attack-side sweep and completed reclaim
-> distinct protected 15S opposing swing
-> displacement MSS
-> first broken-level rejection retest
-> nearest unconsumed 15S / 30S / 1M / 5M target
```

The controlled ablation removes only the 30S/1M source pools and keeps the same
independent-boundary rule, retest, stop, target, 3% NAV risk and NautilusTrader
execution. This tests whether multiple genuine intraday liquidity scales add
independent opportunity without diluting the proven retest edge.

## Evidence

- source commit: `2dedf97b63290818491aec4049f1a744346d39bf`
- workflow run: `31179369614`
- artifact id: `8994209198`
- artifact SHA-256: `2c1a3ef16bc626be19f083c0064bd1d9f1a2dc8cd3ab861dd2d0d595413bc233`
