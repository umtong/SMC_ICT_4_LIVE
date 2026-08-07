#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
python "$CAND/apply_internal_reclaim_block_fix.py"
python "$CAND/apply_internal_reclaim_compatibility.py"
chmod +x "$CAND/run_internal_reclaim_matrix.sh"
bash "$CAND/run_internal_reclaim_matrix.sh"
