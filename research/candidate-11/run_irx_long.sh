#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
PROTOCOL="$CAND/irx_long_protocol.json"
BINDING="$CAND/irx_holdout_candidate.json"
OUT="$CAND/results/IRX_LONG_L1"
SUMMARY="$CAND/results/IRX_LONG/summary.json"

if [ ! -s "$PROTOCOL" ] || [ ! -s "$BINDING" ]; then
  echo "bound IRX long protocol is unavailable" >&2
  exit 66
fi
VARIANT="$(python - "$PROTOCOL" "$BINDING" <<'PY'
import json, sys
protocol = json.load(open(sys.argv[1], encoding='utf-8'))
binding = json.load(open(sys.argv[2], encoding='utf-8'))
if protocol['selected_variant'] != binding['selected_variant']:
    raise SystemExit('IRX long variant differs from frozen binding')
print(protocol['selected_variant'])
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

rm -rf "$OUT" "$CAND/results/IRX_LONG"
mkdir -p "$OUT" "$(dirname "$SUMMARY")"
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
python "$CAND/apply_internal_reclaim_compatibility.py"
python "$CAND/apply_internal_reclaim.py"
python "$CAND/apply_irx_long_protocol.py"
set_variant
python -m py_compile "$CAND/internal_reclaim.py" "$CAND/session_engine.py" "$CAND/run_leadership_scdam.py"
python -m unittest discover -s "$CAND" -p 'test_*.py' -v

python "$CAND/run_leadership_scdam.py" \
  --config "$CAND/config.json" \
  --week L1 \
  --output "$OUT"
for file in run.json data_manifest.json metrics.json scenario_events.jsonl submitted_plans.json order_lifecycle.json orders.csv positions.csv account.csv; do
  test -s "$OUT/$file"
done
python - "$OUT/metrics.json" "$OUT/submitted_plans.json" "$VARIANT" <<'PY'
import json
import sys
from pathlib import Path
metrics_path = Path(sys.argv[1])
plans_path = Path(sys.argv[2])
metrics = json.loads(metrics_path.read_text(encoding='utf-8'))
payload = json.loads(plans_path.read_text(encoding='utf-8'))
plans = payload.get('plans', payload if isinstance(payload, list) else [])
internal = [p for p in plans if isinstance(p, dict) and isinstance(p.get('details'), dict) and p['details'].get('source') == 'INTERNAL_RECLAIM_EXTERNAL_DRAW']
metrics['irx_variant'] = sys.argv[3]
metrics['internal_reclaim_submitted_plans'] = len(internal)
metrics['continuous_long_evaluation'] = True
metrics['success_claim'] = False
metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + '\n', encoding='utf-8')
PY
python "$CAND/evidence_audit.py" "$OUT" --week LONG --output "$OUT/audit.json"

python - "$OUT" "$PROTOCOL" "$BINDING" "$SUMMARY" <<'PY'
from __future__ import annotations
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from math import prod
from pathlib import Path
import sys

out = Path(sys.argv[1])
protocol_path = Path(sys.argv[2])
binding_path = Path(sys.argv[3])
summary_path = Path(sys.argv[4])
metrics = json.loads((out / 'metrics.json').read_text(encoding='utf-8'))
audit = json.loads((out / 'audit.json').read_text(encoding='utf-8'))
protocol = json.loads(protocol_path.read_text(encoding='utf-8'))
start = date.fromisoformat(protocol['interval']['start'])

def dec(value: object) -> Decimal:
    return Decimal(str(value).split()[0].replace(',', ''))

def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(timezone.utc)

trades: list[dict[str, object]] = []
with (out / 'positions.csv').open('r', encoding='utf-8-sig', newline='') as stream:
    for row in csv.DictReader(stream):
        value = row.get('realized_pnl') or row.get('pnl')
        closed = row.get('ts_closed') or row.get('ts_last')
        if value in (None, '', 'nan', 'None') or closed in (None, '', 'nan', 'NaT'):
            continue
        trades.append({
            'pnl': dec(value),
            'closed': parse_time(closed),
            'symbol': str(row.get('instrument_id') or '').split('-PERP', 1)[0],
        })

