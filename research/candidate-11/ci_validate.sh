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
  research/candidate-11/global_allocator.py \
  research/candidate-11/logic.py \
  research/candidate-11/session_engine.py \
  research/candidate-11/run.py \
  research/candidate-11/run_portfolio_scdam.py \
  research/candidate-11/evidence_audit.py
python -m unittest discover -s research/candidate-11 -p 'test_*.py' -v

RESULT_DIR="research/candidate-11/results/$WEEK"
rm -rf "$RESULT_DIR"
python research/candidate-11/run.py --week "$WEEK" --output "$RESULT_DIR"

PORTFOLIO_RESULT_DIR="research/candidate-11/results/PORTFOLIO_$WEEK"
rm -rf "$PORTFOLIO_RESULT_DIR"
python research/candidate-11/run_portfolio_scdam.py \
  --week "$WEEK" \
  --output "$PORTFOLIO_RESULT_DIR"

for file in \
  run.json data_manifest.json metrics.json scenario_events.jsonl \
  submitted_plans.json order_lifecycle.json orders.csv positions.csv account.csv; do
  test -s "$RESULT_DIR/$file"
  test -s "$PORTFOLIO_RESULT_DIR/$file"
done

python research/candidate-11/evidence_audit.py \
  "$PORTFOLIO_RESULT_DIR" \
  --week "$WEEK" \
  --output "$PORTFOLIO_RESULT_DIR/evidence_audit.json"
test -s "$PORTFOLIO_RESULT_DIR/evidence_audit.json"
test -s "$PORTFOLIO_RESULT_DIR/evidence_audit.md"

python - "$RESULT_DIR/metrics.json" "$PORTFOLIO_RESULT_DIR/metrics.json" "$PORTFOLIO_RESULT_DIR/evidence_audit.json" <<'PY'
import json
import sys
from pathlib import Path

for label, path in (("BTC baseline", sys.argv[1]), ("four-market independent SCDAM", sys.argv[2])):
    metrics = json.loads(Path(path).read_text(encoding="utf-8"))
    print(f"Candidate 11 {label} metrics")
    for key in (
        "week_id", "daily_geometric_growth", "closed_trades", "win_rate",
        "payoff_ratio", "closed_trade_max_drawdown", "submitted_plans",
        "scenario_counts", "symbol_counts", "global_slot_overlap_count",
        "partial_entry_fail_closed_count", "liquidation_detected",
        "promising_gate_passed", "complete_gate_passed", "success_claim",
    ):
        print(f"{key}={metrics.get(key)}")

audit = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
print("Candidate 11 independent execution-safety audit")
for key in (
    "classification", "risk_budget_passed", "global_slot_passed",
    "partial_entry_protection_passed", "no_liquidation_passed",
):
    print(f"{key}={audit.get(key)}")
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
    "$RESULT_DIR/account.csv" \
    "$PORTFOLIO_RESULT_DIR/run.json" \
    "$PORTFOLIO_RESULT_DIR/data_manifest.json" \
    "$PORTFOLIO_RESULT_DIR/metrics.json" \
    "$PORTFOLIO_RESULT_DIR/scenario_events.jsonl" \
    "$PORTFOLIO_RESULT_DIR/submitted_plans.json" \
    "$PORTFOLIO_RESULT_DIR/order_lifecycle.json" \
    "$PORTFOLIO_RESULT_DIR/orders.csv" \
    "$PORTFOLIO_RESULT_DIR/positions.csv" \
    "$PORTFOLIO_RESULT_DIR/account.csv" \
    "$PORTFOLIO_RESULT_DIR/evidence_audit.json" \
    "$PORTFOLIO_RESULT_DIR/evidence_audit.md"
  if ! git diff --cached --quiet; then
    git commit -m "candidate-11: record $WEEK portfolio SCDAM evidence [skip ci]"
    git push origin HEAD:research/candidate-11
  fi
fi
