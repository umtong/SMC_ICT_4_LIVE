# Ichi/MBE N→1 fresh account comparison

Evaluated entries: `2026-04-01` through `2026-04-30` UTC.  Every cell is the same four-symbol, one-position, after-cost NautilusTrader account.

| mode | trades | W/L | win rate | PF | expectancy | geo/day | return | MDD | MBE collisions | MBE entries |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ichi_only | 18 | 7/11 | 0.3888888888888889 | 0.6759183934164082 | -105.33098983111113 | -0.000637850338447099 | -0.018959578169600033 | 0.03946772774802354 | 0 | 0 |
| mbe_only | 34 | 26/8 | 0.7647058823529411 | 1.381800117885363 | 67.2501023532353 | 0.0007538689322572623 | 0.022865034800100048 | 0.03528215264502699 | 34 | 34 |
| integrated | 52 | 33/19 | 0.6346153846153846 | 1.0236272750418987 | 5.4449362475 | 9.424997723561646e-05 | 0.00283136684869989 | 0.039283871629003264 | 34 | 34 |

## Causal comparison

- integrated minus Ichi: `{"ending_nav": 2179.094501829997, "expectancy_usdt": 110.77592607861112, "geometric_daily_growth": 0.0007321003156827155, "largest_winner_share": -0.18776933766979167, "losses": 8.0, "max_drawdown": -0.00018385611902027588, "profit_factor": 0.3477088816254904, "total_return": 0.021790945018299923, "trades": 34.0, "win_rate": 0.2457264957264957, "wins": 26.0}`
- episode contrast: `{"ichi_only_omitted_count": 0, "ichi_only_omitted_episode_keys": [], "integrated_added_count": 34, "integrated_added_episode_keys": [["mbe", "BTCUSDT", 1775091899999000000], ["mbe", "BTCUSDT", 1775141399999000000], ["mbe", "BTCUSDT", 1775404199999000000], ["mbe", "BTCUSDT", 1775468399999000000], ["mbe", "BTCUSDT", 1775605199999000000], ["mbe", "BTCUSDT", 1775832299999000000], ["mbe", "BTCUSDT", 1776092699999000000], ["mbe", "BTCUSDT", 1776123899999000000], ["mbe", "BTCUSDT", 1776283499999000000], ["mbe", "BTCUSDT", 1776370199999000000], ["mbe", "BTCUSDT", 1776432599999000000], ["mbe", "BTCUSDT", 1776606599999000000], ["mbe", "BTCUSDT", 1776956699999000000], ["mbe", "BTCUSDT", 1777111199999000000], ["mbe", "ETHUSDT", 1775750999999000000], ["mbe", "ETHUSDT", 1775900099999000000], ["mbe", "ETHUSDT", 1776017999999000000], ["mbe", "ETHUSDT", 1776416699999000000], ["mbe", "ETHUSDT", 1776438299999000000], ["mbe", "ETHUSDT", 1776670499999000000], ["mbe", "ETHUSDT", 1776864599999000000], ["mbe", "ETHUSDT", 1777182899999000000], ["mbe", "SOLUSDT", 1775316299999000000], ["mbe", "SOLUSDT", 1775550599999000000], ["mbe", "SOLUSDT", 1775825699999000000], ["mbe", "SOLUSDT", 1775933699999000000], ["mbe", "SOLUSDT", 1776346499999000000], ["mbe", "SOLUSDT", 1776817499999000000], ["mbe", "SOLUSDT", 1777228199999000000], ["mbe", "SOLUSDT", 1777434899999000000], ["mbe", "SOLUSDT", 1777458899999000000], ["mbe", "XRPUSDT", 1775436599999000000], ["mbe", "XRPUSDT", 1776680699999000000], ["mbe", "XRPUSDT", 1777031399999000000]], "shared_count": 18, "shared_episode_keys": [["ichi", "ETHUSDT", 1775096399999000000], ["ichi", "ETHUSDT", 1775495999999000000], ["ichi", "ETHUSDT", 1775516699999000000], ["ichi", "ETHUSDT", 1776179099999000000], ["ichi", "ETHUSDT", 1776347999999000000], ["ichi", "ETHUSDT", 1776800999999000000], ["ichi", "ETHUSDT", 1777267499999000000], ["ichi", "ETHUSDT", 1777303499999000000], ["ichi", "ETHUSDT", 1777486499999000000], ["ichi", "ETHUSDT", 1777517099999000000], ["ichi", "SOLUSDT", 1775082299999000000], ["ichi", "SOLUSDT", 1775093099999000000], ["ichi", "SOLUSDT", 1775659199999000000], ["ichi", "SOLUSDT", 1775958299999000000], ["ichi", "SOLUSDT", 1776192899999000000], ["ichi", "SOLUSDT", 1776637499999000000], ["ichi", "XRPUSDT", 1776501899999000000], ["ichi", "XRPUSDT", 1776792299999000000]]}`
- zero-collision identity expected: `False`
- zero-collision identity observed: `None`
- mechanically valid cells: `True`
- strict project target: `False`

The result is interpreted trade by trade.  A positive total caused by one outlier, a harmful MBE displacement, or a low-cost accounting difference is not accepted as proof of the integration mechanism.
