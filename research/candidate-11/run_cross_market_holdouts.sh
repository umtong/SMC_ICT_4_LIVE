#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
PROTOCOL="$CAND/cross_market_holdout_protocol.json"
SUMMARY="$CAND/results/CROSS_MARKET_HOLDOUT/summary.json"

if [ ! -s "$PROTOCOL" ]; then
  echo "cross-market holdout protocol is unavailable" >&2
  exit 66
fi
rm -rf "$CAND/results/CROSS_MARKET_HOLDOUT" \
       "$CAND/results/CROSS_HOLDOUT_C4" \
       "$CAND/results/CROSS_HOLDOUT_C5" \
       "$CAND/results/CROSS_HOLDOUT_C6"
mkdir -p "$(dirname "$SUMMARY")"
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
python "$CAND/materialize_cross_market_audit.py"
python "$CAND/materialize_cross_market_holdout_runner.py"
python -m py_compile \
  "$CAND/cross_market.py" \
  "$CAND/run_cross_market_holdout_nautilus.py" \
  "$CAND/audit_cross_market_holdout.py"
python -m unittest "$CAND/test_cross_market.py" -v

for week in C4 C5 C6; do
  out="$CAND/results/CROSS_HOLDOUT_$week"
  mkdir -p "$out"
  python "$CAND/run_cross_market_holdout_nautilus.py" \
    --protocol "$PROTOCOL" \
    --project-config "$CAND/config.json" \
    --week "$week" \
    --output "$out"
  for file in run.json data_manifest.json metrics.json orders.csv positions.csv account.csv scenario_events.jsonl submitted_plans.json order_lifecycle.json; do
    test -s "$out/$file"
  done
  python "$CAND/audit_cross_market_holdout.py" "$out" --week "$week" --output "$out/audit.json"
done

python - "$CAND" "$PROTOCOL" "$SUMMARY" <<'PY'
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
weeks = ['C4', 'C5', 'C6']
metrics = {}
audits = {}
pnls: dict[str, list[Decimal]] = {}

def dec(value: object) -> Decimal:
    return Decimal(str(value).split()[0].replace(',', ''))

for week in weeks:
    result = root / 'results' / f'CROSS_HOLDOUT_{week}'
    metrics[week] = json.loads((result / 'metrics.json').read_text(encoding='utf-8'))
    audits[week] = json.loads((result / 'audit.json').read_text(encoding='utf-8'))
    values: list[Decimal] = []
    with (result / 'positions.csv').open('r', encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            value = row.get('realized_pnl') or row.get('pnl')
            if value not in (None, '', 'nan', 'None'):
                values.append(dec(value))
    pnls[week] = values

all_pnls = [value for week in weeks for value in pnls[week]]
wins = [value for value in all_pnls if value > 0]
losses = [value for value in all_pnls if value < 0]
win_rate = len(wins) / len(all_pnls) if all_pnls else 0.0
payoff = None
if wins and losses:
    payoff = float((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
elif wins:
    payoff = float('inf')
multiplier = prod((
    Decimal(str(metrics[week]['final_nav'])) / Decimal(str(metrics[week]['starting_nav']))
    for week in weeks
), start=Decimal(1))
days = Decimal(21)
daily_growth = float(multiplier ** (Decimal(1) / days) - Decimal(1))
equity = Decimal('100000')
peak = equity
maximum_drawdown = Decimal(0)
for week in weeks:
    local = Decimal(str(metrics[week]['starting_nav']))
    for pnl in pnls[week]:
        equity *= (local + pnl) / local
        local += pnl
        peak = max(peak, equity)
        if peak > 0:
            maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak)
safety = all(
    audits[week].get('implementation_passed') is True
    and not metrics[week].get('engine_errors')
    and not metrics[week].get('liquidation_detected')
    and int(metrics[week].get('global_slot_overlap_count') or 0) == 0
    for week in weeks
)
minimum_week = min(int(metrics[week].get('closed_trades') or 0) for week in weeks)
gate = (
    safety
    and len(all_pnls) >= 24 and minimum_week >= 6
    and len(losses) <= 2 and win_rate >= 0.90
    and (payoff is None or payoff >= 1.20)
    and daily_growth >= 0.01 and float(maximum_drawdown) <= 0.20
)
summary = {
    'schema': 'candidate-11-cross-market-holdout-summary-v1',
    'status': 'COMPLETED',
    'protocol_sha256': sha256(protocol_path.read_bytes()).hexdigest(),
    'weeks': {
        week: {
            key: metrics[week].get(key)
            for key in (
                'evaluation_start', 'evaluation_end_exclusive', 'bars',
                'daily_geometric_growth', 'net_return', 'final_nav',
                'closed_trades', 'wins', 'losses', 'win_rate', 'payoff_ratio',
                'closed_trade_max_drawdown', 'submitted_plans', 'symbol_counts',
                'leader_counts', 'direction_counts', 'detector_event_counts',
                'skip_reasons', 'engine_errors', 'liquidation_detected',
                'global_slot_overlap_count', 'partial_entry_fail_closed_count',
            )
        }
        for week in weeks
    },
    'combined': {
        'calendar_days': 21,
        'nav_multiplier': float(multiplier),
        'daily_geometric_growth': daily_growth,
        'closed_trades': len(all_pnls),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': win_rate,
        'payoff_ratio': None if payoff == float('inf') else payoff,
        'closed_trade_max_drawdown': float(maximum_drawdown),
        'minimum_week_closed_trades': minimum_week,
    },
    'safety_passed': safety,
    'holdout_gate_passed': gate,
    'success_claim': False,
}
summary_path.parent.mkdir(parents=True, exist_ok=True)
summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
print(json.dumps(summary, indent=2, sort_keys=True))
PY
