#!/usr/bin/env python3
"""Run Candidate 51 external-source tournament through the verified Nautilus shell."""
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

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
WORK = REPO / ".work" / "candidate-51-external-v17"
ARTIFACTS = REPO / "artifacts" / "candidate-51"
CACHE = REPO / ".cache" / "candidate-51-external-v17"
EVIDENCE = HERE / "evidence" / "v17-external-tournament"

DEVELOPMENT = ("2025-03-03", "2025-03-09")
HOLDOUT = ("2024-09-09", "2024-09-15")
CONTINUOUS_30D = ("2025-05-01", "2025-05-30")
CONTINUOUS_90D = ("2024-01-01", "2024-03-30")

VARIANTS: dict[str, dict[str, Any]] = {
    "winner_source_15m": {
        "mode": "winner",
        "winner_bucket": 15,
        "edge_z": 4.0,
        "edge_stop_z": 6.0,
        "hold": 360,
        "description": "BTCquant Winner15m public rules and source management",
    },
    "winner_adapted_5m": {
        "mode": "winner",
        "winner_bucket": 5,
        "edge_z": 4.0,
        "edge_stop_z": 6.0,
        "hold": 360,
        "description": "same public Winner rules adapted only to five-minute bars",
    },
    "edge_source_4sigma": {
        "mode": "edge",
        "winner_bucket": 15,
        "edge_z": 4.0,
        "edge_stop_z": 6.0,
        "hold": 180,
        "description": "EdgeBot public 4-sigma VWAP deviation and mean exit",
    },
    "edge_adapted_3sigma": {
        "mode": "edge",
        "winner_bucket": 15,
        "edge_z": 3.0,
        "edge_stop_z": 5.0,
        "hold": 180,
        "description": "one predeclared denser VWAP-deviation adaptation",
    },
    "combined_source": {
        "mode": "combined",
        "winner_bucket": 15,
        "edge_z": 4.0,
        "edge_stop_z": 6.0,
        "hold": 360,
        "description": "one-account arbitration of both public source families",
    },
    "combined_dense": {
        "mode": "combined",
        "winner_bucket": 5,
        "edge_z": 3.0,
        "edge_stop_z": 5.0,
        "hold": 360,
        "description": "one-account arbitration of the two predeclared dense adaptations",
    },
}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def frozen_config(base: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(base)
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
            "max_hold_minutes": int(spec["hold"]),
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "external_family_mode": str(spec["mode"]),
            "winner_bucket_minutes": int(spec["winner_bucket"]),
            "winner_ema_fast": 10,
            "winner_ema_slow": 30,
            "winner_macd_fast": 12,
            "winner_macd_slow": 26,
            "winner_macd_signal": 9,
            "winner_roc_period": 3,
            "winner_roc_threshold": 0.10,
            "winner_adx_period": 14,
            "winner_adx_threshold": 18.0,
            "winner_volume_period": 20,
            "winner_volume_ratio": 1.0,
            "winner_stop_fraction": 0.025,
            "winner_initial_target_fraction": 0.080,
            "winner_trailing_positive": 0.005,
            "winner_trailing_offset": 0.018,
            "winner_roi_0": 0.080,
            "winner_roi_480": 0.050,
            "winner_roi_1440": 0.030,
            "winner_roi_4320": 0.0,
            "edge_bucket_minutes": 15,
            "edge_vwap_period": 20,
            "edge_entry_z": float(spec["edge_z"]),
            "edge_stop_z": float(spec["edge_stop_z"]),
            "edge_min_sigma_fraction": 0.00075,
            "edge_min_reward_r": 1.25,
        }
    )
    return config


