# EasyChart C — causal first-response system

This candidate is one shared decision policy, not an OB/FVG/channel strategy collection.

1. The upstream four-symbol universe reads direction and auction structure, identifies a pre-existing liquidity location, then waits for a price-volume response: rejection/reclaim, accepted break and first efficient pullback, or completed H4 sweep response.
2. OB/FVG are entry-location footprints; trend lines, channels and horizontal structure describe the state and opposing liquidity. A plan is emitted only after the interaction, response and pre-entry geometry are complete.
3. The router uses only information known at plan emission. It estimates whether the first complete favorable response will travel one structural risk unit before the causal stop.
4. The executable plan keeps the upstream entry and causal stop, freezes a one-shot target at at least 1.0R, and is submitted only when realistic fee/slippage cost and expected log growth remain positive.
5. BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT share the same feature schema, model and rules. One account may hold only one position across the universe. Every submitted trade risks 3% of current NAV through quantity sizing. There are no partial entries, partial exits, daily loss limits or trade-count limits.

The development evidence is `results/development_oof.json`. It is leave-one-environment-out across eight non-overlapping 2024–2025 windows. Independent 2026 observations are written to `results/final_observation.json` only after the workflow completes.
