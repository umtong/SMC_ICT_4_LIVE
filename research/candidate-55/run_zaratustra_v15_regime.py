"""Hypothesis-led V15 trend-quality experiment.

This runner first audits every source Bollinger-short and rejected clean-state
episode without an account.  It then performs exactly one structural A/B:
unchanged source BB short versus the predeclared clean-down owner.  A medium
continuous replay is spent only when the predicted episode group changes and
the real one-slot account both support the same explanation.
"""
from __future__ import annotations

import copy
from datetime import date
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path.cwd()
CANDIDATE = ROOT / "research" / "candidate-55"
REUSED = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-55-v15-regime"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
CACHE = ROOT / ".cache" / "candidate-55-v15-regime"

_HELPER_SPEC = importlib.util.spec_from_file_location(
    "candidate55_v15_regime_helpers",
    CANDIDATE / "run_zaratustra_v15_repair.py",
)
if _HELPER_SPEC is None or _HELPER_SPEC.loader is None:
    raise RuntimeError("cannot load Candidate 55 result helpers")
_HELPER = importlib.util.module_from_spec(_HELPER_SPEC)
sys.modules[_HELPER_SPEC.name] = _HELPER
_HELPER_SPEC.loader.exec_module(_HELPER)

SHORT_WINDOWS = {
    "diagnostic-2026-04": ("2026-04-01", "2026-04-30"),
    "fresh-2025-02": ("2025-02-10", "2025-02-16"),
    "fresh-2025-09": ("2025-09-01", "2025-09-14"),
}
MEDIUM_WINDOW = ("2024-10-01", "2024-10-30")
VARIANTS = {
    "source_bb_short": "source_bb_short",
    "clean_down_bb_short": "clean_down_bb_short",
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def days(interval: tuple[str, str]) -> int:
    return (date.fromisoformat(interval[1]) - date.fromisoformat(interval[0])).days + 1


def output_root(variant: str, stage: str) -> Path:
    return ARTIFACTS / f"zaratustra-v15-regime-{variant}-{stage}"


_HELPER.output_root = output_root


def create_config(variant: str) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    config = copy.deepcopy(json.loads((REUSED / "config.json").read_text(encoding="utf-8")))
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
            "zaratustra_variant": "bb_only",
            "zaratustra_startup_30m_candles": 10,
            "zaratustra_rsi_period": 14,
            "zaratustra_di_period": 14,
            "zaratustra_bb_period": 20,
            "zaratustra_source_leverage": 10.0,
            "zaratustra_source_stoploss": 0.15,
            "zaratustra_trailing_positive": 0.012,
            "zaratustra_trailing_offset": 0.107,
            "zaratustra_emergency_target_fraction": 0.50,
            "v15_regime_mode": VARIANTS[variant],
            "v15_regime_bucket_minutes": 30,
            "v15_regime_period": 21,
            "v15_regime_return_eff_threshold": 0.05,
            "v15_regime_range_eff_threshold": 0.03,
            "v15_regime_adx_threshold": 25.0,
            "v15_regime_efficiency_threshold": 0.50,
        }
    )
    path = WORK / f"config-{variant}.json"
    dump(path, config)
    return path


def run_forensics(stage: str, interval: tuple[str, str]) -> dict[str, Any]:
    output = ARTIFACTS / f"zaratustra-v15-regime-forensics-{stage}"
    command = [
        sys.executable,
        str(CANDIDATE / "v15_regime_episode_forensics.py"),
        "--start", interval[0],
        "--end", interval[1],
        "--cache", str(CACHE),
        "--output", str(output),
        "--regime-bucket-minutes", "30",
        "--regime-period", "21",
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REUSED), str(CANDIDATE), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    completed = subprocess.run(command, env=env, check=False)
    summary_path = output / "SUMMARY.json"
    if not summary_path.is_file():
        return {
            "stage": stage,
            "interval": list(interval),
            "produced": False,
            "returncode": int(completed.returncode),
        }
    value = json.loads(summary_path.read_text(encoding="utf-8"))
    value.update(
        {
            "stage": stage,
            "produced": True,
            "returncode": int(completed.returncode),
            "artifact_root": str(output.relative_to(ROOT)),
        }
    )
    return value


