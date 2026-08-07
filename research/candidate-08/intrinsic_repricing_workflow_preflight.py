"""Static and failure-recording preflight for intrinsic repricing validation.

This module keeps workflow shell code small and testable.  It verifies that detector, frozen config,
native runner boundary, staged protocol, project risk contract and workflow wiring all refer to the
same implementation revision before official data or NautilusTrader economic replay begins.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any

from aggtrade_flow_response import FlowResponseConfig
from aggtrade_intrinsic_repricing_signals import (
    IMPLEMENTATION_REVISION,
    IntrinsicRepricingConfig,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
from run_intrinsic_repricing_staged_validation import PROTOCOL_REVISION


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parent.parent
CONFIG_PATH = HERE / "config_intrinsic_repricing_btc_v1.json"
WORKFLOW_PATH = (
    REPOSITORY_ROOT
    / ".github/workflows/candidate-08-intrinsic-repricing-nautilus-v1.yml"
)


def validate_static_contract() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    detector = (HERE / "aggtrade_intrinsic_repricing_signals.py").read_text(
        encoding="utf-8"
    )
    runner = (HERE / "run_aggtrade_intrinsic_repricing_nautilus.py").read_text(
        encoding="utf-8"
    )
    orchestrator = (HERE / "run_intrinsic_repricing_staged_validation.py").read_text(
        encoding="utf-8"
    )
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    default = IntrinsicRepricingConfig()

    checks = {
        "implementation_revision_exact": (
            config["implementation_revision"] == IMPLEMENTATION_REVISION
        ),
        "protocol_revision_exact": (
            PROTOCOL_REVISION == "INTRINSIC_REPRICING_STAGED_VALIDATION_V1"
            and PROTOCOL_REVISION in orchestrator
        ),
        "path_diagnostic_revision_exact": (
            config["trade_path_diagnostic_revision"] == DIAGNOSTIC_REVISION
        ),
        "flow_response_parameters_frozen": (
            config["flow_response_config"] == asdict(FlowResponseConfig())
        ),
        "intrinsic_event_contract_frozen": (
            config["intrinsic_repricing_config"]
            == {"maximum_event_bars": default.maximum_event_bars}
        ),
        "exact_ten_second_cadence": (
            config["ten_second_cadence_contract"]
            == "EXACT_CONSECUTIVE_10_SECONDS"
            and "validate_exact_ten_second_cadence(data)" in detector
        ),
        "activity_budget_is_causal": (
            "causal_activity_budget_series" in detector
            and "build_intrinsic_response_event" in detector
        ),
        "scenario_waits_for_separate_events": (
            "event_a.end_position + 1" in detector
            and "event_b.end_position + 1" in detector
            and "INTRINSIC_REPRICING_CONTINUATION_CONFIRMED" in detector
        ),
        "no_outcome_or_custom_engine_in_detector": all(
            forbidden not in detector
            for forbidden in (
                "realized_pnl",
                "future_high",
                "future_low",
                "win_rate",
                "BacktestEngine(",
                "order_factory",
                "submit_order",
                "fixed_r",
            )
        ),
        "native_engine_not_reimplemented_in_runner": all(
            forbidden not in runner
            for forbidden in (
                "BacktestEngine(",
                "add_venue(",
                "risk_sized_quantity(",
                "order_factory.bracket(",
                "submit_order_list(",
                "default_leverage=",
            )
        ),
        "both_paths_base_and_one_diagnostic_only": (
            '"both_paths"' in runner
            and '"direct_only"' in runner
            and '"reprice_only"' in runner
            and "ONE_POSITIVE_AND_ONE_NEGATIVE_ENTRY_PATH" in orchestrator
        ),
        "project_risk_contract_unchanged": (
            float(config["risk_fraction"]) == 0.03
            and int(config["venue"]["default_leverage"]) == 125
            and "maximum_notional" not in config
            and "risk_multiplier" not in config
        ),
        "btc_first_contract": set(config["assets"]) == {"BTCUSDT"},
        "workflow_uses_exact_sources": (
            "run_aggtrade_intrinsic_repricing_nautilus.py" in workflow
            and "run_intrinsic_repricing_staged_validation.py" in workflow
            and "config_intrinsic_repricing_btc_v1.json" in workflow
        ),
        "workflow_uses_pinned_environment": (
            "sha256:8f4de8a2b2fa28c3f424d114969b1c07765206708f24613b86896ced67532469"
            in workflow
        ),
        "workflow_runner_and_concurrency_exact": (
            "runs-on: ubuntu-24.04" in workflow
            and "cancel-in-progress: true" in workflow
        ),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise RuntimeError(f"intrinsic repricing static preflight failed: {failed}")
    return {
        "implementation_revision": IMPLEMENTATION_REVISION,
        "protocol_revision": PROTOCOL_REVISION,
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
        "checks": checks,
        "passed": True,
    }


def record_preflight_failure(*, root: Path) -> dict[str, Any]:
    status_path = root / "preflight_exit_status.txt"
    if not status_path.exists():
        raise FileNotFoundError(f"preflight status not found: {status_path}")
    payload = {
        "candidate": "candidate-08-intrinsic-repricing-btc-nautilus-v1",
        "protocol_revision": PROTOCOL_REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "preflight_runner_status": int(
            status_path.read_text(encoding="utf-8").strip()
        ),
        "decision": "PREFLIGHT_IMPLEMENTATION_FAILURE",
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "stage_decision.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("validate", "record-failure"),
        default="validate",
    )
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args()
    if args.mode == "validate":
        result = validate_static_contract()
    else:
        if args.root is None:
            parser.error("--root is required for record-failure")
        result = record_preflight_failure(root=args.root.resolve())
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
