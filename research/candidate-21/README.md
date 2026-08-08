# Candidate 21 — Quarter-Hour Flow-Conditioned Auction Router

Candidate 21 is a new alpha policy. It reuses only already-solved infrastructure from the latest internal lineage:

- Candidate 05: verified Binance public-data ingestion, NautilusTrader catalog, fees, latency, portfolio/accounting, continuous NAV, and cost-aware risk algebra.
- Candidate 18: all-or-none price-capped FOK bracket, preventing a partial parent fill from losing contingent protection.
- Candidate 20: one real aggTrade per minute as the sub-minute execution clock, preventing 250 ms latency from degenerating into a one-minute delay.

It does **not** reuse Candidate 19's shock alpha.

## External idea synthesis

External exploration produced three complementary materials:

1. Research on the cryptocurrency quarter-hour effect reports recurring activity/volatility bursts at 15-minute boundaries and out-of-sample predictability in the opening ten seconds.
2. Market-impact and order-flow work distinguishes informed transmission from absorption by comparing aggressive effort with realized price progress and later reversal.
3. Failed-auction/order-flow practice treats strong aggression without progress, followed by reclaim and opposite initiative, as evidence that displayed/latent liquidity absorbed the attack.

Candidate 21 assigns these materials different roles rather than stacking filters:

```text
prior completed 15-minute balance
→ first-10-second boundary flow burst attacks one edge
→ later auction response
   ├─ price + flow + book transmit       → ACCEPTANCE
   ├─ attack is absorbed, reclaimed,
   │  and opposite initiative appears   → FAILED_AUCTION
   └─ evidence remains mixed            → UNRESOLVED / NO TRADE
→ same-leg invalidation and natural target
→ FOK bracket sized from current account NAV
```

## Causal separation

- **Event definition:** lagged same-clock baseline, first-ten-second notional burst, opening aggressor flow, and a violation of one edge of the *previous completed* 15-minute balance.
- **State confirmation:** only strictly later completed bars; 60-second flow/return/efficiency plus L1 imbalance and liquidity refill/withdrawal.
- **Entry:** a separate all-or-none price-capped FOK order after the state becomes terminal.
- **Invalidation:** target reached before confirmation, re-entry geometry already consumed, or incoherent/mixed response.

The boundary bar cannot confirm itself. The phase baseline uses only previous boundary windows. A natural target touched before confirmation closes the scenario rather than allowing a late entry.

## Trade geometry

- Acceptance stop: just inside the broken balance edge.
- Acceptance target: one prior-balance width beyond the broken edge.
- Failed-auction stop: beyond the attack extreme.
- Failed-auction target: the opposite edge of the prior balance.
- Entry cap: the smaller causal leg is preserved through a FOK limit; a stale price does not become a market fill.
- Quantity: current continuous NAV × 3% divided by expected per-unit loss including entry/stop fees and adverse slippage.
- A trade is rejected when its still-unconsumed natural target does not provide at least 1.0 net R after costs.

## Files

- `clock_phase_features.py`: opening-ten-second price response and lagged same-clock normalization.
- `quarter_hour_router.py`: pure causal state machine with no execution or PnL dependency.
- `candidate21_strategy.py`: NautilusTrader strategy and cost-aware bracket submission.
- `candidate21_backtest.py`: thin runner reusing Candidate 05 and Candidate 20.
- `candidate.py`: CLI entry point.
- `tests/`: symmetry, strict-later-observation, no-future-baseline, target-consumption, acceptance, failure, and unresolved contracts.

## Run

```bash
smc4 doctor

PYTHONPATH=research/candidate-21:research/candidate-16:research/candidate-20:research/candidate-19:research/candidate-18:research/candidate-17:research/candidate-05 \
python research/candidate-21/candidate.py stage \
  --config research/candidate-21/config.json \
  --build-start 2024-07-05 \
  --build-end 2024-07-14 \
  --evaluation-start 2024-07-08 \
  --evaluation-end 2024-07-14 \
  --cache .cache/candidate-21/dev-2024-07-08 \
  --output artifacts/candidate-21/dev-2024-07-08 \
  --validation-mode development
```

The committed implementation is a complete executable candidate, but performance claims are made only from generated `run.json`, `metrics.json`, Nautilus reports, continuous `equity.csv`, and scenario event logs.
