#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
CHECK="$CAND/results/CROSS_MARKET_CHECK/status.json"
SUMMARY="$CAND/results/CROSS_MARKET/summary.json"
mkdir -p "$(dirname "$SUMMARY")"
PASSED=false
if [ -s "$CHECK" ]; then
  PASSED="$(python - "$CHECK" <<'PY'
import json, sys
print('true' if json.load(open(sys.argv[1], encoding='utf-8')).get('passed') is True else 'false')
PY
)"
fi
if [ "$PASSED" != "true" ]; then
  python - "$SUMMARY" <<'PY'
import json
from pathlib import Path
import sys
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    'schema': 'candidate-11-cross-market-summary-v1',
    'status': 'NOT_RUN_IMPLEMENTATION_CHECK_NOT_PASSED',
    'three_week_gate_passed': False,
    'success_claim': False,
}, indent=2, sort_keys=True) + '\n', encoding='utf-8')
PY
  exit 0
fi
# The independent C1 screen is now authorized solely by this implementation
# check. Its own audit controls whether C2 and C3 may be opened.
python "$CAND/apply_cross_market_runtime_fixes.py"
python "$CAND/materialize_cross_market_gated_v2.py"
chmod +x "$CAND/run_cross_market_generated_v2.sh"
bash "$CAND/run_cross_market_generated_v2.sh"
