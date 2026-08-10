#!/usr/bin/env python3
"""Run and analyze the behaviour-identical Zaratustra all-candidate audit."""
from __future__ import annotations

import copy
from collections import defaultdict
from datetime import date
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-zara-all-candidate-forensic-v4"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-zara-all-candidate-forensic-v4"
EVIDENCE = HERE / "evidence" / "zara-all-candidate-forensic-v4"
CACHE = ROOT / ".cache" / "candidate-57-zara-all-candidate-forensic-v4"
BASELINE = (
    HERE
    / "evidence"
    / "zaratustra-v5-source-v1"
    / "cases"
    / "continuous_30d-source_level_both.json"
)
START = date(2026, 6, 1)
END = date(2026, 6, 30)


def safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    return value


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(safe(value), indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def build_config() -> Path:
    payload = copy.deepcopy(
        json.loads((C51 / "config.json").read_text(encoding="utf-8"))
    )
    strategy = payload["strategy"]
    for key in (
        "sma_offset_low",
        "sma_offset_high",
        "sma_stop_min_fraction",
        "sma_stop_max_fraction",
        "sma_stop_atr_buffer",
    ):
        strategy.pop(key, None)
    strategy.update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": 480,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "picasso_bucket_minutes": 5,
            "picasso_precedence_mode": "corrected_level",
            "picasso_source_effective_leverage": 1.0,
            "picasso_source_stoploss": 0.0296,
            "picasso_trailing_positive": 0.0013,
            "picasso_trailing_offset": 0.0071,
            "picasso_emergency_target_fraction": 0.20,
            "picasso_roi_0": 100.0,
            "picasso_roi_416": 100.0,
            "picasso_roi_933": 100.0,
            "picasso_roi_1982": 100.0,
            "zara_trigger_mode": "level",
            "zara_side_mode": "both",
            "zara_risk_mode": "source_fraction",
            "zara_rsi_period": 14,
            "zara_di_period": 14,
            "zara_bb_period": 20,
            "zara_rsi_threshold": 50.0,
            "zara_di_threshold": 25.0,
            "zara_source_stop_fraction": 0.0296,
            "zara_target_fraction": 0.20,
            "zara_structural_lookback_5m": 8,
            "zara_atr_period_5m": 14,
            "zara_stop_atr_buffer": 0.25,
            "zara_min_stop_fraction": 0.0015,
            "zara_shadow_round_trip_cost_fraction": 0.0021,
        }
    )
    path = WORK / "config.json"
    dump(path, payload)
    return path


def compare_baseline(metrics: dict[str, Any]) -> dict[str, Any]:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    expected = baseline.get("metrics") or {}
    checks: dict[str, bool] = {}
    for key in (
        "trades",
        "wins",
        "losses",
        "total_return",
        "geometric_daily_growth",
        "profit_factor",
        "max_drawdown",
    ):
        if key in {"trades", "wins", "losses"}:
            checks[key] = int(expected.get(key) or 0) == int(metrics.get(key) or 0)
        else:
            checks[key] = (
                abs(number(expected.get(key), 0.0) - number(metrics.get(key), 0.0))
                <= 1e-10
            )
    return {"identical": all(checks.values()), "checks": checks}


def pearson(xs: list[float], ys: list[float]) -> float | None:
    pairs = [
        (x, y)
        for x, y in zip(xs, ys)
        if math.isfinite(x) and math.isfinite(y)
    ]
    if len(pairs) < 3:
        return None
    left = [pair[0] for pair in pairs]
    right = [pair[1] for pair in pairs]
    mean_left = statistics.fmean(left)
    mean_right = statistics.fmean(right)
    covariance = sum(
        (x - mean_left) * (y - mean_right) for x, y in pairs
    )
    variance_left = sum((x - mean_left) ** 2 for x in left)
    variance_right = sum((y - mean_right) ** 2 for y in right)
    denominator = math.sqrt(variance_left * variance_right)
    return covariance / denominator if denominator > 1e-12 else None


def summarize_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    net = [number(row.get("net_r"), 0.0) for row in rows]
    positive = [value for value in net if value > 0.0]
    negative = [value for value in net if value < 0.0]
    gross_profit = sum(positive)
    gross_loss = -sum(negative)
    return {
        "episodes": len(rows),
        "positive": len(positive),
        "negative": len(negative),
        "mean_net_r": statistics.fmean(net) if net else None,
        "sum_net_r": sum(net),
        "profit_factor_r": (
            gross_profit / gross_loss
            if gross_loss > 1e-12
            else (None if not positive else math.inf)
        ),
        "mean_mfe_r": (
            statistics.fmean(number(row.get("mfe_r"), 0.0) for row in rows)
            if rows
            else None
        ),
        "mean_mae_r": (
            statistics.fmean(number(row.get("mae_r"), 0.0) for row in rows)
            if rows
            else None
        ),
    }


