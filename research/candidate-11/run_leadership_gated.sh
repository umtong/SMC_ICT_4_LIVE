#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
WEEK="${1:-W1}"
DIAGNOSTIC_ONLY=0
PREVIOUS=""
PREVIOUS_ADVANCE_ALLOWED=true

case "$WEEK" in
  W1|W2|W3|W4|W5|W6|W7|W8|W9) ;;
  *) echo "week must be W1 through W9" >&2; exit 64 ;;
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
# collected. Post-screening intervals always execute, but remain sealed as
# diagnostic evidence when the preceding weekly frequency gate did not approve
# promotion. This prevents a frequency gate from blocking falsification.
case "$WEEK" in
  W5) PREVIOUS="W4" ;;
  W6) PREVIOUS="W5" ;;
  W7) PREVIOUS="W6" ;;
  W8) PREVIOUS="W7" ;;
  W9) PREVIOUS="W8" ;;
esac
if [[ -n "$PREVIOUS" ]]; then
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
# Import run() directly so frozen intervals added to config do not require a
# second CLI allow-list. The same Nautilus engine, strategy and execution model
# remain in force.
python - "$CAND" "$CAND/config.json" "$WEEK" "$OUT" <<'PY'
import json
import sys
from pathlib import Path

candidate_dir = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(candidate_dir))
from run_leadership_scdam import run

metrics = run(
    Path(sys.argv[2]).resolve(),
    sys.argv[3],
    Path(sys.argv[4]).resolve(),
)
print(json.dumps(metrics, indent=2, sort_keys=True, default=str))
PY

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

# Call the auditor as a library so W7-W9 remain ordinary named holdouts rather
# than being mislabeled as a generic LONG run.
python - "$CAND" "$OUT" "$WEEK" <<'PY'
import json
import sys
from pathlib import Path

candidate_dir = Path(sys.argv[1]).resolve()
result_dir = Path(sys.argv[2]).resolve()
week = sys.argv[3]
sys.path.insert(0, str(candidate_dir))
from evidence_audit import audit

result = audit(result_dir, week)
output = result_dir / "audit.json"
output.write_text(
    json.dumps(result, indent=2, sort_keys=True, default=str) + "\n",
    encoding="utf-8",
)
lines = ["# Candidate 11 evidence audit", "", f"**{result['classification']}**", ""]
for key in (
    "advance_allowed",
    "success_claim_allowed",
    "evidence_complete",
    "metric_recalculation_passed",
    "risk_budget_passed",
    "global_slot_passed",
    "partial_entry_protection_passed",
    "no_liquidation_passed",
):
    lines.append(f"- {key}: `{result[key]}`")
lines.extend(("", "## Reasons"))
lines.extend(f"- {reason}" for reason in result["reasons"])
(result_dir / "audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps(result, indent=2, sort_keys=True, default=str))
if result["classification"] == "IMPLEMENTATION_OR_EVIDENCE_FAILURE":
    raise SystemExit(2)
PY

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
