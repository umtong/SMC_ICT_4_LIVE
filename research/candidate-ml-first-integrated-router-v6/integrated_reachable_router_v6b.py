#!/usr/bin/env python3
"""Execute the corrected integrated reachable router v6 implementation."""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve()
SOURCE_PATH = HERE.with_name("integrated_reachable_router.py")
source = SOURCE_PATH.read_text(encoding="utf-8")
source = source.replace(
    'RESEARCH / "candidate-ml-first-causal-family-router-v5" / "causal_family_router_fixed.py"',
    'RESEARCH / "candidate-ml-first-causal-family-router-v5" / "causal_family_module.py"',
)
needle = '    hazard_components, hazard_catalog = CORE.search_components(scored[scored["_role"] == "dev"], dev_periods)'
replacement = (
    '    hazard_input = scored.copy()\n'
    '    hazard_input["_expected_log"] = hazard_input["_hazard_score"]\n'
    '    hazard_components, hazard_catalog = CORE.search_components('\
    'hazard_input[hazard_input["_role"] == "dev"], dev_periods)'
)
if needle not in source:
    raise RuntimeError("integrated v6 hazard-search line changed; v6b wrapper cannot apply")
source = source.replace(needle, replacement, 1)
namespace = {"__name__": "__main__", "__file__": str(HERE)}
exec(compile(source, str(SOURCE_PATH), "exec"), namespace, namespace)
