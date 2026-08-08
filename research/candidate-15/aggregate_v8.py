#!/usr/bin/env python3
"""Aggregate Candidate 15 V8 managed-transfer exposed development evidence."""
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
MODULE = "MANAGED_RESIDUAL_TRANSFER_MSS_FVG"
STAGES = {"PARTIAL_CATCH_UP", "PARITY_HANDOFF_RETEST"}


def _route_audit(root: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    submitted = 0
    violations: list[dict[str, Any]] = []
    symbols: Counter[str] = Counter()
    stages: Counter[str] = Counter()
    parity_r_values: list[float] = []
    for interval in protocol["selection"]["intervals"]:
        path = root / "results" / interval / "submitted_plans.json"
        payload = read_object(path)
        plans = payload.get("plans", [])
        if not isinstance(plans, list):
            raise TypeError(f"{path} plans must be a list")
        for plan in plans:
            submitted += 1
            symbol = str(plan.get("symbol", ""))
            symbols[symbol] += 1
            details = plan.get("details", {})
            ownership = (
                details.get("candidate15_v8_ownership")
                if isinstance(details, dict)
                else None
            )
            transfer = (
                details.get("candidate15_v8_transfer")
                if isinstance(details, dict)
                else None
            )
            accepted = (
                set(ownership.get("accepted_symbols", ()))
                if isinstance(ownership, dict)
                else set()
            )
            receiver = (
                ownership.get("laggard_symbol")
                if isinstance(ownership, dict)
                else None
            )
            stage = str(transfer.get("stage", "")) if isinstance(transfer, dict) else ""
            stages[stage or "MISSING"] += 1
            parity_r = (
                float(transfer.get("parity_costed_r"))
                if isinstance(transfer, dict)
                and transfer.get("parity_costed_r") is not None
                else float("nan")
            )
            if isfinite(parity_r):
                parity_r_values.append(parity_r)
            touched = (
                transfer.get("current_bar_touched_parity")
                if isinstance(transfer, dict)
                else None
            )
            closed_beyond = (
                transfer.get("current_bar_closed_beyond_parity")
                if isinstance(transfer, dict)
                else None
            )
            stage_valid = (
                stage == "PARTIAL_CATCH_UP"
                and touched is False
                and isfinite(parity_r)
                and parity_r > 0.0
            ) or (
                stage == "PARITY_HANDOFF_RETEST"
                and touched is True
                and closed_beyond is True
            )
            valid = (
                plan.get("module") == MODULE
                and details.get("module") == MODULE
                and details.get("route") == "MANAGED_RESIDUAL_INFORMATION_TRANSFER"
                and isinstance(ownership, dict)
                and ownership.get("policy") == "EXCLUDED_RESIDUAL_MARKET_ONLY"
                and accepted.issubset(ALLOWED)
                and len(accepted) == 3
                and symbol in ALLOWED
                and symbol not in accepted
                and receiver == symbol
                and ALLOWED - accepted == {symbol}
                and isinstance(transfer, dict)
                and transfer.get("policy") == "MANAGED_RESIDUAL_INFORMATION_TRANSFER"
                and stage in STAGES
                and stage_valid
                and transfer.get("residual_symbol") == symbol
                and set(transfer.get("accepted_symbols", ())) == accepted
                and len(tuple(transfer.get("evidence_event_ids", ()))) == 2
                and int(transfer.get("effective_ts_ns", plan.get("observed_ts_ns", 0)))
                < int(plan.get("observed_ts_ns", 0))
                and transfer.get("prior_parity_consumed") is False
                and float(transfer.get("delivery_gap", 0.0)) > 0.0
                and transfer.get("management_action")
                == "MODIFY_EXISTING_STOP_TO_MINIMUM_POSITIVE_COST_COVER"
                and transfer.get("management_trigger_model")
                == "COMPLETED_CLOSE_AT_OR_BEYOND_PARITY_OR_COST_COVER"
            )
            if not valid:
                violations.append(
                    {
                        "interval": interval,
                        "scenario_id": plan.get("scenario_id"),
                        "symbol": symbol,
                        "ownership": ownership,
                        "transfer": transfer,
                    },
                )
    return {
        "submitted_plans": submitted,
        "symbol_counts": dict(sorted(symbols.items())),
        "stage_counts": dict(sorted(stages.items())),
        "violation_count": len(violations),
        "violations": violations[:25],
        "minimum_parity_costed_r": min(parity_r_values) if parity_r_values else None,
        "maximum_parity_costed_r": max(parity_r_values) if parity_r_values else None,
    }


def _management_audit(root: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    per_interval: dict[str, dict[str, int]] = {}
    fail_closed_types = {
        "TRANSFER_COST_COVER_ALREADY_IN_MARKET",
        "TRANSFER_PROTECTIVE_STOP_NOT_UNIQUE",
        "TRANSFER_STOP_MODIFICATION_EXCEPTION",
        "PROTECTIVE_ORDER_DENIED_FAIL_CLOSED",
        "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED",
    }
    for interval in protocol["selection"]["intervals"]:
        path = root / "results" / interval / "order_lifecycle.json"
        payload = read_object(path)
        events = payload.get("events", [])
        if not isinstance(events, list):
            raise TypeError(f"{path} events must be a list")
        local: Counter[str] = Counter(str(event.get("type", "UNKNOWN")) for event in events)
        counts.update(local)
        per_interval[interval] = dict(sorted(local.items()))
    fail_closed = sum(counts[kind] for kind in fail_closed_types)
    completions = counts["TRANSFER_COMPLETION_CONFIRMED"]
    protected = (
        counts["TRANSFER_STOP_MODIFICATION_SUBMITTED"]
        + counts["TRANSFER_STOP_ALREADY_PROTECTED"]
    )
    return {
        "event_counts": dict(sorted(counts.items())),
        "per_interval": per_interval,
        "transfer_completions": completions,
        "protection_actions": protected,
        "fail_closed_count": fail_closed,
        "completed_transfer_without_action_count": max(0, completions - protected - fail_closed),
    }


def aggregate(root: Path) -> dict[str, Any]:
    payload = aggregate_base(root)
    protocol = read_object(root / "protocol.json")
    route_audit = _route_audit(root, protocol)
    management_audit = _management_audit(root, protocol)

    rejection_counts: Counter[str] = Counter()
    for record in payload.get("intervals", []):
        reasons = record.get("diagnostics", {}).get("reason_code_counts", {})
        for reason in (
            "QHI_V8_TRANSFER_STAGE_UNRESOLVED",
            "QHI_V8_PLAN_NOT_STATE_RESIDUAL",
            "QHI_V7_RESIDUAL_NOT_BEHIND_AT_CONFIRMATION",
            "QHI_V7_NO_UNIQUE_RESIDUAL_RECEIVER",
            "C15_V8_NOT_RESIDUAL_RECEIVER",
        ):
            rejection_counts[reason] += int(reasons.get(reason, 0))

    checks = dict(payload.get("checks", {}))
    checks.pop("only_response_continuation_submitted", None)
    modules = payload.get("module_counts", {})
    checks["only_managed_transfer_submitted"] = (
        route_audit["violation_count"] == 0
        and bool(modules)
        and set(modules) == {MODULE}
    )
    checks["management_integrity"] = (
        management_audit["completed_transfer_without_action_count"] == 0
        and management_audit["fail_closed_count"] == 0
    )

    if not checks.get("minimum_closed_trades") or not checks.get("minimum_active_intervals"):
        classification = "CANDIDATE15_V8_INSUFFICIENT_ACTIVITY"
    elif all(checks.values()):
        classification = "CANDIDATE15_V8_DEVELOPMENT_PROMISING"
    else:
        classification = "CANDIDATE15_V8_DEVELOPMENT_REJECTED"

    payload.update(
        {
            "schema": "candidate-15-v8-managed-transfer-development-aggregate-v1",
            "candidate": protocol["candidate"],
            "protocol": protocol["schema"],
            "classification": classification,
            "checks": checks,
            "managed_transfer_route_audit": route_audit,
            "managed_transfer_management_audit": management_audit,
            "managed_transfer_rejection_counts": dict(rejection_counts),
            "next_evidence_required": (
                "A promising exposed result permits only a frozen, newly "
                "predeclared untouched confirmation screen."
            ),
        },
    )
    write_object(root / "aggregate.json", payload)

    lines = [
        "# Candidate 15 V8 managed residual information transfer",
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
        f"- closed_trade_path_max_drawdown: `{payload.get('closed_trade_path_max_drawdown')}`",
        f"- submitted_managed_transfer_plans: `{route_audit['submitted_plans']}`",
        f"- transfer_stage_counts: `{route_audit['stage_counts']}`",
        f"- transfer_completions: `{management_audit['transfer_completions']}`",
        f"- protection_actions: `{management_audit['protection_actions']}`",
        f"- management_fail_closed_count: `{management_audit['fail_closed_count']}`",
        f"- route_violations: `{route_audit['violation_count']}`",
        "",
        "## Interval evidence",
    ]
    for record in payload.get("intervals", []):
        event_types = record.get("diagnostics", {}).get("event_type_counts", {})
        reasons = record.get("diagnostics", {}).get("reason_code_counts", {})
        local_management = management_audit["per_interval"].get(record["interval"], {})
        lines.append(
            f"- {record['interval']} ({record['start']}): "
            f"daily_geo={record['daily_geometric_growth']}, "
            f"trades={record['closed_trades']}, W/L={record['wins']}/{record['losses']}, "
            f"transfer_states={event_types.get('QHI_V7_BOUNDED_TRANSFER_STATE_QUALIFIED', 0)}, "
            f"stage_rejections={reasons.get('QHI_V8_TRANSFER_STAGE_UNRESOLVED', 0)}, "
            f"protect={local_management.get('TRANSFER_STOP_MODIFICATION_SUBMITTED', 0)}"
        )
    lines.extend(("", "## Development checks"))
    lines.extend(f"- {key}: `{value}`" for key, value in checks.items())
    lines.extend(("", "## Highest-volume diagnostic skips"))
    lines.extend(
        f"- {key}: `{value}`"
        for key, value in payload.get("top_skip_reasons", {}).items()
    )
    lines.extend(
        (
            "",
            "E01-E06 are exposed controlled-development intervals. V8 can only "
            "reject or improve the managed-transfer mechanism; it cannot support "
            "a success claim.",
        ),
    )
    (root / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    print(json.dumps(aggregate(args.root.resolve()), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
