#!/usr/bin/env python3
"""Classify IRX, three microstructure families, and cross-market evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from diagnose_research_runs_v2 import diagnose

ROOT = Path(__file__).resolve().parent
TARGETS = {
    "IRX_MATRIX": ROOT / "results" / "IRX_MATRIX",
    "IRX_HOLDOUT": ROOT / "results" / "IRX_HOLDOUT",
    "IRX_LONG": ROOT / "results" / "IRX_LONG",
    "MICROSTRUCTURE": ROOT / "results" / "MICROSTRUCTURE",
    "MICROSTRUCTURE_V2": ROOT / "results" / "MICROSTRUCTURE_V2",
    "MICROSTRUCTURE_V3": ROOT / "results" / "MICROSTRUCTURE_V3",
    "CROSS_MARKET": ROOT / "results" / "CROSS_MARKET",
}


def load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def main() -> None:
    results = {name: diagnose(name, path) for name, path in TARGETS.items()}
    check_path = ROOT / "results" / "CROSS_MARKET_CHECK" / "status.json"
    check = load(check_path)
    results["CROSS_MARKET_CHECK"] = {
        "target": "CROSS_MARKET_CHECK",
        "summary_present": check is not None,
        "exit_code": None if check is None else check.get("exit_code"),
        "execution_classification": (
            "WAITING_FOR_EVIDENCE"
            if check is None
            else "EXECUTION_COMPLETED"
            if check.get("passed") is True
            else "IMPLEMENTATION_FAILURE"
        ),
        "passed": None if check is None else check.get("passed"),
        "error_tail": [] if check is None else check.get("error_tail", []),
        "success_claim": False,
    }
    output = ROOT / "results" / "RESEARCH_DIAGNOSIS.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema": "candidate-11-research-diagnosis-v3",
        "results": results,
        "success_claim": False,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
