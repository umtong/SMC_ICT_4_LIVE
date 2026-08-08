#!/usr/bin/env python3
"""Aggregate Candidate 15 V7's exposed bounded-transfer development screen."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from math import isfinite
from pathlib import Path
from typing import Any

from aggregate import aggregate as aggregate_v5
from aggregate import read_object, write_object


ALLOWED = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}
MODULE = "BOUNDED_RESIDUAL_TRANSFER_MSS_FVG"


def _route_audit(root: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    submitted = 0
    violations: list[dict[str, Any]] = []
    symbols: Counter[str] = Counter()
    parity_r_values: list[float] = []
    body_ratios: list[float] = []
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
            ownership = details.get("candidate15_v7_ownership") if isinstance(details, dict) else None
            transfer = details.get("candidate15_v7_transfer") if isinstance(details, dict) else None
            accepted = set(ownership.get("accepted_symbols", ())) if isinstance(ownership, dict) else set()
            receiver = ownership.get("laggard_symbol") if isinstance(ownership, dict) else None
            body_ratio = (
                float(transfer.get("residual_to_weakest_sender_body_ratio"))
                if isinstance(transfer, dict)
                and transfer.get("residual_to_weakest_sender_body_ratio") is not None
                else float("nan")
            )
            parity_r = (
                float(transfer.get("parity_costed_r"))
                if isinstance(transfer, dict) and transfer.get("parity_costed_r") is not None
                else float("nan")
            )
            if isfinite(body_ratio):
                body_ratios.append(body_ratio)
            if isfinite(parity_r):
                parity_r_values.append(parity_r)
            valid = (
                plan.get("module") == MODULE
                and details.get("module") == MODULE
                and isinstance(ownership, dict)
                and ownership.get("policy") == "EXCLUDED_RESIDUAL_MARKET_ONLY"
                and accepted.issubset(ALLOWED)
                and len(accepted) == 3
                and symbol in ALLOWED
                and symbol not in accepted
                and receiver == symbol
                and ALLOWED - accepted == {symbol}
                and isinstance(transfer, dict)
                and transfer.get("policy") == "BOUNDED_PARTIAL_CATCH_UP"
                and transfer.get("residual_symbol") == symbol
                and set(transfer.get("accepted_symbols", ())) == accepted
                and len(tuple(transfer.get("evidence_event_ids", ()))) == 2
                and int(transfer.get("effective_ts_ns", plan.get("observed_ts_ns", 0)))
                < int(plan.get("observed_ts_ns", 0))
                and isfinite(body_ratio)
                and 0.5 <= body_ratio < 1.0
                and isfinite(parity_r)
                and 0.0 < parity_r < 1.0
                and transfer.get("parity_consumed_before_plan") is False
                and float(transfer.get("delivery_gap", 0.0)) > 0.0
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
        "violation_count": len(violations),
        "violations": violations[:25],
        "minimum_body_ratio": min(body_ratios) if body_ratios else None,
        "maximum_body_ratio": max(body_ratios) if body_ratios else None,
        "minimum_parity_costed_r": min(parity_r_values) if parity_r_values else None,
        "maximum_parity_costed_r": max(parity_r_values) if parity_r_values else None,
    }


def aggregate(root: Path) -> dict[str, Any]:
    payload = aggregate_v5(root)
    protocol = read_object(root / "protocol.json")
    route_audit = _route_audit(root, protocol)

    rejection_counts: Counter[str] = Counter()
    for record in payload.get("intervals", []):
        diagnostics = record.get("diagnostics", {})
        reasons = diagnostics.get("reason_code_counts", {})
        for reason in (
            "QHI_V7_BOUNDED_TRANSFER_GEOMETRY_UNRESOLVED",
            "QHI_V7_RESIDUAL_NOT_BEHIND_AT_CONFIRMATION",
            "QHI_V7_NO_UNIQUE_RESIDUAL_RECEIVER",
            "C15_V7_NOT_RESIDUAL_RECEIVER",
        ):
            rejection_counts[reason] += int(reasons.get(reason, 0))

    checks = dict(payload.get("checks", {}))
    checks.pop("only_response_continuation_submitted", None)
    modules = payload.get("module_counts", {})
    checks["only_bounded_transfer_submitted"] = (
        route_audit["violation_count"] == 0
        and bool(modules)
        and set(modules) == {MODULE}
    )

    if not checks.get("minimum_closed_trades") or not checks.get("minimum_active_intervals"):
        classification = "CANDIDATE15_V7_INSUFFICIENT_ACTIVITY"
    elif all(checks.values()):
        classification = "CANDIDATE15_V7_DEVELOPMENT_PROMISING"
    else:
        classification = "CANDIDATE15_V7_DEVELOPMENT_REJECTED"

    payload.update(
        {
            "schema": "candidate-15-v7-bounded-transfer-development-aggregate-v1",
            "candidate": protocol["candidate"],
            "protocol": protocol["schema"],
            "classification": classification,
            "checks": checks,
            "bounded_transfer_route_audit": route_audit,
            "bounded_transfer_rejection_counts": dict(rejection_counts),
            "next_evidence_required": (
                "A promising exposed result permits only a frozen, newly "
                "predeclared confirmation screen."
            ),
        },
    )
    write_object(root / "aggregate.json", payload)

    lines = [
        "# Candidate 15 V7 bounded residual information transfer",
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
        f"- initiative_activations: `{payload.get('initiative_activations')}`",
        f"- response_rejections: `{payload.get('response_rejections')}`",
        f"- submitted_bounded_transfer_plans: `{route_audit['submitted_plans']}`",
        f"- bounded_transfer_route_violations: `{route_audit['violation_count']}`",
        "",
        "## Interval evidence",
    ]
    for record in payload.get("intervals", []):
        event_types = record.get("diagnostics", {}).get("event_type_counts", {})
        reasons = record.get("diagnostics", {}).get("reason_code_counts", {})
        lines.append(
            f"- {record['interval']} ({record['start']}): "
            f"daily_geo={record['daily_geometric_growth']}, "
            f"trades={record['closed_trades']}, W/L={record['wins']}/{record['losses']}, "
            f"transfer_states={event_types.get('QHI_V7_BOUNDED_TRANSFER_STATE_QUALIFIED', 0)}, "
            f"geometry_rejections={reasons.get('QHI_V7_BOUNDED_TRANSFER_GEOMETRY_UNRESOLVED', 0)}"
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
            "E01-E06 are exposed controlled-development intervals. V7 can only "
            "reject or improve the bounded-transfer mechanism; it cannot support "
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
