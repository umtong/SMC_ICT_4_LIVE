# Candidate 39 internal reuse and corrections

Candidate 39 does not rebuild the exchange, matching, order, account, data, or continuous-NAV machinery.

## Reused as-is

From `research/candidate-35`:

- checksum-verified Binance input loading and feature files;
- four-symbol same-clock replay;
- one NautilusTrader `BacktestNode`, one strategy process and one continuous margin account;
- global one-entry/one-position arbitration;
- contingent orders, fee model, latency model, adverse-slippage model, funding reserve and liquidation-enabled venue;
- 3% current-NAV planned-loss sizing;
- result reports, daily NAV, gate calculations, run manifest and data manifest.

`run.py` and `strategy.py` are thin dynamic adapters around that shell.  Candidate 39 replaces the causal router and execution-failure policy, not the engine.

## Preserved lessons

- Candidate 04: a session/range boundary matters only after interaction and re-entry/acceptance.
- Candidate 09: volatility-normalized geometry is preferable to fixed price distances.
- Candidate 10: multi-asset opportunity selection is useful, but not arbitrary notional or leverage caps.
- Candidate 14: targets should come from unconsumed auction objectives, not a fitted fixed-R alone.
- Candidate 35: freeze the feature observation at the interaction and let later completed bars confirm it.

## Candidate 16 failure converted into a hard correction

Candidate 16 lost almost the entire seven-day account in its rejection family.  The key logical error was that attack failure/reclaim and opposite-direction initiative were effectively the same observation.  The key execution error was that rejected protective stop children could leave exposure alive.

Candidate 39 changes both:

1. `CASCADE_RECLAIM_REVERSAL` freezes the reclaim on response bar 1 and requires response bar 2 or 3 to provide distinct later opposite initiative.
2. Any order rejection while a position is live cancels residual orders and immediately closes the position.
3. If the entry bar has already crossed the planned stop when the position-open event arrives, the strategy immediately flattens rather than carrying an unprotected timed hold.

## Not reused

- Candidate 35's OI-direction interpretation and clock-phase state labels.
- Candidate 16's immediate reversal after a reclaim.
- Custom account/portfolio simulation.
- Score-based risk multipliers, arbitrary notional ceilings or strategy leverage caps.
- The idea that multiple simultaneous asset signals are independent trades.  Same-side signals from one cross-asset episode produce one selected winner.
