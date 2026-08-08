#!/usr/bin/env python3
"""Materialize the frozen continuous evaluator for a diagnostic account path.

The underlying evaluator, NAV arithmetic, trade parsing, weekly slicing,
Wilson interval, payoff, concentration and drawdown calculations are preserved.
Only evidence-role semantics are extended so a previously inspected interval can
be labelled diagnostic and can never emit a success claim.
"""
from __future__ import annotations

from pathlib import Path


BASE = Path(__file__).resolve().with_name("continuous_aggregate.py")


def replace_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"materialization contract changed: expected one match, got {count}: {old!r}")
    return source.replace(old, new, 1)


def materialized_source() -> str:
    source = BASE.read_text(encoding="utf-8")
    source = replace_once(
        source,
        '''    protocol = load_object(protocol_path)\n    if protocol.get("validation_mode") != "frozen_holdout":\n        raise ValueError("continuous evidence requires validation_mode=frozen_holdout")\n''',
        '''    protocol = load_object(protocol_path)\n    mode = str(protocol.get("validation_mode"))\n    if mode not in {"frozen_holdout", "diagnostic"}:\n        raise ValueError("continuous evidence requires frozen_holdout or diagnostic mode")\n''',
    )
    source = replace_once(
        source,
        'metrics.get("validation_mode") == "frozen_holdout"',
        'metrics.get("validation_mode") == mode',
    )
    source = replace_once(
        source,
        'run.get("validation_mode") == "frozen_holdout"',
        'run.get("validation_mode") == mode',
    )
    source = replace_once(
        source,
        '''    classification = (\n        "CANDIDATE14_CONTIGUOUS_HOLDOUT_PASSED"\n        if gate_passed\n        else "CANDIDATE14_CONTIGUOUS_HOLDOUT_FAILED"\n    )\n''',
        '''    if mode == "diagnostic":\n        classification = (\n            "CANDIDATE14_V6_CONTIGUOUS_DIAGNOSTIC_GATE_PASSED"\n            if gate_passed\n            else "CANDIDATE14_V6_CONTIGUOUS_DIAGNOSTIC_GATE_FAILED"\n        )\n    else:\n        classification = (\n            "CANDIDATE14_CONTIGUOUS_HOLDOUT_PASSED"\n            if gate_passed\n            else "CANDIDATE14_CONTIGUOUS_HOLDOUT_FAILED"\n        )\n''',
    )
    source = replace_once(
        source,
        '        "validation_mode": "frozen_holdout",',
        '        "validation_mode": mode,',
    )
    source = replace_once(
        source,
        '        "success_claim": gate_passed,',
        '        "success_claim": gate_passed and mode == "frozen_holdout",',
    )
    source = replace_once(
        source,
        '        "# Candidate 14 contiguous holdout result",',
        '''        (\n            "# Candidate 14 contiguous holdout result"\n            if result["validation_mode"] == "frozen_holdout"\n            else "# Candidate 14 v6 contiguous diagnostic result"\n        ),''',
    )
    return source


def main() -> int:
    namespace: dict[str, object] = {
        "__name__": "candidate14_v6_continuous_aggregate_materialized",
        "__file__": str(BASE),
    }
    exec(compile(materialized_source(), str(BASE), "exec"), namespace, namespace)
    entry = namespace.get("main")
    if not callable(entry):
        raise RuntimeError("continuous aggregate main entry was not materialized")
    return int(entry())


if __name__ == "__main__":
    raise SystemExit(main())
