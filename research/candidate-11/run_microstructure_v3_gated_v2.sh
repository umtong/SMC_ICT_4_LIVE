#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
DECISION="$CAND/results/RESEARCH_DECISION.json"
SUMMARY="$CAND/results/MICROSTRUCTURE_V3/summary.json"
mkdir -p "$(dirname "$SUMMARY")"

ACTION=""
if [ -s "$DECISION" ]; then
  ACTION="$(python - "$DECISION" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8')).get('next_action') or '')
PY
)"
fi
case "$ACTION" in
  RUN_FROZEN_BALANCE_ACCEPTANCE_WEEKS|OPEN_MULTI_HORIZON_IMPACT_CONTINUATION_FAMILY) ;;
  *)
    python - "$SUMMARY" "$ACTION" <<'PY'
import json
from pathlib import Path
import sys
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    'schema': 'candidate-11-microstructure-v3-summary-v1',
    'status': 'NOT_RUN_PREDECESSOR_EVIDENCE_DID_NOT_AUTHORIZE',
    'observed_next_action': sys.argv[2],
    'three_week_gate_passed': False,
    'success_claim': False,
}, indent=2, sort_keys=True) + '\n', encoding='utf-8')
PY
    exit 0
    ;;
esac

python "$CAND/apply_microstructure_fixes.py"
python "$CAND/apply_microstructure_lifecycle_fix.py"
python "$CAND/apply_microstructure_loader_fix.py"
python "$CAND/apply_microstructure_chunked_loader.py"
python "$CAND/apply_microstructure_causal_baselines.py"
python "$CAND/materialize_microstructure_v3_runner.py"
python "$CAND/materialize_microstructure_v3_gated.py"
chmod +x "$CAND/run_microstructure_v3_generated.sh"
bash "$CAND/run_microstructure_v3_generated.sh"
