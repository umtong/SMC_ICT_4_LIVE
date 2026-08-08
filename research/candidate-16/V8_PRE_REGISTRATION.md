# Candidate 16 v8 pre-registration

## Frozen system

Candidate 16 v8 preserves Candidate 05 v52's original state detector unchanged:
strictly prior peer observations, robust four-asset residual, residual inflection,
OI not expanding, and local tail-flow/depth evidence.

The v7 same-bar inherited confirmation is removed. The v52 setup is converted
into a frozen observation without an order. During the next 15 completed minutes
(the same horizon as the state OI observation), a trade is permitted only when:

1. the residual remains on the same side but contracts in absolute magnitude;
2. price crosses the frozen-state close in the convergence direction;
3. own one-minute ATR-normalized movement exceeds the strictly prior peer median
   in the convergence direction;
4. the current bar itself moves in that direction;
5. current one-minute aggressor flow and displayed depth are aligned.

The later bar is a new auction leg. Entry is a full-or-none FOK LIMIT at a cap
whose adverse room equals the configured adverse-slippage rate. Quantity is
sized from that worst fill, the state-to-confirmation extreme, fees and
slippage, using 3% of current shared-account NAV. The target must be a
pre-existing active directional liquidity pool with at least 1.0 net R after
costs. There is no fallback target.

The one-account four-symbol NautilusTrader runner and final audited global slot
are unchanged.

## Development exclusions

The following intervals have already influenced Candidate 16 research and are
excluded from independent evidence:

- 2023-06-05 through 2023-06-11;
- 2023-08-21 through 2023-08-27;
- 2023-11-20 through 2023-11-26;
- 2024-02-12 through 2024-02-18;
- 2024-02-19 through 2024-03-17.

Candidate 05's three frozen weeks are also excluded:

- 2023-07-09 through 2023-07-15;
- 2023-09-08 through 2023-09-14;
- 2024-01-15 through 2024-01-21.

## Deterministic untouched week

Eligible starts are Mondays from 2023-01-02 through 2025-12-22 whose seven-day
interval overlaps none of the exclusions above. This leaves 143 starts.

Seed:

```text
candidate16-v8-later-residual-convergence|c8b3cbff9c6b83cee4f170753cd689d1fc4e81f1|independent-week-1
```

SHA-256:

```text
37a2b20eaaca3dff3e467dd62093d02c986aadf817509cc3d27c39304f6bc869
```

`int(digest, 16) mod 143 = 50`, selecting:

- build/warm-up: 2024-03-16 through 2024-03-24;
- evaluation: 2024-03-18 through 2024-03-24.

Once opened, only implementation failures may be repaired on these same dates.
The v52 state, v8 later transition, 15-minute horizon, FOK cap, stop, natural
target, costs, 3% risk, global account constraint and promotion gate are frozen.
