#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
WEEK="${1:-W9}"

case "$WEEK" in
  W1|W2|W3|W4|W5|W6|W7|W8|W9) ;;
  *) echo "week must be W1 through W9" >&2; exit 64 ;;
esac

smc4 doctor
python "$CAND/materialize_scdam.py"
python "$CAND/apply_boundary_consumption.py"
python "$CAND/revert_rejected_episode_horizon.py"
python "$CAND/materialize_portfolio.py"
python "$CAND/apply_partial_fill_fail_closed.py"
python "$CAND/apply_market_leadership.py"
python "$CAND/repair_leadership_sweep_timestamp.py"
python "$CAND/patch_evidence_audit_weeks.py"
python "$CAND/materialize_positioning_auction.py"

python -m py_compile \
  "$CAND/positioning_auction.py" \
  "$CAND/run_positioning_auction.py" \
  "$CAND/evidence_audit.py"
python -m unittest discover -s "$CAND" -p 'test_*.py' -v

OUT="$CAND/results/POSITIONING_${WEEK}"
rm -rf "$OUT"
mkdir -p "$OUT"
python "$CAND/run_positioning_auction.py" \
  --config "$CAND/config.json" \
  --week "$WEEK" \
  --output "$OUT"

for file in \
  run.json data_manifest.json metrics.json scenario_events.jsonl \
  submitted_plans.json order_lifecycle.json orders.csv positions.csv account.csv; do
  test -s "$OUT/$file"
done

python "$CAND/evidence_audit.py" \
  "$OUT" \
  --week "$WEEK" \
  --output "$OUT/audit.json"

python - "$OUT/metrics.json" "$OUT/audit.json" <<'PY'
import json
import sys
metrics = json.load(open(sys.argv[1], encoding="utf-8"))
audit = json.load(open(sys.argv[2], encoding="utf-8"))
print(json.dumps({
    "candidate": metrics.get("candidate"),
    "week_id": metrics.get("week_id"),
    "daily_geometric_growth": metrics.get("daily_geometric_growth"),
    "closed_trades": metrics.get("closed_trades"),
    "win_rate": metrics.get("win_rate"),
    "payoff_ratio": metrics.get("payoff_ratio"),
    "closed_trade_max_drawdown": metrics.get("closed_trade_max_drawdown"),
    "leadership_rejection_counts": metrics.get("leadership_rejection_counts"),
    "classification": audit.get("classification"),
    "risk_budget_passed": audit.get("risk_budget_passed"),
    "global_slot_passed": audit.get("global_slot_passed"),
    "no_liquidation_passed": audit.get("no_liquidation_passed"),
}, indent=2, sort_keys=True))
if audit.get("classification") == "IMPLEMENTATION_OR_EVIDENCE_FAILURE":
    raise SystemExit(2)
PY
