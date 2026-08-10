"""Causal-episode tournament for the V15 early acceptance lifecycle.

The known period is used only to verify implementation and characterize the
repair. Two previously unused intervals are consumed sequentially. No long
replay is permitted here: the purpose is to determine whether the same causal
loss repair preserves gross-profit capacity and independent opportunity density
before any expensive validation is justified.
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
WORK = ROOT / ".work" / "candidate-55-v15-lifecycle"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
CACHE = ROOT / ".cache" / "candidate-55-v15-lifecycle"

_SPEC = importlib.util.spec_from_file_location(
    "candidate55_v15_lifecycle_helpers",
    CANDIDATE / "run_zaratustra_v15_repair.py",
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load shared Candidate 55 result helpers")
_HELPER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _HELPER
_SPEC.loader.exec_module(_HELPER)


def output_root(variant: str, stage: str) -> Path:
    return ARTIFACTS / f"zaratustra-v15-lifecycle-{variant}-{stage}"


_HELPER.output_root = output_root
IMPLEMENTATION_WINDOW = ("2026-03-01", "2026-03-07")
FRESH_A = ("2025-06-01", "2025-06-14")
FRESH_B = ("2024-07-01", "2024-07-14")
VARIANTS: dict[str, dict[str, Any]] = {
    "source": {
        "v15_lifecycle_mode": "source",
        "v15_acceptance_deadline_minutes": 3,
    },
    "accept_flow_1": {
        "v15_lifecycle_mode": "accept_flow_1",
        "v15_acceptance_deadline_minutes": 1,
    },
    "accept_depth_3": {
        "v15_lifecycle_mode": "accept_depth_3",
        "v15_acceptance_deadline_minutes": 3,
    },
    "accept_strict_3": {
        "v15_lifecycle_mode": "accept_strict_3",
        "v15_acceptance_deadline_minutes": 3,
    },
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def days(interval: tuple[str, str]) -> int:
    return (date.fromisoformat(interval[1]) - date.fromisoformat(interval[0])).days + 1


def create_config(variant: str) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    config = copy.deepcopy(
        json.loads((REUSED / "config.json").read_text(encoding="utf-8"))
    )
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
            "zaratustra_variant": "edge_exact",
            "zaratustra_startup_30m_candles": 10,
            "zaratustra_rsi_period": 14,
            "zaratustra_di_period": 14,
            "zaratustra_bb_period": 20,
            "zaratustra_source_leverage": 10.0,
            "zaratustra_source_stoploss": 0.15,
            "zaratustra_trailing_positive": 0.012,
            "zaratustra_trailing_offset": 0.107,
            "zaratustra_emergency_target_fraction": 0.50,
            "v15_relative_mode": "bb_relative_short",
            "v15_relative_lookback_minutes": 60,
            "v15_relative_min_fraction": 0.001,
            "v15_relative_max_fraction": 0.004,
            "v15_short_min_premium_change_5m": -0.00005,
            "v15_long_max_premium_change_5m": 0.00005,
            "v15_accepted_mode": "relative_basis_short",
            "v15_acceptance_efficiency_min": 0.007,
            "v15_acceptance_absorption_max": 0.37,
            **VARIANTS[variant],
        }
    )
    path = WORK / f"config-{variant}.json"
    dump(path, config)
    return path


def run_backtest(
    config: Path,
    variant: str,
    stage: str,
    interval: tuple[str, str],
) -> int:
    command = [
        sys.executable,
        str(REUSED / "launch.py"),
        "--config",
        str(config),
        "--start",
        interval[0],
        "--end",
        interval[1],
        "--cache",
        str(CACHE),
        "--output",
        str(output_root(variant, stage)),
        "--workspace",
        str(WORK / variant / stage),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REUSED) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    print("RUN", variant, stage, interval, flush=True)
    return int(subprocess.run(command, env=env, check=False).returncode)


def read_result(
    variant: str,
    stage: str,
    interval: tuple[str, str],
    code: int,
) -> dict[str, Any]:
    result = _HELPER.read_result(variant, stage, interval, code)
    path = output_root(variant, stage) / "strategy_diagnostics.json"
    if result.get("produced") and path.is_file():
        diagnostics = json.loads(path.read_text(encoding="utf-8"))
        for key in (
            "acceptance_positions_bound",
            "acceptance_checks",
            "acceptance_feature_stale",
            "acceptance_satisfied",
            "acceptance_failed_exits",
            "acceptance_price_failures",
            "acceptance_relative_failures",
            "acceptance_flow_failures",
            "acceptance_depth_failures",
            "acceptance_strict_failures",
            "entry_submissions",
            "max_open_positions_observed",
            "global_position_violations",
            "order_rejections",
            "liquidations",
        ):
            result[key] = diagnostics.get(key)
    return result


def run_one(
    configs: dict[str, Path],
    rows: list[dict[str, Any]],
    variant: str,
    stage: str,
    interval: tuple[str, str],
) -> None:
    code = run_backtest(configs[variant], variant, stage, interval)
    rows.append(read_result(variant, stage, interval, code))


def aggregate(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    return _HELPER.aggregate(rows, variant)


def comparison(source: dict[str, Any], repair: dict[str, Any]) -> dict[str, Any]:
    return _HELPER.comparison(source, repair)


def rank_repairs(aggregates: dict[str, dict[str, Any]]) -> list[str]:
    source = aggregates["source"]
    names = [name for name in aggregates if name != "source"]
    return sorted(
        names,
        key=lambda name: (
            int(aggregates[name]["positive_windows"]),
            float(aggregates[name]["net_pnl"]),
            float(comparison(source, aggregates[name])["gross_loss_reduction"]),
            float(comparison(source, aggregates[name])["gross_profit_retention"]),
            int(aggregates[name]["trades"]),
        ),
        reverse=True,
    )


def main() -> int:
    configs = {name: create_config(name) for name in VARIANTS}
    rows: list[dict[str, Any]] = []

    for variant in VARIANTS:
        run_one(
            configs,
            rows,
            variant,
            "implementation-2026-03",
            IMPLEMENTATION_WINDOW,
        )

    for variant in VARIANTS:
        run_one(configs, rows, variant, "fresh-a-2025-06", FRESH_A)

    first_two = {name: aggregate(rows, name) for name in VARIANTS}
    order = rank_repairs(first_two)

    second_stage_variants = ["source", *order[:2]]
    for variant in second_stage_variants:
        run_one(configs, rows, variant, "fresh-b-2024-07", FRESH_B)

    full_aggregates = {
        name: aggregate(rows, name) for name in second_stage_variants
    }
    comparisons = {
        name: comparison(full_aggregates["source"], full_aggregates[name])
        for name in second_stage_variants
        if name != "source"
    }
    final = {
        "candidate": "candidate-55",
        "method": "CAUSAL_EARLY_ACCEPTANCE_LIFECYCLE",
        "research_question": (
            "Can a completed-minute acceptance lifecycle preserve the high-capacity "
            "V15 BB profit engine while replacing its long full-stop loss engine?"
        ),
        "source_entry_changed": False,
        "source_stop_changed": False,
        "source_trailing_changed": False,
        "variant_definitions": VARIANTS,
        "implementation_window": IMPLEMENTATION_WINDOW,
        "fresh_a": FRESH_A,
        "fresh_b": FRESH_B,
        "all_runs": rows,
        "first_two_stage_aggregates": first_two,
        "repair_order_after_fresh_a": order,
        "second_stage_variants": second_stage_variants,
        "full_aggregates": full_aggregates,
        "comparisons_to_source": comparisons,
        "long_replay_consumed": False,
        "production_ready": False,
        "next_decision": (
            "Inspect every lifecycle exit, retained winner, retained full stop, "
            "missed source winner and replacement trade before choosing whether "
            "a fresh medium replay has information value."
        ),
    }
    dump(ARTIFACTS / "zaratustra-v15-lifecycle-final-result.json", final)
    dump(CANDIDATE / "evidence" / "v15-lifecycle" / "RESULT.json", final)
    print(json.dumps(final, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
