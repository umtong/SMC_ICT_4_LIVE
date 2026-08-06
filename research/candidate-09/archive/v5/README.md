# Candidate 09 v5 — discarded complete candidate; timing/objective lesson retained

Reproducible Nautilus run: GitHub Actions run `31107808050`; result commit `ce37d1aeafe9b0546638aaf1529d6e92dd1ae83f`; engine SHA-256 `1e26e4dd60088319a121aa1dadb8ef1c1d67f9587a2a14210b04623fb85e98ea`.

V5 preserved only the accepted-breakout-failure reversal mechanism and added completed 5m/15m/60m/1440m auction levels. The baseline entered immediately on failure displacement and targeted the opposite source-range edge. On the unchanged three BTC weeks:

- pooled daily geometric return: **-0.600345%**
- pooled NAV multiple: **0.881265x**
- trades: **19**, wins: **7**, losses: **12**
- week returns: **-7.6201%, -5.8982%, +1.7604%**
- maximum segment drawdown: **12.618%**

The one-variable ablations also rejected naive opportunity expansion: removing 5m levels produced **-1.393831% daily**; restoring 240m produced **-0.675729% daily**. The midpoint-target ablation made only one trade, but that trade returned **+6.7166%**, indicating that immediate failure chasing plus a far opposite-edge target—not necessarily the accepted-liquidity-failure premise itself—was the dominant defect.

Post-run diagnostics also showed that all baseline winners were failed downside acceptances followed by BUY entries and that the 60m source class was the only positive source class in aggregate. V6 does not hard-code long-only or 60m-only behavior. It adds a causal completed-240m auction regime, waits for a failed-level retest and rejection instead of chasing, and uses the source midpoint as the first equilibrium objective. Each added element is isolated by a single-variable ablation.
