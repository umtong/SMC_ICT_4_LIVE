#!/usr/bin/env python3
"""Behaviour-identical re-entry audit for public ZaratustraV5."""
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

from trade_ledger_forensics import analyze as analyze_trades

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-zara-reentry-forensic-v5"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-zara-reentry-forensic-v5"
EVIDENCE = HERE / "evidence" / "zara-reentry-forensic-v5"
CACHE = ROOT / ".cache" / "candidate-57-zara-reentry-forensic-v5"
BASELINE = (
    HERE / "evidence" / "zaratustra-v5-source-v1" / "cases" /
    "continuous_30d-source_level_both.json"
)
START = date(2026, 6, 1)
END = date(2026, 6, 30)
FEATURE_KEYS = (
    "source_score",
    "source_stop_fraction",
    "forensic_continuous_episode_id",
    "forensic_continuous_episode_start_ts",
    "forensic_entry_ordinal_in_continuous_episode",
    "forensic_continuous_episode_age_at_entry_minutes",
    "forensic_is_reentry_in_continuous_episode",
    "forensic_elapsed_minutes",
    "forensic_mfe_r",
    "forensic_mae_r",
    "forensic_trailing_activation_minute",
    "forensic_first_source_invalidation_minute",
    "forensic_same_side_state_ratio",
)


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
        json.dumps(safe(value), indent=2, sort_keys=True, allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def distribution(values: list[float]) -> dict[str, float | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {"min": None, "q25": None, "median": None, "q75": None, "max": None}

    def q(fraction: float) -> float:
        position = (len(clean) - 1) * fraction
        lo, hi = math.floor(position), math.ceil(position)
        if lo == hi:
            return clean[lo]
        weight = position - lo
        return clean[lo] * (1.0 - weight) + clean[hi] * weight

    return {"min": clean[0], "q25": q(.25), "median": q(.5), "q75": q(.75), "max": clean[-1]}


def build_config() -> Path:
    payload = copy.deepcopy(json.loads((C51 / "config.json").read_text(encoding="utf-8")))
    strategy = payload["strategy"]
    for key in ("sma_offset_low", "sma_offset_high", "sma_stop_min_fraction", "sma_stop_max_fraction", "sma_stop_atr_buffer"):
        strategy.pop(key, None)
    strategy.update({
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
    })
    path = WORK / "config.json"
    dump(path, payload)
    return path


def compare_baseline(metrics: dict[str, Any]) -> dict[str, Any]:
    expected = json.loads(BASELINE.read_text(encoding="utf-8")).get("metrics") or {}
    checks: dict[str, bool] = {}
    for key in ("trades", "wins", "losses", "total_return", "geometric_daily_growth", "profit_factor", "max_drawdown"):
        if key in {"trades", "wins", "losses"}:
            checks[key] = int(expected.get(key) or 0) == int(metrics.get(key) or 0)
        else:
            checks[key] = abs(number(expected.get(key), 0.0) - number(metrics.get(key), 0.0)) <= 1e-10
    return {"identical": all(checks.values()), "checks": checks}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actual_r = [number(row.get("actual_r"), 0.0) for row in rows]
    winners = [value for value in actual_r if value > 0.0]
    losses = [value for value in actual_r if value < 0.0]
    gross_profit = sum(winners)
    gross_loss = -sum(losses)
    exit_counts: dict[str, int] = defaultdict(int)
    episode_ids: set[int] = set()
    for row in rows:
        exit_counts[str(row.get("exit_reason"))] += 1
        episode_id = int(number(row.get("forensic_continuous_episode_id"), 0.0))
        if episode_id > 0:
            episode_ids.add(episode_id)
    return {
        "trades": len(rows),
        "episodes": len(episode_ids),
        "wins": len(winners),
        "losses": len(losses),
        "win_rate": len(winners) / len(rows) if rows else None,
        "mean_r": statistics.fmean(actual_r) if actual_r else None,
        "sum_r": sum(actual_r),
        "profit_factor_r": gross_profit / gross_loss if gross_loss > 1e-12 else (None if not winners else math.inf),
        "mfe_r": distribution([number(row.get("forensic_mfe_r")) for row in rows]),
        "mae_r": distribution([number(row.get("forensic_mae_r")) for row in rows]),
        "episode_age_minutes": distribution([number(row.get("forensic_continuous_episode_age_at_entry_minutes")) for row in rows]),
        "source_state_ratio": distribution([number(row.get("forensic_same_side_state_ratio")) for row in rows]),
        "exit_counts": dict(exit_counts),
    }


def render(report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    groups = report["entry_groups"]
    lines = [
        "# ZaratustraV5 continuous-episode re-entry forensic v5",
        "",
        "The source account is unchanged; every actual trade is tagged by causal state episode.",
        "",
        f"- baseline identical: {report['baseline_identity']['identical']}",
        f"- trades: {metrics.get('trades')}",
        f"- tagged trades: {report.get('tagged_trades')}",
        f"- independent source episodes represented by account trades: {report.get('account_episode_count')}",
        f"- repeated trades collapsed by causal episode: {report.get('repeated_trade_count')}",
        "",
        "| entry group | trades | episodes | W/L | mean R | PF(R) | median MFE R | median MAE R | median episode age | trailing exits | stop/bracket exits |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("first_entry", "reentry", "second_entry", "third_plus"):
        row = groups[name]
        exits = row.get("exit_counts") or {}
        lines.append(
            f"| {name} | {row.get('trades')} | {row.get('episodes')} | {row.get('wins')}/{row.get('losses')} | "
            f"{row.get('mean_r')} | {row.get('profit_factor_r')} | "
            f"{(row.get('mfe_r') or {}).get('median')} | {(row.get('mae_r') or {}).get('median')} | "
            f"{(row.get('episode_age_minutes') or {}).get('median')} | "
            f"{exits.get('PUBLIC_ZARATUSTRA_TRAILING', 0)} | {exits.get('SOURCE_STOP_OR_BRACKET', 0)} |"
        )
    lines += [
        "",
        "## Predeclared interpretation",
        "",
        f"- re-entry exhaustion hypothesis supported: {report['hypothesis']['supported']}",
        f"- reason: {report['hypothesis']['reason']}",
        "",
        "A one-entry-per-continuous-state policy is tested on untouched data only if re-entry deterioration is broad in R, MFE/MAE and exit mix. Otherwise repeated entry remains part of the source renewal mechanism and is merely collapsed for independent-opportunity reporting.",
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
    output, workspace = ARTIFACTS / "continuous_30d", WORK / "workspace"
    for path in (output, workspace):
        if path.exists(): shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run([
        sys.executable, str(C51 / "launch.py"), "--config", str(build_config()),
        "--start", START.isoformat(), "--end", END.isoformat(), "--cache", str(CACHE),
        "--output", str(output), "--workspace", str(workspace),
    ], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(C51)}, check=False)
    metrics_path, diagnostics_path = output / "metrics.json", output / "strategy_diagnostics.json"
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        dump(EVIDENCE / "failure.json", {"returncode": completed.returncode})
        return completed.returncode or 2
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    forensic = analyze_trades(output, int(metrics.get("trades") or 0), FEATURE_KEYS)
    ledger = forensic["trade_ledger"]
    tagged = [row for row in ledger if number(row.get("forensic_continuous_episode_id"), 0.0) > 0]
    first = [row for row in tagged if int(number(row.get("forensic_entry_ordinal_in_continuous_episode"), 0.0)) == 1]
    reentries = [row for row in tagged if int(number(row.get("forensic_entry_ordinal_in_continuous_episode"), 0.0)) >= 2]
    second = [row for row in tagged if int(number(row.get("forensic_entry_ordinal_in_continuous_episode"), 0.0)) == 2]
    third = [row for row in tagged if int(number(row.get("forensic_entry_ordinal_in_continuous_episode"), 0.0)) >= 3]
    groups = {
        "first_entry": summarize(first), "reentry": summarize(reentries),
        "second_entry": summarize(second), "third_plus": summarize(third),
    }
    first_mean, re_mean = number(groups["first_entry"].get("mean_r"), 0.0), number(groups["reentry"].get("mean_r"), 0.0)
    first_mfe, re_mfe = number((groups["first_entry"].get("mfe_r") or {}).get("median"), 0.0), number((groups["reentry"].get("mfe_r") or {}).get("median"), 0.0)
    first_mae, re_mae = number((groups["first_entry"].get("mae_r") or {}).get("median"), 0.0), number((groups["reentry"].get("mae_r") or {}).get("median"), 0.0)
    episodes = {int(number(row.get("forensic_continuous_episode_id"), 0.0)) for row in tagged}
    supported = len(reentries) >= 20 and re_mean <= first_mean - 0.10 and re_mfe < first_mfe and re_mae > first_mae
    reason = (
        f"reentries={len(reentries)}; first mean={first_mean:.3f}R; reentry mean={re_mean:.3f}R; "
        f"first/reentry median MFE={first_mfe:.3f}/{re_mfe:.3f}R; "
        f"first/reentry median MAE={first_mae:.3f}/{re_mae:.3f}R"
    )
    identity = compare_baseline(metrics)
    report = {
        "experiment": "candidate-57-zara-reentry-forensic-v5", "policy_changed": False,
        "baseline_identity": identity, "metrics": metrics, "diagnostics": diagnostics,
        "trade_forensics": forensic, "tagged_trades": len(tagged),
        "account_episode_count": len(episodes), "repeated_trade_count": len(tagged) - len(episodes),
        "entry_groups": groups, "hypothesis": {"supported": supported, "reason": reason},
    }
    dump(EVIDENCE / "reentry_report.json", report)
    render(report)
    valid = identity["identical"] and forensic.get("ledger_matches_metrics") and len(tagged) == int(metrics.get("trades") or 0)
    return 0 if valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
