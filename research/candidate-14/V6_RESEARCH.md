# Candidate 14 v6 — Auction-Origin Ownership

## Why this revision exists

The frozen Candidate 14 L1 account path failed over 84 continuous days with 15 trades, 3 wins and 12 losses. Post-failure causal reconstruction found that the detector can mark one external-liquidity event as both a rejection candidate and an acceptance candidate. The ordinary FAR transition subsequently checks only `rejection_seed`, so generic reclaim, MSS and displacement can silently relabel an unresolved acceptance-origin auction as a failed-auction reversal.

In the inspected L1 ledger, all three executed FAR trades with `acceptance_seed=false` won, while all seven executed FAR trades with `acceptance_seed=true` lost. This split is not accepted as new performance evidence; it identifies a categorical state contradiction already visible in the source logic.

## One controlled change

```text
exclusive rejection origin
(rejection_seed=true, acceptance_seed=false)
→ ordinary reclaim + MSS + displacement FAR is eligible

mixed or acceptance origin
(acceptance_seed=true)
→ ordinary FAR is ineligible
→ remain with the acceptance hypothesis or expire
→ a future candidate must define a separate observable acceptance-failure transition
```

No magnitude threshold, symbol/session whitelist, market-leadership threshold, entry rule, stop, target, cost, risk fraction, quantity rule or NautilusTrader execution path is changed.

## Evidence role

`2026-05-11` through `2026-08-03` is reused because it contains the diagnosed failure. It is a controlled development diagnostic, not a holdout. The branch must never emit a success claim from this interval even if every performance gate passes.

A surviving mechanism must be frozen before a new non-overlapping continuous interval is reserved and collected.

## Reproduction

```bash
smc4 doctor
export PYTHONPATH="$PWD/research/candidate-14:$PWD/src"
python -m unittest discover -s research/candidate-14 -p 'test_*.py' -v
bash research/candidate-14/run_week.sh L1
python research/candidate-14/v6_continuous_aggregate.py \
  --result-dir research/candidate-14/results/L1 \
  --protocol research/candidate-14/protocol.json \
  --output research/candidate-14/aggregate.json
```
