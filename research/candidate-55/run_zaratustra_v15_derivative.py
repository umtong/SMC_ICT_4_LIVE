"""Frozen source-versus-derivative-state V15 account experiment.

One development conclusion is tested: lower-band shorts should be owned only
when the perpetual leads spot downward (negative five-minute premium change)
and completed-minute aggressor flow is sell-side.  OI sign labels new-short
sponsorship versus liquidation release but is not tuned.  Source entry, stop,
trailing, risk and global arbitration are frozen.
"""
from __future__ import annotations

import copy
from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path.cwd()
CANDIDATE = ROOT / "research" / "candidate-55"
REUSED = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-55-v15-derivative"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
CACHE = ROOT / ".cache" / "candidate-55-v15-derivative"

_HELPER_SPEC = importlib.util.spec_from_file_location(
    "candidate55_v15_derivative_helpers",
    CANDIDATE / "run_zaratustra_v15_repair.py",
)
if _HELPER_SPEC is None or _HELPER_SPEC.loader is None:
    raise RuntimeError("cannot load Candidate 55 result helpers")
_HELPER = importlib.util.module_from_spec(_HELPER_SPEC)
sys.modules[_HELPER_SPEC.name] = _HELPER
_HELPER_SPEC.loader.exec_module(_HELPER)

WINDOWS = {
    "fresh-2024-08": ("2024-08-05", "2024-08-11"),
    "fresh-2025-04": ("2025-04-07", "2025-04-13"),
    "fresh-2026-06": ("2026-06-08", "2026-06-14"),
}
VARIANTS = {
    "source_bb_short": "source_bb_short",
    "derivative_sell_short": "derivative_sell_short",
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def output_root(variant: str, stage: str) -> Path:
    return ARTIFACTS / f"zaratustra-v15-derivative-{variant}-{stage}"

_HELPER.output_root = output_root


def create_config(variant: str) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    config = copy.deepcopy(json.loads((REUSED / "config.json").read_text(encoding="utf-8")))
    for key in (
        "sma_offset_low", "sma_offset_high", "sma_stop_min_fraction",
        "sma_stop_max_fraction", "sma_stop_atr_buffer",
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
            "v15_derivative_mode": VARIANTS[variant],
        }
    )
    path = WORK / f"config-{variant}.json"
    dump(path, config)
    return path


def run_backtest(config: Path, variant: str, stage: str, interval: tuple[str, str]) -> int:
    command = [
        sys.executable, str(REUSED / "launch.py"),
        "--config", str(config),
        "--start", interval[0], "--end", interval[1],
        "--cache", str(CACHE),
        "--output", str(output_root(variant, stage)),
        "--workspace", str(WORK / variant / stage),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REUSED), str(CANDIDATE), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return int(subprocess.run(command, env=env, check=False).returncode)


def main() -> int:
    configs = {variant: create_config(variant) for variant in VARIANTS}
    rows: list[dict[str, Any]] = []
    for stage, interval in WINDOWS.items():
        for variant in VARIANTS:
            code = run_backtest(configs[variant], variant, stage, interval)
            rows.append(_HELPER.read_result(variant, stage, interval, code))

    source = _HELPER.aggregate(rows, "source_bb_short")
    derivative = _HELPER.aggregate(rows, "derivative_sell_short")
    comparison = _HELPER.comparison(source, derivative)
    positive_windows = sum(
        bool(row.get("produced"))
        and row.get("variant") == "derivative_sell_short"
        and float(row.get("ending_nav") or 0.0) > float(row.get("starting_nav") or 0.0)
        for row in rows
    )
    warrant = {
        "positive_in_at_least_two_windows": positive_windows >= 2,
        "profit_factor_above_1_15": float(derivative.get("profit_factor") or 0.0) > 1.15,
        "gross_profit_retention_at_least_30pct": float(comparison.get("gross_profit_retention") or 0.0) >= 0.30,
        "gross_loss_reduction_at_least_50pct": float(comparison.get("gross_loss_reduction") or 0.0) >= 0.50,
        "at_least_half_trade_per_day": float(derivative.get("trades_per_day") or 0.0) >= 0.50,
        "mechanically_valid": bool(derivative.get("mechanically_valid")),
    }
    result = {
        "candidate": "candidate-55",
        "family": "V15_BB_SHORT_DERIVATIVE_LED_SELL",
        "development_evidence": {
            "windows": ["2024-10", "2025-02", "2025-09", "2026-04"],
            "natural_boundaries_only": True,
            "finding": (
                "premium_change_5m < 0 and flow_60s < 0 preserved the only pooled positive causal quadrant; "
                "premium_change_5m >= 0 was the dominant loss engine."
            ),
        },
        "frozen_policy": {
            "source_component": "V15 Bollinger short",
            "premium_condition": "premium_change_5m < 0",
            "flow_condition": "flow_60s < 0",
            "oi_role": "label OI build versus OI release; never threshold",
            "source_entry_changed": False,
            "source_stop_changed": False,
            "source_trailing_changed": False,
            "risk_fraction": 0.03,
            "global_slots": 1,
        },
        "prediction": (
            "The policy should remove spot-led/absorbed false positives while preserving new-short and liquidation-cascade gross profit."
        ),
        "falsification": (
            "It fails if fresh windows do not retain at least 30% of gross profit while removing at least half of gross loss, "
            "or if replacement arbitration leaves cost-after PF at or below 1.15."
        ),
        "runs": rows,
        "source_aggregate": source,
        "derivative_aggregate": derivative,
        "comparison": comparison,
        "positive_derivative_windows": positive_windows,
        "medium_warrant": warrant,
        "medium_replay_consumed": False,
        "production_ready": False,
    }
    dump(ARTIFACTS / "zaratustra-v15-derivative-final-result.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