def run_case(
    *,
    label: str,
    config_path: Path,
    interval: tuple[str, str],
) -> tuple[int, Path]:
    output = ARTIFACTS / f"external-v17-{label}"
    workspace = WORK / f"workspace-{label}"
    command = [
        sys.executable,
        str(HERE / "launch.py"),
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
    env["PYTHONPATH"] = str(HERE)
    completed = subprocess.run(
        command,
        cwd=REPO,
        env=env,
        check=False,
    )
    return completed.returncode, output


def summarize(output: Path, returncode: int) -> dict[str, Any]:
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    if returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        return {
            "produced": False,
            "returncode": returncode,
        }
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    return {
        "produced": True,
        "returncode": returncode,
        **{
            key: metrics.get(key)
            for key in (
                "calendar_days",
                "ending_nav",
                "total_return",
                "geometric_daily_growth",
                "max_drawdown",
                "min_equity",
                "trades",
                "wins",
                "losses",
                "win_rate",
                "profit_factor",
                "expectancy_usdt",
                "largest_winner_share",
            )
        },
        "source_signals": diagnostics.get(
            "source_signals_before_execution_filters"
        ),
        "winner_entries": diagnostics.get("winner_entries"),
        "edge_mr_entries": diagnostics.get("edge_mr_entries"),
        "entries": diagnostics.get("entry_submissions"),
        "selected_symbols": diagnostics.get("selected_symbols"),
        "actionable_family_counts": diagnostics.get(
            "actionable_family_counts"
        ),
        "unresolved_reason_counts": diagnostics.get(
            "unresolved_reason_counts"
        ),
        "global_position_violations": diagnostics.get(
            "global_position_violations"
        ),
        "order_rejections": diagnostics.get("order_rejections"),
    }


def component_pass(row: dict[str, Any], days: int) -> bool:
    return bool(
        row.get("produced")
        and int(row.get("trades") or 0) >= days
        and float(row.get("expectancy_usdt") or 0.0) > 0.0
        and float(row.get("geometric_daily_growth") or 0.0) > 0.0
        and float(row.get("max_drawdown") or 1.0) <= 0.20
        and float(row.get("min_equity") or 0.0) > 0.0
        and int(row.get("global_position_violations") or 0) == 0
        and int(row.get("order_rejections") or 0) == 0
    )


def target_pass(row: dict[str, Any], days: int) -> bool:
    return bool(
        component_pass(row, days)
        and float(row.get("geometric_daily_growth") or 0.0) >= 0.01
    )


def preserve_case(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in (
        "metrics.json",
        "strategy_diagnostics.json",
        "run.json",
        "data_manifest.json",
    ):
        path = source / name
        if path.is_file():
            shutil.copy2(path, destination / name)


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    base = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    configs: dict[str, Path] = {}
    for name, spec in VARIANTS.items():
        path = WORK / f"{name}.json"
        write_json(path, frozen_config(base, spec))
        configs[name] = path

    manifest = {
        "family": "external_source_tournament_v17",
        "sources": {
            "BTCquant_Winner15m": {
                "repository": "win-boom/BTCquant",
                "path": "user_data/strategies/winner_strat.py",
                "public_claim": {
                    "annual_return": 0.276,
                    "sharpe": 3.23,
                    "win_rate": 0.658,
                    "six_sliding_windows_profitable": True,
                },
            },
            "EdgeBot_mr_meanrev_v3": {
                "site": "edgebotlab.com",
                "public_description": (
                    "BTCUSDT 15m; enter at 4 sigma from 20-period VWAP; exit at mean"
                ),
                "public_claim": {
                    "live_months": 14,
                    "trades": 1847,
                    "win_rate": 0.623,
                    "max_drawdown": 0.082,
                },
                "completion": (
                    "hard outer-sigma invalidation; no averaging or repeated episode entries"
                ),
            },
        },
        "variants": VARIANTS,
        "development_interval": DEVELOPMENT,
        "untouched_interval": HOLDOUT,
        "conditional_30d_interval": CONTINUOUS_30D,
        "conditional_90d_interval": CONTINUOUS_90D,
        "engine": "NautilusTrader BacktestNode",
        "risk_fraction": 0.03,
        "global_entry_or_position_limit": 1,
        "router_sha256": hashlib.sha256(
            (HERE / "router.py").read_bytes()
        ).hexdigest(),
        "strategy_sha256": hashlib.sha256(
            (HERE / "strategy.py").read_bytes()
        ).hexdigest(),
    }
    write_json(EVIDENCE / "manifest.json", manifest)

    development: dict[str, dict[str, Any]] = {}
    outputs: dict[str, Path] = {}
    for name in VARIANTS:
        returncode, output = run_case(
            label=f"{name}-development",
            config_path=configs[name],
            interval=DEVELOPMENT,
        )
        outputs[name] = output
        development[name] = summarize(output, returncode)
    eligible = [
        name
        for name, row in development.items()
        if component_pass(row, 7)
    ]
    eligible.sort(
        key=lambda name: (
            -float(
                development[name].get("geometric_daily_growth") or 0.0
            ),
            -float(development[name].get("expectancy_usdt") or 0.0),
            -int(development[name].get("trades") or 0),
            name,
        )
    )
    selected = eligible[0] if eligible else None
    development_decision = {
        "comparison": development,
        "eligible": eligible,
        "selected": selected,
        "status": "TEST_UNTOUCHED" if selected else "NO_EXTERNAL_SURVIVOR",
    }
    write_json(EVIDENCE / "development.json", development_decision)
    for name, output in outputs.items():
        preserve_case(
            output,
            EVIDENCE / "development-cases" / name,
        )

    holdout_row: dict[str, Any] = {
        "variant": selected,
        "produced": False,
        "component_pass": False,
    }
    holdout_output: Path | None = None
    if selected is not None:
        returncode, holdout_output = run_case(
            label=f"{selected}-holdout",
            config_path=configs[selected],
            interval=HOLDOUT,
        )
        holdout_row = {
            "variant": selected,
            **summarize(holdout_output, returncode),
        }
        holdout_row["component_pass"] = component_pass(
            holdout_row,
            7,
        )
        preserve_case(
            holdout_output,
            EVIDENCE / "holdout",
        )
        shutil.copy2(
            configs[selected],
            EVIDENCE / "selected_config.json",
        )
    write_json(EVIDENCE / "holdout_assessment.json", holdout_row)

    row_30d: dict[str, Any] = {
        "variant": selected,
        "produced": False,
        "target_pass": False,
    }
    output_30d: Path | None = None
    if holdout_row.get("component_pass") and selected is not None:
        returncode, output_30d = run_case(
            label=f"{selected}-continuous-30d",
            config_path=configs[selected],
            interval=CONTINUOUS_30D,
        )
        row_30d = {
            "variant": selected,
            **summarize(output_30d, returncode),
        }
        row_30d["target_pass"] = target_pass(row_30d, 30)
        preserve_case(
            output_30d,
            EVIDENCE / "continuous-30d",
        )
    write_json(EVIDENCE / "continuous_30d_assessment.json", row_30d)

    row_90d: dict[str, Any] = {
        "variant": selected,
        "produced": False,
        "target_pass": False,
    }
    output_90d: Path | None = None
    if row_30d.get("target_pass") and selected is not None:
        returncode, output_90d = run_case(
            label=f"{selected}-continuous-90d",
            config_path=configs[selected],
            interval=CONTINUOUS_90D,
        )
        row_90d = {
            "variant": selected,
            **summarize(output_90d, returncode),
        }
        row_90d["target_pass"] = target_pass(row_90d, 90)
        preserve_case(
            output_90d,
            EVIDENCE / "continuous-90d",
        )
    write_json(EVIDENCE / "continuous_90d_assessment.json", row_90d)

    final = {
        "selected": selected,
        "development_component_pass": bool(selected),
        "holdout_component_pass": bool(
            holdout_row.get("component_pass")
        ),
        "continuous_30d_target_pass": bool(
            row_30d.get("target_pass")
        ),
        "continuous_90d_target_pass": bool(
            row_90d.get("target_pass")
        ),
        "goal_met": bool(row_90d.get("target_pass")),
        "next_action": (
            "PROMOTE_FOR_LONGER_CONTINUOUS_AND_SHADOW_VALIDATION"
            if row_90d.get("target_pass")
            else "ABANDON_OR_MINE_ONLY_CAUSAL_COMPONENTS"
        ),
    }
    write_json(EVIDENCE / "FINAL_GATE.json", final)
    print(json.dumps(final, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