pnls = [trade['pnl'] for trade in trades]
wins = [value for value in pnls if value > 0]
losses = [value for value in pnls if value < 0]
win_rate = len(wins) / len(pnls) if pnls else 0.0
payoff = None
if wins and losses:
    payoff = float((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
elif wins:
    payoff = float('inf')

equity = Decimal(str(metrics['starting_nav']))
peak = equity
max_drawdown = Decimal(0)
for pnl in pnls:
    equity += pnl
    peak = max(peak, equity)
    if peak > 0:
        max_drawdown = max(max_drawdown, (peak - equity) / peak)

blocks = []
for block in range(3):
    block_start = start.toordinal() + 30 * block
    block_end = block_start + 30
    block_pnls = [
        trade['pnl'] for trade in trades
        if block_start <= trade['closed'].date().toordinal() < block_end
    ]
    block_multiplier = float((Decimal('100000') + sum(block_pnls, Decimal(0))) / Decimal('100000'))
    blocks.append({
        'index': block + 1,
        'closed_trades': len(block_pnls),
        'net_pnl': str(sum(block_pnls, Decimal(0))),
        'positive': sum(block_pnls, Decimal(0)) > 0,
        'simple_nav_multiplier': block_multiplier,
    })

profits = sorted((value for value in wins), reverse=True)
total_profit = sum(profits, Decimal(0))
top5_share = float(sum(profits[:5], Decimal(0)) / total_profit) if total_profit > 0 else 1.0
symbol_pnl: defaultdict[str, Decimal] = defaultdict(Decimal)
symbol_trades: Counter[str] = Counter()
for trade in trades:
    symbol_pnl[trade['symbol']] += trade['pnl']
    symbol_trades[trade['symbol']] += 1
positive_blocks = sum(block['positive'] for block in blocks)
active_symbols = sum(count > 0 for count in symbol_trades.values())
safety = (
    audit.get('evidence_complete') is True
    and audit.get('metric_recalculation_passed') is True
    and audit.get('risk_budget_passed') is True
    and audit.get('global_slot_passed') is True
    and audit.get('partial_entry_protection_passed') is True
    and audit.get('no_liquidation_passed') is True
    and not metrics.get('engine_errors')
    and not metrics.get('liquidation_detected')
    and int(metrics.get('global_slot_overlap_count') or 0) == 0
)
long_gate = (
    safety
    and len(pnls) >= 90
    and len(losses) <= max(5, int(0.10 * len(pnls)))
    and win_rate >= 0.90
    and (payoff is None or payoff >= 1.20)
    and float(metrics.get('daily_geometric_growth') or 0.0) >= 0.01
    and float(max_drawdown) <= 0.20
    and positive_blocks >= 2
    and min(block['closed_trades'] for block in blocks) >= 20
    and top5_share <= 0.50
    and active_symbols >= 3
)
summary = {
    'schema': 'candidate-11-irx-long-summary-v1',
    'status': 'COMPLETED',
    'protocol_sha256': sha256(protocol_path.read_bytes()).hexdigest(),
    'binding_sha256': sha256(binding_path.read_bytes()).hexdigest(),
    'interval': protocol['interval'],
    'selected_variant': protocol['selected_variant'],
    'metrics': {
        key: metrics.get(key)
        for key in (
            'daily_geometric_growth', 'net_return', 'final_nav', 'closed_trades',
            'wins', 'losses', 'win_rate', 'payoff_ratio',
            'closed_trade_max_drawdown', 'submitted_plans',
            'internal_reclaim_submitted_plans', 'liquidation_detected',
            'global_slot_overlap_count', 'engine_errors',
        )
    },
    'independent_recalculation': {
        'closed_trades': len(pnls),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': win_rate,
        'payoff_ratio': None if payoff == float('inf') else payoff,
        'closed_trade_max_drawdown': float(max_drawdown),
        'top5_gross_profit_share': top5_share,
        'positive_30d_blocks': positive_blocks,
        'blocks': blocks,
        'symbol_trade_counts': dict(symbol_trades),
        'symbol_net_pnl': {key: str(value) for key, value in symbol_pnl.items()},
        'active_symbols': active_symbols,
    },
    'safety_passed': safety,
    'long_gate_passed': long_gate,
    'success_claim': False,
}
summary_path.parent.mkdir(parents=True, exist_ok=True)
summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
print(json.dumps(summary, indent=2, sort_keys=True))
PY
