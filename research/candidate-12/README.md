# Candidate 12 — Session Auction Displacement

## Status

This is an independent candidate under active W1 design validation. It is **not** a success claim until NautilusTrader account-NAV evidence passes the W1 gate.

## Causal scenarios

Only completed session information is tradable. One-minute observations are aggregated causally into completed five-minute bars.

1. **Failed auction**
   - completed Asia or London range exists;
   - the next active window accesses one range boundary;
   - price closes back inside;
   - internal structure breaks away from the sweep with displacement;
   - a pullback holds without violating the sweep;
   - reacceleration emits a plan toward pre-existing structural liquidity.
2. **Accepted auction**
   - price sustains multiple closes beyond a completed range;
   - displacement and directional flow agree;
   - price retests the accepted boundary or the displacement mean threshold;
   - the retest holds and reaccelerates toward pre-existing structural liquidity.

A wick, FVG, session time, or structure break alone is never an entry.

## Execution and risk

- NautilusTrader exclusively owns order matching, contingent order lifecycle, fees, margin, positions, and NAV.
- Entry is market after completed-bar confirmation; target is a post-only limit at structural liquidity; invalidation is stop-market beyond the causal pullback or sweep.
- Quantity is current whole-account NAV × 3% divided by expected loss per unit, including entry/stop costs and adverse-tick allowance.
- No nominal cap, score multiplier, or arbitrary strategy leverage cap is added.
- The strategy submits only when no position and no working entry order exist.

## Efficient validation protocol

`W1` is the sole design gate. W2/W3 and long evaluation are prohibited until W1 simultaneously has enough closed trades, strong post-cost NAV growth, acceptable payoff, and high win rate without liquidation or evidence errors.

```bash
smc4 doctor
python -m unittest discover -s research/candidate-12 -p 'test_*.py' -v
python research/candidate-12/run.py \
  --symbol BTCUSDT \
  --week W1 \
  --output artifacts/candidate-12/BTCUSDT-W1
```

The authoritative outputs are `run.json`, `metrics.json`, `scenario_events.jsonl`, `submitted_plans.json`, `order_lifecycle.json`, `orders.csv`, `positions.csv`, `account.csv`, and `data_manifest.json`.
