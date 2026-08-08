# Candidate 15 — Sequential Price–Flow Response Router

Candidate 15 keeps Candidate 14's complete NautilusTrader path and changes one
thing: the parent auction must resolve before either inherited child scenario may
trade.

```text
external liquidity trade-through
        ↓
latest-extreme response episode
        ↓
price / aggressor conversion evidence
   ↙          ↓           ↘
FAILURE   UNRESOLVED   ACCEPTANCE
   ↓          ↓           ↓
  FAR      NO TRADE       AAC
```

## Why this candidate exists

Candidate 13 produced strong but sparse evidence: seven wins from seven closed
trades across five holdout weeks, with aggregate daily geometric growth above the
project threshold. Candidate 14 expanded opportunity density, but its frozen
84-day continuous account produced 15 trades, only three wins and a 19.26% NAV
loss. Its own failure analysis identified state aliasing: the same surface
sweep/reclaim observations mixed genuine failure with true acceptance.

The next useful change is therefore not another threshold on the eventual trade
outcome. It is an online state router based only on information visible before
entry.

## State evidence

For each newest sweep extreme, the router calibrates a non-negative
contemporaneous response between one-minute log return and signed taker-flow
pressure over completed pre-event bars. Every later completed bar contributes
four bounded channels:

1. directional price response;
2. price conversion under the magnitude of aggressive pressure;
3. directional response unexplained by the calibrated impact;
4. close occupancy beyond the latest crossed external boundary.

The channel mean accumulates sequentially. A symmetric `log(9)` boundary and
`log(2)` full-agreement increment are fixed methodological conventions rather
than PnL-fitted parameters. Until a boundary is crossed, the state is
`UNRESOLVED` and neither FAR nor AAC may enter. A new extreme or boundary resets
the episode, so stale evidence is not inherited by a new causal leg.

The implementation deliberately calls the available bar field an aggressor-flow
proxy, not full limit-order-book OFI. The external microstructure literature is
used to choose the causal question; the repository's actual data contract
determines what can be measured.

## Preserved invariants

- NautilusTrader owns orders, fills, fees, margin, positions and NAV.
- Current whole-account NAV and 3% planned loss determine quantity.
- At most one pending entry or open position exists across all four instruments.
- Candidate 14's entry, invalidation, target, leadership and execution semantics
  remain unchanged.
- `UNRESOLVED` is a real no-trade state.
- No outcome-fitted route whitelist, risk multiplier or leverage cap is added.

## Predeclared screen

`protocol.json` freezes three seven-day intervals before Candidate 15 outcomes:

- `D1`: known Candidate 14 loss cluster, used only for mechanism diagnosis;
- `H1`: ordinary-regime confirmation;
- `S1`: liquidation-stress confirmation.

The jobs run independently for parallel information gain. Their aggregate is
explicitly a weekly-reset screen, not continuous-account evidence and not a
success claim.

## Run

```bash
bash research/candidate-15/run_week.sh D1
bash research/candidate-15/run_week.sh H1
bash research/candidate-15/run_week.sh S1
python research/candidate-15/aggregate.py
```

## Research basis

- Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*,
  arXiv:1011.6402.
- Adams & MacKay, *Bayesian Online Changepoint Detection*,
  arXiv:0710.3742.
- Abhishek & Mannor, *A nonparametric sequential test for online randomized
  experiments*, arXiv:1610.02490.
- Hu & Zhang, *Stochastic Price Dynamics in Response to Order Flow Imbalance*,
  arXiv:2505.17388.

`RESULT.md` and `aggregate.json` are generated from the fresh GitHub Actions
Nautilus runs.