def run_backtest(config: Path, variant: str, stage: str, interval: tuple[str, str]) -> int:
    command = [
        sys.executable,
        str(REUSED / "launch.py"),
        "--config", str(config),
        "--start", interval[0],
        "--end", interval[1],
        "--cache", str(CACHE),
        "--output", str(output_root(variant, stage)),
        "--workspace", str(WORK / variant / stage),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REUSED), str(CANDIDATE), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    print("RUN", variant, stage, interval, flush=True)
    return int(subprocess.run(command, env=env, check=False).returncode)


def pnl_number(value: Any) -> float:
    text = str(value).strip().split()[0].replace("_", "").replace(",", "")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def regime_trade_engine(root: Path) -> dict[str, Any]:
    path = root / "closed_scenarios.json"
    if not path.is_file():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[str, list[float]] = {}
    for row in rows:
        diagnostics = row.get("diagnostics", {})
        label = str(diagnostics.get("regime_label", "MISSING"))
        grouped.setdefault(label, []).append(pnl_number(row.get("realized_pnl")))
    output: dict[str, Any] = {}
    for label, pnls in sorted(grouped.items()):
        gross_profit = sum(value for value in pnls if value > 0.0)
        gross_loss = -sum(value for value in pnls if value < 0.0)
        output[label] = {
            "trades": len(pnls),
            "wins": sum(value > 0.0 for value in pnls),
            "losses": sum(value < 0.0 for value in pnls),
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "net_pnl": sum(pnls),
            "profit_factor": (
                gross_profit / gross_loss
                if gross_loss > 0.0
                else (None if gross_profit > 0.0 else 0.0)
            ),
        }
    return output


def read_result(variant: str, stage: str, interval: tuple[str, str], code: int) -> dict[str, Any]:
    result = _HELPER.read_result(variant, stage, interval, code)
    root = output_root(variant, stage)
    diagnostics_path = root / "strategy_diagnostics.json"
    if result.get("produced") and diagnostics_path.is_file():
        diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        for key in (
            "regime_raw_actionable",
            "regime_short_candidates",
            "regime_long_rejections",
            "regime_stale_rejections",
            "regime_state_rejections",
            "regime_clean_eligible",
            "regime_selected",
            "regime_alternative_symbol_selected",
            "regime_label_counts",
        ):
            result[key] = diagnostics.get(key)
        result["trade_engine_by_regime"] = regime_trade_engine(root)
    return result


