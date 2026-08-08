# Candidate 18 — Execution-Preserving Initiative Router

Candidate 18 is a direct response to Candidate 17's untouched failure. Candidate 17 lost 9.94%, its remembered-defense branch produced zero confirmations, most executed reversals were early initiatives, and one next-bar market parent filled beyond its planned stop before protection could become valid.

The replacement policy is deliberately small:

- a clean displayed-liquidity failed auction keeps a reversal path only when the strictly later opposite initiative either survives the complete three-bar causal window or appears immediately with above-baseline notional;
- repeated defense has no trade unless depletion is independently proven, so the inactive memory branch closes unresolved;
- true-acceptance continuation remains available through independent book withdrawal, fresh OI expansion and the first defended retest;
- every accepted signal is already complete at the finished bar. Execution uses a native IOC LIMIT parent: it can fill only at or better than the precomputed cap, otherwise it cancels. Quantity is sized from that worst permissible fill including fees and adverse slippage;
- everything else is unresolved/no-trade.

Two failed execution experiments are retained as evidence rather than hidden. A native STOP_LIMIT parent could reach the bar venue after its trigger had already crossed and reject the bracket. BID/ASK local emulation removed the rejection but had no quote trigger in the shared bar-only replay, so it produced no fills. The final IOC price cap matches the available execution data without adding a custom matching engine.

NautilusTrader still owns orders, fills, fees, positions, margin, portfolio accounting and NAV. Candidate 05 owns the runner and Candidate 16/17 own the inherited market-state and fail-close safety contracts.
