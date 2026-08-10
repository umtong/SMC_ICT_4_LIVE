#!/usr/bin/env python3
"""Decompose all observed 4h jump candidates and the actually traded account."""
from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any


def number(value: Any) -> float:
    if value is None:
        return math.nan
    match = re.search(r"[-+]?\d[\d_,]*(?:\.\d+)?", str(value))
    return (
        float(match.group(0).replace(",", "").replace("_", ""))
        if match is not None
        else math.nan
    )


def quantiles(values: list[float]) -> dict[str, float | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {"min": None, "q25": None, "median": None, "q75": None, "max": None}

    def q(frac: float) -> float:
        position = frac * (len(clean) - 1)
        lo = int(math.floor(position))
        hi = int(math.ceil(position))
        if lo == hi:
            return clean[lo]
        weight = position - lo
        return clean[lo] * (1.0 - weight) + clean[hi] * weight

    return {
        "min": clean[0],
        "q25": q(0.25),
        "median": q(0.50),
        "q75": q(0.75),
        "max": clean[-1],
    }


def stats(rows: list[dict[str, Any]], key: str = "exit_net_r") -> dict[str, Any]:
    values = [
        float(row[key])
        for row in rows
        if row.get(key) is not None and math.isfinite(float(row[key]))
    ]
    positive = [value for value in values if value > 0.0]
    negative = [value for value in values if value < 0.0]
    return {
        "episodes": len(rows),
        "resolved": len(values),
        "positive": len(positive),
        "negative": len(negative),
        "positive_share": len(positive) / len(values) if values else 0.0,
        "mean_r": sum(values) / len(values) if values else None,
        "sum_r": sum(values),
        "profit_factor_r": (
            sum(positive) / -sum(negative)
            if negative
            else None
        ),
        "r_distribution": quantiles(values),
        "mfe_fraction": quantiles(
            [float(row.get("mfe_fraction", math.nan)) for row in rows]
        ),
        "mae_fraction": quantiles(
            [float(row.get("mae_fraction", math.nan)) for row in rows]
        ),
    }


def actual_map(scenarios: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for row in scenarios:
        result[(str(row["symbol"]), int(row["episode_ts"]))] = row
    return result


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source = args.source
    out = args.out
    required = [
        source / "jump_candidate_audit.json",
        source / "closed_scenarios.json",
        source / "metrics.json",
        source / "strategy_diagnostics.json",
        source / "positions.csv",
        source / "orders.csv",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        write_json(out / "implementation_failure.json", {"missing": missing})
        return 1

    audit = json.loads((source / "jump_candidate_audit.json").read_text())
    scenarios = json.loads((source / "closed_scenarios.json").read_text())
    metrics = json.loads((source / "metrics.json").read_text())
    diagnostics = json.loads((source / "strategy_diagnostics.json").read_text())
    actual = actual_map(scenarios)

    for row in audit:
        key = (str(row["symbol"]), int(row["episode_ts"]))
        traded = actual.get(key)
        row["actual_executed"] = traded is not None
        row["actual_scenario_id"] = (
            None if traded is None else traded.get("scenario_id")
        )
        row["actual_realized_pnl"] = (
            None if traded is None else number(traded.get("realized_pnl"))
        )
        risk = (
            math.nan
            if traded is None
            else float(traded.get("risk_budget") or math.nan)
        )
        row["actual_after_cost_r"] = (
            None
            if traded is None
            or not math.isfinite(risk)
            or risk <= 0.0
            else float(row["actual_realized_pnl"]) / risk
        )
        row["shadow_actual_r_error"] = (
            None
            if row.get("actual_after_cost_r") is None
            or row.get("exit_net_r") is None
            else float(row["actual_after_cost_r"]) - float(row["exit_net_r"])
        )

    resolved = [
        row
        for row in audit
        if not bool(row.get("censored"))
        and row.get("exit_net_r") is not None
    ]
    executed = [row for row in resolved if row["actual_executed"]]
    nonexecuted = [row for row in resolved if not row["actual_executed"]]
    router_selected = [row for row in resolved if row.get("router_selected")]
    router_rejected = [row for row in resolved if not row.get("router_selected")]
    blocked = [
        row
        for row in resolved
        if row.get("slot_state_at_boundary") != "FLAT"
    ]
    flat_candidates = [
        row
        for row in resolved
        if row.get("slot_state_at_boundary") == "FLAT"
    ]

    by_boundary: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in resolved:
        by_boundary[int(row["episode_ts"])].append(row)
    collisions = [rows for rows in by_boundary.values() if len(rows) > 1]
    collision_records = []
    for rows in collisions:
        selected = next((row for row in rows if row.get("router_selected")), None)
        best = max(rows, key=lambda row: float(row["exit_net_r"]))
        collision_records.append(
            {
                "episode_ts": int(rows[0]["episode_ts"]),
                "candidates": len(rows),
                "selected_symbol": (
                    None if selected is None else selected["symbol"]
                ),
                "selected_r": (
                    None if selected is None else selected["exit_net_r"]
                ),
                "best_symbol": best["symbol"],
                "best_r": best["exit_net_r"],
                "selected_was_best": (
                    selected is not None
                    and selected["candidate_id"] == best["candidate_id"]
                ),
                "candidate_rows": [
                    {
                        "symbol": row["symbol"],
                        "side": row["side"],
                        "router_score": row["router_score"],
                        "causal_zscore": (
                            row.get("diagnostics") or {}
                        ).get("causal_zscore"),
                        "residual_z": (
                            row.get("diagnostics") or {}
                        ).get("cross_sectional_residual_z"),
                        "absolute_return": (
                            row.get("diagnostics") or {}
                        ).get("absolute_return"),
                        "router_selected": row["router_selected"],
                        "slot_state": row["slot_state_at_boundary"],
                        "actual_executed": row["actual_executed"],
                        "outcome": row["outcome"],
                        "exit_net_r": row["exit_net_r"],
                    }
                    for row in sorted(rows, key=lambda item: item["symbol"])
                ],
            }
        )

    by_slot = {
        state: stats([row for row in resolved if row["slot_state_at_boundary"] == state])
        for state in sorted({str(row["slot_state_at_boundary"]) for row in resolved})
    }
    by_symbol = {
        symbol: stats([row for row in resolved if row["symbol"] == symbol])
        for symbol in sorted({str(row["symbol"]) for row in resolved})
    }
    by_outcome = Counter(str(row.get("outcome")) for row in audit)
    actual_errors = [
        float(row["shadow_actual_r_error"])
        for row in executed
        if row.get("shadow_actual_r_error") is not None
    ]

    summary = {
        "engine": "NautilusTrader actual account plus non-trading shadow episode audit",
        "binary_gate": False,
        "all_candidates": stats(resolved),
        "candidate_rows_total": len(audit),
        "censored_candidates": sum(bool(row.get("censored")) for row in audit),
        "actual_completed_trades": len(scenarios),
        "actual_metrics": {
            key: metrics.get(key)
            for key in (
                "starting_nav",
                "ending_nav",
                "total_return",
                "geometric_daily_growth",
                "max_drawdown",
                "trades",
                "wins",
                "losses",
                "profit_factor",
            )
        },
        "actual_executed_shadow": stats(executed),
        "nonexecuted_shadow": stats(nonexecuted),
        "router_selected_shadow": stats(router_selected),
        "router_rejected_shadow": stats(router_rejected),
        "blocked_by_account_slot": stats(blocked),
        "flat_boundary_candidates": stats(flat_candidates),
        "by_slot_state": by_slot,
        "by_symbol": by_symbol,
        "outcome_counts": dict(sorted(by_outcome.items())),
        "collision_boundaries": {
            "count": len(collisions),
            "selected_best_count": sum(
                bool(row["selected_was_best"]) for row in collision_records
            ),
            "selected_best_share": (
                sum(bool(row["selected_was_best"]) for row in collision_records)
                / len(collision_records)
                if collision_records
                else None
            ),
            "records": collision_records,
        },
        "shadow_actual_consistency": {
            "matched_executed": len(executed),
            "r_error_distribution": quantiles(actual_errors),
            "mean_absolute_r_error": (
                sum(abs(value) for value in actual_errors) / len(actual_errors)
                if actual_errors
                else None
            ),
            "warning": (
                "Shadow outcomes diagnose opportunity geometry only; actual "
                "Nautilus fills/account results remain authoritative."
            ),
        },
        "strategy_diagnostics": diagnostics,
        "end_validity": {
            key: value
            for key, value in (metrics.get("checks") or {}).items()
            if key
            in {
                "closed_position_rows_match_trade_count",
                "no_open_positions_at_end",
                "no_active_orders_at_end",
                "single_entry_intent",
                "single_position",
                "no_global_position_violation",
            }
        },
    }

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "summary.json", summary)
    write_json(out / "episode_rows.json", audit)
    write_json(out / "collision_episodes.json", collision_records)
    for name in (
        "metrics.json",
        "strategy_diagnostics.json",
        "closed_scenarios.json",
        "scenario_events.jsonl",
        "positions.csv",
        "orders.csv",
        "run.json",
        "data_manifest.json",
        "jump_candidate_audit.json",
    ):
        candidate = source / name
        if candidate.is_file():
            shutil.copy2(candidate, out / name)

    lines = [
        "# Candidate 57 jump all-candidate causal episode audit",
        "",
        "This is a forensic decomposition, not a pass/fail gate. Actual account "
        "performance remains the Nautilus result; shadow paths only reveal "
        "what the source selector, arbitration and occupied global slot hid.",
        "",
        f"- candidate rows: {len(audit)}",
        f"- resolved candidate rows: {len(resolved)}",
        f"- censored candidate rows: {summary['censored_candidates']}",
        f"- actual completed trades: {len(scenarios)}",
        f"- collision boundaries: {len(collisions)}",
        f"- candidates observed while the account slot was occupied: {len(blocked)}",
        "",
        "Read `episode_rows.json` and `collision_episodes.json` before changing "
        "the selector, stop or account arbitration.",
    ]
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
