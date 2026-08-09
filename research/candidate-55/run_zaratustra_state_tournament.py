"""Run the Candidate 55 ZaratustraV5 causal-management tournament."""
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
WORK = ROOT / ".work" / "candidate-55-zaratustra-state-v2"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
EVIDENCE = CANDIDATE / "evidence" / "v5-zaratustra-state"
CACHE = ROOT / ".cache" / "candidate-55-zaratustra-state-v2"

VARIANTS = {
    "strict_5m": "strict_5m",
    "majority_5m": "majority_5m",
    "strict_15m": "strict_15m",
    "majority_15m": "majority_15m",
}
DEVELOPMENT = ("2026-07-29", "2026-08-04")
HOLDOUT = ("2026-07-01", "2026-07-07")
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
        "family": "public_ZaratustraV5_edge_with_state_invalidation",
        "parent_evidence": {
            "workflow_run": 31322697772,
            "artifact": 9040704073,
            "development_interval": ["2026-07-29", "2026-08-04"],
            "frozen_entry_variant": "edge_both",
            "ending_nav": 99936.31909696,
            "geometric_daily_growth": -0.00009099755652730579,
            "trades": 8,
            "win_rate": 0.75,
            "profit_factor": 1.1675575847577817,
            "expectancy_usdt": 64.84027961999998,
            "max_drawdown": 0.04888679277121999,
            "structural_failure": (
                "one full source stop erased six small trend-following wins"
            ),
        },
        "repair": {
            "entry_policy_changed": False,
            "source_entry": "5m/15m/30m RSI50 + DI25 + BB-middle rising edge",
            "changed_component": "management only",
            "strict": "exit after any source component fails",
            "majority": "exit after at least two source components fail",
            "timeframes_tested_minutes": [5, 15],
            "source_price_stop_retained": True,
            "source_trailing_retained": True,
            "new_numeric_price_thresholds": False,
        },
        "source": {
            "repository": "remiotore/ccxt-freqtrade",
            "path": "strategies/ZaratustraV5.py",
            "blob_sha": "af0ba19353574b7bb60a983402014375917e1f15",
        },
        "project_contract": {
            "engine": "NautilusTrader BacktestNode",
            "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "global_position_limit": 1,
            "risk_fraction": 0.03,
            "real_binance_1m_ohlc_execution": True,
            "cost_model": (
                "project fees + adverse slippage + funding reserve"
            ),
        },
        "development_interval": list(DEVELOPMENT),
        "untouched_interval": list(HOLDOUT),
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
                "zaratustra_variant": "edge_both",
                "zaratustra_startup_30m_candles": 30,
                "zaratustra_rsi_period": 14,
                "zaratustra_di_period": 14,
                "zaratustra_bb_period": 20,
                "zaratustra_source_leverage": 10.0,
                "zaratustra_source_stoploss": 0.296,
                "zaratustra_trailing_positive": 0.013,
                "zaratustra_trailing_offset": 0.071,
                "zaratustra_emergency_target_fraction": 0.50,
                "zaratustra_state_exit_mode": mode,
            }
        )
        path = WORK / f"{name}.json"
        dump(path, config)
        paths[name] = path
    return paths


def output_root(variant: str, stage: str) -> Path:
    return ARTIFACTS / f"zaratustra-state-v2-{variant}-{stage}"


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
            "state_exit_checks": diagnostics.get(
                "zaratustra_state_exit_checks"
            ),
            "state_exits": diagnostics.get("zaratustra_state_exits"),
            "state_exit_histogram": diagnostics.get(
                "zaratustra_state_exit_failed_component_histogram"
            ),
            "trailing_activations": diagnostics.get(
                "zaratustra_trailing_activations"
            ),
            "trailing_exits": diagnostics.get(
                "zaratustra_trailing_exits"
            ),
            "global_position_violations": diagnostics.get(
                "global_position_violations"
            ),
            "order_rejections": diagnostics.get("order_rejections"),
            "real_binance_ohlc_execution": diagnostics.get(
                "real_binance_ohlc_execution"
            ),
            "entry_policy_frozen": diagnostics.get("entry_policy_frozen"),
        }
    )
    return row


def positive_gate(row: dict[str, Any], days: int) -> dict[str, bool]:
    return {
        "trades_at_least_days": int(row.get("trades") or 0) >= days,
        "positive_growth": float(
            row.get("geometric_daily_growth") or 0.0
        )
        > 0.0,
        "positive_expectancy": float(row.get("expectancy_usdt") or 0.0)
        > 0.0,
        "drawdown_lte_20pct": float(row.get("max_drawdown") or 1.0)
        <= 0.20,
        "positive_equity": float(row.get("min_equity") or 0.0) > 0.0,
        "one_position": int(
            row.get("global_position_violations") or 0
        )
        == 0,
        "no_rejections": int(row.get("order_rejections") or 0) == 0,
        "real_ohlc": int(row.get("real_binance_ohlc_execution") or 0)
        == 1,
        "entry_frozen": row.get("entry_policy_frozen") == "edge_both",
    }


