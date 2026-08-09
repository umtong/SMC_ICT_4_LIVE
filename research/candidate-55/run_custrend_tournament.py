"""Run Candidate 55's forward public-CusTrend tournament.

The strategy was selected from an external completed-solution ranking before
these July/August 2026 bars were evaluated.  Only the source-compatible level
entry and a deduplicated edge interpretation compete.  NautilusTrader remains
the execution, matching, portfolio and continuous-NAV engine.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path.cwd()
CANDIDATE = ROOT / "research" / "candidate-55"
REUSED = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-55-custrend-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
EVIDENCE = CANDIDATE / "evidence" / "v6-custrend"
CACHE = ROOT / ".cache" / "candidate-55-custrend-v1"

VARIANTS = {
    "level_both": "level_both",
    "edge_both": "edge_both",
}
DEVELOPMENT = ("2026-07-09", "2026-08-07")
HOLDOUT = ("2026-05-25", "2026-06-23")
CONTINUOUS = ("2026-04-01", "2026-04-30")
COMPACT_FILES = (
    "metrics.json",
    "strategy_diagnostics.json",
    "run.json",
    "data_manifest.json",
    "closed_scenarios.json",
)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def create_configs() -> dict[str, Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    source = json.loads((REUSED / "config.json").read_text())
    manifest = {
        "candidate": "candidate-55",
        "family": "public_CusTrend_coralTrend_Adx_EMA_Oct_1h",
        "selection": {
            "selected_before_forward_interval_was_evaluated": True,
            "reason": (
                "external completed-solution report combined high frequency, "
                "77.2% win rate, PF 1.66, positive 58/61 months and 13.76% DD"
            ),
        },
        "source": {
            "repository": "remiotore/ccxt-freqtrade",
            "path": "strategies/CusTrend_coralTrend_Adx_EMA_Oct_1h.py",
            "blob_sha": "49f5057b67ac8a41ccc63ffa92f5810704b79c4c",
            "timeframe": "1h",
            "informative_timeframe": "4h",
            "leverage": 4.0,
            "stoploss_profit_ratio": -0.347,
            "trailing_positive": 0.01,
            "trailing_offset": 0.012,
            "roi": {"0": 0.101, "373": 0.068, "1088": 0.025, "1336": 0.0},
            "public_fixed_backtest_discovery_signal": {
                "timerange": ["2021-01-01", "2026-01-01"],
                "pairs": 33,
                "max_open_trades": 10,
                "trades": 20374,
                "win_rate": 0.772,
                "profit_factor": 1.66,
                "total_profit_fraction": 23.5453,
                "max_drawdown_fraction": 0.1376,
                "positive_months": "58/61",
                "accepted_as_project_evidence": False,
            },
        },
        "project_contract": {
            "engine": "NautilusTrader BacktestNode",
            "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "global_position_limit": 1,
            "risk_fraction": 0.03,
            "real_binance_1m_ohlc_execution": True,
            "complete_delayed_4h_candles_only": True,
            "exact_shifted_volume_mean": True,
            "trailing_detail": "causal 1m; no same-minute activation-and-hit",
            "cost_model": (
                "project fees + adverse slippage + funding reserve"
            ),
        },
        "development_interval": list(DEVELOPMENT),
        "holdout_interval": list(HOLDOUT),
        "conditional_continuous_interval": list(CONTINUOUS),
        "variants": VARIANTS,
        "router_sha256": hashlib.sha256(
            (REUSED / "router.py").read_bytes()
        ).hexdigest(),
        "strategy_sha256": hashlib.sha256(
            (REUSED / "strategy.py").read_bytes()
        ).hexdigest(),
    }
    dump(WORK / "variant_manifest.json", manifest)

    paths: dict[str, Path] = {}
    for name, mode in VARIANTS.items():
        config = copy.deepcopy(source)
        for key in (
            "sma_offset_low",
            "sma_offset_high",
            "sma_stop_min_fraction",
            "sma_stop_max_fraction",
            "sma_stop_atr_buffer",
        ):
            config["strategy"].pop(key, None)
        config["strategy"].update(
            {
                "cooldown_minutes": 0,
                "max_hold_minutes": 1_000_000,
                "funding_flatten_minute": 60,
                "funding_blackout_before_minutes": -1,
                "funding_blackout_after_minutes": -1,
                "mbe_bucket_minutes": 60,
                "mbe_variant": mode,
                "mbe_rsi_period": 14,
                "mbe_tema_period": 9,
                "mbe_bb_period": 20,
                "mbe_source_effective_leverage": 4.0,
                "mbe_source_stoploss": 0.347,
                "mbe_trailing_positive": 0.010,
                "mbe_trailing_offset": 0.012,
                "mbe_emergency_target_fraction": 0.25,
                "mbe_roi_0": 0.101,
                "mbe_roi_15": 0.068,
                "mbe_roi_41": 0.025,
                "mbe_roi_114": 0.0,
                "mbe_roi_180": 0.0,
                "mbe_roi_420": 0.0,
            }
        )
        path = WORK / f"{name}.json"
        dump(path, config)
        paths[name] = path
    return paths


def output_root(variant: str, stage: str) -> Path:
    return ARTIFACTS / f"custrend-v1-{variant}-{stage}"


def run_backtest(
    variant: str,
    config_path: Path,
    stage: str,
    interval: tuple[str, str],
) -> int:
    output = output_root(variant, stage)
    workspace = WORK / f"{variant}-{stage}"
    command = [
        sys.executable,
        str(REUSED / "launch.py"),
        "--config",
        str(config_path),
        "--start",
        interval[0],
        "--end",
        interval[1],
        "--cache",
        str(CACHE),
        "--output",
        str(output),
        "--workspace",
        str(workspace),
    ]
    env = dict(os.environ)
    old = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(REUSED) if not old else str(REUSED) + os.pathsep + old
    )
    print("RUN", variant, stage, interval, flush=True)
    completed = subprocess.run(command, env=env, check=False)
    (WORK / "status").mkdir(parents=True, exist_ok=True)
    (WORK / "status" / f"{variant}-{stage}.txt").write_text(
        f"{completed.returncode}\n"
    )
    return int(completed.returncode)


def read_result(
    variant: str,
    stage: str,
    returncode: int | None = None,
) -> dict[str, Any]:
    root = output_root(variant, stage)
    metrics_path = root / "metrics.json"
    diagnostics_path = root / "strategy_diagnostics.json"
    if not metrics_path.is_file() or not diagnostics_path.is_file():
        return {
            "produced": False,
            "returncode": returncode,
            "artifact_root": str(root.relative_to(ROOT)),
        }
    metrics = json.loads(metrics_path.read_text())
    diagnostics = json.loads(diagnostics_path.read_text())
    row: dict[str, Any] = {
        "produced": True,
        "returncode": returncode,
        "artifact_root": str(root.relative_to(ROOT)),
    }
    for key in (
        "ending_nav",
        "total_return",
        "geometric_daily_growth",
        "max_drawdown",
        "trades",
        "wins",
        "losses",
        "win_rate",
        "profit_factor",
        "expectancy_usdt",
        "largest_winner_share",
        "min_equity",
    ):
        row[key] = metrics.get(key)
    row.update(
        {
            "source_signals": diagnostics.get(
                "source_signals_before_execution_filters"
            ),
            "entries": diagnostics.get("entry_submissions"),
            "selected_symbols": diagnostics.get("selected_symbols"),
            "source_exit_signals": diagnostics.get("source_exit_signals"),
            "trailing_exits": diagnostics.get("mbe_trailing_exits"),
            "roi_exits": diagnostics.get("mbe_roi_exits"),
            "global_position_violations": diagnostics.get(
                "global_position_violations"
            ),
            "order_rejections": diagnostics.get("order_rejections"),
            "max_open_positions_observed": diagnostics.get(
                "max_open_positions_observed"
            ),
            "real_binance_1m_execution": diagnostics.get(
                "real_binance_1m_execution"
            ),
            "complete_delayed_4h_ema_only": diagnostics.get(
                "complete_delayed_4h_ema_only"
            ),
            "exact_shifted_volume_mean": diagnostics.get(
                "exact_shifted_volume_mean"
            ),
        }
    )
    return row


def gate(row: dict[str, Any], days: int, target_growth: float) -> dict[str, bool]:
    return {
        "trades_at_least_days": int(row.get("trades") or 0) >= days,
        "growth_target": float(
            row.get("geometric_daily_growth") or 0.0
        ) >= target_growth,
        "positive_expectancy": float(row.get("expectancy_usdt") or 0.0) > 0.0,
        "drawdown_lte_20pct": float(row.get("max_drawdown") or 1.0) <= 0.20,
        "positive_equity": float(row.get("min_equity") or 0.0) > 0.0,
        "one_position": int(row.get("global_position_violations") or 0) == 0,
        "no_rejections": int(row.get("order_rejections") or 0) == 0,
        "real_1m_execution": int(row.get("real_binance_1m_execution") or 0) == 1,
        "complete_delayed_4h": int(row.get("complete_delayed_4h_ema_only") or 0) == 1,
        "exact_shifted_volume": int(row.get("exact_shifted_volume_mean") or 0) == 1,
    }


def rank(names: list[str], comparison: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        names,
        key=lambda name: (
            -float(comparison[name].get("geometric_daily_growth") or 0.0),
            -float(comparison[name].get("expectancy_usdt") or 0.0),
            -int(comparison[name].get("trades") or 0),
            name,
        ),
    )


def copy_compact(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for filename in COMPACT_FILES:
        path = source / filename
        if path.is_file():
            shutil.copy2(path, destination / filename)


def persist(
    development: dict[str, Any],
    holdout: dict[str, Any],
    continuous: dict[str, Any],
    selected: str | None,
) -> dict[str, Any]:
    if continuous.get("project_gate_pass"):
        decision = "PASS_30D_PROJECT_GATE"
        reason = "Frozen external solution passed the 30-day continuous-account project gate."
    elif continuous.get("produced"):
        decision = "REJECT_OR_MINE_COMPONENTS"
        reason = "Frozen survivor failed at least one 30-day project gate."
    elif selected:
        decision = "EXECUTION_INCOMPLETE"
        reason = "A holdout survivor existed but conditional 30-day evidence was incomplete."
    else:
        decision = "REJECT_OR_MINE_COMPONENTS"
        reason = "No CusTrend interpretation survived both positive-alpha gates."
    result = {
        "candidate": "candidate-55",
        "family": "public_CusTrend_coralTrend_Adx_EMA_Oct_1h",
        "decision": decision,
        "reason": reason,
        "selected_variant": selected,
        "development": development,
        "holdout": holdout,
        "continuous_30d": continuous,
        "source_claim_accepted_as_evidence": False,
        "long_horizon_run": False,
        "production_ready": False,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    dump(EVIDENCE / "RESULT.json", result)
    shutil.copy2(WORK / "variant_manifest.json", EVIDENCE / "variant_manifest.json")
    dump(EVIDENCE / "development" / "comparison.json", development)
    dump(EVIDENCE / "holdout" / "assessment.json", holdout)
    for name in VARIANTS:
        copy_compact(output_root(name, "development"), EVIDENCE / "development" / name)
        copy_compact(output_root(name, "holdout"), EVIDENCE / "holdout" / name)
    if selected:
        copy_compact(output_root(selected, "continuous-30d"), EVIDENCE / "continuous")
    return result


def main() -> int:
    configs = create_configs()
    development_comparison: dict[str, dict[str, Any]] = {}
    for name, path in configs.items():
        code = run_backtest(name, path, "development", DEVELOPMENT)
        row = read_result(name, "development", code)
        checks = gate(row, 30, 0.0) if row.get("produced") else {}
        row["checks"] = checks
        row["gate_pass"] = bool(checks) and all(checks.values())
        development_comparison[name] = row
    eligible = rank(
        [name for name, row in development_comparison.items() if row.get("gate_pass")],
        development_comparison,
    )
    survivors = eligible[:1]
    development = {
        "comparison": development_comparison,
        "eligible": eligible,
        "survivors": survivors,
        "family_status": "TEST_HOLDOUT" if survivors else "REJECT_OR_MINE_COMPONENTS",
    }
    dump(ARTIFACTS / "custrend-v1-development-comparison.json", development)

    holdout_comparison: dict[str, dict[str, Any]] = {}
    for name in survivors:
        code = run_backtest(name, configs[name], "holdout", HOLDOUT)
        row = read_result(name, "holdout", code)
        checks = gate(row, 30, 0.0) if row.get("produced") else {}
        row["checks"] = checks
        row["gate_pass"] = bool(checks) and all(checks.values())
        holdout_comparison[name] = row
    holdout_eligible = rank(
        [name for name, row in holdout_comparison.items() if row.get("gate_pass")],
        holdout_comparison,
    )
    selected = holdout_eligible[0] if holdout_eligible else None
    holdout = {
        "comparison": holdout_comparison,
        "eligible": holdout_eligible,
        "selected": selected,
        "gate_pass": selected is not None,
    }
    dump(ARTIFACTS / "custrend-v1-holdout-assessment.json", holdout)

    continuous: dict[str, Any] = {"produced": False, "project_gate_pass": False}
    if selected:
        code = run_backtest(selected, configs[selected], "continuous-30d", CONTINUOUS)
        continuous = read_result(selected, "continuous-30d", code)
        checks = gate(continuous, 30, 0.01)
        continuous["checks"] = checks
        continuous["project_gate_pass"] = all(checks.values())

    result = persist(development, holdout, continuous, selected)
    dump(ARTIFACTS / "custrend-v1-final-result.json", result)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
