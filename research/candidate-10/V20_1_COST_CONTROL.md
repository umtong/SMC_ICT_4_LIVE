# Candidate 10 v20.1 — Size-Dependent Impact Cost Control

## Classification

This is an implementation and cost-accounting control over the unchanged v20 signal, not a new signal hypothesis.

The following remain identical to v20:

- structural pool creation and identity;
- OI-state full candidate and exact no-OI ablation;
- two-completed-bar rejection/acceptance classification;
- first post-confirmation TradeTick entry;
- structural stop and target;
- Binance data, preselected weeks, seed and fees;
- current whole-account NAV and 3% planned-loss budget;
- no arbitrary nominal cap, leverage cap or score-based risk multiplier.

## Controlled correction

The original v20 impact reserve did not depend on the system's own order size and was used in plan qualification and sizing but was not separately debited from NAV. v20.1 resolves those infrastructure gaps before a success result can be accepted.

For a proposed quantity `q`, entry price `p`, causal median completed five-minute quote volume `V`, and causal ATR `sigma`, expected impact per side is:

```text
max(causal stress floor, sigma × sqrt(q × p / V))
```

Quantity and impact are solved as a fixed point so that:

```text
quantity × (
  entry-to-stop distance
  + entry and stop taker fees
  + expected entry and exit impact
) <= current NAV × 3%
```

Exchange size-increment rounding is downward and the planned-loss budget is rechecked after rounding. No nominal-size cap is introduced.

NautilusTrader remains the source of orders, fills, commissions, positions, margin and account NAV. A parallel conservative ledger debits the causal size-dependent impact estimate at each closed trade. Target passage uses this impact-adjusted NAV and impact-adjusted drawdown, never the more optimistic engine-only NAV.

## Drawdown observation

Open-risk mark-to-market drawdown is observed on every replayed raw TradeTick as well as on completed five-minute bars. This changes no order or scenario decision.

## Process isolation

The immutable original v20 source archive is extracted unchanged. `sitecustomize.py` installs the cost overlay automatically in the parent and every isolated worker process. This prevents full and ablation workers from silently using different execution-cost logic.

## Acceptance rule

A v20 result is not accepted as a project success unless the v20.1 conservative ledger also passes the predeclared growth, trade-count, win-count, concentration, order-error, causal-integrity and drawdown gates.
