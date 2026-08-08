# Candidate 35 — Clock-Phase Auction Router

Candidate 35 is a four-asset, one-account NautilusTrader system for BTCUSDT,
ETHUSDT, SOLUSDT and XRPUSDT. It acts only after all four completed one-minute
observations for the same UTC minute are available.

The external idea was decomposed into one policy:

1. Treat the first three completed minutes of each quarter hour as an auction
   response to the preceding 15-minute impulse.
2. Route to continuation only when displacement is accepted with participation,
   flow/efficiency and cross-asset support.
3. Route to reversal only when a sufficiently large prior impulse fails to hold
   its boundary extension and the opposite response confirms exhaustion.
4. Otherwise return `UNRESOLVED`; rank all actionable symbols and submit only the
   strongest single bracket.

NautilusTrader owns orders, contingent children, partial-fill OTO release,
fees, latency, positions, margin, liquidation and continuous NAV. Planned loss
is current account NAV × 3%, divided by stop distance plus entry/stop costs,
adverse slippage reserve and funding reserve. There is no strategy-level
notional or leverage-based size cap; exchange contract quantity limits remain
binding.

## Commands

```bash
python -m unittest discover -s research/candidate-35 -p 'test_*.py' -v
python research/candidate-35/launch.py \
  --config research/candidate-35/config.json \
  --start 2026-07-01 --end 2026-07-07 \
  --cache .cache/c35 --workspace .cache/c35-work \
  --output artifacts/c35-smoke
```

For long validation, build checksum-verified monthly chunks with
`build_chunk.py`, then replay the common root through
`run_continuous.py --input-root`.
