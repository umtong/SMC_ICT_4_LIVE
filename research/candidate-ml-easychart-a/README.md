# EasyChart-A causal liquidity-control system

This candidate treats OB, FVG, trend/channel boundaries and fakeout/trap shapes as
parts of one liquidity episode rather than independent signals. A plan exists only
when a meaningful inherited source is approached, order-flow/price response shows
control or control transfer, a structural invalidation is explicit, and a reachable
opposite frontier gives at least 1R after modeled cost.

The implementation reuses the repository's proven ML-k control-transfer action
harvest, fill lifecycle, cost-adjusted R outcomes and continuous NAV accounting.
Six independent causal scenario families share the same logic across BTCUSDT,
ETHUSDT, SOLUSDT and XRPUSDT. Exact-time collisions are resolved by scenario
priority and executable geometry; one global pending order or position is allowed,
and each causal episode is consumed at most once. Stop risk is 3% of current NAV.

Run:

```bash
python research/candidate-ml-easychart-a/easychart_a_policy.py \
  --root <directory-containing-departure_actions.csv.gz> \
  --output <result-directory>
```