def aggregate(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    return _HELPER.aggregate(rows, variant)


def compare(source: dict[str, Any], repair: dict[str, Any]) -> dict[str, Any]:
    return _HELPER.comparison(source, repair)


def account_window_comparison(rows: list[dict[str, Any]], stage: str) -> dict[str, Any]:
    source = next(
        row for row in rows
        if row.get("stage") == stage and row.get("variant") == "source_bb_short"
    )
    clean = next(
        row for row in rows
        if row.get("stage") == stage and row.get("variant") == "clean_down_bb_short"
    )
    return {
        "stage": stage,
        "source_produced": bool(source.get("produced")),
        "clean_produced": bool(clean.get("produced")),
        "source_net": float(source.get("ending_nav") or 0.0) - float(source.get("starting_nav") or 0.0),
        "clean_net": float(clean.get("ending_nav") or 0.0) - float(clean.get("starting_nav") or 0.0),
        "net_improvement": (
            float(clean.get("ending_nav") or 0.0) - float(clean.get("starting_nav") or 0.0)
            - float(source.get("ending_nav") or 0.0) + float(source.get("starting_nav") or 0.0)
        ),
        "source_gross_profit": float(source.get("gross_profit") or 0.0),
        "clean_gross_profit": float(clean.get("gross_profit") or 0.0),
        "source_gross_loss": float(source.get("gross_loss") or 0.0),
        "clean_gross_loss": float(clean.get("gross_loss") or 0.0),
        "source_trades": int(source.get("trades") or 0),
        "clean_trades": int(clean.get("trades") or 0),
    }


def main() -> int:
    configs = {variant: create_config(variant) for variant in VARIANTS}

    forensic_rows = [
        run_forensics(stage, interval)
        for stage, interval in SHORT_WINDOWS.items()
    ]
    forensic_supported = sum(
        bool(row.get("prediction_supported_in_this_window"))
        for row in forensic_rows
        if row.get("produced")
    )

    account_rows: list[dict[str, Any]] = []
    for stage, interval in SHORT_WINDOWS.items():
        for variant in VARIANTS:
            code = run_backtest(configs[variant], variant, stage, interval)
            account_rows.append(read_result(variant, stage, interval, code))

    source_aggregate = aggregate(account_rows, "source_bb_short")
    clean_aggregate = aggregate(account_rows, "clean_down_bb_short")
    short_comparison = compare(source_aggregate, clean_aggregate)
    by_window = [account_window_comparison(account_rows, stage) for stage in SHORT_WINDOWS]
    improved_windows = sum(float(row["net_improvement"]) > 0.0 for row in by_window)

    causal_warrant = {
        "forensic_prediction_supported_in_at_least_two_windows": forensic_supported >= 2,
        "real_account_improved_in_at_least_two_windows": improved_windows >= 2,
        "aggregate_net_improved": float(short_comparison.get("net_improvement") or 0.0) > 0.0,
        "gross_profit_retention_at_least_55pct": float(short_comparison.get("gross_profit_retention") or 0.0) >= 0.55,
        "gross_loss_reduction_at_least_30pct": float(short_comparison.get("gross_loss_reduction") or 0.0) >= 0.30,
        "clean_profit_factor_above_one": float(clean_aggregate.get("profit_factor") or 0.0) > 1.0,
        "clean_opportunity_density_at_least_half_per_day": float(clean_aggregate.get("trades_per_day") or 0.0) >= 0.5,
        "mechanically_valid": bool(clean_aggregate.get("mechanically_valid")),
    }
    run_medium = all(causal_warrant.values())

    medium_rows: list[dict[str, Any]] = []
    medium_comparison: dict[str, Any] = {
        "produced": False,
        "not_run_reason": "episode prediction and short account did not jointly justify medium replay",
    }
    if run_medium:
        for variant in VARIANTS:
            code = run_backtest(configs[variant], variant, "medium-2024-10", MEDIUM_WINDOW)
            medium_rows.append(read_result(variant, "medium-2024-10", MEDIUM_WINDOW, code))
        account_rows.extend(medium_rows)
        source_medium = aggregate(medium_rows, "source_bb_short")
        clean_medium = aggregate(medium_rows, "clean_down_bb_short")
        medium_comparison = {
            "produced": True,
            "source": source_medium,
            "clean": clean_medium,
            "comparison": compare(source_medium, clean_medium),
        }

    result = {
        "candidate": "candidate-55",
        "family": "V15_BB_SHORT_FIXED_TREND_QUALITY",
        "hypothesis": (
            "The V15 Bollinger-short opportunity engine should be owned only during clean downside price discovery; "
            "choppy/non-directional states are predicted to contain a disproportionate false-positive loss engine."
        ),
        "predicted_trade_changes": {
            "remove": "source BB-short episodes labelled choppy, ranging or clean-up before arbitration",
            "preserve": "BB-short episodes labelled trending_down_clean with source entry/stop/trailing unchanged",
            "expected_cost": "fewer V-shaped or early reversal wins and fewer total trades",
            "falsification": (
                "clean states fail to improve episode follow-through, or account gross profit falls in the same proportion "
                "as gross loss, or replacement trades erase the apparent benefit"
            ),
        },
        "external_reused_solution": (
            "go-trader composite clean/choppy regime split: ATR-normalized displacement/range, Kaufman efficiency and ADX"
        ),
        "fixed_configuration": {
            "source_entry": "V15 Bollinger short edge",
            "regime_bucket_minutes": 30,
            "regime_period": 21,
            "return_eff_threshold": 0.05,
            "range_eff_threshold": 0.03,
            "adx_threshold": 25.0,
            "efficiency_threshold": 0.50,
            "source_stop_changed": False,
            "source_trailing_changed": False,
            "risk_fraction": 0.03,
            "global_slots": 1,
        },
        "forensics": forensic_rows,
        "forensic_supported_windows": forensic_supported,
        "account_runs": account_rows,
        "account_by_window": by_window,
        "source_short_aggregate": source_aggregate,
        "clean_short_aggregate": clean_aggregate,
        "short_comparison": short_comparison,
        "causal_warrant_for_medium": causal_warrant,
        "medium_replay_consumed": bool(medium_rows),
        "medium_comparison": medium_comparison,
        "long_replay_consumed": False,
        "long_not_run_reason": (
            "This experiment is a single causal state test. Long validation requires a strong medium continuous result first."
        ),
        "production_ready": False,
    }
    dump(ARTIFACTS / "zaratustra-v15-regime-final-result.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
