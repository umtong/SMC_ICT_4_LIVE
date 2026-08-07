#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
PROTOCOL="$CAND/microstructure_v3_protocol.json"

if [ ! -s "$PROTOCOL" ]; then
  echo "microstructure-v3 protocol must be committed before market-data access" >&2
  exit 66
fi

# The frozen protocol, not a stale cross-family decision file, authorizes this
# independent screen.  The generated evaluator runs smc4 doctor, focused
# causality/risk tests, and NautilusTrader evidence production.
python "$CAND/materialize_microstructure_v3_gated.py"
chmod +x "$CAND/run_microstructure_v3_generated.sh"
bash "$CAND/run_microstructure_v3_generated.sh"
