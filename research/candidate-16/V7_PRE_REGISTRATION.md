# Candidate 16 v7 pre-registration

## Why this family owns the next experiment

Candidate 16 v6 showed that a single-market post-confirmation move of roughly
6–8 bp during the first five minutes cannot overcome 20 bp of complete modeled
round-trip friction. The next family must therefore begin with a larger economic
price interval rather than another local microstructure filter.

Candidate 05 v52 already implements a different cause: one asset becomes an
ATR-normalized robust outlier relative to the median of the other three project
assets, then begins idiosyncratic convergence. Its original workflow never
reached NautilusTrader because workflow injection removed a class imported by
the shared runner. No economic result exists for v52.

## Frozen economic strategy

The following original file is reused unchanged:

```text
research/candidate-05/strategy_v52_cross_sectional_residual.py
```

Its fixed sequence is:

1. each peer observation must be strictly earlier than the current symbol bar;
2. own five-minute ATR-normalized return minus median peer return;
3. absolute robust-MAD z-score at least the original fixed 2.5 boundary;
4. the residual begins converging rather than merely remaining extreme;
5. current one-minute own-versus-peer movement supports convergence;
6. fifteen-minute OI is not expanding;
7. tail flow, flow inflection, depth, response efficiency and activity support
   convergence;
8. the inherited causal rejection / confirmation / structural stop / liquidity
   objective / 3% current-NAV sizing remain unchanged.

Candidate 16 v7 changes only registration. It composes the existing v52 class
with the existing final shared-account one-slot lifecycle and exposes four
importable per-symbol classes. It does not change v52 constants, market logic,
configs, costs, risk, execution or account calculation.

## Deterministic untouched week

Eligible starts are Mondays from 2023-01-02 through 2025-12-22 excluding any
seven-day interval which overlaps:

- Candidate 05 frozen weeks: 2023-07-09..15, 2023-09-08..14,
  2024-01-15..21;
- Candidate 16 development/evaluation intervals: 2023-06-05..11,
  2023-08-21..27, 2023-11-20..26, 2024-02-19..03-17.

This leaves 144 eligible Monday starts.

Seed:

```text
candidate16-v7-cross-sectional-residual|5d883b27dbebffb0e5a07aec6a4aad56a41308af|independent-week-1
```

SHA-256:

```text
1ee0d36c2d68018dbfe9af7a38dae32777e1c9c115c866fe4e1dcaaa2243c2a2
```

`int(digest, 16) mod 144 = 50`, selecting:

- build/warm-up: 2024-02-10 through 2024-02-18;
- evaluation: 2024-02-12 through 2024-02-18.

After opening the result, only implementation failures may be repaired on the
same dates. No v52 state, threshold, direction, confirmation, stop, target,
cost, risk, global account rule or promotion gate may change.
