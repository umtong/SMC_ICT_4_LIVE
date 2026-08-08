# Candidate 18 — State Router with Atomic Price-Capped Execution

Candidate 18 extracts a complete decision policy from displayed-liquidity
failed auctions and true acceptance rather than trading surface patterns.

## Market-state policy

- A clean failed auction keeps a reversal path only when the strictly later
  opposite initiative either survives the full three-bar causal window or
  arrives immediately with above-baseline traded notional.
- Repeated defense without independently proven depletion closes unresolved.
- True acceptance requires outside residence, directional book withdrawal,
  fresh OI expansion and the first defended retest.
- All other states are no-trade.

## Execution evolution

Candidate 17's market parent could fill beyond its planned stop. Candidate 18
v1 used a capped IOC LIMIT parent. Untouched data then exposed an atomicity bug:
a partial IOC fill canceled the OTO children and left naked exposure. The full
v1 failure and results are retained in `V1_FAILURE.md`.

The effective v2 parent is a native NautilusTrader FOK LIMIT bracket. It fills
the complete risk-sized quantity immediately at or better than the cap, or
opens no position. Quantity is still sized from the worst permissible fill,
including fees and adverse slippage, with a maximum planned account loss of 3%.
No custom matcher, portfolio simulator or account engine is introduced.

NautilusTrader owns orders, fills, fees, positions, margin, portfolio accounting
and continuous NAV. Candidate 05 supplies the runner and Candidate 16/17 supply
the inherited causal state and fail-close contracts.
