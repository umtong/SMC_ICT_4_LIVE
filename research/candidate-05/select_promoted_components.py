#!/usr/bin/env python3
"""Select independently promoted components without changing frozen contracts."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _metric(value: dict[str, Any] | None, name: str, default: float = -math.inf) -> float:
    if not value:
        return default
    try:
        number = float(value.get(name, default))
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _continuous(decision: dict[str, Any] | None) -> dict[str, Any] | None:
    if not decision:
        return None
    value = decision.get("continuous_metrics")
    return value if isinstance(value, dict) else None


def choose(
    *,
    latest: dict[str, Any],
    v59: dict[str, Any],
    v62: dict[str, Any],
) -> dict[str, Any]:
    promotion = latest.get("promotion", {})
    chosen: set[str] = set()
    reasons: dict[str, str] = {}
    if promotion.get("v56_early_flow_core"):
        chosen.add("v56")
        reasons["v56"] = "FROZEN_PAIRED_OOS_PASS"
    if promotion.get("v58_forced_basis_reversion"):
        chosen.add("v58")
        reasons["v58"] = "FROZEN_PAIRED_OOS_PASS"
    if v62.get("classification") == "V62_POST_FUNDING_RESET_PASSED_DEV_OOS_AND_CONTINUOUS":
        chosen.add("v62")
        reasons["v62"] = "DEV_OOS_CONTINUOUS_PASS"

    v55_pass = bool(promotion.get("v55_spot_price_discovery"))
    v59_pass = (
        v59.get("classification")
        == "V59_BOUNDARY_RETEST_PASSED_DEV_OOS_AND_CONTINUOUS"
    )
    v55_decision = latest.get("decisions", {}).get("v55_loop") or {}
    v55_environment = (v55_decision.get("selection") or {}).get("environment") or {}

    if v55_pass and v59_pass and v55_environment:
        # Both call the same spot-direction predicate, but v59 was validated
        # under strict constants. A relaxed v55 global cannot be composed with
        # that strict contract. Select the stronger continuous result before the
        # next untouched path instead of mutating either family.
        v55_metrics = _continuous(v55_decision)
        v59_metrics = _continuous(v59)
        v55_score = (
            _metric(v55_metrics, "geometric_daily_growth"),
            _metric(v55_metrics, "profit_factor"),
            _metric(v55_metrics, "total_return"),
        )
        v59_score = (
            _metric(v59_metrics, "geometric_daily_growth"),
            _metric(v59_metrics, "profit_factor"),
            _metric(v59_metrics, "total_return"),
        )
        if v55_score > v59_score:
            chosen.add("v55")
            reasons["v55"] = "RELAXED_V55_OUTRANKED_STRICT_V59_CONTINUOUS"
            reasons["v59_excluded"] = "GLOBAL_SPOT_THRESHOLD_CONFLICT"
        else:
            chosen.add("v59")
            reasons["v59"] = "STRICT_V59_OUTRANKED_RELAXED_V55_CONTINUOUS"
            reasons["v55_excluded"] = "GLOBAL_SPOT_THRESHOLD_CONFLICT"
            v55_environment = {}
    else:
        if v55_pass:
            chosen.add("v55")
            reasons["v55"] = (
                "STRICT_DEV_OOS_CONTINUOUS_PASS"
                if not v55_environment
                else "ONE_VARIABLE_DEV_OOS_CONTINUOUS_PASS"
            )
        if v59_pass:
            chosen.add("v59")
            reasons["v59"] = "STRICT_DEV_OOS_CONTINUOUS_PASS"

    components = sorted(chosen)
    return {
        "schema": "candidate-05-promoted-component-selection-v1",
        "ready": bool(latest.get("all_expected_complete")),
        "components": components,
        "environment": v55_environment if "v55" in chosen else {},
        "reasons": reasons,
        "spot_contract_conflict_resolved": bool(
            v55_pass and v59_pass and ((v55_decision.get("selection") or {}).get("environment") or {})
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", type=Path, required=True)
    parser.add_argument("--v59", type=Path, required=True)
    parser.add_argument("--v62", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-output", type=Path, required=True)
    args = parser.parse_args()
    result = choose(
        latest=json.loads(args.latest.read_text()),
        v59=json.loads(args.v59.read_text()),
        v62=json.loads(args.v62.read_text()),
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    args.env_output.write_text(
        f"export CANDIDATE05_COMPONENTS={','.join(result['components'])}\n"
        + "".join(
            f"export {key}={value}\n"
            for key, value in result["environment"].items()
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
