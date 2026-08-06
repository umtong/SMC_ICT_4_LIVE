#!/usr/bin/env bash
set -euo pipefail

WEEK="${1:-W1}"
case "$WEEK" in
  W1|W2|W3) ;;
  *) echo "unsupported frozen week: $WEEK" >&2; exit 2 ;;
esac

smc4 doctor
python -m py_compile \
  research/candidate-11/bar_adapter.py \
  research/candidate-11/logic.py \
  research/candidate-11/session_engine.py \
  research/candidate-11/run.py
python -m unittest discover -s research/candidate-11 -p 'test_*.py' -v

RESULT_DIR="research/candidate-11/results/$WEEK"
rm -rf "$RESULT_DIR"
python research/candidate-11/run.py --week "$WEEK" --output "$RESULT_DIR"

for file in \
  run.json data_manifest.json metrics.json scenario_events.jsonl \
  submitted_plans.json order_lifecycle.json orders.csv positions.csv account.csv; do
  test -s "$RESULT_DIR/$file"
done

python - "$RESULT_DIR/metrics.json" <<'PY'
import json
import sys
from pathlib import Path

metrics = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print("Candidate 11 SCDAM metrics")
for key in (
    "week_id",
    "daily_geometric_growth",
    "closed_trades",
    "win_rate",
    "payoff_ratio",
    "closed_trade_max_drawdown",
    "submitted_plans",
    "liquidation_detected",
    "promising_gate_passed",
    "complete_gate_passed",
    "success_claim",
):
    print(f"{key}={metrics.get(key)}")
PY

if [[ "${GITHUB_EVENT_NAME:-}" != "pull_request" ]]; then
  git config user.name "candidate-11-bot"
  git config user.email "candidate-11-bot@users.noreply.github.com"
  git add \
    "$RESULT_DIR/run.json" \
    "$RESULT_DIR/data_manifest.json" \
    "$RESULT_DIR/metrics.json" \
    "$RESULT_DIR/scenario_events.jsonl" \
    "$RESULT_DIR/submitted_plans.json" \
    "$RESULT_DIR/order_lifecycle.json" \
    "$RESULT_DIR/orders.csv" \
    "$RESULT_DIR/positions.csv" \
    "$RESULT_DIR/account.csv"
  if ! git diff --cached --quiet; then
    git commit -m "candidate-11: record $WEEK Nautilus evidence [skip ci]"
    git push origin HEAD:research/candidate-11
  fi
fi
