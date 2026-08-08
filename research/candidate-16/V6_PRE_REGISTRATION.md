# Candidate 16 v6 pre-registration

## Frozen economic system

- State: one-minute displacement clears complete modeled round-trip friction,
  notional is above its causal median baseline, aggressor flow is aligned,
  five-minute OI change is positive and fresh, and completed-minute closing L1
  pressure is aligned.
- No order is placed on the initiative bar.
- Transition: a strictly later counter-direction bar closes on the directional
  side of the initiative open/close midpoint.
- Entry confirmation: a still later bar closes through the pullback boundary
  with renewed directional aggressor flow and closing L1 pressure.
- Entry: all-or-none FOK LIMIT at a precomputed worst-fill cap.
- Stop: pullback extreme plus the frozen ATR buffer.
- Target: pre-existing active directional liquidity with at least 1.0 net R
  after configured costs. No fallback target exists.
- Risk: 3% of contemporaneous account NAV at the worst permissible fill.
- Performance gate is unchanged.

## Development exclusions

- 2023-11-20 through 2023-11-26 was used to repair L1 joins and diagnose the
  sweep-first hierarchy.
- 2023-06-05 through 2023-06-11 was opened for v5/v5b and used to reject the
  crowded-initiative fade and identify the complementary aligned state.

Neither period is independent evidence for v6.

## Deterministic untouched screen

Eligible Mondays run from 2023-05-22 through 2024-03-18 with the three-day
warm-up and seven-day evaluation contained in one calendar month, excluding the
two development Mondays above. There are 30 eligible Mondays.

Seed:

```text
candidate16-v6-informed-initiative-continuation|f7207386d09b90a4d3a6e23eb42a67252572948b|independent-week-1
```

SHA-256:

```text
85e236fb4ea6a916f1f93b9ee0da231798efcc043dbec0702fbd59cfa407484e
```

`int(digest, 16) mod 30 = 8`, selecting:

- build: 2023-08-18 through 2023-08-27
- evaluation: 2023-08-21 through 2023-08-27

After the result is opened, only implementation errors may be repaired without
changing this period. State, sequence, entry, stop, target, costs, risk and gate
are frozen.
