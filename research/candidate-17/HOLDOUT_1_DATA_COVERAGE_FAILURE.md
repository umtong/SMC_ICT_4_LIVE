# Candidate 17 v2 — holdout 1 data-coverage failure

## Frozen run

- workflow: `candidate-17-v2-untouched-btc`
- successful execution run: `31249319588`
- workflow commit: `bfb55db1d2d0ab692e482fb1e3fe1cb799a83522`
- frozen strategy commit: `3bfc9a45f1fa29aef7479b2deb723944d13b87d0`
- evaluation: 2022-02-07 through 2022-02-13 UTC

## Observed result

- integrity: passed
- orders/trades: 0 / 0
- ending NAV: 100,000 USDT
- gate: failed
- parent auctions: 0
- feature rows: 14,390
- `feature_ready=True`: 0
- median/max depth snapshot age: 60 / 60 seconds

Every requested daily `bookDepth` archive from build start 2022-02-04 through evaluation end 2022-02-13 returned a recorded `bookDepth_missing_404` artifact. Price bars, aggregate trades, metrics, and basis data existed, but the displayed-liquidity state required by Candidate 17 could not be calculated. The run therefore contains no observation of the strategy's decision policy and is not evidence for or against its alpha.

The zero-trade result remains recorded as a failed weekly gate. It is not silently replaced or described as a pass. A second untouched week may be selected only from dates where the strategy's mandatory public L2 archive is structurally available. The strategy and parameters remain unchanged.
