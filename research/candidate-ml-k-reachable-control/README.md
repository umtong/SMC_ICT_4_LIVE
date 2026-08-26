# Candidate ML-k — Reachable Control Transfer

## One integrated decision policy

This candidate does not turn OB, FVG, trend lines, channels, fakeouts, or traps
into separate strategies. It keeps the deterministic liquidity-episode policy as
the market interpreter:

1. read direction, liquidity and multi-scale structure;
2. identify one causal episode and its source/destination geometry;
3. require evidence of control transfer (acceptance, reclaim, displacement,
   absorption, or mitigation response);
4. plan one first-return entry, structural invalidation and inherited target;
5. estimate, from strictly earlier mature observations, whether the order fills
   and whether the target is reached before the stop;
6. occupy the single global account slot only for the highest positive expected
   log-growth candidate.

The ML layer is deliberately narrow. It does not discover signals from raw price,
does not use a symbol ID, absolute price, outcome fields, MFE/MAE, or any data
created after the decision timestamp. It estimates two first-passage hazards for
explicit plans created by the causal market model.

## Missing piece

Across the inspected branches, the recurring failure was not lack of more signal
families. It was a mismatch between a valid liquidity event and an unreachable
raw-liquidity destination. Systems over-generated late departures with targets
around 6–9R, while useful evidence clustered around first defended returns and
nearby structural completion. Conversely, adding many confirmation gates reduced
activity to nearly zero.

`reachable_frontier_prior` encodes that missing geometry without introducing a
fixed-R target lattice. It describes whether the inherited target is local to the
same episode, whether entry is a first return/mitigation, and whether acceptance
or reclaim evidence supports the transfer. The causal model then learns the
actual target-before-stop probability, including the planned RR.

## Fixed account contract

- universe: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`;
- one continuous account and one global pending/position slot;
- one plan per causal episode;
- no scale-in or scale-out;
- TP and SL declared before entry;
- gross planned RR at least `1.0R`;
- stop risk fixed at `3%` of current NAV by quantity;
- full account acts as margin; leverage is the consequence of stop distance;
- no daily loss cap and no trade-count cap.

`risk_sized_quantity` is the exact quantity contract. For example, a 0.5% stop
implies approximately 6x notional exposure and a 2% stop approximately 1.5x.

## Reproduction

The workflow `.github/workflows/research-candidate-ml-k-short-diagnostic.yml`
reuses the existing liquidity episode generator and its Nautilus-backed research
execution. It harvests two chronological development windows plus one untouched
fresh window for all four symbols, runs unit tests, performs strict-causal
routing, and uploads the full episode/scored/selected/trade artifacts.

Local equivalent after dependencies are installed:

```bash
BASE=research/candidate-liquidity-episode-policy-v1
OUT=research_results/candidate_ml_k
CACHE=.cache/candidate_ml_k

python "$BASE/reproduce.py" harvest --start 2025-05-01 --end 2025-05-15 \
  --warmup-days 45 --period dev-2025-may-a --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache "$CACHE" --output "$OUT/dev-2025-may-a"
python "$BASE/reproduce.py" harvest --start 2025-06-01 --end 2025-06-15 \
  --warmup-days 45 --period dev-2025-jun-b --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache "$CACHE" --output "$OUT/dev-2025-jun-b"
python "$BASE/reproduce.py" harvest --start 2025-08-01 --end 2025-08-15 \
  --warmup-days 45 --period fresh-2025-aug-c --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache "$CACHE" --output "$OUT/fresh-2025-aug-c"
python research/candidate-ml-k-reachable-control/reachable_control_router.py \
  --root "$OUT" --output "$OUT/routed"
```

The first development window cannot trade because no mature history exists. The
second window may use only labels resolved before it starts. The fresh window may
use only mature labels from the two earlier development windows. This is a
policy property, not a post-hoc validation gate.
