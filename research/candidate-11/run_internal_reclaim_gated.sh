#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
WEEK="${1:-W3}"

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
python "$CAND/apply_market_leadership.py"
python "$CAND/apply_price_discovery_revision.py"
python "$CAND/apply_internal_reclaim.py"

python -m py_compile \
  "$CAND/internal_reclaim.py" \
  "$CAND/session_engine.py" \
  "$CAND/market_leadership.py" \
  "$CAND/run_leadership_scdam.py"
python -m unittest discover -s "$CAND" -p 'test_*.py' -v

OUT="$CAND/results/HYBRID_${WEEK}"
rm -rf "$OUT"
mkdir -p "$OUT"
python "$CAND/run_leadership_scdam.py" \
  --config "$CAND/config.json" \
  --week "$WEEK" \
  --output "$OUT"

for file in \
  run.json data_manifest.json metrics.json scenario_events.jsonl \
  submitted_plans.json order_lifecycle.json orders.csv positions.csv account.csv; do
  test -s "$OUT/$file"
done

python - "$OUT/metrics.json" "$OUT/submitted_plans.json" <<'PY'
import json
import sys
from pathlib import Path

metrics_path = Path(sys.argv[1])
plans_path = Path(sys.argv[2])
metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
payload = json.loads(plans_path.read_text(encoding="utf-8"))
plans = payload.get("plans", payload if isinstance(payload, list) else [])
internal = [
    plan for plan in plans
    if isinstance(plan, dict)
    and isinstance(plan.get("details"), dict)
    and plan["details"].get("source") == "INTERNAL_RECLAIM_EXTERNAL_DRAW"
]
metrics["strategy_family"] = "SCDAM_PRICE_DISCOVERY_PLUS_INTERNAL_RECLAIM"
metrics["internal_reclaim_submitted_plans"] = len(internal)
metrics["diagnostic_only"] = True
metrics["success_claim"] = False
temporary = metrics_path.with_suffix(metrics_path.suffix + ".tmp")
temporary.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(metrics_path)
PY

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
    "week_id": metrics.get("week_id"),
    "daily_geometric_growth": metrics.get("daily_geometric_growth"),
    "closed_trades": metrics.get("closed_trades"),
    "wins": metrics.get("wins"),
    "losses": metrics.get("losses"),
    "win_rate": metrics.get("win_rate"),
    "payoff_ratio": metrics.get("payoff_ratio"),
    "closed_trade_max_drawdown": metrics.get("closed_trade_max_drawdown"),
    "submitted_plans": metrics.get("submitted_plans"),
    "internal_reclaim_submitted_plans": metrics.get("internal_reclaim_submitted_plans"),
    "liquidation_detected": metrics.get("liquidation_detected"),
    "global_slot_overlap_count": metrics.get("global_slot_overlap_count"),
    "classification": audit.get("classification"),
}, indent=2, sort_keys=True))
if audit.get("classification") == "IMPLEMENTATION_OR_EVIDENCE_FAILURE":
    raise SystemExit(2)
PY
