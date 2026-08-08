#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
WORK="/tmp/candidate-11-irx-matrix"
SHARED_DATA="$WORK/data"
SUMMARY="$CAND/results/IRX_MATRIX/summary.json"
rm -rf "$WORK" "$CAND/results/IRX_MATRIX" "$CAND/results/IRX_SELECTED_W3" "$CAND/results/IRX_SELECTED_W8" "$CAND/results/IRX_SELECTED_W9"
mkdir -p "$WORK" "$SHARED_DATA" "$(dirname "$SUMMARY")"

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
python "$CAND/apply_internal_reclaim.py"
python -m py_compile "$CAND/internal_reclaim.py" "$CAND/session_engine.py" "$CAND/run_leadership_scdam.py"
python -m unittest discover -s "$CAND" -p 'test_*.py' -v

run_variant() {
  local variant="$1" week="$2"
  local out="$WORK/$variant/$week"
  mkdir -p "$out"
  ln -s "$SHARED_DATA" "$out/data"
  case "$variant" in
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
    *) echo "unknown variant $variant" >&2; exit 64 ;;
  esac
  python "$CAND/run_leadership_scdam.py" \
    --config "$CAND/config.json" \
    --week "$week" \
    --output "$out"
  rm "$out/data"
  python - "$out/metrics.json" "$out/submitted_plans.json" "$variant" <<'PY'
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
metrics["diagnostic_only"] = True
metrics["success_claim"] = False
metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

for variant in STRICT BALANCED LEADERSHIP_DOMINANT; do
  for week in W3 W9 W8; do
    run_variant "$variant" "$week"
  done
done

python - "$WORK" "$SUMMARY" <<'PY'
from __future__ import annotations
import json
from math import log
from pathlib import Path
import shutil
import sys

work = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
variants = ("STRICT", "BALANCED", "LEADERSHIP_DOMINANT")
weeks = ("W3", "W9", "W8")
records = []
for variant in variants:
    metrics = {}
    for week in weeks:
        path = work / variant / week / "metrics.json"
        metrics[week] = json.loads(path.read_text(encoding="utf-8"))
    diagnostic = [metrics["W3"], metrics["W9"]]
    all_weeks = diagnostic + [metrics["W8"]]
    safety = all(
        not m.get("engine_errors")
        and not m.get("liquidation_detected")
        and int(m.get("global_slot_overlap_count") or 0) == 0
        and m.get("event_log_valid") is True
        for m in all_weeks
    )
    losses = sum(int(m.get("losses") or 0) for m in all_weeks)
    diagnostic_closed = sum(int(m.get("closed_trades") or 0) for m in diagnostic)
    diagnostic_internal = sum(int(m.get("internal_reclaim_submitted_plans") or 0) for m in diagnostic)
    diagnostic_log_growth = sum(log(max(float(m.get("final_nav") or 0) / float(m.get("starting_nav") or 1), 1e-12)) for m in diagnostic)
    w8_preserved = (
        int(metrics["W8"].get("losses") or 0) == 0
        and int(metrics["W8"].get("wins") or 0) >= 3
        and float(metrics["W8"].get("daily_geometric_growth") or 0.0) >= 0.02
    )
    eligible = safety and losses == 0 and w8_preserved and diagnostic_internal >= 1 and diagnostic_closed >= 1
    records.append({
        "variant": variant,
        "eligible": eligible,
        "safety_passed": safety,
        "losses_all_three_weeks": losses,
        "diagnostic_closed_trades": diagnostic_closed,
        "diagnostic_internal_plans": diagnostic_internal,
        "diagnostic_log_growth": diagnostic_log_growth,
        "w8_preserved": w8_preserved,
        "weeks": {
            week: {
                key: metrics[week].get(key)
                for key in (
                    "daily_geometric_growth", "net_return", "final_nav",
                    "closed_trades", "wins", "losses", "win_rate", "payoff_ratio",
                    "closed_trade_max_drawdown", "submitted_plans",
                    "internal_reclaim_submitted_plans", "engine_errors",
                    "liquidation_detected", "global_slot_overlap_count",
                )
            }
            for week in weeks
        },
    })

eligible = [record for record in records if record["eligible"]]
selected = None
if eligible:
    eligible.sort(
        key=lambda record: (
            -record["diagnostic_internal_plans"],
            -record["diagnostic_closed_trades"],
            -record["diagnostic_log_growth"],
            variants.index(record["variant"]),
        )
    )
    selected = eligible[0]["variant"]

summary = {
    "schema": "candidate-11-internal-reclaim-matrix-v1",
    "purpose": "diagnose an independent internal-liquidity reclaim family without weakening the frozen FAR/AAC gate",
    "selection_rule": "safety and zero losses across W3/W9/W8; preserve revised W8; then maximize diagnostic internal plans, closed trades, and log NAV growth",
    "selected_variant": selected,
    "records": records,
    "success_claim": False,
}
summary_path.parent.mkdir(parents=True, exist_ok=True)
summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
if selected is not None:
    candidate_root = summary_path.parents[2]
    for week in weeks:
        source = work / selected / week
        destination = candidate_root / "results" / f"IRX_SELECTED_{week}"
        shutil.copytree(source, destination)
PY

cat "$SUMMARY"
