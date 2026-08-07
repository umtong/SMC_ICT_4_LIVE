"""Static and failure-recording preflight for delayed reacceptance validation."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any

from aggtrade_delayed_reacceptance_signals_v2 import (
    IMPLEMENTATION_REVISION,
    DelayedReacceptanceConfig,
)
from aggtrade_flow_response import FlowResponseConfig
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
from run_delayed_reacceptance_staged_validation import PROTOCOL_REVISION


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parent.parent
CONFIG_PATH = HERE / "config_delayed_reacceptance_btc_v1.json"
WORKFLOW_PATH = (
    REPOSITORY_ROOT
    / ".github/workflows/candidate-08-delayed-reacceptance-nautilus-v1.yml"
)


def validate_static_contract() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    detector = (HERE / "aggtrade_delayed_reacceptance_signals.py").read_text(
        encoding="utf-8"
    )
    safe_detector = (HERE / "aggtrade_delayed_reacceptance_signals_v2.py").read_text(
        encoding="utf-8"
    )
    runner = (HERE / "run_aggtrade_delayed_reacceptance_nautilus.py").read_text(
        encoding="utf-8"
    )
    orchestrator = (HERE / "run_delayed_reacceptance_staged_validation.py").read_text(
        encoding="utf-8"
    )
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    default = DelayedReacceptanceConfig()

    checks = {
        "implementation_revision_exact": (
            config["implementation_revision"] == IMPLEMENTATION_REVISION
        ),
        "protocol_revision_exact": (
            PROTOCOL_REVISION == "DELAYED_REACCEPTANCE_STAGED_VALIDATION_V1"
            and PROTOCOL_REVISION in orchestrator
        ),
        "path_revision_exact": (
            config["trade_path_diagnostic_revision"] == DIAGNOSTIC_REVISION
        ),
        "response_parameters_frozen": (
            config["flow_response_config"] == asdict(FlowResponseConfig())
        ),
        "setup_expiry_frozen": (
            config["delayed_reacceptance_config"]
            == {"setup_expiry_minutes": default.setup_expiry_minutes}
        ),
        "causal_sequence_present": all(
            text in detector
            for text in (
                "INITIAL_OUTWARD_RESPONSE_CONFIRMED_NO_ENTRY",
                "BOUNDARY_RECLAIMED_AFTER_INITIAL_RESPONSE",
                "DELAYED_OUTWARD_REACCEPTANCE_CONFIRMED",
                "TARGET_REACHED_BEFORE_REACCEPTANCE",
            )
        ),
        "safe_observability_revision_present": all(
            text in safe_detector
            for text in (
                "_observable_feature",
                "LevelKind.HIGH",
                "LevelKind.LOW",
            )
        ),
        "no_custom_engine_or_outcome_in_detector": all(
            forbidden not in detector
            for forbidden in (
                "realized_pnl",
                "future_high",
                "future_low",
                "win_rate",
                "model_score",
                "risk_multiplier",
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
        "one_ablation_only": (
            "INITIAL_INITIATIVE_RESPONSE_STATE_REQUIREMENT" in orchestrator
            and "SINGLE_ABLATION_SUPPORTS_NEW_BASE_REBUILD" in orchestrator
            and "promotion_permitted_from_ablation" in orchestrator
        ),
        "project_risk_contract_unchanged": (
            float(config["risk_fraction"]) == 0.03
            and int(config["venue"]["default_leverage"]) == 125
            and "maximum_notional" not in config
            and "risk_multiplier" not in config
        ),
        "btc_first_contract": set(config["assets"]) == {"BTCUSDT"},
        "workflow_sources_exact": (
            "run_aggtrade_delayed_reacceptance_nautilus.py" in workflow
            and "run_delayed_reacceptance_staged_validation.py" in workflow
            and "config_delayed_reacceptance_btc_v1.json" in workflow
        ),
        "workflow_pinned_environment": (
            "sha256:8f4de8a2b2fa28c3f424d114969b1c07765206708f24613b86896ced67532469"
            in workflow
        ),
        "workflow_runner_and_concurrency": (
            "runs-on: ubuntu-24.04" in workflow
            and "cancel-in-progress: true" in workflow
        ),
    }
    failed = sorted(name for name, value in checks.items() if not value)
    if failed:
        raise RuntimeError(f"delayed reacceptance static preflight failed: {failed}")
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
        "candidate": "candidate-08-delayed-reacceptance-btc-nautilus-v1",
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
