"""Run Candidate 55's source-first ZaratustraV13 tournament."""
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
WORK = ROOT / ".work" / "candidate-55-zaratustra-v13-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
EVIDENCE = CANDIDATE / "evidence" / "v4-zaratustra-v13"
CACHE = ROOT / ".cache" / "candidate-55-zaratustra-v13-v1"

VARIANTS = {
    "source_both": "source_both",
    "source_long": "source_long",
    "source_short": "source_short",
    "di_both": "di_both",
}
DEVELOPMENT = ("2026-07-22", "2026-07-28")
HOLDOUT = ("2026-06-22", "2026-06-28")
CONTINUOUS = ("2026-05-01", "2026-05-30")
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
        "family": "public_ZaratustraV13",
        "source": {
            "repository": "remiotore/ccxt-freqtrade",
            "path": "strategies/ZaratustraV13.py",
            "blob_sha": "c8e46aa6b0164f6638c379e3cbd7ba7d9b28cd23",
            "timeframe": "5m",
            "entry": (
                "asymmetric DI level OR Bollinger-band breakout edge"
            ),
            "minimal_roi": {},
            "explicit_exit_signal": False,
            "source_stoploss_profit_ratio": -0.296,
            "source_leverage": 10.0,
            "source_trailing_positive": 0.01,
            "source_trailing_offset": 0.10,
            "source_trailing_only_offset_is_reached": True,
            "public_fixed_backtest_discovery_signal": {
                "timerange": ["2021-01-01", "2026-01-01"],
                "pairs": 33,
                "max_open_trades": 10,
                "stake_usdt": 100,
                "starting_wallet_usdt": 1000,
                "trades": 150813,
                "win_rate": 0.761,
                "profit_factor": 1.52,
                "total_profit_fraction": 539.3976,
                "max_drawdown_fraction": 0.1710,
                "cagr_fraction": 2.518,
                "average_leverage": 10.0,
                "average_profit_per_trade_fraction": 0.0359,
                "average_duration": "2h54m",
                "positive_months": "60/61",
                "accepted_as_project_evidence": False,
            },
            "public_walk_forward_discovery_signal": {
                "timerange": ["2026-01-01", "2026-07-01"],
                "trades": 7247,
                "total_profit_fraction": 21.5859,
                "win_rate": 0.775,
                "profit_factor": 1.44,
                "max_drawdown_fraction": 0.2697,
                "accepted_as_project_evidence": False,
            },
        },
        "project_contract": {
            "engine": "NautilusTrader BacktestNode",
            "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "global_position_limit": 1,
            "risk_fraction": 0.03,
            "real_binance_1m_ohlc_execution": True,
            "trailing_detail": "causal 1m; no same-minute activation-and-hit",
            "cost_model": (
                "project fees + adverse slippage + funding reserve"
            ),
        },
        "development_interval": list(DEVELOPMENT),
        "untouched_interval": list(HOLDOUT),
        "conditional_continuous_interval": list(CONTINUOUS),
        "source_code_frozen_before_all_intervals": True,
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
                "zaratustra_variant": mode,
                "zaratustra_startup_30m_candles": 10,
                "zaratustra_rsi_period": 14,
                "zaratustra_di_period": 14,
                "zaratustra_bb_period": 20,
                "zaratustra_source_leverage": 10.0,
                "zaratustra_source_stoploss": 0.296,
                "zaratustra_trailing_positive": 0.010,
                "zaratustra_trailing_offset": 0.100,
                "zaratustra_emergency_target_fraction": 0.50,
            }
        )
        path = WORK / f"{name}.json"
        dump(path, config)
        paths[name] = path
    return paths


def output_root(variant: str, stage: str) -> Path:
    return ARTIFACTS / f"zaratustra-v13-v1-{variant}-{stage}"


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
            "trailing_activations": diagnostics.get(
                "zaratustra_trailing_activations"
            ),
            "trailing_exits": diagnostics.get(
                "zaratustra_trailing_exits"
            ),
            "unresolved_reason_counts": diagnostics.get(
                "unresolved_reason_counts"
            ),
            "global_position_violations": diagnostics.get(
                "global_position_violations"
            ),
            "order_rejections": diagnostics.get("order_rejections"),
            "max_open_positions_observed": diagnostics.get(
                "max_open_positions_observed"
            ),
            "real_binance_ohlc_execution": diagnostics.get(
                "real_binance_ohlc_execution"
            ),
            "one_minute_trailing_detail": diagnostics.get(
                "one_minute_trailing_detail"
            ),
            "same_minute_trail_activation_and_hit_allowed": diagnostics.get(
                "same_minute_trail_activation_and_hit_allowed"
            ),
            "source_asymmetric_dx_clause_preserved": diagnostics.get(
                "source_asymmetric_dx_clause_preserved"
            ),
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
        "one_minute_trailing": int(
            row.get("one_minute_trailing_detail") or 0
        )
        == 1,
        "no_same_minute_trail_hindsight": int(
            row.get("same_minute_trail_activation_and_hit_allowed") or 0
        )
        == 0,
        "source_asymmetry_preserved": int(
            row.get("source_asymmetric_dx_clause_preserved") or 0
        )
        == 1,
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
            "Frozen V13 interpretation passed the 30-day continuous-account "
            "project gate under real 1m execution."
        )
    elif continuous.get("produced"):
        decision = "REJECT_OR_MINE_SIGNAL"
        reason = "Frozen survivor failed at least one 30-day project gate."
    elif selected:
        decision = "EXECUTION_INCOMPLETE"
        reason = "A holdout survivor existed but 30-day evidence was incomplete."
    else:
        decision = "REJECT_OR_MINE_SIGNAL"
        reason = "No V13 variant survived both short positive-alpha gates."

    result = {
        "candidate": "candidate-55",
        "family": "public_ZaratustraV13",
        "decision": decision,
        "reason": reason,
        "selected_variant": selected,
        "development": development,
        "holdout": holdout,
        "continuous_30d": continuous,
        "source_claim_accepted_as_evidence": False,
        "one_minute_trailing_detail": True,
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

    development_eligible = []
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
            else "REJECT_OR_MINE_SIGNAL"
        ),
    }
    dump(
        ARTIFACTS / "zaratustra-v13-v1-development-comparison.json",
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
        ARTIFACTS / "zaratustra-v13-v1-holdout-assessment.json",
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
        ARTIFACTS / "zaratustra-v13-v1-final-result.json", result
    )
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
