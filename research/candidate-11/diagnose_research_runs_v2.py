#!/usr/bin/env python3
"""Classify every active Candidate 11 research family from committed evidence."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parent
TARGETS = {
    "IRX_MATRIX": ROOT / "results" / "IRX_MATRIX",
    "IRX_HOLDOUT": ROOT / "results" / "IRX_HOLDOUT",
    "IRX_LONG": ROOT / "results" / "IRX_LONG",
    "MICROSTRUCTURE": ROOT / "results" / "MICROSTRUCTURE",
    "MICROSTRUCTURE_V2": ROOT / "results" / "MICROSTRUCTURE_V2",
}

PATTERNS: tuple[tuple[str, str], ...] = (
    (r"ModuleNotFoundError|ImportError|cannot import name", "SOURCE_INTERFACE_IMPORT_FAILURE"),
    (r"SyntaxError|IndentationError|TabError", "SOURCE_SYNTAX_FAILURE"),
    (r"FAILED \(|AssertionError|FAIL:|ERROR:", "UNIT_OR_REGRESSION_TEST_FAILURE"),
    (r"TradePlan field|unexpected keyword argument|required positional argument|TypeError:", "SOURCE_INTERFACE_TYPE_FAILURE"),
    (r"BarType|CryptoPerpetual|RiskSizer|order_factory|BacktestEngine|Nautilus", "NAUTILUS_API_OR_MODEL_FAILURE"),
    (r"failed to download|HTTP Error|timed out|Connection reset|corrupt ZIP", "MARKET_DATA_DOWNLOAD_FAILURE"),
    (r"unexpected archive|timestamp magnitude|empty aggregate|ParserError", "MARKET_DATA_SCHEMA_FAILURE"),
    (r"ORDER_DENIED|ORDER_REJECTED|ORDER_LIST_SUBMISSION_EXCEPTION", "EXECUTION_MODEL_FAILURE"),
    (r"LOGIC_FAILURE_NO_EXECUTABLE_PLANS|NO_CLOSED_TRADES|SCREEN_FAILED", "LOGIC_OR_FREQUENCY_FAILURE"),
)


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def classify_log(text: str, exit_code: int | None) -> str:
    if exit_code == 0:
        return "EXECUTION_COMPLETED"
    for pattern, label in PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    return "UNCLASSIFIED_EXECUTION_FAILURE"


def result_metrics(summary: dict[str, Any] | None) -> dict[str, Any]:
    if summary is None:
        return {}
    for key in ("combined", "independent_recalculation", "metrics"):
        value = summary.get(key)
        if isinstance(value, dict):
            return value
    weeks = summary.get("weeks")
    if isinstance(weeks, dict) and weeks:
        first = weeks[sorted(weeks)[0]]
        return first if isinstance(first, dict) else {}
    return {}


def diagnose(name: str, root: Path) -> dict[str, Any]:
    status = load_json(root / "execution_status.json") or {}
    summary = load_json(root / "summary.json")
    text = (root / "execution.log").read_text(encoding="utf-8", errors="replace") if (root / "execution.log").is_file() else ""
    exit_code = status.get("exit_code") if isinstance(status.get("exit_code"), int) else None
    metrics = result_metrics(summary)
    gate = None
    if summary is not None:
        gate = summary.get(
            "three_week_gate_passed",
            summary.get("holdout_gate_passed", summary.get("long_gate_passed")),
        )
    errors = [
        line.rstrip() for line in text.splitlines()
        if re.search(r"error|fail|exception|traceback|denied|rejected|missing|invalid|expected|assert", line, flags=re.IGNORECASE)
    ][-40:]
    return {
        "target": name,
        "evidence_directory_present": root.is_dir(),
        "execution_status_present": (root / "execution_status.json").is_file(),
        "execution_log_present": (root / "execution.log").is_file(),
        "summary_present": summary is not None,
        "exit_code": exit_code,
        "execution_classification": classify_log(text, exit_code),
        "summary_status": None if summary is None else summary.get("status"),
        "selected_variant": None if summary is None else summary.get("selected_variant"),
        "gate_passed": gate,
        "closed_trades": metrics.get("closed_trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "daily_geometric_growth": metrics.get("daily_geometric_growth"),
        "payoff_ratio": metrics.get("payoff_ratio"),
        "closed_trade_max_drawdown": metrics.get("closed_trade_max_drawdown"),
        "error_tail": errors,
        "success_claim": False,
    }


def main() -> None:
    results = {name: diagnose(name, path) for name, path in TARGETS.items()}
    output = ROOT / "results" / "RESEARCH_DIAGNOSIS.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema": "candidate-11-research-diagnosis-v2",
        "results": results,
        "success_claim": False,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
