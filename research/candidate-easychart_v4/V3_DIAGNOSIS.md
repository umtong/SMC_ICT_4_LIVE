# v3 diagnostic carried into v4

The latest v3 GitHub Actions diagnostic used four symbols in one continuous
NautilusTrader account from 2024-02-01 through 2024-02-14.

- 20 completed positions / 14 days.
- Final NAV 83,487.1925 from 100,000.
- Daily geometric growth -1.28085%.
- Planned-loss budget breaches: 0.
- Entry slippage reserve exceedances: 0.
- Missing exit classifications: 0.
- Emergency protective exits: 0.

This separates the dominant failure: the execution/account implementation was
internally consistent, while the alpha interpretation was structurally wrong.
The v3 overlap route often treated an OB/FVG edge as if it were a genuine
trendline/channel acceptance boundary and used small trigger geometry for the
stop. That produced attractive nominal RR but frequent immediate invalidation.

v4 reuses the valid Nautilus layer and replaces only that decision logic. The
v3 figures are development evidence, not an out-of-sample claim and not a
reason to blacklist OB, FVG or any asset.
