#!/usr/bin/env python3
"""Activate v21 counterflow-absorption expansion controls without parameter search."""
from __future__ import annotations

import re
from pathlib import Path

path = Path(__file__).resolve().parent / "run.py"
text = path.read_text(encoding="utf-8")
text = re.sub(
    r'\A#!/usr/bin/env python3\n"""Run candidate-09(?: v(?:18|20))? with NautilusTrader and emit reproducible evidence\.\n\n.*?\n"""',
    '''#!/usr/bin/env python3
"""Run candidate-09 v21 with NautilusTrader and emit reproducible evidence.

No search or parameter optimizer is present. The accepted-failure and value-rejection
mean-reversion families are retired. The counterflow-absorption expansion baseline
forms a completed 15-minute source auction, requires outside acceptance, and observes
opposing aggressor flow which cannot reenter the source. Entry occurs only after aligned
re-expansion toward an adjacent-auction
objective. Exact single-layer controls run on the same frozen BTC weeks; the three-year
interval runs only after the pooled baseline gate passes.
"""''',
    text,
    count=1,
    flags=re.DOTALL,
)
text, count = re.subn(
    r'ABLATIONS = \(\n(?:    "[^"]+",\n)+\)',
    '''ABLATIONS = (
    "baseline",
    "no-absorption",
    "no-reexpansion",
    "no-balanced-source",
)''',
    text,
    count=1,
)
if count != 1:
    raise SystemExit(f"expected one ablation tuple, changed {count}")
required = (
    '"no-absorption"',
    '"no-reexpansion"',
    '"no-balanced-source"',
    "def evaluate_gate(",
    "def evaluate_long(",
    "evidence_details_for_output",
    "sizing_infeasible_signal_count",
)
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f"incomplete v21 runner activation: {missing}")
path.write_text(text, encoding="utf-8")
