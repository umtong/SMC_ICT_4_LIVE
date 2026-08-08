# Candidate 18 — Execution-Preserving Initiative Router

Candidate 18 is a direct response to Candidate 17's untouched failure. Candidate 17 lost 9.94%, its remembered-defense branch produced zero confirmations, and most executed reversals were early initiatives. One market parent also filled beyond its planned stop before protection could become valid.

The replacement policy is deliberately small:

- clean displayed-liquidity failed auctions keep a reversal path only when the later opposite initiative either survives the complete three-bar causal window or appears immediately with above-baseline notional;
- repeated defense has no trade unless depletion is independently proven, so the inactive memory branch is removed;
- accepted setups do not enter at the next bar's market price. They place a directional STOP_LIMIT parent with a capped worst fill, bracketed by the inherited structural stop and natural liquidity objective;
- true-acceptance continuation remains available through Candidate 17's independent book-withdrawal and fresh-OI logic;
- everything else is unresolved/no-trade.

NautilusTrader still owns orders, fills, fees, positions, margin, portfolio accounting and NAV. Candidate 05 owns the runner and Candidate 16/17 own the inherited state and safety contracts.
