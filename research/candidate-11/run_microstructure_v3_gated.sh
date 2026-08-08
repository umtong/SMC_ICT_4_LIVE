#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
DECISION="$CAND/results/RESEARCH_DECISION.json"
SUMMARY="$CAND/results/MICROSTRUCTURE_V3/summary.json"

mkdir -p "$(dirname "$SUMMARY")"
ALLOWED=false
if [ -s "$DECISION" ]; then
  ALLOWED="$(python - "$DECISION" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding='utf-8'))
print('true' if value.get('next_action') == 'OPEN_MULTI_HORIZON_IMPACT_CONTINUATION_FAMILY' else 'false')
PY
)"
fi
if [ "$ALLOWED" != "true" ]; then
  python - "$SUMMARY" <<'PY'
import json
from pathlib import Path
import sys
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    'schema': 'candidate-11-microstructure-v3-summary-v1',
    'status': 'NOT_RUN_PREDECESSOR_EVIDENCE_DID_NOT_AUTHORIZE',
    'three_week_gate_passed': False,
    'success_claim': False,
}, indent=2, sort_keys=True) + '\n', encoding='utf-8')
PY
  exit 0
fi

python "$CAND/apply_microstructure_fixes.py"
python "$CAND/apply_microstructure_lifecycle_fix.py"
python "$CAND/apply_microstructure_loader_fix.py"
python "$CAND/materialize_microstructure_v3_runner.py"
python "$CAND/materialize_microstructure_v3_gated.py"
chmod +x "$CAND/run_microstructure_v3_generated.sh"
bash "$CAND/run_microstructure_v3_generated.sh"
