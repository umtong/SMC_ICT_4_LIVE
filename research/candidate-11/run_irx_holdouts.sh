#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
BINDING="$CAND/irx_holdout_candidate.json"
PROTOCOL="$CAND/irx_holdout_protocol.json"
SUMMARY="$CAND/results/IRX_HOLDOUT/summary.json"
SHARED_DATA="/tmp/candidate-11-irx-holdout-data"

rm -rf "$CAND/results/IRX_HOLDOUT" "$CAND/results/IRX_HOLDOUT_W10" "$CAND/results/IRX_HOLDOUT_W11" "$CAND/results/IRX_HOLDOUT_W12" "$SHARED_DATA"
mkdir -p "$(dirname "$SUMMARY")" "$SHARED_DATA"

if [ ! -s "$BINDING" ] || [ ! -s "$PROTOCOL" ]; then
  python - "$SUMMARY" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    "schema": "candidate-11-irx-holdout-summary-v1",
    "status": "NOT_RUN_NO_MATRIX_ELIGIBLE_BINDING",
    "holdout_gate_passed": False,
    "success_claim": False,
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  exit 0
fi

VARIANT="$(python - "$BINDING" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['selected_variant'])
PY
)"

set_variant() {
  case "$VARIANT" in
    STRICT)
      export C11_IRX_MIN_TARGET_ATR=0.75 C11_IRX_MAX_TARGET_ATR=12.0
      export C11_IRX_MIN_SWEEP_ATR=0.03 C11_IRX_MAX_SWEEP_ATR=1.50
      export C11_IRX_MIN_BODY_ATR=0.20 C11_IRX_MIN_LOCATION=0.65
      export C11_IRX_MIN_IMPULSE=0.80 C11_IRX_MIN_REL_VOLUME=0.80
      export C11_IRX_MIN_BUY_FRACTION=0.52 C11_IRX_MAX_BUY_FRACTION=0.48
      ;;
    BALANCED)
      export C11_IRX_MIN_TARGET_ATR=0.50 C11_IRX_MAX_TARGET_ATR=12.0
      export C11_IRX_MIN_SWEEP_ATR=0.02 C11_IRX_MAX_SWEEP_ATR=1.50
      export C11_IRX_MIN_BODY_ATR=0.15 C11_IRX_MIN_LOCATION=0.60
      export C11_IRX_MIN_IMPULSE=0.55 C11_IRX_MIN_REL_VOLUME=0.65
      export C11_IRX_MIN_BUY_FRACTION=0.51 C11_IRX_MAX_BUY_FRACTION=0.49
      ;;
    LEADERSHIP_DOMINANT)
      export C11_IRX_MIN_TARGET_ATR=0.35 C11_IRX_MAX_TARGET_ATR=14.0
      export C11_IRX_MIN_SWEEP_ATR=0.015 C11_IRX_MAX_SWEEP_ATR=1.75
      export C11_IRX_MIN_BODY_ATR=0.12 C11_IRX_MIN_LOCATION=0.57
      export C11_IRX_MIN_IMPULSE=0.35 C11_IRX_MIN_REL_VOLUME=0.50
      export C11_IRX_MIN_BUY_FRACTION=0.505 C11_IRX_MAX_BUY_FRACTION=0.495
      ;;
    *) echo "invalid frozen IRX variant: $VARIANT" >&2; exit 65 ;;
  esac
}

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
python "$CAND/apply_internal_reclaim_parameters.py"
python "$CAND/apply_internal_reclaim_block_fix.py"
python "$CAND/apply_internal_reclaim.py"
python "$CAND/apply_irx_holdout_protocol_v2.py"
set_variant

python -m py_compile \
  "$CAND/internal_reclaim.py" \
  "$CAND/session_engine.py" \
  "$CAND/run_leadership_scdam.py" \
  "$CAND/evidence_audit.py"
python -m unittest discover -s "$CAND" -p 'test_*.py' -v

for WEEK in W10 W11 W12; do
  OUT="$CAND/results/IRX_HOLDOUT_$WEEK"
  mkdir -p "$OUT"
  ln -s "$SHARED_DATA" "$OUT/data"
  python "$CAND/run_leadership_scdam.py" \
    --config "$CAND/config.json" \
    --week "$WEEK" \
    --output "$OUT"
  rm "$OUT/data"
  python - "$OUT/metrics.json" "$OUT/submitted_plans.json" "$VARIANT" <<'PY'
import json
import sys
from pathlib import Path
metrics_path = Path(sys.argv[1])
plans_path = Path(sys.argv[2])
variant = sys.argv[3]
metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
payload = json.loads(plans_path.read_text(encoding="utf-8"))
plans = payload.get("plans", payload if isinstance(payload, list) else [])
internal = [p for p in plans if isinstance(p, dict) and isinstance(p.get("details"), dict) and p["details"].get("source") == "INTERNAL_RECLAIM_EXTERNAL_DRAW"]
metrics["irx_variant"] = variant
metrics["internal_reclaim_submitted_plans"] = len(internal)
metrics["untouched_irx_holdout"] = True
metrics["success_claim"] = False
metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  python "$CAND/evidence_audit.py" "$OUT" --week "$WEEK" --output "$OUT/audit.json"
done