def analyze_collisions(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    by_ts: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in episodes:
        by_ts[int(row["episode_ts"])].append(row)
    records: list[dict[str, Any]] = []
    selected_deltas: list[float] = []
    selected_minus_least: list[float] = []
    selected_best = 0
    least_best = 0
    all_negative = 0
    score_return_correlations: list[float] = []
    for episode_ts, rows in sorted(by_ts.items()):
        if len(rows) < 2:
            continue
        same_side = len({int(row["side"]) for row in rows}) == 1
        ordered = sorted(rows, key=lambda row: number(row.get("source_score")), reverse=True)
        selected_rows = [row for row in rows if bool(row.get("router_selected"))]
        selected = selected_rows[0] if selected_rows else ordered[0]
        least = ordered[-1]
        best = max(rows, key=lambda row: number(row.get("net_r"), -math.inf))
        selected_r = number(selected.get("net_r"), 0.0)
        least_r = number(least.get("net_r"), 0.0)
        best_r = number(best.get("net_r"), 0.0)
        selected_deltas.append(selected_r - best_r)
        selected_minus_least.append(selected_r - least_r)
        selected_best += int(selected is best)
        least_best += int(least is best)
        all_negative += int(all(number(row.get("net_r"), 0.0) < 0.0 for row in rows))
        corr = pearson(
            [number(row.get("source_score")) for row in rows],
            [number(row.get("net_r")) for row in rows],
        )
        if corr is not None:
            score_return_correlations.append(corr)
        records.append(
            {
                "episode_ts": episode_ts,
                "candidates": len(rows),
                "same_side": same_side,
                "slot_states": sorted({str(row.get("slot_state_at_start")) for row in rows}),
                "selected_symbol": selected.get("symbol"),
                "selected_score": selected.get("source_score"),
                "selected_net_r": selected_r,
                "selected_mfe_r": selected.get("mfe_r"),
                "selected_mae_r": selected.get("mae_r"),
                "least_score_symbol": least.get("symbol"),
                "least_score": least.get("source_score"),
                "least_score_net_r": least_r,
                "least_score_mfe_r": least.get("mfe_r"),
                "least_score_mae_r": least.get("mae_r"),
                "best_symbol": best.get("symbol"),
                "best_net_r": best_r,
                "selected_was_best": selected is best,
                "least_was_best": least is best,
                "all_negative": all(number(row.get("net_r"), 0.0) < 0.0 for row in rows),
                "candidate_rows": sorted(
                    rows,
                    key=lambda row: number(row.get("source_score")),
                    reverse=True,
                ),
            }
        )
    count = len(records)
    return {
        "count": count,
        "selected_best_share": selected_best / count if count else None,
        "least_best_share": least_best / count if count else None,
        "common_mode_all_negative_share": all_negative / count if count else None,
        "mean_selected_minus_best_r": (
            statistics.fmean(selected_deltas) if selected_deltas else None
        ),
        "mean_selected_minus_least_r": (
            statistics.fmean(selected_minus_least) if selected_minus_least else None
        ),
        "median_selected_minus_least_r": (
            statistics.median(selected_minus_least)
            if selected_minus_least
            else None
        ),
        "negative_selected_minus_least_share": (
            sum(value < 0.0 for value in selected_minus_least)
            / len(selected_minus_least)
            if selected_minus_least
            else None
        ),
        "mean_within_boundary_score_return_correlation": (
            statistics.fmean(score_return_correlations)
            if score_return_correlations
            else None
        ),
        "records": records,
    }


def render(report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    collisions = report["collision_analysis"]
    groups = report["episode_groups"]
    lines = [
        "# ZaratustraV5 all-candidate forensic v4",
        "",
        "The Nautilus account is unchanged; every shadow episode is non-trading.",
        "",
        f"- baseline identical: {report['baseline_identity']['identical']}",
        f"- actual trades: {metrics.get('trades')}",
        f"- raw source signals: {report['raw_source_signals']}",
        f"- independent continuous episodes: {report['continuous_episode_starts']}",
        f"- collision boundaries: {collisions.get('count')}",
        "",
        "## Shadow episode groups",
        "",
        "| group | episodes | positive/negative | mean net R | PF(R) | mean MFE R | mean MAE R |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("router_selected", "router_rejected", "flat_start", "blocked_start"):
        row = groups[name]
        lines.append(
            f"| {name} | {row.get('episodes')} | {row.get('positive')}/{row.get('negative')} | "
            f"{row.get('mean_net_r')} | {row.get('profit_factor_r')} | "
            f"{row.get('mean_mfe_r')} | {row.get('mean_mae_r')} |"
        )
    lines += [
        "",
        "## Collision arbitration",
        "",
        f"- selected best share: {collisions.get('selected_best_share')}",
        f"- least-score best share: {collisions.get('least_best_share')}",
        f"- common-mode all-negative share: {collisions.get('common_mode_all_negative_share')}",
        f"- mean selected minus least-score R: {collisions.get('mean_selected_minus_least_r')}",
        f"- median selected minus least-score R: {collisions.get('median_selected_minus_least_r')}",
        f"- selected worse than least-score share: {collisions.get('negative_selected_minus_least_share')}",
        f"- mean within-boundary score/return correlation: {collisions.get('mean_within_boundary_score_return_correlation')}",
        "",
        "## Predeclared interpretation",
        "",
        f"- max-extension arbitration hypothesis supported: {report['hypothesis']['supported']}",
        f"- reason: {report['hypothesis']['reason']}",
        "",
        "No arbitration policy is changed by this audit. An untouched minimum-score comparison is justified only if the repeated collision evidence shows a broad rank effect rather than a few outliers. If common-mode losses dominate, the missing component is a market-wide state classifier, not symbol ranking.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not BASELINE.is_file():
        raise RuntimeError(f"missing baseline: {BASELINE}")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    output = ARTIFACTS / "continuous_30d"
    workspace = WORK / "workspace"
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)

    completed = subprocess.run(
        [
            sys.executable,
            str(C51 / "launch.py"),
            "--config",
            str(build_config()),
            "--start",
            START.isoformat(),
            "--end",
            END.isoformat(),
            "--cache",
            str(CACHE),
            "--output",
            str(output),
            "--workspace",
            str(workspace),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(C51)},
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        dump(
            EVIDENCE / "failure.json",
            {
                "returncode": completed.returncode,
                "metrics_exists": metrics_path.is_file(),
                "diagnostics_exists": diagnostics_path.is_file(),
            },
        )
        return completed.returncode or 2

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    episodes = list(diagnostics.get("zara_shadow_completed_episodes") or [])
    raw_signals = int(diagnostics.get("zara_shadow_raw_source_signals") or 0)
    episode_starts = int(
        diagnostics.get("zara_shadow_continuous_episode_starts") or 0
    )
    groups = {
        "router_selected": summarize_group(
            [row for row in episodes if bool(row.get("router_selected"))]
        ),
        "router_rejected": summarize_group(
            [row for row in episodes if not bool(row.get("router_selected"))]
        ),
        "flat_start": summarize_group(
            [row for row in episodes if row.get("slot_state_at_start") == "FLAT"]
        ),
        "blocked_start": summarize_group(
            [row for row in episodes if row.get("slot_state_at_start") != "FLAT"]
        ),
    }
    collisions = analyze_collisions(episodes)
    count = int(collisions.get("count") or 0)
    selected_best = number(collisions.get("selected_best_share"), 1.0)
    selected_minus_least = number(
        collisions.get("mean_selected_minus_least_r"), 0.0
    )
    worse_share = number(
        collisions.get("negative_selected_minus_least_share"), 0.0
    )
    common_mode = number(
        collisions.get("common_mode_all_negative_share"), 1.0
    )
    supported = (
        count >= 10
        and selected_best <= 0.35
        and selected_minus_least <= -0.15
        and worse_share >= 0.60
        and common_mode <= 0.50
    )
    reason = (
        f"collisions={count}; selected-best={selected_best:.3f}; "
        f"selected-minus-least={selected_minus_least:.3f}R; "
        f"selected-worse-share={worse_share:.3f}; "
        f"common-mode-loss-share={common_mode:.3f}"
    )
    identity = compare_baseline(metrics)
    report = {
        "experiment": "candidate-57-zara-all-candidate-forensic-v4",
        "policy_changed": False,
        "baseline_identity": identity,
        "metrics": metrics,
        "raw_source_signals": raw_signals,
        "continuous_episode_starts": episode_starts,
        "raw_to_independent_ratio": raw_signals / max(1, episode_starts),
        "episode_groups": groups,
        "collision_analysis": collisions,
        "hypothesis": {"supported": supported, "reason": reason},
        "shadow_warning": (
            "Shadow paths diagnose opportunity geometry only; NautilusTrader actual "
            "account fills and NAV remain authoritative."
        ),
    }
    dump(EVIDENCE / "all_candidate_report.json", report)
    render(report)
    valid = identity["identical"] and len(episodes) == episode_starts
    return 0 if valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
