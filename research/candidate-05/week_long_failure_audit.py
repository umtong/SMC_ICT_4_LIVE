#!/usr/bin/env python3
"""Diagnose why short restarted screens can outperform continuous evaluation.

This is an evidence reader, not a backtest engine.  It consumes completed
NautilusTrader artifacts from one continuous run and several independently
restarted blocks of the same calendar range.  It never creates orders, fills,
positions, fees or NAV.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Iterable


FLOAT_TOLERANCE = 1.0e-9


@dataclass(frozen=True, slots=True)
class RunEvidence:
    label: str
    root: Path
    metrics: dict[str, Any]
    daily_returns: dict[str, float]

    @property
    def start(self) -> date:
        return date.fromisoformat(str(self.metrics["evaluation_start"]))

    @property
    def end(self) -> date:
        return date.fromisoformat(str(self.metrics["evaluation_end"]))

    @property
    def multiple(self) -> float:
        return 1.0 + finite_number(self.metrics.get("total_return"), -1.0)


def finite_number(value: Any, default: float = float("nan")) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def integer(value: Any, default: int = 0) -> int:
    number = finite_number(value, float(default))
    return int(number)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compound(returns: Iterable[float]) -> float:
    multiple = 1.0
    for value in returns:
        number = finite_number(value)
        if not math.isfinite(number) or number <= -1.0:
            return -1.0
        multiple *= 1.0 + number
    return multiple - 1.0


def rolling_compounds(
    daily_returns: dict[str, float],
    window: int,
) -> list[dict[str, Any]]:
    if window <= 0:
        raise ValueError("window must be positive")
    rows = [
        (date.fromisoformat(day), finite_number(value))
        for day, value in sorted(daily_returns.items())
    ]
    results: list[dict[str, Any]] = []
    for index in range(0, len(rows) - window + 1):
        selected = rows[index : index + window]
        if any(
            selected[offset][0].toordinal() + 1 != selected[offset + 1][0].toordinal()
            for offset in range(len(selected) - 1)
        ):
            continue
        value = compound(item[1] for item in selected)
        results.append(
            {
                "start": selected[0][0].isoformat(),
                "end": selected[-1][0].isoformat(),
                "total_return": value,
                "geometric_daily_growth": (
                    (1.0 + value) ** (1.0 / window) - 1.0
                    if value > -1.0
                    else -1.0
                ),
            },
        )
    return results


def load_run(root: Path, label: str) -> RunEvidence:
    metrics_path = root / "metrics.json"
    daily_path = root / "daily_returns.json"
    if not metrics_path.exists():
        raise FileNotFoundError(f"missing Nautilus metrics: {metrics_path}")
    metrics = read_json(metrics_path)
    if not isinstance(metrics, dict):
        raise TypeError(f"metrics must be an object: {metrics_path}")
    if daily_path.exists():
        daily = read_json(daily_path)
    else:
        daily = metrics.get("daily_returns", {})
    if not isinstance(daily, dict):
        raise TypeError(f"daily returns must be an object: {root}")
    normalized = {
        str(day): finite_number(value)
        for day, value in daily.items()
        if math.isfinite(finite_number(value))
    }
    return RunEvidence(label=label, root=root, metrics=metrics, daily_returns=normalized)


def discover_runs(root: Path) -> tuple[RunEvidence, list[RunEvidence]]:
    metric_paths = sorted(root.rglob("metrics.json"))
    runs: list[RunEvidence] = []
    for path in metric_paths:
        directory = path.parent
        label = directory.name
        runs.append(load_run(directory, label))
    continuous = [run for run in runs if run.label == "continuous"]
    blocks = sorted(
        (run for run in runs if run.label.startswith("block-")),
        key=lambda run: run.start,
    )
    if len(continuous) != 1:
        raise RuntimeError(
            f"expected exactly one continuous run, found {[run.label for run in continuous]}",
        )
    if not blocks:
        raise RuntimeError("no restarted block runs found")
    return continuous[0], blocks


def continuous_return_for_block(
    continuous: RunEvidence,
    block: RunEvidence,
) -> float:
    selected = [
        value
        for day, value in sorted(continuous.daily_returns.items())
        if block.start <= date.fromisoformat(day) <= block.end
    ]
    expected_days = (block.end - block.start).days + 1
    if len(selected) != expected_days:
        raise RuntimeError(
            f"continuous run has {len(selected)} days for {block.label}, expected {expected_days}",
        )
    return compound(selected)


def pnl_records(root: Path) -> list[dict[str, Any]]:
    candidates = [
        root / "closed_scenarios_all.json",
        root / "closed_scenarios.json",
    ]
    candidates.extend(sorted(root.glob("symbols/*/closed_scenarios.json")))
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, float]] = set()
    for path in candidates:
        if not path.exists():
            continue
        payload = read_json(path)
        if not isinstance(payload, list):
            continue
        symbol = path.parent.name if path.parent.parent.name == "symbols" else ""
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            pnl = finite_number(
                raw.get("realized_pnl_number", raw.get("realized_pnl")),
            )
            if not math.isfinite(pnl):
                continue
            ts = event_timestamp_ns(raw)
            branch = str(raw.get("branch", raw.get("scenario", "UNKNOWN")))
            item_symbol = str(raw.get("symbol", symbol))
            key = (item_symbol, branch, ts, round(pnl, 8))
            if key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "symbol": item_symbol,
                    "branch": branch,
                    "ts_event": ts,
                    "pnl": pnl,
                },
            )
    records.sort(key=lambda item: (int(item["ts_event"]), str(item["symbol"])))
    return records


def event_timestamp_ns(record: dict[str, Any]) -> int:
    for key in (
        "entry_ts",
        "opened_ts",
        "ts_opened",
        "open_ts",
        "ts_event",
        "exit_ts",
        "closed_ts",
    ):
        value = record.get(key)
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return 0


def lag_one_autocorrelation(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    left = values[:-1]
    right = values[1:]
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left, right)
    )
    left_var = sum((value - left_mean) ** 2 for value in left)
    right_var = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_var * right_var)
    return numerator / denominator if denominator > 0.0 else None


def longest_loss_streak(values: list[float]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value < 0.0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def episode_count(records: list[dict[str, Any]], gap_minutes: int = 180) -> int:
    """Diagnostic dependence count, never a trading or promotion rule."""
    if not records:
        return 0
    gap_ns = gap_minutes * 60 * 1_000_000_000
    episodes = 1
    previous = int(records[0]["ts_event"])
    for record in records[1:]:
        current = int(record["ts_event"])
        if previous <= 0 or current <= 0 or current - previous > gap_ns:
            episodes += 1
        previous = current
    return episodes


def analyze(root: Path) -> dict[str, Any]:
    continuous, blocks = discover_runs(root)
    if not bool(continuous.metrics.get("integrity_pass", True)):
        raise RuntimeError("continuous Nautilus run failed integrity")
    if not all(bool(block.metrics.get("integrity_pass", True)) for block in blocks):
        raise RuntimeError("one or more restarted blocks failed integrity")

    block_rows: list[dict[str, Any]] = []
    restart_path_changed = False
    for block in blocks:
        continuous_return = continuous_return_for_block(continuous, block)
        delta = block.multiple - 1.0 - continuous_return
        if abs(delta) > FLOAT_TOLERANCE:
            restart_path_changed = True
        block_rows.append(
            {
                "label": block.label,
                "start": block.start.isoformat(),
                "end": block.end.isoformat(),
                "restarted_total_return": block.multiple - 1.0,
                "same_dates_inside_continuous_total_return": continuous_return,
                "restart_delta": delta,
                "restarted_trades": integer(block.metrics.get("trades")),
                "restarted_wins": integer(block.metrics.get("wins")),
                "restarted_win_rate": finite_number(block.metrics.get("win_rate")),
            },
        )

    chunk_multiple = math.prod(block.multiple for block in blocks)
    continuous_multiple = continuous.multiple
    rolling = rolling_compounds(continuous.daily_returns, 7)
    rolling_returns = [float(item["total_return"]) for item in rolling]
    records = pnl_records(continuous.root)
    pnls = [float(item["pnl"]) for item in records]

    some_positive_short_window = any(value > 0.0 for value in rolling_returns)
    continuous_negative = continuous_multiple < 1.0
    if restart_path_changed:
        classification = "STATEFUL_RESTART_OR_BOUNDARY_PATH_BIAS_CONFIRMED"
    elif continuous_negative and some_positive_short_window:
        classification = "SHORT_SAMPLE_SELECTION_AND_REGIME_MIXTURE_CONFIRMED"
    elif continuous_negative:
        classification = "CONTINUOUS_NEGATIVE_EXPECTANCY_CONFIRMED"
    else:
        classification = "NO_WEEK_TO_CONTINUOUS_FAILURE_IN_THIS_RANGE"

    return {
        "schema": "candidate-05-week-long-failure-audit-v1",
        "classification": classification,
        "engine": "NautilusTrader artifacts only",
        "continuous": {
            "start": continuous.start.isoformat(),
            "end": continuous.end.isoformat(),
            "total_return": continuous_multiple - 1.0,
            "geometric_daily_growth": finite_number(
                continuous.metrics.get("geometric_daily_growth"),
            ),
            "trades": integer(continuous.metrics.get("trades")),
            "wins": integer(continuous.metrics.get("wins")),
            "win_rate": finite_number(continuous.metrics.get("win_rate")),
            "profit_factor": finite_number(continuous.metrics.get("profit_factor")),
            "max_drawdown": finite_number(continuous.metrics.get("max_drawdown")),
        },
        "independently_restarted_blocks": {
            "count": len(blocks),
            "compound_total_return": chunk_multiple - 1.0,
            "delta_vs_continuous": chunk_multiple - continuous_multiple,
            "path_changed": restart_path_changed,
            "rows": block_rows,
        },
        "continuous_rolling_seven_day_distribution": {
            "count": len(rolling_returns),
            "positive_windows": sum(value > 0.0 for value in rolling_returns),
            "positive_share": (
                sum(value > 0.0 for value in rolling_returns) / len(rolling_returns)
                if rolling_returns
                else None
            ),
            "minimum": min(rolling_returns) if rolling_returns else None,
            "median": median(rolling_returns) if rolling_returns else None,
            "maximum": max(rolling_returns) if rolling_returns else None,
            "windows": rolling,
        },
        "trade_dependence_diagnostics": {
            "records_available": len(records),
            "lag_one_pnl_autocorrelation": lag_one_autocorrelation(pnls),
            "longest_loss_streak": longest_loss_streak(pnls),
            "three_hour_episode_count": episode_count(records, 180),
            "trades_per_diagnostic_episode": (
                len(records) / episode_count(records, 180)
                if episode_count(records, 180) > 0
                else None
            ),
            "note": (
                "Three-hour clustering is descriptive only. It estimates dependence; "
                "it is not a risk cap, entry filter or promotion threshold."
            ),
        },
        "interpretation_contract": {
            "weekly_positive_is_validation": False,
            "weekly_negative_can_falsify": True,
            "new_branch_requires_incremental_trade_attribution": True,
            "reused_week_is_out_of_sample": False,
            "candidate_must_freeze_before_untouched_blocks": True,
        },
    }


def markdown_report(result: dict[str, Any]) -> str:
    continuous = result["continuous"]
    blocks = result["independently_restarted_blocks"]
    rolling = result["continuous_rolling_seven_day_distribution"]
    dependence = result["trade_dependence_diagnostics"]
    lines = [
        "# Candidate 05 — week-to-continuous failure audit",
        "",
        f"**Classification: `{result['classification']}`**",
        "",
        "This report compares one continuous NautilusTrader run with independently",
        "restarted blocks covering the same calendar dates. No order, fill, fee,",
        "position or NAV was constructed outside NautilusTrader.",
        "",
        "## Continuous result",
        "",
        f"- Range: {continuous['start']} through {continuous['end']}",
        f"- Total return: {continuous['total_return']:.9%}",
        f"- Geometric daily growth: {continuous['geometric_daily_growth']:.9%}",
        f"- Trades / wins: {continuous['trades']} / {continuous['wins']}",
        f"- Win rate: {continuous['win_rate']:.6%}",
        f"- Profit factor: {continuous['profit_factor']:.6f}",
        f"- Maximum drawdown: {continuous['max_drawdown']:.6%}",
        "",
        "## Restart effect",
        "",
        f"- Restarted-block compound return: {blocks['compound_total_return']:.9%}",
        f"- Difference versus continuous: {blocks['delta_vs_continuous']:.9%}",
        f"- Trading path changed: `{blocks['path_changed']}`",
        "",
        "| Block | Restarted | Same dates continuous | Delta | Trades / wins |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in blocks["rows"]:
        lines.append(
            "| {label} | {restart:.6%} | {continuous:.6%} | {delta:.6%} | {trades} / {wins} |".format(
                label=row["label"],
                restart=row["restarted_total_return"],
                continuous=row["same_dates_inside_continuous_total_return"],
                delta=row["restart_delta"],
                trades=row["restarted_trades"],
                wins=row["restarted_wins"],
            ),
        )
    lines.extend(
        [
            "",
            "## Seven-day distribution inside the continuous path",
            "",
            f"- Windows: {rolling['count']}",
            f"- Positive windows: {rolling['positive_windows']}",
            f"- Positive share: {rolling['positive_share']}",
            f"- Minimum / median / maximum: {rolling['minimum']} / {rolling['median']} / {rolling['maximum']}",
            "",
            "## Dependence diagnostics",
            "",
            f"- PnL records: {dependence['records_available']}",
            f"- Lag-one PnL autocorrelation: {dependence['lag_one_pnl_autocorrelation']}",
            f"- Longest loss streak: {dependence['longest_loss_streak']}",
            f"- Three-hour diagnostic episodes: {dependence['three_hour_episode_count']}",
            f"- Trades per diagnostic episode: {dependence['trades_per_diagnostic_episode']}",
            "",
            "## Decision",
            "",
            "A positive seven-day screen is only evidence that the hypothesis was not",
            "falsified in that sample. It is not validation. A reused week is a",
            "development set, and every new branch must prove incremental expectancy",
            "before inherited baseline trades may be credited to it.",
            "",
        ],
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()

    result = analyze(args.root.resolve())
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(markdown_report(result), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
