#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
python "$CAND/apply_microstructure_fixes.py"
python "$CAND/apply_microstructure_lifecycle_fix.py"
python "$CAND/apply_microstructure_loader_fix.py"
python "$CAND/apply_microstructure_chunked_loader.py"
python "$CAND/apply_microstructure_causal_baselines.py"
chmod +x "$CAND/run_microstructure_v2_gated.sh"
bash "$CAND/run_microstructure_v2_gated.sh"
