"""V3 exact-cadence staged validation entrypoint for the flow-response candidate.

The established deterministic first/screen/one-diagnostic orchestration is reused, while every
revision-sensitive global and evidence validator is rebound to the exact V3 detector and complete-
horizon V2 path diagnostic. Decision labels and persisted stage evidence are normalized to V3 so no
V2 result can be mistaken for current evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import run_flow_response_staged_validation as base
from aggtrade_flow_response_auction_signals_v3 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION


PROTOCOL_REVISION = "FLOW_RESPONSE_STAGED_VALIDATION_V2_EXACT_CADENCE"
_ORIGINAL_VALIDATE_BASE_SUMMARY = base.validate_base_summary


base.IMPLEMENTATION_REVISION = IMPLEMENTATION_REVISION
base.INITIATIVE_FAMILY = INITIATIVE_FAMILY
base.ABSORPTION_FAMILY = ABSORPTION_FAMILY
base.PROTOCOL_REVISION = PROTOCOL_REVISION


def _summary_ref(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    summary = json.loads(path.read_text(encoding="utf-8"))
    return {
        "path": str(path),
        "sha256": base._sha256(path),
        "suite": summary.get("suite"),
        "implementation_revision": summary.get("implementation_revision"),
        "ten_second_cadence_contract": summary.get("ten_second_cadence_contract"),
        "trade_path_diagnostic_revision": summary.get(
            "trade_path_diagnostic_revision"
        ),
        "flow_response_family_mode": summary.get("flow_response_family_mode"),
        "suite_gate_passed": bool(summary.get("suite_gate_passed", False)),
        "closed_trades": int(summary.get("closed_trades", 0)),
        "wins": int(summary.get("wins", 0)),
        "combined_daily_geometric_growth": float(
            summary.get("combined_daily_geometric_growth", 0.0)
        ),
        "scenario_family_results": summary.get("scenario_family_results", {}),
        "trade_path_diagnostic_summary": summary.get(
            "trade_path_diagnostic_summary", {}
        ),
    }


def validate_base_summary(
    summary: Mapping[str, Any],
    *,
    expected_suite: str,
) -> tuple[str, ...]:
    """Return implementation/evidence errors only; never infer economic failure."""

    errors = list(
        _ORIGINAL_VALIDATE_BASE_SUMMARY(
            summary,
            expected_suite=expected_suite,
        )
    )
    if str(summary.get("ten_second_cadence_contract")) != "EXACT_CONSECUTIVE_10_SECONDS":
        errors.append("TEN_SECOND_CADENCE_CONTRACT_NOT_EXACT")
    if str(summary.get("trade_path_diagnostic_revision")) != DIAGNOSTIC_REVISION:
        errors.append("TRADE_PATH_DIAGNOSTIC_REVISION_NOT_EXACT")
    path_summary = summary.get("trade_path_diagnostic_summary", {})
    if isinstance(path_summary, Mapping):
        closed_trades = int(summary.get("closed_trades", 0))
        expected_counts = {DIAGNOSTIC_REVISION: closed_trades}
        if dict(path_summary.get("diagnostic_revision_counts", {})) != expected_counts:
            errors.append("TRADE_PATH_DIAGNOSTIC_REVISION_COUNTS_NOT_EXACT")
        if str(path_summary.get("expected_diagnostic_revision")) != DIAGNOSTIC_REVISION:
            errors.append("TRADE_PATH_EXPECTED_REVISION_NOT_EXACT")
    return tuple(dict.fromkeys(errors))


base._summary_ref = _summary_ref
base.validate_base_summary = validate_base_summary


def _normalize_decision_labels(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_decision_labels(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_decision_labels(item) for item in value]
    if isinstance(value, str):
        return value.replace("FLOW_RESPONSE_V2", "FLOW_RESPONSE_V3")
    return value


def execute_staged_validation(**kwargs: Any) -> tuple[int, dict[str, Any]]:
    status, decision = base.execute_staged_validation(**kwargs)
    normalized = _normalize_decision_labels(decision)
    normalized["protocol_revision"] = PROTOCOL_REVISION
    normalized["implementation_revision"] = IMPLEMENTATION_REVISION
    root = Path(kwargs["root"])
    base._write_json(root / "stage_decision.json", normalized)
    return status, normalized


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pattern-config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    parser.add_argument(
        "--runner",
        type=Path,
        default=Path(__file__).resolve().parent
        / "run_aggtrade_flow_response_auction_nautilus.py",
    )
    args = parser.parse_args()

    status, decision = execute_staged_validation(
        config=args.config.resolve(),
        pattern_config=args.pattern_config.resolve(),
        root=args.root.resolve(),
        data_cache=args.data_cache.resolve(),
        runner=args.runner.resolve(),
        python_executable=sys.executable,
    )
    print(json.dumps(decision, indent=2, sort_keys=True), flush=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
