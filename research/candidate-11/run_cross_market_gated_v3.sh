#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
python "$CAND/apply_cross_market_runtime_fixes.py"
chmod +x "$CAND/run_cross_market_gated_v2.sh"
bash "$CAND/run_cross_market_gated_v2.sh"
