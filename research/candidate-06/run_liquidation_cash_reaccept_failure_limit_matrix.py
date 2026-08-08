#!/usr/bin/env python3
"""W2-first LCOR reaccept-failure passive half-back execution campaign."""

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
)
from run_liquidation_cash_reaccept_failure_matrix import (
    _mechanism_feasible,
    _run,
)


VARIANTS = (
    (
        "lcor_reaccept_failure_half_back_limit",
        (
            "Eligible: preserve the full first-failure, original reacceptance "
            "and second-failure state sequence, then place one native post-only "
            "limit at the exact midpoint of the completed second-failure close "
            "and the pre-existing failed ownership boundary."
        ),
        {
            "lcor_reaccept_failure_entry_execution": (
                "FAILED_BOUNDARY_HALF_BACK_LIMIT"
            ),
        },
        True,
    ),
    (
        "lcor_reaccept_failure_market_attribution",
        (
            "Attribution only: preserve the identical causal signal, stop and "
            "target but submit at the completed second-failure close as a "
            "market entry."
        ),
        {
            "lcor_reaccept_failure_entry_execution": (
                "MARKET_ON_SECOND_FAILURE"
            ),
        },
        False,
    ),
)


def _configured(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = _base(raw)
    config["candidate"] = (
        "candidate-06-lcor-reaccept-failure-half-back-v8.3"
    )
    config["version"] = "8.3.0"
    config["hypothesis"] = (
        "The reaccept-failure state is directionally useful but its completed "
        "second-failure close has insufficient post-cost geometry. Without "
        "changing context, direction, stop or objective, a passive retest at "
        "the exact equilibrium between the second-failure close and the "
        "already-known failed ownership boundary can restore executable net "
        "reward/risk. The order expires with the same fixed LCOR auction."
    )
    config["validation"]["stage"] = (
        "lcor_reaccept_failure_half_back_w2_first"
    )
    config["logic"].update(
        {
            "lcor_enable_failure_reversal": False,
            "lcor_enable_reaccept_failure_reversal": True,
            "lcor_failure_require_spot_flow": True,
            "lcor_failure_require_perp_flow": True,
            "lcor_failure_require_directional_body": True,
        },
    )
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
    entries = int(diagnostics.get("entries_submitted", 0))
    trades = int(metrics.get("trades", 0))
    net = float(metrics.get("net_pnl_after_cost", 0.0))
    if (
        reasons.get(
            "REACCEPTED_OWNERSHIP_FAILED_AGAIN_WITH_OPPOSITE_INITIATIVE",
            0,
        )
        == 0
    ):
        classification = "NO_REACCEPT_FAILURE_SIGNAL"
    elif reasons.get("LCOR_REACCEPT_FAILURE_ENTRY_ARMED", 0) == 0:
        classification = "SECOND_FAILURE_WITHOUT_LIVE_OBJECTIVE"
    elif entries == 0:
        classification = "PLACEMENT_REJECTED_BEFORE_NATIVE_ORDER"
    elif trades == 0:
        classification = "PASSIVE_LIMIT_UNFILLED_OR_UNCLOSED"
    elif _mechanism_feasible(record):
        classification = "W2_HALF_BACK_EXECUTION_MECHANISM_PASSED"
    elif net <= 0.0:
        classification = "FILLED_HALF_BACK_NEGATIVE_AFTER_COST"
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
        "entries_submitted": entries,
        "signals_armed": diagnostics.get("signals_armed", 0),
        "causal_counts": counts,
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v8.3 LCOR Reaccept-Failure Half-Back",
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
            "## Fixed causal and execution contract",
            "",
            (
                "- The LCOR v8.2 context, direction, first failure, "
                "reacceptance and second failure are unchanged."
            ),
            (
                "- The second-failure close remains the signal timestamp; "
                "the event bar cannot fill the later limit retroactively."
            ),
            (
                "- Entry is the exact 50% equilibrium between that completed "
                "close and the pre-existing failed ownership boundary."
            ),
            (
                "- The limit is post-only and expires at the end of the same "
                "15-minute LCOR auction."
            ),
            (
                "- Recovery-test invalidation, live objective, structural RR, "
                "minimum 0.60 post-cost delayed RR, fees and one-tick "
                "slippage are unchanged."
            ),
            (
                "- The market-at-second-failure branch is attribution only "
                "and cannot select."
            ),
            (
                "- W2 may only unlock untouched W1/W3. Final success still "
                "requires the existing frozen three-week >=1% geometric daily "
                "NAV and robustness gate."
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
            "lcor-reaccept-failure-halfback-w2-first"
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
        "candidate": (
            "candidate-06-lcor-reaccept-failure-half-back-v8.3"
        ),
        "design": (
            "v8.2 frozen reaccept-failure signal -> native post-only "
            "failed-boundary half-back limit -> same-auction expiry -> "
            "unchanged recovery-test stop and live objective"
        ),
        "variant_priority": [VARIANTS[0][0]],
        "selection_rule": (
            "The full half-back limit branch may unlock frozen W1/W3 only "
            "after W2 submits a native order, closes at least one post-cost "
            "winning trade, has positive net PnL, <=25% drawdown and no "
            "errors. The market branch cannot select."
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
                "At least one W2 half-back variant did not produce valid "
                "Nautilus metrics."
            ),
        }
        _write(root, summary)
        return 5

    selected_record = w2[0]
    if not _mechanism_feasible(selected_record):
        summary = {
            **base_summary,
            "terminal_status": (
                "W2_LCOR_HALF_BACK_EXECUTION_MECHANISM_REJECTED"
            ),
        }
        _write(root, summary)
        return 2

    selected = str(selected_record["name"])
    locked = copy.deepcopy(configs[selected])
    locked["validation"]["stage"] = (
        "lcor_reaccept_failure_half_back_frozen_three_week"
    )
    locked_path = (
        candidate_dir
        / "config.lcor_reaccept_failure_halfback.locked.json"
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
            "FROZEN_THREE_WEEK_LCOR_HALF_BACK_GATE_PASSED"
            if authorized
            else (
                "FROZEN_THREE_WEEK_LCOR_HALF_BACK_"
                "TARGET_NOT_REPLICATED"
            )
        ),
    }
    _write(root, summary)
    return 0 if authorized else 3


if __name__ == "__main__":
    raise SystemExit(main())
