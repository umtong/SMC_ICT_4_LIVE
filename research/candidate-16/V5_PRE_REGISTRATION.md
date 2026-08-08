# Candidate 16 v5 pre-registration

## Frozen economic system

- Branch: `research/candidate-16-v5-crowded-initiative-rejection`
- State: cost-exceeding one-minute initiative, aligned aggressor flow, positive
  five-minute OI change, and opposing completed-minute closing L1 pressure.
- Confirmation: a strictly later completed bar must move price, aggressor flow,
  and closing L1 pressure in the fade direction.
- Entry: price-capped STOP_LIMIT re-arm; no order on the shock bar.
- Invalidation: shock extreme plus the frozen ATR buffer.
- Target: shock origin or an already active causal liquidity pool with at least
  1.0 cost-after net R. There is no fallback target.
- Risk: 3% of contemporaneous account NAV at the worst permissible entry fill.
- Costs and performance gate are unchanged from v4b.

## Development evidence

The repaired 2023-11-20 through 2023-11-26 v4b data was used only to identify
that the sweep-first hierarchy had no gross directional edge and to form the
new state-first crowded-initiative hypothesis. It is development data and is
excluded from independent evidence.

## Deterministic untouched screen

The eligible set is every Monday from 2023-05-22 through 2024-03-18 for which
three warm-up days and the seven-day evaluation remain in one calendar month,
excluding 2023-11-20. This produces 31 eligible Mondays.

Seed:

```text
candidate16-v5-crowded-initiative-rejection|ed817d350ce3415729faada3e7dbae3e56afcae9|independent-week-1
```

SHA-256:

```text
d10a3397bde9c15d8c48aed90c45d9f9c3f1a3f522aca83f3b759110b25a58ab
```

`int(digest, 16) mod 31 = 1`, selecting:

- build: 2023-06-02 through 2023-06-11
- evaluation: 2023-06-05 through 2023-06-11

Only implementation errors may be repaired after opening this result. Economic
state, confirmation, entry, invalidation, target, costs, risk, gate, and dates
remain frozen.