def rank(
    names: list[str], comparison: dict[str, dict[str, Any]]
) -> list[str]:
    return sorted(
        names,
        key=lambda name: (
            -float(
                comparison[name].get("geometric_daily_growth") or 0.0
            ),
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


def persist_evidence(
    development: dict[str, Any],
    holdout: dict[str, Any],
    continuous: dict[str, Any],
    selected: str | None,
) -> dict[str, Any]:
    if continuous.get("project_gate_pass"):
        decision = "PASS_30D_PROJECT_GATE"
        reason = (
            "Frozen state-management repair passed the 30-day continuous "
            "account gate."
        )
    elif continuous.get("produced"):
        decision = "REJECT_OR_REDESIGN"
        reason = "Frozen survivor failed at least one 30-day project gate."
    elif selected:
        decision = "EXECUTION_INCOMPLETE"
        reason = "A holdout survivor existed but 30-day evidence was incomplete."
    else:
        decision = "REJECT_OR_REDESIGN"
        reason = "No state-management variant survived both short gates."
    result = {
        "candidate": "candidate-55",
        "family": "public_ZaratustraV5_edge_with_state_invalidation",
        "decision": decision,
        "reason": reason,
        "selected_variant": selected,
        "development": development,
        "holdout": holdout,
        "continuous_30d": continuous,
        "entry_policy_changed": False,
        "long_horizon_run": False,
        "production_ready": False,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    dump(EVIDENCE / "RESULT.json", result)
    shutil.copy2(
        WORK / "variant_manifest.json", EVIDENCE / "variant_manifest.json"
    )
    dump(EVIDENCE / "development" / "comparison.json", development)
    dump(EVIDENCE / "holdout" / "assessment.json", holdout)
    for name in VARIANTS:
        copy_compact(
            output_root(name, "development"),
            EVIDENCE / "development" / name,
        )
        copy_compact(
            output_root(name, "holdout"),
            EVIDENCE / "holdout" / name,
        )
    if selected:
        copy_compact(
            output_root(selected, "continuous-30d"),
            EVIDENCE / "continuous",
        )
    return result


def main() -> int:
    configs = create_configs()
    development_comparison: dict[str, dict[str, Any]] = {}
    for name, path in configs.items():
        code = run_backtest(name, path, "development", DEVELOPMENT)
        development_comparison[name] = read_result(
            name, "development", code
        )

    development_eligible: list[str] = []
    for name, row in development_comparison.items():
        checks = positive_gate(row, 7) if row.get("produced") else {}
        row["checks"] = checks
        row["gate_pass"] = bool(checks) and all(checks.values())
        if row["gate_pass"]:
            development_eligible.append(name)
    development_eligible = rank(
        development_eligible, development_comparison
    )
    survivors = development_eligible[:2]
    development = {
        "comparison": development_comparison,
        "eligible": development_eligible,
        "survivors": survivors,
        "family_status": (
            "TEST_UNTOUCHED"
            if survivors
            else "REJECT_OR_REDESIGN"
        ),
    }
    dump(
        ARTIFACTS / "zaratustra-state-v2-development-comparison.json",
        development,
    )

    holdout_comparison: dict[str, dict[str, Any]] = {}
    for name in survivors:
        code = run_backtest(name, configs[name], "holdout", HOLDOUT)
        row = read_result(name, "holdout", code)
        checks = positive_gate(row, 7) if row.get("produced") else {}
        row["checks"] = checks
        row["gate_pass"] = bool(checks) and all(checks.values())
        holdout_comparison[name] = row
    holdout_eligible = rank(
        [
            name
            for name, row in holdout_comparison.items()
            if row.get("gate_pass")
        ],
        holdout_comparison,
    )
    selected = holdout_eligible[0] if holdout_eligible else None
    holdout = {
        "comparison": holdout_comparison,
        "eligible": holdout_eligible,
        "selected": selected,
        "gate_pass": selected is not None,
    }
    dump(
        ARTIFACTS / "zaratustra-state-v2-holdout-assessment.json",
        holdout,
    )

    continuous: dict[str, Any] = {
        "produced": False,
        "project_gate_pass": False,
    }
    if selected:
        code = run_backtest(
            selected,
            configs[selected],
            "continuous-30d",
            CONTINUOUS,
        )
        continuous = read_result(selected, "continuous-30d", code)
        checks = positive_gate(continuous, 30)
        checks["daily_geometric_growth_at_least_1pct"] = float(
            continuous.get("geometric_daily_growth") or 0.0
        ) >= 0.01
        continuous["checks"] = checks
        continuous["project_gate_pass"] = all(checks.values())

    result = persist_evidence(
        development, holdout, continuous, selected
    )
    dump(
        ARTIFACTS / "zaratustra-state-v2-final-result.json", result
    )
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
