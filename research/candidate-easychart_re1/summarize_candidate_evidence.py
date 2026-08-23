#!/usr/bin/env python3
"""Summarize one four-symbol continuous-account candidate from workflow artifacts.

This is deliberately a small research utility, not a growing validation suite.
It reads NautilusTrader outputs already produced by the strategy, preserves the
trade rows, and reports only the quantities needed to decide whether the market
logic deserves a longer continuous run: independent trade frequency, win rate,
planned geometry, holding time, expectancy, NAV return and drawdown.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

import pandas as pd


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    output: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            output.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            output.update(flatten(item, f"{prefix}[{index}]"))
    else:
        output[prefix] = value
    return output


def metric(values: dict[str, Any], names: list[str]) -> float | None:
    matches: list[tuple[int, float]] = []
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        for name in names:
            if norm(name) in norm(key):
                matches.append((len(norm(key)), float(value)))
                break
    return min(matches, default=(0, None))[1]


def pick(df: pd.DataFrame, names: list[str], *, numeric: bool = False) -> str | None:
    matches: list[tuple[int, int, str]] = []
    for column in df.columns:
        column_key = norm(column)
        for name in names:
            name_key = norm(name)
            if column_key == name_key or name_key in column_key:
                if numeric and pd.to_numeric(df[column], errors="coerce").notna().sum() == 0:
                    continue
                matches.append((0 if column_key == name_key else 1, len(column_key), column))
                break
    return min(matches, default=(9, 999, None))[2]


def parse_time(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        values = pd.to_numeric(series, errors="coerce")
        median = values.dropna().abs().median()
        unit = "ns" if median and median > 1e17 else "ms" if median and median > 1e11 else "s"
        return pd.to_datetime(values, unit=unit, utc=True, errors="coerce")
    return pd.to_datetime(series, utc=True, errors="coerce")


def trade_frame(raw: pd.DataFrame, period: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    work = pd.DataFrame(index=raw.index)
    columns = {
        "planned_rr": pick(raw, ["gross_rr", "planned_rr", "plan_rr", "reward_risk"], numeric=True),
        "r": pick(raw, ["realized_r", "net_r", "r_multiple", "pnl_r", "result_r"], numeric=True),
        "pnl": pick(raw, ["net_pnl", "realized_pnl", "pnl"], numeric=True),
        "outcome": pick(raw, ["outcome", "exit_reason", "result"]),
        "family": pick(raw, ["family", "scenario_family", "scale_name"]),
        "side": pick(raw, ["side", "direction"]),
        "symbol": pick(raw, ["symbol", "instrument", "instrument_id"]),
        "entry": pick(raw, ["entry_time_ns", "entry_time", "entry_ts"]),
        "exit": pick(raw, ["exit_time_ns", "exit_time", "exit_ts"]),
    }
    for name in ("planned_rr", "r", "pnl"):
        column = columns[name]
        if column:
            work[name] = pd.to_numeric(raw[column], errors="coerce")
    for name in ("family", "side", "symbol", "outcome"):
        column = columns[name]
        if column:
            work[name] = raw[column].astype(str)
    if columns["entry"] and columns["exit"]:
        work["hold_minutes"] = (
            parse_time(raw[columns["exit"]]) - parse_time(raw[columns["entry"]])
        ).dt.total_seconds() / 60.0
    if "outcome" in work:
        value = work["outcome"].str.upper()
        work["win"] = value.str.contains("TP|TARGET|WIN|PROFIT")
        unknown = ~value.str.contains("TP|TARGET|WIN|PROFIT|SL|STOP|LOSS")
        if "r" in work:
            work.loc[unknown, "win"] = work.loc[unknown, "r"] > 0
        elif "pnl" in work:
            work.loc[unknown, "win"] = work.loc[unknown, "pnl"] > 0
    elif "r" in work:
        work["win"] = work["r"] > 0
    elif "pnl" in work:
        work["win"] = work["pnl"] > 0
    work["period"] = period
    return work


def summarize_period(
    metrics_path: Path,
    period: str,
    days: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    values = flatten(metrics)
    audit_paths = list(metrics_path.parent.rglob("trade_audit.csv"))
    raw = pd.read_csv(audit_paths[0]) if audit_paths else pd.DataFrame()
    trades = trade_frame(raw, period)
    reported = metric(values, ["completed_trades", "closed_trades", "trade_count", "total_trades"])
    count = len(raw) if len(raw) else int(reported or 0)
    win_rate = float(trades["win"].mean()) if "win" in trades and len(trades) else metric(
        values,
        ["win_rate", "winning_percentage"],
    )
    if win_rate is not None and 1.0 < win_rate <= 100.0:
        win_rate /= 100.0
    average_r = (
        float(trades["r"].mean())
        if "r" in trades and trades["r"].notna().any()
        else metric(values, ["expectancy_r", "average_r", "mean_r"])
    )
    total_r = (
        float(trades["r"].sum())
        if "r" in trades and trades["r"].notna().any()
        else metric(values, ["total_r", "sum_r"])
    )
    average_rr = (
        float(trades["planned_rr"].mean())
        if "planned_rr" in trades and trades["planned_rr"].notna().any()
        else metric(values, ["average_gross_rr", "average_planned_rr", "avg_gross_rr"])
    )
    median_rr = (
        float(trades["planned_rr"].median())
        if "planned_rr" in trades and trades["planned_rr"].notna().any()
        else None
    )
    median_hold = (
        float(trades["hold_minutes"].median())
        if "hold_minutes" in trades and trades["hold_minutes"].notna().any()
        else metric(values, ["median_hold_minutes", "median_holding_minutes"])
    )
    initial_nav = metric(values, ["initial_nav", "starting_balance", "initial_balance", "starting_equity"])
    final_nav = metric(values, ["final_nav", "ending_balance", "final_balance", "ending_equity"])
    net_return = (
        final_nav / initial_nav - 1.0
        if final_nav is not None and initial_nav is not None and initial_nav > 0.0
        else metric(values, ["net_return", "total_return"])
    )
    if net_return is not None and 1.0 < net_return <= 100.0:
        net_return /= 100.0
    max_drawdown = metric(values, ["max_drawdown", "maximum_drawdown"])
    if max_drawdown is not None and 1.0 < max_drawdown <= 100.0:
        max_drawdown /= 100.0
    return (
        {
            "period": period,
            "days": days,
            "trades": count,
            "trades_per_day": count / days,
            "win_rate": win_rate,
            "average_r": average_r,
            "total_r": total_r,
            "average_planned_rr": average_rr,
            "median_planned_rr": median_rr,
            "median_hold_minutes": median_hold,
            "initial_nav": initial_nav,
            "final_nav": final_nav,
            "net_return": net_return,
            "max_drawdown": max_drawdown,
            "trade_audit_rows": len(raw),
            "reported_trade_count": reported,
            "metrics_path": str(metrics_path),
        },
        trades,
    )


def group_stats(trades: pd.DataFrame, groups: list[str]) -> list[dict[str, Any]]:
    available = [name for name in groups if name in trades]
    if trades.empty or not available:
        return []
    rows: list[dict[str, Any]] = []
    for keys, group in trades.groupby(available, dropna=False):
        values = keys if isinstance(keys, tuple) else (keys,)
        record = dict(zip(available, values, strict=True))
        record.update(
            {
                "trades": len(group),
                "win_rate": float(group["win"].mean()) if "win" in group else None,
                "average_r": float(group["r"].mean()) if "r" in group and group["r"].notna().any() else None,
                "total_r": float(group["r"].sum()) if "r" in group and group["r"].notna().any() else None,
                "average_planned_rr": float(group["planned_rr"].mean())
                if "planned_rr" in group and group["planned_rr"].notna().any()
                else None,
                "median_hold_minutes": float(group["hold_minutes"].median())
                if "hold_minutes" in group and group["hold_minutes"].notna().any()
                else None,
            }
        )
        rows.append(record)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--period-days-json", type=Path, required=True)
    parser.add_argument("--medium-period", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    args = parser.parse_args()

    period_days = json.loads(args.period_days_json.read_text(encoding="utf-8"))
    period_rows: list[dict[str, Any]] = []
    trade_frames: list[pd.DataFrame] = []
    for metrics_path in args.root.rglob("metrics.json"):
        text = str(metrics_path).lower()
        period = next((name for name in period_days if name.lower() in text), None)
        if period is None:
            continue
        row, trades = summarize_period(metrics_path, period, int(period_days[period]))
        period_rows.append(row)
        if not trades.empty:
            trade_frames.append(trades)

    periods = pd.DataFrame(period_rows).sort_values("period")
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    medium = periods[periods["period"] == args.medium_period]
    diagnostic = periods[periods["period"] != args.medium_period]
    blockers: list[str] = []
    strict = False
    promotion = False
    if len(medium) == 1:
        item = medium.iloc[0]
        strict = bool(
            (item["trades_per_day"] or 0.0) >= 1.0
            and (item["win_rate"] or 0.0) >= 0.70
            and (item["average_r"] is None or item["average_r"] > 0.0)
            and (item["net_return"] is None or item["net_return"] > 0.0)
            and (item["median_planned_rr"] is None or 1.0 <= item["median_planned_rr"] <= 2.5)
            and (item["median_hold_minutes"] is None or 15.0 <= item["median_hold_minutes"] <= 360.0)
            and (item["max_drawdown"] is None or item["max_drawdown"] < 0.30)
        )
        promotion = bool(
            (item["trades_per_day"] or 0.0) >= 0.75
            and (item["win_rate"] or 0.0) >= 0.65
            and (item["average_r"] is None or item["average_r"] > 0.0)
            and (item["net_return"] is None or item["net_return"] > 0.0)
            and (item["max_drawdown"] is None or item["max_drawdown"] < 0.35)
        )
        if (item["trades_per_day"] or 0.0) < 1.0:
            blockers.append("MEDIUM_FREQUENCY_BELOW_ONE_PER_DAY")
        if (item["win_rate"] or 0.0) < 0.70:
            blockers.append("MEDIUM_WIN_RATE_BELOW_70_PERCENT")
        if item["average_r"] is not None and item["average_r"] <= 0.0:
            blockers.append("MEDIUM_NONPOSITIVE_EXPECTANCY_R")
        if item["net_return"] is not None and item["net_return"] <= 0.0:
            blockers.append("MEDIUM_NONPOSITIVE_NAV_RETURN")
    else:
        blockers.append("MEDIUM_EVIDENCE_MISSING_OR_DUPLICATE")

    weighted_win_rate: float | None = None
    positive_periods = 0
    robust = False
    if len(diagnostic):
        total = max(float(diagnostic["trades"].fillna(0).sum()), 1.0)
        weighted_win_rate = float(
            (diagnostic["win_rate"].fillna(0.0) * diagnostic["trades"].fillna(0.0)).sum()
            / total
        )
        positive_periods = int(
            ((diagnostic["total_r"].fillna(0.0) > 0.0) | (diagnostic["net_return"].fillna(0.0) > 0.0)).sum()
        )
        robust = bool(weighted_win_rate >= 0.65 and positive_periods >= max(1, len(diagnostic) - 1))
        if not robust:
            blockers.append("DIAGNOSTIC_REGIME_ROBUSTNESS_NOT_ESTABLISHED")
    else:
        blockers.append("DIAGNOSTIC_EVIDENCE_MISSING")

    ready = bool(strict and robust)
    medium_row = medium.iloc[0].to_dict() if len(medium) == 1 else {}
    score = (
        (4.0 if ready else 0.0)
        + (2.0 if promotion else 0.0)
        + (1.0 if robust else 0.0)
        + float(medium_row.get("win_rate") or 0.0)
        + float(medium_row.get("average_r") or 0.0)
        + 0.35 * min(float(medium_row.get("trades_per_day") or 0.0), 2.0)
    )
    record = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate": args.candidate,
        "periods": periods.to_dict("records"),
        "family_side": group_stats(trades, ["family", "side"]),
        "period_family": group_stats(trades, ["period", "family"]),
        "diagnostic_weighted_win_rate": weighted_win_rate,
        "diagnostic_positive_periods": positive_periods,
        "diagnostic_robust": robust,
        "medium_promotion_profile": promotion,
        "strict_target_profile": strict,
        "ready_for_long_continuous_and_paper": ready,
        "selection_score": score,
        "blockers": [] if ready else blockers,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    periods.to_csv(args.output_dir / f"{args.prefix}_periods.csv", index=False)
    trades.to_csv(args.output_dir / f"{args.prefix}_trades.csv", index=False)
    (args.output_dir / f"{args.prefix}_evidence.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    report = [
        f"# {args.candidate} evidence",
        "",
        f"Ready for long continuous and paper: **{ready}**",
        f"Medium promotion profile: **{promotion}**",
        "",
        periods.to_markdown(index=False),
        "",
        "## Family and side",
        "",
        pd.DataFrame(record["family_side"]).to_markdown(index=False)
        if record["family_side"]
        else "No trade rows were available.",
        "",
        "Blockers: " + (", ".join(record["blockers"]) if record["blockers"] else "none"),
    ]
    (args.output_dir / f"{args.prefix}_EVIDENCE.md").write_text(
        "\n".join(report) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
