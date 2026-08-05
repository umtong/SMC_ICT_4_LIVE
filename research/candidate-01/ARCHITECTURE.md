# Architecture and invariants

```text
Binance Vision / live kline
        │
        ▼
validated AuctionBar ──────────────┐
        │                           │ observation time
        ▼                           │
completed-range detector            │
trailing structure detector         │
flow/activity normalizer            │
        │                           │
        └──────────┬────────────────┘
                   ▼
          AuctionStateMachine
   WATCHING → SWEPT/ACCEPTING
       → ARMED → PLAN/INVALIDATED
                   │
                   ▼
         delayed execution check
       price order + net RR + NAV
                   │
                   ▼
             GlobalEntryGate
                   │
                   ▼
      Nautilus bracket / margin / fees
                   │
                   ▼
       position + NAV + event evidence
```

## Hard invariants

- No detector reads a future bar.
- Event observation time is never earlier than event time.
- A plan is emitted only after an ordered terminal state transition.
- A pre-evaluation signal cannot enter the evaluation interval.
- Quantity uses current account equity and per-unit expected loss after costs.
- No strategy-level notional cap or score-dependent size is applied.
- A pending entry owns the same global slot as an open position.
- The gate is not released by a child-order fault while exposure remains.
- Every run is flattened at its evaluation boundary.
- Stop orders and engine liquidation are explicitly enabled.
- Missing data, missing flow context, and non-monotonic time fail closed.
