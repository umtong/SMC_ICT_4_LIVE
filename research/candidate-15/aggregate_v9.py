#!/usr/bin/env python3
"""Aggregate Candidate 15 V9 beta-coherent exposed development evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from math import isfinite
from pathlib import Path
from typing import Any

from aggregate import aggregate as aggregate_base
from aggregate import read_object, write_object


ALLOWED = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}
MODULE = "BETA_COHERENT_DIFFUSION_LAG_MSS_FVG"
HORIZONS = {"24", "48", "96", "192"}


def _route_audit(root: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    submitted = 0
    violations: list[dict[str, Any]] = []
    symbols: Counter[str] = Counter()
    body_ratios: list[float] = []
    completion_r: list[float] = []
    for interval in protocol["selection"]["intervals"]:
        payload = read_object(root / "results" / interval / "submitted_plans.json")
        plans = payload.get("plans", [])
        if not isinstance(plans, list):
            raise TypeError("submitted plans must be a list")
        for plan in plans:
            submitted += 1
            symbol = str(plan.get("symbol", ""))
            symbols[symbol] += 1
            details = plan.get("details", {}) if isinstance(plan.get("details"), dict) else {}
            ownership = details.get("candidate15_v9_ownership")
            transfer = details.get("candidate15_v9_transfer")
            accepted = set(ownership.get("accepted_symbols", ())) if isinstance(ownership, dict) else set()
            receiver = ownership.get("laggard_symbol") if isinstance(ownership, dict) else None
            betas = transfer.get("beta_zero_intercept_by_horizon", {}) if isinstance(transfer, dict) else {}
            state_gaps = transfer.get("state_delivery_gap_by_horizon", {}) if isinstance(transfer, dict) else {}
            geometry_gaps = transfer.get("geometry_delivery_gap_by_horizon", {}) if isinstance(transfer, dict) else {}
            ratio = float(transfer.get("residual_to_weakest_sender_body_ratio", float("nan"))) if isinstance(transfer, dict) else float("nan")
            costed_r = float(transfer.get("completion_costed_r", float("nan"))) if isinstance(transfer, dict) else float("nan")
            if isfinite(ratio): body_ratios.append(ratio)
            if isfinite(costed_r): completion_r.append(costed_r)
            valid = (
                plan.get("module") == MODULE
                and details.get("module") == MODULE
                and details.get("route") == "BETA_COHERENT_DIFFUSION_LAG"
                and isinstance(ownership, dict)
                and ownership.get("policy") == "EXCLUDED_RESIDUAL_MARKET_ONLY"
                and len(accepted) == 3
                and accepted.issubset(ALLOWED)
                and symbol in ALLOWED
                and ALLOWED - accepted == {symbol}
                and receiver == symbol
                and isinstance(transfer, dict)
                and transfer.get("policy") == "BETA_COHERENT_DIFFUSION_LAG"
                and transfer.get("stage") == "BETA_COHERENT_DIFFUSION_LAG"
                and transfer.get("residual_symbol") == symbol
                and set(transfer.get("accepted_symbols", ())) == accepted
                and set(str(key) for key in betas) == HORIZONS
                and all(float(value) > 0.0 for value in betas.values())
                and set(str(key) for key in state_gaps) == HORIZONS
                and all(float(value) > 0.0 for value in state_gaps.values())
                and set(str(key) for key in geometry_gaps) == HORIZONS
                and all(float(value) > 0.0 for value in geometry_gaps.values())
                and isfinite(ratio) and 0.5 <= ratio < 1.0
                and isfinite(costed_r) and costed_r > 0.0
                and transfer.get("prior_parity_consumed") is False
                and transfer.get("estimation_cutoff") == "STRICTLY_BEFORE_FIRST_EVIDENCE_EVENT"
                and transfer.get("management_action") == "MODIFY_EXISTING_STOP_TO_MINIMUM_POSITIVE_COST_COVER"
                and transfer.get("management_trigger_model") == "COMPLETED_CLOSE_AT_OR_BEYOND_BETA_DELIVERY_OR_COST_COVER"
            )
            if not valid:
                violations.append({"interval": interval, "scenario_id": plan.get("scenario_id"), "symbol": symbol, "ownership": ownership, "transfer": transfer})
    return {
        "submitted_plans": submitted,
        "symbol_counts": dict(sorted(symbols.items())),
        "violation_count": len(violations),
        "violations": violations[:25],
        "minimum_body_ratio": min(body_ratios) if body_ratios else None,
        "maximum_body_ratio": max(body_ratios) if body_ratios else None,
        "minimum_completion_costed_r": min(completion_r) if completion_r else None,
        "maximum_completion_costed_r": max(completion_r) if completion_r else None,
    }


def _management_audit(root: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    per_interval: dict[str, dict[str, int]] = {}
    fail_types = {
        "TRANSFER_COST_COVER_ALREADY_IN_MARKET",
        "TRANSFER_PROTECTIVE_STOP_NOT_UNIQUE",
        "TRANSFER_STOP_MODIFICATION_EXCEPTION",
        "PROTECTIVE_ORDER_DENIED_FAIL_CLOSED",
        "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED",
    }
    for interval in protocol["selection"]["intervals"]:
        events = read_object(root / "results" / interval / "order_lifecycle.json").get("events", [])
        local = Counter(str(event.get("type", "UNKNOWN")) for event in events)
        counts.update(local)
        per_interval[interval] = dict(sorted(local.items()))
    completions = counts["TRANSFER_COMPLETION_CONFIRMED"]
    actions = counts["TRANSFER_STOP_MODIFICATION_SUBMITTED"] + counts["TRANSFER_STOP_ALREADY_PROTECTED"]
    failed = sum(counts[kind] for kind in fail_types)
    return {
        "event_counts": dict(sorted(counts.items())),
        "per_interval": per_interval,
        "transfer_completions": completions,
        "protection_actions": actions,
        "fail_closed_count": failed,
        "completed_without_action_count": max(0, completions - actions - failed),
    }


def aggregate(root: Path) -> dict[str, Any]:
    payload = aggregate_base(root)
    protocol = read_object(root / "protocol.json")
    route = _route_audit(root, protocol)
    management = _management_audit(root, protocol)
    checks = dict(payload.get("checks", {}))
    checks.pop("only_response_continuation_submitted", None)
    modules = payload.get("module_counts", {})
    checks["only_beta_coherent_diffusion_submitted"] = (
        route["violation_count"] == 0 and bool(modules) and set(modules) == {MODULE}
    )
    checks["management_integrity"] = (
        management["completed_without_action_count"] == 0
        and management["fail_closed_count"] == 0
    )
    if not checks.get("minimum_closed_trades") or not checks.get("minimum_active_intervals"):
        classification = "CANDIDATE15_V9_INSUFFICIENT_ACTIVITY"
    elif all(checks.values()):
        classification = "CANDIDATE15_V9_DEVELOPMENT_PROMISING"
    else:
        classification = "CANDIDATE15_V9_DEVELOPMENT_REJECTED"
    payload.update({
        "schema":"candidate-15-v9-beta-coherent-development-aggregate-v1",
        "candidate":protocol["candidate"],
        "protocol":protocol["schema"],
        "classification":classification,
        "checks":checks,
        "beta_coherent_route_audit":route,
        "beta_management_audit":management,
        "success_claim":False,
    })
    write_object(root / "aggregate.json", payload)
    lines=[
        "# Candidate 15 V9 beta-coherent diffusion lag",
        "",
        f"**{classification}**",
        "",
        "- development_only: `True`",
        "- success_claim: `False`",
        f"- weekly_reset_nav_multiple: `{payload.get('weekly_reset_nav_multiple')}`",
        f"- daily_geometric_growth: `{payload.get('daily_geometric_growth')}`",
        f"- closed_trades: `{payload.get('closed_trades')}`",
        f"- wins / losses: `{payload.get('wins')} / {payload.get('losses')}`",
        f"- win_rate: `{payload.get('win_rate')}`",
        f"- payoff_ratio: `{payload.get('payoff_ratio')}`",
        f"- active_intervals: `{payload.get('active_intervals')}`",
        f"- submitted_beta_coherent_plans: `{route['submitted_plans']}`",
        f"- route_violations: `{route['violation_count']}`",
        f"- transfer_completions: `{management['transfer_completions']}`",
        f"- protection_actions: `{management['protection_actions']}`",
        f"- management_fail_closed_count: `{management['fail_closed_count']}`",
        "",
        "## Interval evidence",
    ]
    for record in payload.get("intervals", []):
        event_types=record.get("diagnostics",{}).get("event_type_counts",{})
        lines.append(
            f"- {record['interval']} ({record['start']}): daily_geo={record['daily_geometric_growth']}, "
            f"trades={record['closed_trades']}, W/L={record['wins']}/{record['losses']}, "
            f"beta_states={event_types.get('QHI_V9_BETA_COHERENT_TRANSFER_STATE_QUALIFIED',0)}"
        )
    lines.extend(("", "## Development checks"))
    lines.extend(f"- {key}: `{value}`" for key,value in checks.items())
    lines.extend(("", "E01-E06 are exposed mechanism-development intervals and cannot support a success claim."))
    (root / "RESULT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return payload


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args(); print(json.dumps(aggregate(args.root.resolve()),indent=2,sort_keys=True,default=str)); return 0


if __name__=="__main__":
    raise SystemExit(main())
