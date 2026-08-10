#!/usr/bin/env python3
"""Multi-period directional anatomy for the 4h jump-reversion family.

This script reuses already-consumed all-candidate shadow paths.  It asks whether
fading upward jumps (short reversals) and fading downward jumps (long reversals)
are the same mechanism.  Simultaneous symbols at one completed four-hour
boundary remain one causal event; pooled candidate rows are descriptive only.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
OUT = HERE / "evidence" / "jump-side-anatomy-multiperiod-v1"
PERIODS = {
    "december_2025": (
        HERE / "evidence" / "jump-all-candidate-forensic-v1" / "episode_rows.json"
    ),
    "april_2026": (
        HERE
        / "evidence"
        / "jump-taker-alignment-fresh-v1"
        / "source_without_taker_filter"
        / "episode_rows.json"
    ),
    "june_2026": (
        HERE
        / "evidence"
        / "jump-state-arbitration-fresh-v1"
        / "source_max_z__no_taker"
        / "episode_rows.json"
    ),
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        float(row["exit_net_r"])
        for row in rows
        if row.get("exit_net_r") is not None
        and math.isfinite(float(row["exit_net_r"]))
    ]
    positive = [value for value in values if value > 0.0]
    negative = [value for value in values if value < 0.0]
    return {
        "rows": len(rows),
        "resolved": len(values),
        "wins": len(positive),
        "losses": len(negative),
        "win_rate": len(positive) / len(values) if values else None,
        "sum_r": sum(values),
        "mean_r": sum(values) / len(values) if values else None,
        "profit_factor_r": sum(positive) / -sum(negative) if negative else None,
    }


def absolute_z(row: dict[str, Any]) -> float:
    diagnostics = row.get("diagnostics") or {}
    value = diagnostics.get("causal_zscore")
    if value is None:
        value = diagnostics.get("jump_absolute_z", 0.0)
    return abs(float(value or 0.0))


def one_per_boundary(
    rows: list[dict[str, Any]],
    eligible: Callable[[dict[str, Any]], bool],
    mode: str,
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if eligible(row):
            grouped[int(row["episode_ts"])].append(row)
    selected: list[dict[str, Any]] = []
    for boundary in sorted(grouped):
        candidates = grouped[boundary]
        if mode == "max_z":
            candidates.sort(key=lambda row: (-absolute_z(row), str(row["symbol"])))
        elif mode == "least_z":
            candidates.sort(key=lambda row: (absolute_z(row), str(row["symbol"])))
        else:
            raise ValueError(mode)
        selected.append(candidates[0])
    return selected


def actual_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if bool(row.get("actual_executed"))]


def summarize_period(rows: list[dict[str, Any]]) -> dict[str, Any]:
    long_rows = [row for row in rows if int(row["side"]) > 0]
    short_rows = [row for row in rows if int(row["side"]) < 0]
    actual = actual_rows(rows)
    return {
        "symbol_candidates": {
            "all": stats(rows),
            "long_reversal": stats(long_rows),
            "short_reversal": stats(short_rows),
        },
        "actual_account_trade_subset": {
            "all": stats(actual),
            "long_reversal": stats(
                [row for row in actual if int(row["side"]) > 0]
            ),
            "short_reversal": stats(
                [row for row in actual if int(row["side"]) < 0]
            ),
        },
        "one_per_independent_boundary_shadow": {
            "source_max_z_all": stats(
                one_per_boundary(rows, lambda row: True, "max_z")
            ),
            "source_max_z_long_only": stats(
                one_per_boundary(rows, lambda row: int(row["side"]) > 0, "max_z")
            ),
            "source_max_z_short_only": stats(
                one_per_boundary(rows, lambda row: int(row["side"]) < 0, "max_z")
            ),
            "least_z_all": stats(
                one_per_boundary(rows, lambda row: True, "least_z")
            ),
            "least_z_long_only": stats(
                one_per_boundary(rows, lambda row: int(row["side"]) > 0, "least_z")
            ),
            "least_z_short_only": stats(
                one_per_boundary(rows, lambda row: int(row["side"]) < 0, "least_z")
            ),
        },
        "independent_boundaries": len({int(row["episode_ts"]) for row in rows}),
        "long_boundaries": len(
            {int(row["episode_ts"]) for row in long_rows}
        ),
        "short_boundaries": len(
            {int(row["episode_ts"]) for row in short_rows}
        ),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows_by_period: dict[str, list[dict[str, Any]]] = {}
    for name, path in PERIODS.items():
        if not path.is_file():
            raise RuntimeError(f"missing consumed source anatomy: {path}")
        rows_by_period[name] = json.loads(path.read_text(encoding="utf-8"))

    period_results = {
        name: summarize_period(rows) for name, rows in rows_by_period.items()
    }
    pooled = [row for rows in rows_by_period.values() for row in rows]
    short_period_sums = {
        name: period_results[name]["symbol_candidates"]["short_reversal"]["sum_r"]
        for name in PERIODS
    }
    long_period_sums = {
        name: period_results[name]["symbol_candidates"]["long_reversal"]["sum_r"]
        for name in PERIODS
    }
    result = {
        "experiment": "candidate-57-jump-side-anatomy-multiperiod-v1",
        "development_only": True,
        "periods": {name: str(path) for name, path in PERIODS.items()},
        "period_results": period_results,
        "pooled_descriptive_only": summarize_period(pooled),
        "directional_stability": {
            "short_candidate_sum_r_by_period": short_period_sums,
            "long_candidate_sum_r_by_period": long_period_sums,
            "short_positive_periods": sum(
                int(float(value or 0.0) > 0.0)
                for value in short_period_sums.values()
            ),
            "long_positive_periods": sum(
                int(float(value or 0.0) > 0.0)
                for value in long_period_sums.values()
            ),
        },
        "interpretation_constraint": (
            "candidate rows are not independent; only one-per-boundary rows may "
            "motivate a tradable short-only policy, and that policy still requires "
            "a new continuous one-slot untouched account"
        ),
    }
    dump(OUT / "comparison.json", result)

    lines = [
        "# 4h jump reversal — multi-period side anatomy",
        "",
        "All periods were already consumed before this comparison. Symbol-level "
        "rows are descriptive; the boundary summaries preserve the causal unit.",
        "",
        "| period | long rows | long mean R | long PF | short rows | short mean R | short PF |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, summary in period_results.items():
        long_summary = summary["symbol_candidates"]["long_reversal"]
        short_summary = summary["symbol_candidates"]["short_reversal"]
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    str(long_summary["resolved"]),
                    str(long_summary["mean_r"]),
                    str(long_summary["profit_factor_r"]),
                    str(short_summary["resolved"]),
                    str(short_summary["mean_r"]),
                    str(short_summary["profit_factor_r"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## One candidate per boundary, source max-z",
            "",
            "| period | long boundaries | long mean R | long PF | short boundaries | short mean R | short PF |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name, summary in period_results.items():
        boundary = summary["one_per_independent_boundary_shadow"]
        long_summary = boundary["source_max_z_long_only"]
        short_summary = boundary["source_max_z_short_only"]
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    str(long_summary["resolved"]),
                    str(long_summary["mean_r"]),
                    str(long_summary["profit_factor_r"]),
                    str(short_summary["resolved"]),
                    str(short_summary["mean_r"]),
                    str(short_summary["profit_factor_r"]),
                ]
            )
            + " |"
        )
    (OUT / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result["directional_stability"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
