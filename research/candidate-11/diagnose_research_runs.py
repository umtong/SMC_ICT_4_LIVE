#!/usr/bin/env python3
"""Classify Candidate 11 execution failures without inventing performance."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parent
TARGETS = {
    "IRX_MATRIX": ROOT / "results" / "IRX_MATRIX",
    "MICROSTRUCTURE": ROOT / "results" / "MICROSTRUCTURE",
    "IRX_HOLDOUT": ROOT / "results" / "IRX_HOLDOUT",
}

PATTERNS: tuple[tuple[str, str], ...] = (
    (r"ModuleNotFoundError|ImportError|cannot import name", "SOURCE_INTERFACE_IMPORT_FAILURE"),
    (r"SyntaxError|IndentationError|TabError", "SOURCE_SYNTAX_FAILURE"),
    (r"FAILED \(|AssertionError|FAIL:|ERROR:", "UNIT_OR_REGRESSION_TEST_FAILURE"),
    (r"TradePlan field|unexpected keyword argument|required positional argument|TypeError:", "SOURCE_INTERFACE_TYPE_FAILURE"),
    (r"BarType|CryptoPerpetual|RiskSizer|order_factory|BacktestEngine|Nautilus", "NAUTILUS_API_OR_MODEL_FAILURE"),
    (r"failed to download|HTTP Error|timed out|Connection reset|corrupt ZIP", "MARKET_DATA_DOWNLOAD_FAILURE"),
    (r"unexpected archive|timestamp magnitude|empty aggregate|CSV|ParserError", "MARKET_DATA_SCHEMA_FAILURE"),
    (r"ORDER_DENIED|ORDER_REJECTED|ORDER_LIST_SUBMISSION_EXCEPTION", "EXECUTION_MODEL_FAILURE"),
    (r"LOGIC_FAILURE_NO_EXECUTABLE_PLANS|NO_CLOSED_TRADES|M1_SCREEN_FAILED", "LOGIC_OR_FREQUENCY_FAILURE"),
)


def classify(text: str, exit_code: int | None) -> str:
    if exit_code == 0:
        return "EXECUTION_COMPLETED"
    for pattern, label in PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    return "UNCLASSIFIED_EXECUTION_FAILURE"


def tail_error_lines(text: str, limit: int = 80) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines()]
    selected = [
        line for line in lines
        if re.search(
            r"error|fail|exception|traceback|denied|rejected|missing|invalid|expected|assert",
            line,
            flags=re.IGNORECASE,
        )
    ]
    return (selected or lines)[-limit:]


def diagnose(name: str, path: Path) -> dict[str, Any]:
    status_path = path / "execution_status.json"
    log_path = path / "execution.log"
    status: dict[str, Any] = {}
    if status_path.is_file():
        try:
            value = json.loads(status_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                status = value
        except Exception as exc:
            status = {"status_parse_error": f"{type(exc).__name__}: {exc}"}
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    exit_code = status.get("exit_code")
    if not isinstance(exit_code, int):
        exit_code = None
    summary_path = path / "summary.json"
    summary: dict[str, Any] = {}
    if summary_path.is_file():
        try:
            value = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                summary = value
        except Exception as exc:
            summary = {"summary_parse_error": f"{type(exc).__name__}: {exc}"}
    return {
        "target": name,
        "path": str(path),
        "execution_status_present": status_path.is_file(),
        "execution_log_present": log_path.is_file(),
        "summary_present": summary_path.is_file(),
        "exit_code": exit_code,
        "classification": classify(text, exit_code),
        "status": status,
        "summary_status": summary.get("status"),
        "selected_variant": summary.get("selected_variant"),
        "matrix_or_holdout_gate": summary.get("three_week_gate_passed", summary.get("holdout_gate_passed")),
        "error_tail": tail_error_lines(text),
        "success_claim": False,
    }


def main() -> None:
    results = {name: diagnose(name, path) for name, path in TARGETS.items()}
    output = ROOT / "results" / "RESEARCH_DIAGNOSIS.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema": "candidate-11-research-diagnosis-v1",
        "results": results,
        "success_claim": False,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
