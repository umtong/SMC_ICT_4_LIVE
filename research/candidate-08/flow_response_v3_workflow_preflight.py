"""Static workflow/evidence preflight for candidate-08 flow-response V3.

This module exists to keep GitHub Actions shell syntax out of the research contract.  It validates
that the frozen V3 detector, exact-cadence protocol, V4 zero-trade evidence wrapper, project risk
contract and workflow wiring agree.  It can also record a preflight implementation failure after the
shell step has written its numeric exit status.

It does not load market data, generate signals, alter an order, execute a backtest or evaluate PnL.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any

from aggtrade_flow_response import FlowResponseConfig
from aggtrade_flow_response_auction_signals_v3 import (
    IMPLEMENTATION_REVISION,
    FlowResponseAuctionConfig,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
from run_aggtrade_flow_response_auction_nautilus_v4 import (
    EVIDENCE_WRAPPER_REVISION,
)
from run_flow_response_staged_validation_v2 import PROTOCOL_REVISION


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parent.parent
CONFIG_PATH = HERE / "config_flow_response_auction_btc_v1.json"
WORKFLOW_PATH = (
    REPOSITORY_ROOT
    / ".github/workflows/candidate-08-flow-response-auction-nautilus-v3.yml"
)


def validate_static_contract() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    detector = (HERE / "aggtrade_flow_response_auction_signals_v3.py").read_text(
        encoding="utf-8"
    )
    wrapper = (HERE / "run_aggtrade_flow_response_auction_nautilus_v4.py").read_text(
        encoding="utf-8"
    )
    orchestrator = (HERE / "run_flow_response_staged_validation_v2.py").read_text(
        encoding="utf-8"
    )
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    default_response = FlowResponseConfig()
    default_auction = FlowResponseAuctionConfig()
    checks = {
        "implementation_revision_exact": (
            config["implementation_revision"] == IMPLEMENTATION_REVISION
        ),
        "exact_ten_second_config": (
            config["ten_second_cadence_contract"]
            == "EXACT_CONSECUTIVE_10_SECONDS"
        ),
        "path_diagnostic_revision_exact": (
            config["trade_path_diagnostic_revision"] == DIAGNOSTIC_REVISION
        ),
        "flow_response_parameters_frozen": (
            config["flow_response_config"] == asdict(default_response)
        ),
        "auction_parameters_frozen": (
            config["flow_response_auction_config"]
            == {
                "interaction_response_windows": (
                    default_auction.interaction_response_windows
                ),
                "reversal_confirmation_windows": (
                    default_auction.reversal_confirmation_windows
                ),
            }
        ),
        "detector_checks_exact_cadence": (
            "validate_exact_ten_second_cadence(data)" in detector
            and "EXACT_CONSECUTIVE_10_SECONDS" in detector
        ),
        "evidence_wrapper_exact": (
            EVIDENCE_WRAPPER_REVISION
            == "EXPLICIT_ZERO_TRADE_PATH_REVISION_COUNT_V4"
            and EVIDENCE_WRAPPER_REVISION in wrapper
        ),
        "evidence_wrapper_does_not_reimplement_engine": (
            "BacktestEngine(" not in wrapper
            and "risk_sized_quantity(" not in wrapper
            and "order_factory.bracket(" not in wrapper
        ),
        "orchestrator_revision_exact": (
            PROTOCOL_REVISION
            == "FLOW_RESPONSE_STAGED_VALIDATION_V2_EXACT_CADENCE"
            and PROTOCOL_REVISION in orchestrator
        ),
        "workflow_uses_v3_runner_and_orchestrator": (
            "run_aggtrade_flow_response_auction_nautilus_v4.py" in workflow
            and "run_flow_response_staged_validation_v2.py" in workflow
        ),
        "workflow_uses_pinned_environment": (
            "sha256:8f4de8a2b2fa28c3f424d114969b1c07765206708f24613b86896ced67532469"
            in workflow
        ),
        "workflow_runner_and_concurrency_exact": (
            "runs-on: ubuntu-24.04" in workflow
            and "cancel-in-progress: true" in workflow
        ),
        "project_risk_contract_unchanged": (
            float(config["risk_fraction"]) == 0.03
            and int(config["venue"]["default_leverage"]) == 125
            and "maximum_notional" not in config
            and "risk_multiplier" not in config
        ),
        "btc_first_contract": set(config["assets"]) == {"BTCUSDT"},
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise RuntimeError(f"V3 static preflight failed: {failed}")
    return {
        "implementation_revision": IMPLEMENTATION_REVISION,
        "protocol_revision": PROTOCOL_REVISION,
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
        "evidence_wrapper_revision": EVIDENCE_WRAPPER_REVISION,
        "checks": checks,
        "passed": True,
    }


def record_preflight_failure(*, root: Path) -> dict[str, Any]:
    status_path = root / "preflight_exit_status.txt"
    if not status_path.exists():
        raise FileNotFoundError(f"preflight status not found: {status_path}")
    status = int(status_path.read_text(encoding="utf-8").strip())
    payload = {
        "candidate": "candidate-08-flow-response-auction-btc-nautilus-v1",
        "protocol_revision": PROTOCOL_REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
        "evidence_wrapper_revision": EVIDENCE_WRAPPER_REVISION,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "preflight_runner_status": status,
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
