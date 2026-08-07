#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
WEEK="${1:-W1}"
DIAGNOSTIC_ONLY=0
PREVIOUS=""
PREVIOUS_ADVANCE_ALLOWED=true

case "$WEEK" in
  W1|W2|W3|W4|W5|W6) ;;
  *) echo "week must be W1 through W6" >&2; exit 64 ;;
esac

smc4 doctor
python "$CAND/materialize_scdam.py"
python "$CAND/materialize_portfolio.py"
python "$CAND/apply_market_leadership.py"
python "$CAND/repair_leadership_sweep_timestamp.py"
python "$CAND/apply_market_leadership.py"

python -m py_compile \
  "$CAND/logic.py" \
  "$CAND/market_leadership.py" \
  "$CAND/run_leadership_scdam.py"
python -m unittest discover -s "$CAND" -p 'test_*.py' -v

# Advancement controls success claims, not whether independent evidence may be
# collected. W5/W6 therefore still run when the preceding weekly frequency
# gate fails, but the resulting metrics are explicitly sealed as diagnostic.
if [[ "$WEEK" == "W5" || "$WEEK" == "W6" ]]; then
  if [[ "$WEEK" == "W5" ]]; then PREVIOUS="W4"; else PREVIOUS="W5"; fi
  PREVIOUS_AUDIT="$CAND/results/LEADERSHIP_${PREVIOUS}/audit.json"
  test -s "$PREVIOUS_AUDIT"
  if ! python - "$PREVIOUS_AUDIT" <<'PY'
import json
import sys
audit = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if audit.get("advance_allowed") is True else 1)
PY
  then
    DIAGNOSTIC_ONLY=1
    PREVIOUS_ADVANCE_ALLOWED=false
    echo "previous week did not authorize promotion; executing ${WEEK} as diagnostic-only evidence" >&2
  fi
fi

OUT="$CAND/results/LEADERSHIP_${WEEK}"
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

if [[ "$DIAGNOSTIC_ONLY" == "1" ]]; then
  python - "$OUT/metrics.json" "$PREVIOUS" "$PREVIOUS_ADVANCE_ALLOWED" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
metrics = json.loads(path.read_text(encoding="utf-8"))
metrics["success_claim"] = False
metrics["diagnostic_only"] = True
metrics["prior_week"] = sys.argv[2]
metrics["prior_advance_allowed"] = sys.argv[3].lower() == "true"
temporary = path.with_suffix(path.suffix + ".tmp")
temporary.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(path)
PY
fi

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
    "diagnostic_only": metrics.get("diagnostic_only", False),
    "prior_advance_allowed": metrics.get("prior_advance_allowed"),
    "classification": audit.get("classification"),
    "advance_allowed": audit.get("advance_allowed"),
}, indent=2, sort_keys=True))
if audit.get("classification") == "IMPLEMENTATION_OR_EVIDENCE_FAILURE":
    raise SystemExit(2)
PY
