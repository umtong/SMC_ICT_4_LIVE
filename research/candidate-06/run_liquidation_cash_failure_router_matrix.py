#!/usr/bin/env python3
"""W2-first LCOR failed-ownership router campaign."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping

from run_liquidation_cash_ownership_matrix import (
    _aggregate,
    _base,
    _counts,
    _long_gate,
    _run,
    _week_feasible,
)


VARIANTS = (
    (
        "lcor_failure_router_cross_venue_flow",
        (
            "Eligible: after later spot and perpetual acceptance of a liquidation-led "
            "boundary, route only a simultaneous cash-boundary loss with adverse cash "
            "flow and a perpetual boundary failure with matching opposite body, flow "
            "and close location into a new reversal leg."
        ),
        {
            "lcor_enable_failure_reversal": True,
            "lcor_failure_require_spot_flow": True,
            "lcor_failure_require_perp_flow": True,
            "lcor_failure_require_directional_body": True,
        },
        True,
    ),
    (
        "lcor_failure_router_price_only_attribution",
        (
            "Attribution only: preserve the accepted cross-venue chronology and "
            "boundary failure, but remove the cash-flow, perpetual-flow and body "
            "requirements from the reversal classification."
        ),
        {
            "lcor_enable_failure_reversal": True,
            "lcor_failure_require_spot_flow": False,
            "lcor_failure_require_perp_flow": False,
            "lcor_failure_require_directional_body": False,
        },
        False,
    ),
)


def _configured(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = _base(raw)
    config["candidate"] = "candidate-06-lcor-failure-router-v8.1"
    config["version"] = "8.1.0"
    config["hypothesis"] = (
        "A liquidation-led boundary which is accepted first by cash and then by "
        "perpetuals is not always continuation. If both markets subsequently fail "
        "that accepted ownership with opposite initiative, the failure defines a "
        "new auction leg whose completed failure bar supplies entry, invalidation "
        "and still-live opposite liquidity objectives."
    )
    config["validation"]["stage"] = "lcor_failure_router_w2_first"
    return config


def _diagnose(record: Mapping[str, Any]) -> dict[str, Any]:
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        return {
            "classification": "IMPLEMENTATION_OR_DATA_FAILURE",
            "error": record.get("error") or record.get("stderr_tail"),
        }
    diagnostics = dict(metrics.get("diagnostics", {}))
    counts = dict(record.get("causal_counts", {}))
    reasons = dict(counts.get("reason_counts", {}))
    trades = int(metrics.get("trades", 0))
    growth = float(metrics.get("geometric_daily_nav_growth", 0.0))
    pf = metrics.get("profit_factor")
    if trades == 0:
        if reasons.get(
            "CASH_AND_PERPETUAL_ACCEPTANCE_FAILED_WITH_OPPOSITE_INITIATIVE",
            0,
        ) == 0:
            classification = "NO_CROSS_VENUE_ACCEPTANCE_FAILURE_REVERSAL"
        elif reasons.get("LCOR_FAILURE_REVERSAL_ENTRY_ARMED", 0) == 0:
            classification = "FAILURE_CONFIRMED_BUT_NO_STILL_LIVE_OBJECTIVE"
        elif int(diagnostics.get("entries_submitted", 0)) == 0:
            classification = "FAILURE_ENTRY_ARMED_BUT_EXECUTION_ABSTAINED"
        else:
            classification = "ORDER_SUBMITTED_WITHOUT_CLOSED_TRADE"
    elif growth <= 0.0 or (pf is not None and float(pf) < 1.0):
        classification = "NEGATIVE_COST_AFTER_FAILURE_REVERSAL_EXPECTANCY"
    elif _week_feasible(record):
        classification = "W2_FAILURE_ROUTER_FEASIBILITY_PASSED"
    else:
        classification = "POSITIVE_BUT_W2_FAILURE_ROUTER_INCOMPLETE"
    return {
        "classification": classification,
        "geometric_daily_nav_growth": growth,
        "trades": trades,
        "wins": metrics.get("wins"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": pf,
        "max_drawdown_nav": metrics.get("max_drawdown_nav"),
        "gate_failures": metrics.get("gate_failures", []),
        "entry_abstentions": diagnostics.get("entry_abstentions", {}),
        "entries_submitted": diagnostics.get("entries_submitted", 0),
        "signals_armed": diagnostics.get("signals_armed", 0),
        "causal_counts": counts,
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v8.1 LCOR Failed-Ownership Router",
        "",
        f"Terminal status: `{summary.get('terminal_status')}`",
        (
            f"Selected: `{summary.get('selected')}`"
            if summary.get("selected")
            else "Selected: none"
        ),
        f"Long evaluation authorized: `{summary.get('long_evaluation_authorized')}`",
        "",
        "|variant|week|eligible|geom/day|trades|wins|win rate|PF|max DD|diagnosis|",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for record in [
        *summary.get("w2_results", []),
        *summary.get("frozen_validation", []),
    ]:
        metrics = record.get("metrics", {})
        diagnosis = record.get("diagnosis", {})
        lines.append(
            (
                "|{name}|{week}|{eligible}|{growth:.6%}|{trades}|{wins}|"
                "{win:.2%}|{pf}|{dd:.2%}|{diagnosis}|"
            ).format(
                name=record.get("name"),
                week=int(record.get("week_index", 0)) + 1,
                eligible=record.get("eligible_for_selection"),
                growth=float(metrics.get("geometric_daily_nav_growth", 0.0)),
                trades=metrics.get("trades"),
                wins=metrics.get("wins"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                diagnosis=diagnosis.get("classification"),
            ),
        )
    aggregate = summary.get("aggregate")
    if aggregate:
        lines.extend(
            [
                "",
                "## Frozen aggregate",
                "",
                f"- Evaluation days: `{aggregate.get('evaluation_days')}`",
                f"- Trades: `{aggregate.get('trades')}`",
                f"- Wins: `{aggregate.get('wins')}`",
                f"- Win rate: `{float(aggregate.get('win_rate', 0.0)):.2%}`",
                (
                    "- Pooled geometric NAV growth/day: "
                    f"`{float(aggregate.get('pooled_geometric_daily_nav_growth', 0.0)):.6%}`"
                ),
                f"- Positive weeks: `{aggregate.get('positive_weeks')}/3`",
                (
                    "- Worst weekly max drawdown: "
                    f"`{float(aggregate.get('worst_weekly_max_drawdown', 0.0)):.2%}`"
                ),
            ],
        )
    lines.extend(
        [
            "",
            "## Fixed causal contract",
            "",
            "- The initiating OI contraction is compared only with prior completed OI losses.",
            "- Cash acceptance must be later than the liquidation-led event; perpetual acceptance must be later still.",
            "- A reversal exists only after the accepted cash boundary and accepted perpetual boundary both fail on a completed bar.",
            "- The eligible branch also requires adverse cash flow, matching perpetual flow, a directional body and opposite close location.",
            "- The failure bar opens a new auction leg; its extreme plus the unchanged ATR buffer defines invalidation.",
            "- Every target remains beyond the completed failure bar and must satisfy the unchanged structural and net RR contracts.",
            "- The price-only branch is attribution evidence and cannot select.",
            "- Orders, fills, fees, slippage, positions and whole-account NAV remain in NautilusTrader.",
            "- Planned loss remains three percent of current whole-account NAV and one global slot remains unchanged.",
        ],
    )
    if summary.get("error"):
        lines.extend(
            ["", "## Error", "", "```text", str(summary["error"])[-16000:], "```"],
        )
    return "\n".join(lines) + "\n"


def _write(root: Path, summary: Mapping[str, Any]) -> None:
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/lcor-failure-router-w2-first"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    base = _configured(raw)
    configs: dict[str, dict[str, Any]] = {}
    w2: list[dict[str, Any]] = []

    for name, description, patch, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(dict(patch))
        configs[name] = config
        config_dir = root / "configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{name}-week-2.json"
        config_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_output = root / "runs" / name / "week-2"
        record = _run(config_path, run_output, 1, candidate_dir, repository)
        record.update(
            {
                "name": name,
                "description": description,
                "eligible_for_selection": eligible,
                "week_index": 1,
                "config_path": str(config_path.relative_to(repository)),
                "run_output": str(run_output.relative_to(repository)),
                "causal_counts": _counts(run_output),
            },
        )
        record["diagnosis"] = _diagnose(record)
        w2.append(record)

    base_summary: dict[str, Any] = {
        "candidate": "candidate-06-lcor-failure-router-v8.1",
        "design": (
            "liquidation-led sweep -> later cash acceptance -> later perpetual "
            "acceptance -> simultaneous cross-venue ownership failure -> opposite "
            "initiative reversal with failure-bar invalidation and live objective"
        ),
        "variant_priority": [VARIANTS[0][0]],
        "selection_rule": (
            "the full cross-venue flow-confirmed failure branch must reach at least "
            "1% post-cost W2 geometric growth with at least two closed trades, one "
            "win, PF>1 and <=25% drawdown; price-only attribution cannot select"
        ),
        "w2_results": w2,
        "frozen_validation": [],
        "selected": None,
        "aggregate": None,
        "long_evaluation_authorized": False,
    }
    valid = all(
        int(record.get("returncode", 1)) == 0
        and isinstance(record.get("metrics"), Mapping)
        for record in w2
    )
    if not valid:
        summary = {
            **base_summary,
            "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE",
            "error": "At least one W2 failure-router variant did not produce valid Nautilus metrics.",
        }
        _write(root, summary)
        return 5

    selected_record = w2[0]
    if not _week_feasible(selected_record):
        summary = {
            **base_summary,
            "terminal_status": "W2_LCOR_FAILURE_ROUTER_GATE_FAILED",
        }
        _write(root, summary)
        return 2

    selected = str(selected_record["name"])
    locked = copy.deepcopy(configs[selected])
    locked["validation"]["stage"] = "lcor_failure_router_frozen_three_week"
    locked_path = candidate_dir / "config.lcor_failure_router.locked.json"
    locked_path.write_text(
        json.dumps(locked, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    frozen: list[dict[str, Any]] = []
    for week_index in (0, 2):
        config_path = root / "configs" / f"{selected}-week-{week_index + 1}.json"
        config_path.write_text(
            json.dumps(locked, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_output = root / "runs" / selected / f"week-{week_index + 1}"
        record = _run(config_path, run_output, week_index, candidate_dir, repository)
        record.update(
            {
                "name": selected,
                "description": VARIANTS[0][1],
                "eligible_for_selection": True,
                "week_index": week_index,
                "config_path": str(config_path.relative_to(repository)),
                "run_output": str(run_output.relative_to(repository)),
                "causal_counts": _counts(run_output),
            },
        )
        record["diagnosis"] = _diagnose(record)
        frozen.append(record)
        if int(record.get("returncode", 1)) != 0 or not isinstance(
            record.get("metrics"), Mapping,
        ):
            summary = {
                **base_summary,
                "selected": selected,
                "locked_config": str(locked_path.relative_to(repository)),
                "frozen_validation": frozen,
                "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE_ON_FROZEN_WEEK",
                "error": record.get("error") or record.get("stderr_tail"),
            }
            _write(root, summary)
            return 5

    ordered = [
        next(item for item in frozen if item["week_index"] == 0),
        selected_record,
        next(item for item in frozen if item["week_index"] == 2),
    ]
    aggregate = _aggregate(ordered)
    authorized = _long_gate(aggregate)
    summary = {
        **base_summary,
        "selected": selected,
        "locked_config": str(locked_path.relative_to(repository)),
        "frozen_validation": frozen,
        "aggregate": aggregate,
        "long_evaluation_authorized": authorized,
        "terminal_status": (
            "FROZEN_THREE_WEEK_LCOR_FAILURE_ROUTER_GATE_PASSED"
            if authorized
            else "FROZEN_THREE_WEEK_LCOR_FAILURE_ROUTER_TARGET_NOT_REPLICATED"
        ),
    }
    _write(root, summary)
    return 0 if authorized else 3


if __name__ == "__main__":
    raise SystemExit(main())
