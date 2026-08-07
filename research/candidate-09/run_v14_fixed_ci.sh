#!/usr/bin/env bash
set -euo pipefail

root=research/candidate-09
archive="$root/archive/v13"
out="$root/evidence/latest"
diag="$root/diagnostics/v14-fixed-gate-long"

# Preserve the first frozen v13 archive. Re-running v14 must never overwrite it with
# already-promoted v14 source or later evidence.
if [[ ! -f "$archive/run.py" || ! -f "$archive/state_engine_v13_direct.py" ]]; then
  mkdir -p "$archive/evidence" "$archive/tests_v13"
  cp "$root/config.json" "$archive/config.json"
  cp "$root/config_v13.json" "$archive/config_v13.json"
  cp "$root/state_engine.py" "$archive/state_engine.py"
  cp "$root/state_engine_v13_direct.py" "$archive/state_engine_v13_direct.py"
  cp "$root/nautilus_strategy.py" "$archive/nautilus_strategy.py"
  cp "$root/run.py" "$archive/run.py"
  cp -a "$root/tests_v13/." "$archive/tests_v13/"
  for name in summary.json REPORT.md outcomes.csv trades.csv fills.csv events.jsonl run.json data_manifest.json; do
    [[ -f "$out/$name" ]] && cp "$out/$name" "$archive/evidence/$name"
  done
fi

cp "$root/state_engine_v14_direct.py" "$root/state_engine.py"
cp "$root/config_v14.json" "$root/config.json"
# The original result commit already contains the v14 run contract. Apply the
# structural promotion only when starting from the archived v13 runner.
if ! grep -q 'def evaluate_long(' "$root/run.py" || ! grep -q '"accepted-extreme-stop"' "$root/run.py"; then
  python "$root/apply_v14_run_patch.py"
fi
python "$root/apply_v14_evidence_fix.py"
python "$root/apply_v14_account_exhaustion_fix.py"
cp "$root/tests_v14/test_state_engine.py" "$root/tests/test_state_engine.py"
cp "$root/tests_v14/test_run_gate.py" "$root/tests/test_run_gate.py"
cp "$root/tests_v14/test_evidence_contract.py" "$root/tests/test_evidence_contract.py"
cp "$root/tests_v14/test_account_exhaustion_contract.py" "$root/tests/test_account_exhaustion_contract.py"

cmp -s "$root/state_engine.py" "$root/state_engine_v14_direct.py"
cmp -s "$root/config.json" "$root/config_v14.json"
grep -q 'evidence_details_for_output' "$root/run.py"
grep -q 'sizing_infeasible_signal_count' "$root/run.py"
grep -q 'sizing_failure_reason' "$root/nautilus_strategy.py"

rm -rf "$out" "$diag"
mkdir -p "$out" "$diag"
sha256sum \
  "$root/state_engine_v10_direct.py" \
  "$root/state_engine_v13_direct.py" \
  "$root/state_engine_v14_direct.py" \
  "$root/nautilus_strategy.py" \
  "$root/config_v14.json" \
  "$root/apply_v14_run_patch.py" \
  "$root/apply_v14_evidence_fix.py" \
  "$root/apply_v14_account_exhaustion_fix.py" \
  "$root/tests_v14/test_state_engine.py" \
  "$root/tests_v14/test_run_gate.py" \
  "$root/tests_v14/test_evidence_contract.py" \
  "$root/tests_v14/test_account_exhaustion_contract.py" > "$diag/source_sha256.txt"

set +e
smc4 doctor > "$diag/doctor.log" 2>&1; doctor=$?
(
  cd "$root"
  python -m compileall -q .
) > "$diag/compile.log" 2>&1; compile=$?
(
  cd "$root"
  python -m unittest discover -s tests -p 'test_*.py' -v
) > "$diag/tests.log" 2>&1; tests=$?
gate=99
if [[ $doctor == 0 && $compile == 0 && $tests == 0 ]]; then
  python "$root/run.py" gate \
    --config "$root/config.json" \
    --cache "$SMC4_DATA_ROOT" \
    --output "$out" \
    --auto-long > "$diag/gate.log" 2>&1
  gate=$?
else
  echo 'gate not run because prerequisite failed' > "$diag/gate.log"
fi
set -e

DOCTOR=$doctor COMPILE=$compile TESTS=$tests GATE=$gate python - <<'PY'
import json
import os
from pathlib import Path

root = Path('research/candidate-09')
diag = root / 'diagnostics/v14-fixed-gate-long'
out = root / 'evidence/latest'
codes = {key.lower(): int(os.environ[key]) for key in ('DOCTOR', 'COMPILE', 'TESTS', 'GATE')}
failure = next((name for name, value in codes.items() if value != 0), None)
summary = {}
if (out / 'summary.json').exists():
    summary = json.loads((out / 'summary.json').read_text())
payload = {
    'candidate_generation': 'v14-consistent-failed-boundary-invalidation',
    'classification': 'IMPLEMENTATION_ERROR' if failure else 'EXECUTED',
    'economic_status': summary.get('status'),
    'codes': codes,
    'first_failure': failure,
    'controlled_fix': [
        'run tests from candidate root rather than relying on ambient import paths',
        'persist long-BTC baseline trades, fills, and events alongside fixed-week baseline evidence',
        'record minimum-quantity signal infeasibility separately from true cost-floor account exhaustion',
        'continue the native run after known economic infeasibility and re-raise every unrecognized sizing error',
    ],
    'economic_logic_changed': False,
    'fixed_weeks_preserved': True,
    'v13_boundary_stop_all_promoted': True,
    'v4_equilibrium_target_preserved': True,
    'standard_market_adapter_preserved': True,
    'risk_fraction_preserved': 0.03,
    'full_cost_contract_preserved': True,
    'strategy_parameter_search_performed': False,
    'screen_semantics': {
        'positive_negative_and_inactive_subperiods_permitted': True,
        'pooled_geometric_growth': True,
        'pooled_trade_count': True,
        'pooled_single_trade_profit_share': True,
        'aggregate_old_opportunity_burden_preserved': '15 trades across 21 days',
    },
    'long_evaluation_predeclared': {
        'start': '2022-01-01',
        'end_exclusive': '2025-01-01',
        'minimum_daily_geometric_return': 0.01,
        'minimum_trades_per_calendar_day': 0.5,
        'minimum_active_months': 30,
        'maximum_drawdown': 0.30,
    },
    'single_variable_ablations': [
        'accepted-extreme-stop',
        'salvage-only',
        'no-flow',
    ],
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'trigger_sha': os.environ.get('GITHUB_SHA'),
}
(diag / 'diagnostic.json').write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
if not (out / 'summary.json').exists():
    (out / 'summary.json').write_text(json.dumps({
        'candidate': 'candidate-09-v14',
        'status': 'IMPLEMENTATION_ERROR',
        'first_failure': failure,
        'codes': codes,
    }, indent=2, sort_keys=True) + '\n')
for path in diag.glob('*.log'):
    lines = path.read_text(errors='replace').splitlines()
    path.write_text('\n'.join(lines[-4500:]) + ('\n' if lines else ''))
PY

exit 0
