"""Run a structural-capacity tournament for public ZaratustraV15."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path.cwd()
CANDIDATE = ROOT / "research" / "candidate-55"
REUSED = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-55-zaratustra-v15-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
CACHE = ROOT / ".cache" / "candidate-55-zaratustra-v15-v1"

VARIANTS = {
    "source_level_exact": "source_level_exact",
    "edge_exact": "edge_exact",
    "edge_normalized": "edge_normalized",
    "edge_normalized_di": "edge_normalized_di",
    "bb_only": "bb_only",
}
PROJECT_ELIGIBLE = {
    "edge_exact",
    "edge_normalized",
    "edge_normalized_di",
    "bb_only",
}
DEVELOPMENT = ("2026-07-22", "2026-07-28")
HOLDOUT = ("2026-06-22", "2026-06-28")
CONTINUOUS = ("2026-05-01", "2026-05-30")


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def create_configs() -> dict[str, Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    source = json.loads((REUSED / "config.json").read_text(encoding="utf-8"))
    manifest = {
        "candidate": "candidate-55",
        "family": "public_ZaratustraV15_structural_capacity",
        "source": {
            "repository": "remiotore/ccxt-freqtrade",
            "path": "strategies/ZaratustraV15.py",
            "blob_sha": "7f1e39e37949d732fa6b675b93fd808a73b8445c",
            "timeframe": "5m",
            "entry": "DI+OBV+MFI+ATR state OR Bollinger breakout",
            "source_stoploss_profit_ratio": -0.15,
            "source_leverage": 10.0,
            "source_trailing_positive": 0.012,
            "source_trailing_offset": 0.107,
            "source_trailing_only_offset_is_reached": True,
            "source_absolute_atr_threshold": 0.2,
            "predeclared_dimensionless_repair": "ATR/close < 0.002",
            "public_discovery_signal": {
                "pairs": 33,
                "timerange": ["2021-01-01", "2026-01-01"],
                "trades": 237825,
                "win_rate": 0.599,
                "profit_factor": 1.29,
                "average_profit_per_trade_fraction": 0.0185,
                "average_duration": "1h48m",
                "max_drawdown_fraction": 0.1857,
                "cagr_fraction": 2.375,
                "positive_months": "60/61",
                "accepted_as_project_evidence": False,
            },
        },
        "structural_screen": {
            "reason": (
                "The public system has enough five-minute signal density and "
                "per-trade payoff to be mathematically capable of 1%/day under "
                "one slot and a 3% planned-loss budget if the edge transfers."
            ),
            "development_min_geometric_daily_growth": 0.002,
            "development_min_profit_factor": 1.15,
            "development_min_independent_trades_per_day": 1.0,
            "diagnostic_level_reentry_never_project_eligible": True,
        },
        "project_contract": {
            "engine": "NautilusTrader BacktestNode",
            "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "global_position_limit": 1,
            "risk_fraction": 0.03,
            "real_binance_1m_execution": True,
            "fees_slippage_funding_reserved": True,
            "same_minute_trail_hindsight_allowed": False,
        },
        "development_interval": list(DEVELOPMENT),
        "untouched_interval": list(HOLDOUT),
        "conditional_continuous_interval": list(CONTINUOUS),
        "variants": VARIANTS,
        "project_eligible_variants": sorted(PROJECT_ELIGIBLE),
        "router_sha256": hashlib.sha256((REUSED / "router.py").read_bytes()).hexdigest(),
        "strategy_sha256": hashlib.sha256((REUSED / "strategy.py").read_bytes()).hexdigest(),
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
                "zaratustra_variant": mode,
                "zaratustra_startup_30m_candles": 10,
                "zaratustra_rsi_period": 14,
                "zaratustra_di_period": 14,
                "zaratustra_bb_period": 20,
                "zaratustra_source_leverage": 10.0,
                "zaratustra_source_stoploss": 0.15,
                "zaratustra_trailing_positive": 0.012,
                "zaratustra_trailing_offset": 0.107,
                "zaratustra_emergency_target_fraction": 0.50,
            }
        )
        path = WORK / f"{name}.json"
        dump(path, config)
        paths[name] = path
    return paths


def output_root(variant: str, stage: str) -> Path:
    return ARTIFACTS / f"zaratustra-v15-v1-{variant}-{stage}"


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
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REUSED) if not previous else str(REUSED) + os.pathsep + previous
    print("RUN", variant, stage, interval, flush=True)
    completed = subprocess.run(command, env=env, check=False)
    return int(completed.returncode)


def read_result(variant: str, stage: str, returncode: int | None) -> dict[str, Any]:
    root = output_root(variant, stage)
    metrics_path = root / "metrics.json"
    diagnostics_path = root / "strategy_diagnostics.json"
    if not metrics_path.is_file() or not diagnostics_path.is_file():
        return {
            "produced": False,
            "returncode": returncode,
            "artifact_root": str(root.relative_to(ROOT)),
        }
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    row: dict[str, Any] = {
        "produced": True,
        "returncode": returncode,
        "artifact_root": str(root.relative_to(ROOT)),
        "project_eligible_variant": variant in PROJECT_ELIGIBLE,
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
            "source_signals": diagnostics.get("source_signals_before_execution_filters"),
            "entries": diagnostics.get("entry_submissions"),
            "selected_symbols": diagnostics.get("selected_symbols"),
            "trailing_activations": diagnostics.get("zaratustra_trailing_activations"),
            "trailing_exits": diagnostics.get("zaratustra_trailing_exits"),
            "unresolved_reason_counts": diagnostics.get("unresolved_reason_counts"),
            "global_position_violations": diagnostics.get("global_position_violations"),
            "order_rejections": diagnostics.get("order_rejections"),
            "max_open_positions_observed": diagnostics.get("max_open_positions_observed"),
            "real_binance_ohlc_execution": diagnostics.get("real_binance_ohlc_execution"),
            "one_minute_trailing_detail": diagnostics.get("one_minute_trailing_detail"),
            "same_minute_trail_activation_and_hit_allowed": diagnostics.get(
                "same_minute_trail_activation_and_hit_allowed"
            ),
        }
    )
    days = 30 if stage == "continuous-30d" else 7
    row["trades_per_day"] = float(row.get("trades") or 0) / days
    row["growth_capacity_fraction_of_target"] = float(
        row.get("geometric_daily_growth") or 0.0
    ) / 0.01
    return row


def mechanics(row: dict[str, Any]) -> dict[str, bool]:
    return {
        "positive_equity": float(row.get("min_equity") or 0.0) > 0.0,
        "no_rejections": int(row.get("order_rejections") or 0) == 0,
        "one_position": int(row.get("global_position_violations") or 0) == 0,
        "max_one_observed_position": int(row.get("max_open_positions_observed") or 0) <= 1,
        "real_ohlc": int(row.get("real_binance_ohlc_execution") or 0) == 1,
        "one_minute_trailing": int(row.get("one_minute_trailing_detail") or 0) == 1,
        "no_same_minute_trail_hindsight": int(
            row.get("same_minute_trail_activation_and_hit_allowed") or 0
        ) == 0,
        "drawdown_lte_20pct": float(row.get("max_drawdown") or 1.0) <= 0.20,
    }


def structural_gate(row: dict[str, Any], days: int) -> dict[str, bool]:
    return {
        **mechanics(row),
        "project_eligible_rising_edge_or_natural_edge": bool(
            row.get("project_eligible_variant")
        ),
        "independent_trades_at_least_days": int(row.get("trades") or 0) >= days,
        "geometric_daily_growth_at_least_0_2pct": float(
            row.get("geometric_daily_growth") or 0.0
        ) >= 0.002,
        "profit_factor_at_least_1_15": float(row.get("profit_factor") or 0.0) >= 1.15,
        "positive_expectancy": float(row.get("expectancy_usdt") or 0.0) > 0.0,
        "diagnostic_drawdown_lte_10pct": float(row.get("max_drawdown") or 1.0) <= 0.10,
    }


def project_gate(row: dict[str, Any], days: int) -> dict[str, bool]:
    return {
        **mechanics(row),
        "independent_trades_at_least_days": int(row.get("trades") or 0) >= days,
        "geometric_daily_growth_at_least_1pct": float(
            row.get("geometric_daily_growth") or 0.0
        ) >= 0.01,
        "positive_expectancy": float(row.get("expectancy_usdt") or 0.0) > 0.0,
    }


def rank(names: list[str], comparison: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        names,
        key=lambda name: (
            -float(comparison[name].get("geometric_daily_growth") or 0.0),
            -float(comparison[name].get("profit_factor") or 0.0),
            -float(comparison[name].get("expectancy_usdt") or 0.0),
            -int(comparison[name].get("trades") or 0),
            name,
        ),
    )


def main() -> int:
    configs = create_configs()

    development_comparison: dict[str, dict[str, Any]] = {}
    for name, path in configs.items():
        code = run_backtest(name, path, "development", DEVELOPMENT)
        row = read_result(name, "development", code)
        checks = structural_gate(row, 7) if row.get("produced") else {}
        row["structural_checks"] = checks
        row["structural_pass"] = bool(checks) and all(checks.values())
        development_comparison[name] = row

    eligible = rank(
        [name for name, row in development_comparison.items() if row.get("structural_pass")],
        development_comparison,
    )
    survivors = eligible[:2]
    development = {
        "comparison": development_comparison,
        "eligible": eligible,
        "survivors": survivors,
        "family_status": "STRUCTURAL_SURVIVOR" if survivors else "STRUCTURALLY_REJECTED",
    }
    dump(ARTIFACTS / "zaratustra-v15-v1-development-comparison.json", development)

    holdout_comparison: dict[str, dict[str, Any]] = {}
    for name in survivors:
        code = run_backtest(name, configs[name], "holdout", HOLDOUT)
        row = read_result(name, "holdout", code)
        checks = structural_gate(row, 7) if row.get("produced") else {}
        row["structural_checks"] = checks
        row["structural_pass"] = bool(checks) and all(checks.values())
        holdout_comparison[name] = row
    holdout_eligible = rank(
        [name for name, row in holdout_comparison.items() if row.get("structural_pass")],
        holdout_comparison,
    )
    selected = holdout_eligible[0] if holdout_eligible else None
    holdout = {
        "comparison": holdout_comparison,
        "eligible": holdout_eligible,
        "selected": selected,
        "gate_pass": selected is not None,
    }
    dump(ARTIFACTS / "zaratustra-v15-v1-holdout-assessment.json", holdout)

    continuous: dict[str, Any] = {"produced": False, "project_gate_pass": False}
    if selected is not None:
        code = run_backtest(selected, configs[selected], "continuous-30d", CONTINUOUS)
        continuous = read_result(selected, "continuous-30d", code)
        checks = project_gate(continuous, 30) if continuous.get("produced") else {}
        continuous["project_checks"] = checks
        continuous["project_gate_pass"] = bool(checks) and all(checks.values())

    if continuous.get("project_gate_pass"):
        decision = "PASS_30D_PROJECT_GATE"
        reason = "A structurally screened V15 variant passed the full 30-day project gate."
    elif continuous.get("produced"):
        decision = "REJECT_AFTER_30D"
        reason = "The frozen structural survivor failed the 30-day 1%/day continuous-account gate."
    elif selected is not None:
        decision = "EXECUTION_INCOMPLETE"
        reason = "A holdout survivor existed but the continuous result was incomplete."
    else:
        decision = "STRUCTURALLY_REJECTED"
        reason = (
            "No independent-edge V15 variant simultaneously supplied sufficient "
            "trade density, at least 0.2%/day short-stage growth, PF >= 1.15, "
            "positive expectancy and valid execution in both short intervals."
        )

    result = {
        "candidate": "candidate-55",
        "family": "public_ZaratustraV15_structural_capacity",
        "decision": decision,
        "reason": reason,
        "selected_variant": selected,
        "development": development,
        "holdout": holdout,
        "continuous_30d": continuous,
        "source_level_reentry_is_diagnostic_only": True,
        "source_claim_accepted_as_project_evidence": False,
        "long_horizon_run": False,
        "production_ready": bool(continuous.get("project_gate_pass")),
    }
    dump(ARTIFACTS / "zaratustra-v15-v1-final-result.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
