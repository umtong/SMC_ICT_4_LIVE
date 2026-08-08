#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
PROTOCOL="$CAND/microstructure_protocol.json"
DATA_DIR="/tmp/candidate-11-microstructure-data"
SUMMARY="$CAND/results/MICROSTRUCTURE/summary.json"

if [ ! -s "$PROTOCOL" ]; then
  echo "microstructure protocol must be committed before market data access" >&2
  exit 66
fi

rm -rf "$CAND/results/MICROSTRUCTURE" "$CAND/results/MICRO_M1" "$CAND/results/MICRO_M2" "$CAND/results/MICRO_M3" "$DATA_DIR"
mkdir -p "$(dirname "$SUMMARY")" "$DATA_DIR"

smc4 doctor
python "$CAND/materialize_scdam.py"
python "$CAND/apply_boundary_consumption.py"
python "$CAND/revert_rejected_episode_horizon.py"
python "$CAND/materialize_portfolio.py"
python "$CAND/apply_partial_fill_fail_closed.py"
python "$CAND/apply_market_leadership.py"
python "$CAND/apply_price_discovery_revision.py"
python "$CAND/apply_microstructure_fixes.py"
python -m py_compile \
  "$CAND/microstructure.py" \
  "$CAND/run_microstructure_nautilus.py" \
  "$CAND/audit_microstructure.py"
python -m unittest discover -s "$CAND" -p 'test_*.py' -v

run_week() {
  local week="$1"
  local out="$CAND/results/MICRO_$week"
  mkdir -p "$out"
  python "$CAND/run_microstructure_nautilus.py" \
    --protocol "$PROTOCOL" \
    --week "$week" \
    --output "$out" \
    --data-dir "$DATA_DIR" \
    --project-config "$CAND/config.json"
  for file in run.json data_manifest.json metrics.json orders.csv positions.csv account.csv scenario_events.jsonl submitted_plans.json order_lifecycle.json; do
    test -s "$out/$file"
  done
  python "$CAND/audit_microstructure.py" "$out" --week "$week" --output "$out/audit.json"
}

run_week M1
M1_ADVANCE="$(python - "$CAND/results/MICRO_M1/audit.json" <<'PY'
import json, sys
print("true" if json.load(open(sys.argv[1], encoding="utf-8")).get("screening_gate_passed") is True else "false")
PY
)"

if [ "$M1_ADVANCE" = "true" ]; then
  run_week M2
  run_week M3
fi

python - "$CAND" "$PROTOCOL" "$SUMMARY" "$M1_ADVANCE" <<'PY'
from __future__ import annotations
import csv
from decimal import Decimal
from hashlib import sha256
import json
from math import prod
from pathlib import Path
import sys

root = Path(sys.argv[1])
protocol_path = Path(sys.argv[2])
summary_path = Path(sys.argv[3])
m1_advance = sys.argv[4].lower() == "true"
weeks = ["M1"] + (["M2", "M3"] if m1_advance else [])
metrics = {}
audits = {}
pnls: dict[str, list[Decimal]] = {}

def dec(value: object) -> Decimal:
    return Decimal(str(value).split()[0].replace(",", ""))

for week in weeks:
    result = root / "results" / f"MICRO_{week}"
    metrics[week] = json.loads((result / "metrics.json").read_text(encoding="utf-8"))
    audits[week] = json.loads((result / "audit.json").read_text(encoding="utf-8"))
    values: list[Decimal] = []
    with (result / "positions.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            value = row.get("realized_pnl") or row.get("pnl")
            if value not in (None, "", "nan", "None"):
                values.append(dec(value))
    pnls[week] = values

complete_matrix = weeks == ["M1", "M2", "M3"]
all_pnls = [value for week in weeks for value in pnls[week]]
wins = [value for value in all_pnls if value > 0]
losses = [value for value in all_pnls if value < 0]
win_rate = len(wins) / len(all_pnls) if all_pnls else 0.0
payoff = None
if wins and losses:
    payoff = float((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
elif wins:
    payoff = float("inf")

multipliers = [
    Decimal(str(metrics[week]["final_nav"])) / Decimal(str(metrics[week]["starting_nav"]))
    for week in weeks
]
multiplier = prod(multipliers, start=Decimal(1))
days = Decimal(7 * len(weeks))
daily_growth = float(multiplier ** (Decimal(1) / days) - Decimal(1))
combined_equity = Decimal("100000")
peak = combined_equity
max_drawdown = Decimal(0)
for week in weeks:
    local_equity = Decimal(str(metrics[week]["starting_nav"]))
    for pnl in pnls[week]:
        combined_equity *= (local_equity + pnl) / local_equity
        local_equity += pnl
        peak = max(peak, combined_equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - combined_equity) / peak)

safety = all(
    audits[week].get("implementation_passed") is True
    and not metrics[week].get("liquidation_detected")
    and not metrics[week].get("engine_errors")
    for week in weeks
)
minimum_week_trades = min(int(metrics[week].get("closed_trades") or 0) for week in weeks)
full_gate = (
    complete_matrix
    and safety
    and len(all_pnls) >= 30
    and minimum_week_trades >= 8
    and len(losses) <= 3
    and win_rate >= 0.90
    and (payoff is None or payoff >= 1.20)
    and daily_growth >= 0.01
    and float(max_drawdown) <= 0.20
)
summary = {
    "schema": "candidate-11-microstructure-summary-v1",
    "status": "M1_SCREEN_FAILED" if not m1_advance else "THREE_WEEK_MATRIX_COMPLETED",
    "protocol_sha256": sha256(protocol_path.read_bytes()).hexdigest(),
    "weeks": {
        week: {
            key: metrics[week].get(key)
            for key in (
                "evaluation_start", "evaluation_end_exclusive", "one_second_bars",
                "daily_geometric_growth", "net_return", "final_nav",
                "closed_trades", "wins", "losses", "win_rate", "payoff_ratio",
                "closed_trade_max_drawdown", "submitted_plans", "scenario_counts",
                "detector_event_counts", "skip_reasons", "engine_errors",
                "liquidation_detected", "partial_entry_fail_closed_count",
            )
        }
        for week in weeks
    },
    "combined": {
        "calendar_days": int(days),
        "nav_multiplier": float(multiplier),
        "daily_geometric_growth": daily_growth,
        "closed_trades": len(all_pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "payoff_ratio": None if payoff == float("inf") else payoff,
        "closed_trade_max_drawdown": float(max_drawdown),
        "minimum_week_closed_trades": minimum_week_trades,
    },
    "m1_screening_gate_passed": m1_advance,
    "safety_passed": safety,
    "three_week_gate_passed": full_gate,
    "success_claim": False,
}
summary_path.parent.mkdir(parents=True, exist_ok=True)
summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
PY
