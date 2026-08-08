#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CANDIDATE_ROOT="$(cd "$HERE/.." && pwd)"
STRATEGY="$CANDIDATE_ROOT/session_portfolio_v1"
ROOT="$(cd "$CANDIDATE_ROOT/../.." && pwd)"
INTERVAL="${1:?usage: run_holdout.sh H1|H2|H3 [OUTPUT_DIR]}"
OUT="${2:-$HERE/results/$INTERVAL}"

export PYTHONPATH="$STRATEGY:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

smc4 doctor
python -m py_compile \
  "$STRATEGY/bar_adapter.py" \
  "$STRATEGY/global_allocator.py" \
  "$STRATEGY/logic.py" \
  "$STRATEGY/market_leadership.py" \
  "$STRATEGY/session_engine.py" \
  "$STRATEGY/session_auction_i7.py" \
  "$STRATEGY/session_auction_bridge.py" \
  "$STRATEGY/semantic_logic.py" \
  "$STRATEGY/semantic_market_leadership.py" \
  "$STRATEGY/runner_materializer.py" \
  "$STRATEGY/portfolio_materializer.py" \
  "$STRATEGY/run_leadership_scdam.py" \
  "$STRATEGY/evidence_audit.py" \
  "$HERE/holdout_runner.py"

rm -rf "$OUT"
mkdir -p "$OUT"
python "$HERE/holdout_runner.py" "$INTERVAL" "$OUT"

for file in \
  run.json data_manifest.json metrics.json summary.json audit.json audit.md \
  source_lock.json effective_config.json scenario_events.jsonl \
  submitted_plans.json order_lifecycle.json orders.csv positions.csv account.csv; do
  test -s "$OUT/$file"
done