python - "$CAND" "$BINDING" "$PROTOCOL" "$SUMMARY" <<'PY'
from __future__ import annotations
import csv
from decimal import Decimal
from hashlib import sha256
import json
from math import prod
from pathlib import Path
import sys

root = Path(sys.argv[1])
binding_path = Path(sys.argv[2])
protocol_path = Path(sys.argv[3])
summary_path = Path(sys.argv[4])
weeks = ("W10", "W11", "W12")
metrics = {}
audits = {}
pnls_by_week: dict[str, list[Decimal]] = {}

def dec(value: object) -> Decimal:
    return Decimal(str(value).split()[0].replace(",", ""))

for week in weeks:
    result = root / "results" / f"IRX_HOLDOUT_{week}"
    metrics[week] = json.loads((result / "metrics.json").read_text(encoding="utf-8"))
    audits[week] = json.loads((result / "audit.json").read_text(encoding="utf-8"))
    pnls = []
    with (result / "positions.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            value = row.get("realized_pnl") or row.get("pnl")
            if value not in (None, "", "nan", "None"):
                pnls.append(dec(value))
    pnls_by_week[week] = pnls

multipliers = [
    Decimal(str(metrics[week]["final_nav"])) / Decimal(str(metrics[week]["starting_nav"]))
    for week in weeks
]
chained_multiplier = prod(multipliers, start=Decimal(1))
daily_growth = float(chained_multiplier ** (Decimal(1) / Decimal(21)) - Decimal(1))
all_pnls = [value for week in weeks for value in pnls_by_week[week]]
wins = [value for value in all_pnls if value > 0]
losses = [value for value in all_pnls if value < 0]
win_rate = len(wins) / len(all_pnls) if all_pnls else 0.0
payoff = None
if wins and losses:
    payoff = float((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
elif wins:
    payoff = float("inf")

combined_equity = Decimal("100000")
peak = combined_equity
max_drawdown = Decimal(0)
for week in weeks:
    local_equity = Decimal(str(metrics[week]["starting_nav"]))
    for pnl in pnls_by_week[week]:
        factor = (local_equity + pnl) / local_equity
        combined_equity *= factor
        local_equity += pnl
        peak = max(peak, combined_equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - combined_equity) / peak)

safety = all(
    not metrics[week].get("engine_errors")
    and not metrics[week].get("liquidation_detected")
    and int(metrics[week].get("global_slot_overlap_count") or 0) == 0
    and metrics[week].get("event_log_valid") is True
    and audits[week].get("evidence_complete") is True
    and audits[week].get("risk_budget_passed") is True
    and audits[week].get("global_slot_passed") is True
    and audits[week].get("partial_entry_protection_passed") is True
    for week in weeks
)
closed = len(all_pnls)
minimum_week_trades = min(int(metrics[week].get("closed_trades") or 0) for week in weeks)
internal_plans = sum(int(metrics[week].get("internal_reclaim_submitted_plans") or 0) for week in weeks)
holdout_gate = (
    safety
    and closed >= 12
    and minimum_week_trades >= 2
    and len(losses) <= 1
    and win_rate >= 0.90
    and (payoff is None or payoff >= 1.20)
    and daily_growth >= 0.01
    and float(max_drawdown) <= 0.20
)
summary = {
    "schema": "candidate-11-irx-holdout-summary-v1",
    "status": "COMPLETED",
    "selected_variant": json.loads(binding_path.read_text(encoding="utf-8"))["selected_variant"],
    "protocol_sha256": sha256(protocol_path.read_bytes()).hexdigest(),
    "binding_sha256": sha256(binding_path.read_bytes()).hexdigest(),
    "weeks": {
        week: {
            key: metrics[week].get(key)
            for key in (
                "evaluation_start", "evaluation_end_exclusive", "daily_geometric_growth",
                "net_return", "final_nav", "closed_trades", "wins", "losses",
                "win_rate", "payoff_ratio", "closed_trade_max_drawdown",
                "submitted_plans", "internal_reclaim_submitted_plans",
                "engine_errors", "liquidation_detected", "global_slot_overlap_count",
            )
        }
        for week in weeks
    },
    "combined": {
        "calendar_days": 21,
        "nav_multiplier": float(chained_multiplier),
        "daily_geometric_growth": daily_growth,
        "closed_trades": closed,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "payoff_ratio": None if payoff == float("inf") else payoff,
        "all_closed_trades_won": bool(wins) and not losses,
        "closed_trade_max_drawdown": float(max_drawdown),
        "minimum_week_closed_trades": minimum_week_trades,
        "internal_reclaim_submitted_plans": internal_plans,
    },
    "safety_passed": safety,
    "holdout_gate_passed": holdout_gate,
    "success_claim": False,
}
summary_path.parent.mkdir(parents=True, exist_ok=True)
summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
PY
