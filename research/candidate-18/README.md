# Candidate 18 — State Router with Protected Partial Execution

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
v1 introduced a price-capped IOC LIMIT, but its implicit venue setup allowed a
partial fill to lose its OTO children and remain naked. `V1_FAILURE.md` retains
that failure.

Candidate 18 v2 used FOK and proved all-or-none safety, but failed both viewed
weeks after discarding partial-fill opportunities. `V2_FOK_RESULTS.md` retains
those results.

The effective v3 uses the same price-capped IOC bracket and explicitly sets the
NautilusTrader venue to `oto_trigger_mode=PARTIAL`. Every partial parent fill
must release and resize its stop and target children pro-rata. Quantity is
still calculated from the worst permissible entry fill, fees, adverse
slippage and a maximum 3% planned account loss.

NautilusTrader owns orders, partial fills, contingent-child release, fees,
positions, margin, portfolio accounting and continuous NAV. Candidate 05
supplies the runner; Candidate 16/17 supply the inherited causal state and
fail-close contracts. No custom matching, account or portfolio engine exists.
