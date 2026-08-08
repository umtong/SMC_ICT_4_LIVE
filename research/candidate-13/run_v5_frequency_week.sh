#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INTERVAL="${1:?usage: run_v5_frequency_week.sh INTERVAL [OUTPUT_DIR]}"
OUTPUT_DIR="${2:-$ROOT/research/candidate-13/v5/frequency/development/results/$INTERVAL}"

cd "$ROOT"
smc4 doctor
export PYTHONPATH="$ROOT/research/candidate-13:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
python research/candidate-13/candidate13_runner_v5.py \
  "$INTERVAL" \
  "$OUTPUT_DIR" \
  --protocol research/candidate-13/protocol-v5-frequency-development.json
