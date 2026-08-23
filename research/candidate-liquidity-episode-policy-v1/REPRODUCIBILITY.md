# Reproducing Candidate Liquidity Episode Policy V1

All commands below start from the repository and branch. Nothing depends on a
chat attachment.

```bash
git clone https://github.com/umtong/SMC_ICT_4_LIVE.git
cd SMC_ICT_4_LIVE
git checkout candidate-liquidity-episode-policy-v1
```

The GitHub workflow uses this immutable container:

```text
ghcr.io/umtong/smc-ict-4-live-research@sha256:8f4de8a2b2fa28c3f424d114969b1c07765206708f24613b86896ced67532469
```

## 1. Verify the restoration without market downloads

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  ghcr.io/umtong/smc-ict-4-live-research@sha256:8f4de8a2b2fa28c3f424d114969b1c07765206708f24613b86896ced67532469 \
  bash -lc '
    uv pip install --python "$VIRTUAL_ENV/bin/python" \
      --requirement research/candidate-liquidity-episode-policy-v1/runtime-requirements.txt
    python research/candidate-liquidity-episode-policy-v1/reproduce.py \
      verify --output artifacts/reproduction/verify
    python research/candidate-liquidity-episode-policy-v1/self_check_reproduction.py \
      --output artifacts/reproduction/verify/self_check.json
  '
```

This checks the missing base router, imports every policy layer, confirms that the
strict causal wrapper replaced the base predictor and feature conversion, and
exercises deterministic one-global-slot arbitration with mature-label timing.

## 2. Reproduce the fixed four-symbol public-data smoke interval

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  ghcr.io/umtong/smc-ict-4-live-research@sha256:8f4de8a2b2fa28c3f424d114969b1c07765206708f24613b86896ced67532469 \
  bash -lc '
    uv pip install --python "$VIRTUAL_ENV/bin/python" \
      --requirement research/candidate-liquidity-episode-policy-v1/runtime-requirements.txt
    python research/candidate-liquidity-episode-policy-v1/reproduce.py harvest \
      --start 2025-02-01 \
      --end 2025-02-04 \
      --warmup-days 75 \
      --period dev-2025-feb \
      --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
      --cache .cache/candidate-liquidity-episode-policy-v1 \
      --output artifacts/reproduction/smoke/dev-2025-feb
    python research/candidate-liquidity-episode-policy-v1/reproduce.py inspect \
      --root artifacts/reproduction/smoke \
      --output artifacts/reproduction/smoke/inspection.json
  '
```

The decision window is half-open: `[2025-02-01, 2025-02-04)`. Warm-up data and
the label tail are fetched by the existing point-in-time market preparation
stack. The resulting `summary.json` records the triggering source SHA,
container digest, symbols and period.

## 3. Route any harvested chronological universe

Each period directory must contain `departure_actions.csv.gz` and
`summary.json`. Period names must begin with one of `dev`, `eval`, `fresh`,
`holdout` or `cal`.

```bash
python research/candidate-liquidity-episode-policy-v1/reproduce.py route \
  --root artifacts/reproduction/universe \
  --output artifacts/reproduction/account
```

The strict router trains only on chronologically earlier, mature `dev` labels.
Periods without sufficient mature history remain ineligible. The account
arbiter merges all symbols by event time and permits only one pending order or
open position at once.

## Durable GitHub evidence

After the branch workflow completes, inspect:

```text
research_results/candidate_liquidity_episode_policy_v1/latest/run.json
research_results/candidate_liquidity_episode_policy_v1/latest/contract/verify.json
research_results/candidate_liquidity_episode_policy_v1/latest/contract/self_check.json
research_results/candidate_liquidity_episode_policy_v1/latest/smoke/.../summary.json
research_results/candidate_liquidity_episode_policy_v1/latest/smoke/.../inspection.json
```

`run.json.source_sha` must match the source commit checked out by the workflow.
The large CSV and logs are GitHub Actions artifacts; compact committed JSON is
kept so the run is discoverable even after artifact retention expires.

## What this first reproduction run does not claim

The fixed smoke interval is an implementation and data-path reproduction, not a
long continuous performance result. An actual long continuous account record
must use contiguous evaluation windows, the same four-symbol global slot, one
NAV, and execution-cost accounting. A process-restart shadow self-check is not
the same as exchange-connected paper operation; the evidence must name the mode
accurately.
