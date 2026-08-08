#!/usr/bin/env python3
"""W2 mechanism-first LCOR reaccept-failure campaign."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from run_liquidation_cash_ownership_matrix import (
    _aggregate,
    _base,
    _counts,
    _long_gate,
)


VARIANTS = (
    (
        "lcor_reaccept_failure_cross_venue_flow",
        (
            "Eligible: record the first accepted-ownership failure without "
            "trading, require a later synchronized original-direction "
            "reacceptance, and trade only a strictly later second synchronized "
            "failure with adverse cash and perpetual initiative."
        ),
        {
            "lcor_enable_failure_reversal": False,
            "lcor_enable_reaccept_failure_reversal": True,
            "lcor_failure_require_spot_flow": True,
            "lcor_failure_require_perp_flow": True,
            "lcor_failure_require_directional_body": True,
        },
        True,
    ),
    (
        "lcor_reaccept_failure_price_only_attribution",
        (
            "Attribution only: preserve first-failure, reacceptance and "
            "second-failure chronology but remove cash-flow, perpetual-flow "
            "and directional-body requirements from failure classification."
        ),
        {
            "lcor_enable_failure_reversal": False,
            "lcor_enable_reaccept_failure_reversal": True,
            "lcor_failure_require_spot_flow": False,
            "lcor_failure_require_perp_flow": False,
            "lcor_failure_require_directional_body": False,
        },
        False,
    ),
)


def _configured(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = _base(raw)
    config["candidate"] = "candidate-06-lcor-reaccept-failure-v8.2"
    config["version"] = "8.2.0"
    config["hypothesis"] = (
        "The first failure of a post-liquidation cash/perpetual ownership "
        "transfer can be a false reversal. A later synchronized recovery of "
        "the original direction followed by a strictly later second "
        "cross-venue failure defines a new auction leg with better causal "
        "geometry: the recovery-test extreme supplies invalidation and "
        "unchanged live opposite objectives supply reward."
    )
    config["validation"]["stage"] = (
        "lcor_reaccept_failure_w2_mechanism_first"
    )
    return config


def _run(
    config_path: Path,
    output: Path,
    week_index: int,
    candidate_dir: Path,
    repository: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(
                candidate_dir
                / "run_liquidation_cash_reaccept_failure_validation.py"
            ),
            "--config",
            str(config_path),
            "--output",
            str(output),
            "--week-index",
            str(week_index),
            "--allow-gate-fail",
        ],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    record: dict[str, Any] = {
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-5000:],
        "stderr_tail": completed.stderr[-16000:],
    }
    metrics_path = output / "metrics.json"
    if metrics_path.exists():
        record["metrics"] = json.loads(
            metrics_path.read_text(encoding="utf-8"),
        )
        record["gate_passed"] = bool(
            record["metrics"].get("gate_passed"),
        )
    else:
        record["gate_passed"] = False
        error_path = output / "errors.log"
        if error_path.exists():
            record["error"] = error_path.read_text(
                encoding="utf-8",
                errors="replace",
            )[-16000:]
    return record


def _mechanism_feasible(record: Mapping[str, Any]) -> bool:
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        return False
    counts = dict(record.get("causal_counts", {}))
    reasons = dict(counts.get("reason_counts", {}))
    diagnostics = dict(metrics.get("diagnostics", {}))
    return bool(
        reasons.get(
            "FIRST_CROSS_VENUE_OWNERSHIP_FAILURE_OBSERVED_"
            "AWAITING_ORIGINAL_REACCEPT",
            0,
        )
        >= 1
        and reasons.get(
            "ORIGINAL_DIRECTION_REACCEPTED_BOTH_BOUNDARIES_"
            "AFTER_FIRST_FAILURE",
            0,
        )
        >= 1
        and reasons.get(
            "REACCEPTED_OWNERSHIP_FAILED_AGAIN_WITH_OPPOSITE_INITIATIVE",
            0,
        )
        >= 1
        and reasons.get("LCOR_REACCEPT_FAILURE_ENTRY_ARMED", 0) >= 1
        and int(diagnostics.get("entries_submitted", 0)) >= 1
        and int(metrics.get("trades", 0)) >= 1
        and int(metrics.get("wins", 0)) >= 1
        and float(metrics.get("net_pnl_after_cost", 0.0)) > 0.0
        and float(metrics.get("max_drawdown_nav", 1.0)) <= 0.25
        and not metrics.get("errors")
    )


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
    if (
        reasons.get(
            "FIRST_CROSS_VENUE_OWNERSHIP_FAILURE_OBSERVED_"
            "AWAITING_ORIGINAL_REACCEPT",
            0,
        )
        == 0
    ):
        classification = "NO_FIRST_ACCEPTED_OWNERSHIP_FAILURE"
    elif (
        reasons.get(
            "ORIGINAL_DIRECTION_REACCEPTED_BOTH_BOUNDARIES_"
            "AFTER_FIRST_FAILURE",
            0,
        )
        == 0
    ):
        classification = "FIRST_FAILURE_WITHOUT_ORIGINAL_REACCEPT"
    elif (
        reasons.get(
            "REACCEPTED_OWNERSHIP_FAILED_AGAIN_WITH_OPPOSITE_INITIATIVE",
            0,
        )
        == 0
    ):
        classification = "REACCEPT_HELD_WITHOUT_SECOND_FAILURE"
    elif reasons.get("LCOR_REACCEPT_FAILURE_ENTRY_ARMED", 0) == 0:
        classification = "SECOND_FAILURE_WITHOUT_LIVE_OBJECTIVE"
    elif int(diagnostics.get("entries_submitted", 0)) == 0:
        classification = "SECOND_FAILURE_ENTRY_EXECUTION_ABSTAINED"
    elif int(metrics.get("trades", 0)) == 0:
        classification = "ORDER_SUBMITTED_WITHOUT_CLOSED_TRADE"
    elif _mechanism_feasible(record):
        classification = "W2_REACCEPT_FAILURE_MECHANISM_PASSED"
    elif float(metrics.get("net_pnl_after_cost", 0.0)) <= 0.0:
        classification = "NEGATIVE_COST_AFTER_REACCEPT_FAILURE"
    else:
        classification = "POSITIVE_BUT_MECHANISM_CONTRACT_INCOMPLETE"
    return {
        "classification": classification,
        "mechanism_gate_passed": _mechanism_feasible(record),
        "geometric_daily_nav_growth": metrics.get(
            "geometric_daily_nav_growth",
        ),
        "net_pnl_after_cost": metrics.get("net_pnl_after_cost"),
        "trades": metrics.get("trades"),
        "wins": metrics.get("wins"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown_nav": metrics.get("max_drawdown_nav"),
        "gate_failures": metrics.get("gate_failures", []),
        "entry_abstentions": diagnostics.get(
            "entry_abstentions",
            {},
        ),
        "entries_submitted": diagnostics.get(
            "entries_submitted",
            0,
        ),
        "signals_armed": diagnostics.get("signals_armed", 0),
        "causal_counts": counts,
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v8.2 LCOR Reaccept-Failure Router",
        "",
        f"Terminal status: `{summary.get('terminal_status')}`",
        (
            f"Selected: `{summary.get('selected')}`"
            if summary.get("selected")
            else "Selected: none"
        ),
        (
            "W2 mechanism expansion authorized: "
            f"`{summary.get('w2_mechanism_expansion_authorized')}`"
        ),
        (
            "Long evaluation authorized: "
            f"`{summary.get('long_evaluation_authorized')}`"
        ),
        "",
        (
            "|variant|week|eligible|mechanism|geom/day|trades|wins|"
            "win rate|PF|max DD|diagnosis|"
        ),
        (
            "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|"
        ),
    ]
    for record in [
        *summary.get("w2_results", []),
        *summary.get("frozen_validation", []),
    ]:
        metrics = record.get("metrics", {})
        diagnosis = record.get("diagnosis", {})
        lines.append(
            (
                "|{name}|{week}|{eligible}|{mechanism}|{growth:.6%}|"
                "{trades}|{wins}|{win:.2%}|{pf}|{dd:.2%}|"
                "{diagnosis}|"
            ).format(
                name=record.get("name"),
                week=int(record.get("week_index", 0)) + 1,
                eligible=record.get("eligible_for_selection"),
                mechanism=diagnosis.get("mechanism_gate_passed"),
                growth=float(
                    metrics.get("geometric_daily_nav_growth", 0.0),
                ),
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
                "## Frozen three-week aggregate",
                "",
                (
                    "- Evaluation days: "
                    f"`{aggregate.get('evaluation_days')}`"
                ),
                f"- Trades: `{aggregate.get('trades')}`",
                f"- Wins: `{aggregate.get('wins')}`",
                (
                    "- Win rate: "
                    f"`{float(aggregate.get('win_rate', 0.0)):.2%}`"
                ),
                (
                    "- Pooled geometric NAV growth/day: "
                    f"`{float(aggregate.get('pooled_geometric_daily_nav_growth', 0.0)):.6%}`"
                ),
                (
                    "- Positive weeks: "
                    f"`{aggregate.get('positive_weeks')}/3`"
                ),
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
            (
                "- The first accepted cross-venue ownership failure is "
                "context only and cannot trade."
            ),
            (
                "- The original direction must later reaccept both cash and "
                "perpetual boundaries with matching completed-bar flow."
            ),
            (
                "- Only a strictly later second cross-venue failure may open "
                "the reversal leg."
            ),
            (
                "- The recovery-test extreme plus the unchanged ATR buffer "
                "defines invalidation."
            ),
            (
                "- The same live opposite objective family, structural RR, "
                "net delayed RR, fees and slippage remain unchanged."
            ),
            (
                "- W2 uses a mechanism gate only to authorize untouched W1 "
                "and W3 execution; it is not a success claim."
            ),
            (
                "- The frozen three-week aggregate retains the existing "
                ">=1% geometric daily NAV, trade count, win-rate, "
                "drawdown and concentration gate."
            ),
            (
                "- The price-only branch is attribution evidence and cannot "
                "select."
            ),
            (
                "- Orders, fills, positions, commissions and whole-account "
                "NAV remain in NautilusTrader."
            ),
            (
                "- Planned loss remains three percent of current whole-account "
                "NAV and one global slot remains unchanged."
            ),
        ],
    )
    if summary.get("error"):
        lines.extend(
            [
                "",
                "## Error",
                "",
                "```text",
                str(summary["error"])[-16000:],
                "```",
            ],
        )
    return "\n".join(lines) + "\n"


def _write(root: Path, summary: Mapping[str, Any]) -> None:
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "SUMMARY.md").write_text(
        _render(summary),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "artifacts/candidate-06/"
            "lcor-reaccept-failure-w2-first"
        ),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = json.loads(
        (candidate_dir / "config.json").read_text(encoding="utf-8"),
    )
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
        record = _run(
            config_path,
            run_output,
            1,
            candidate_dir,
            repository,
        )
        record.update(
            {
                "name": name,
                "description": description,
                "eligible_for_selection": eligible,
                "week_index": 1,
                "config_path": str(
                    config_path.relative_to(repository),
                ),
                "run_output": str(
                    run_output.relative_to(repository),
                ),
                "causal_counts": _counts(run_output),
            },
        )
        record["diagnosis"] = _diagnose(record)
        w2.append(record)

    base_summary: dict[str, Any] = {
        "candidate": "candidate-06-lcor-reaccept-failure-v8.2",
        "design": (
            "accepted liquidation relay -> first failure (no trade) -> "
            "original cross-venue reaccept -> strictly later second "
            "cross-venue failure -> reversal with recovery-test invalidation"
        ),
        "variant_priority": [VARIANTS[0][0]],
        "selection_rule": (
            "The full flow-confirmed branch may unlock frozen W1/W3 only "
            "after W2 produces the complete chronology, submits at least one "
            "Nautilus order, closes at least one post-cost winning trade, "
            "has positive net PnL, <=25% drawdown and no errors. This is a "
            "mechanism gate, not the final target gate."
        ),
        "w2_results": w2,
        "frozen_validation": [],
        "selected": None,
        "aggregate": None,
        "w2_mechanism_expansion_authorized": False,
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
            "error": (
                "At least one W2 reaccept-failure variant did not "
                "produce valid Nautilus metrics."
            ),
        }
        _write(root, summary)
        return 5

    selected_record = w2[0]
    if not _mechanism_feasible(selected_record):
        summary = {
            **base_summary,
            "terminal_status": (
                "W2_LCOR_REACCEPT_FAILURE_MECHANISM_REJECTED"
            ),
        }
        _write(root, summary)
        return 2

    selected = str(selected_record["name"])
    locked = copy.deepcopy(configs[selected])
    locked["validation"]["stage"] = (
        "lcor_reaccept_failure_frozen_three_week"
    )
    locked_path = (
        candidate_dir / "config.lcor_reaccept_failure.locked.json"
    )
    locked_path.write_text(
        json.dumps(locked, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    frozen: list[dict[str, Any]] = []
    for week_index in (0, 2):
        config_path = (
            root
            / "configs"
            / f"{selected}-week-{week_index + 1}.json"
        )
        config_path.write_text(
            json.dumps(locked, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_output = (
            root
            / "runs"
            / selected
            / f"week-{week_index + 1}"
        )
        record = _run(
            config_path,
            run_output,
            week_index,
            candidate_dir,
            repository,
        )
        record.update(
            {
                "name": selected,
                "description": VARIANTS[0][1],
                "eligible_for_selection": True,
                "week_index": week_index,
                "config_path": str(
                    config_path.relative_to(repository),
                ),
                "run_output": str(
                    run_output.relative_to(repository),
                ),
                "causal_counts": _counts(run_output),
            },
        )
        record["diagnosis"] = _diagnose(record)
        frozen.append(record)
        if (
            int(record.get("returncode", 1)) != 0
            or not isinstance(record.get("metrics"), Mapping)
        ):
            summary = {
                **base_summary,
                "selected": selected,
                "locked_config": str(
                    locked_path.relative_to(repository),
                ),
                "frozen_validation": frozen,
                "w2_mechanism_expansion_authorized": True,
                "terminal_status": (
                    "IMPLEMENTATION_OR_DATA_FAILURE_ON_FROZEN_WEEK"
                ),
                "error": (
                    record.get("error")
                    or record.get("stderr_tail")
                ),
            }
            _write(root, summary)
            return 5

    ordered = [
        next(
            item for item in frozen if item["week_index"] == 0
        ),
        selected_record,
        next(
            item for item in frozen if item["week_index"] == 2
        ),
    ]
    aggregate = _aggregate(ordered)
    authorized = _long_gate(aggregate)
    summary = {
        **base_summary,
        "selected": selected,
        "locked_config": str(
            locked_path.relative_to(repository),
        ),
        "frozen_validation": frozen,
        "aggregate": aggregate,
        "w2_mechanism_expansion_authorized": True,
        "long_evaluation_authorized": authorized,
        "terminal_status": (
            "FROZEN_THREE_WEEK_LCOR_REACCEPT_FAILURE_GATE_PASSED"
            if authorized
            else (
                "FROZEN_THREE_WEEK_LCOR_REACCEPT_FAILURE_"
                "TARGET_NOT_REPLICATED"
            )
        ),
    }
    _write(root, summary)
    return 0 if authorized else 3


if __name__ == "__main__":
    raise SystemExit(main())
