"""Predeclared v0.5 Session Equilibrium Retest experiments in NautilusTrader."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


def _run(config_path: Path, output: Path, week_index: int, candidate_dir: Path, repository: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(candidate_dir / "run_validation.py"),
            "--config",
            str(config_path),
            "--output",
            str(output),
            "--week-index",
            str(week_index),
            "--allow-gate-fail",
        ],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    record: dict[str, Any] = {
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-12000:],
    }
    metrics_path = output / "metrics.json"
    if metrics_path.exists():
        record["metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
        record["gate_passed"] = bool(record["metrics"].get("gate_passed"))
    else:
        record["gate_passed"] = False
    return record


def _base(base: dict[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(base)
    config["logic"].update(
        {
            "engine": "SESSION_EQUILIBRIUM_RETEST",
            "asia_start_minute_utc": 0,
            "asia_end_minute_utc": 360,
            "london_start_minute_utc": 420,
            "london_end_minute_utc": 660,
            "new_york_start_minute_utc": 780,
            "new_york_end_minute_utc": 1020,
            "session_use_asia_levels": True,
            "session_use_previous_day_levels": True,
            "session_use_flow_proxy": True,
            "session_sweep_min_atr": 0.10,
            "session_response_bars": 3,
            "session_displacement_body_atr": 0.45,
            "session_displacement_close_atr": 0.08,
            "session_displacement_flow_ratio": 0.05,
            "session_displacement_retest_bars": 6,
            "session_displacement_retrace_fraction": 0.50,
            "session_displacement_invalidation_atr": 0.05,
            "session_displacement_max_opposing_flow": 0.22,
            "session_retest_rejection_body_atr": 0.12,
            "session_acceptance_close_atr": 0.12,
            "session_acceptance_body_atr": 0.45,
            "session_acceptance_flow_ratio": 0.08,
            "session_retest_bars": 7,
            "session_retest_band_atr": 0.18,
            "session_acceptance_reclaim_atr": 0.08,
            "session_retest_max_opposing_flow": 0.20,
            "session_projection_fraction": 1.0,
            "minimum_structural_rr": 1.25,
            "stop_buffer_atr": 0.10,
            "cooldown_bars": 5,
            "ambiguous_cooldown_bars": 3,
            "enable_srr": True,
            "enable_sac": True,
        }
    )
    return config


def _unchanged(_: dict[str, Any]) -> None:
    return None


def _srr_only(config: dict[str, Any]) -> None:
    config["logic"]["enable_sac"] = False


def _asia_only(config: dict[str, Any]) -> None:
    config["logic"]["session_use_previous_day_levels"] = False


def _previous_day_only(config: dict[str, Any]) -> None:
    config["logic"]["session_use_asia_levels"] = False


def _price_only(config: dict[str, Any]) -> None:
    config["logic"]["session_use_flow_proxy"] = False


VARIANTS: tuple[tuple[str, str, Callable[[dict[str, Any]], None]], ...] = (
    ("equilibrium_full", "Displacement-retest entries; internal, equilibrium, then external objectives.", _unchanged),
    ("equilibrium_srr_only", "Ablation: reversal family only.", _srr_only),
    ("equilibrium_asia_only", "Ablation: completed Asia range boundaries only.", _asia_only),
    ("equilibrium_previous_day_only", "Ablation: previous UTC-day boundaries only.", _previous_day_only),
    ("equilibrium_price_only", "Ablation: price structure without directional taker-buy proxy.", _price_only),
)


def _render(results: list[dict[str, Any]], selected: str | None, frozen: list[dict[str, Any]]) -> str:
    lines = [
        "# Candidate 06 v0.5 Session Equilibrium Retest",
        "",
        "Selection uses fixed causal priority rather than maximum backtest return.",
        "",
        f"Selected: `{selected}`" if selected else "Selected: none",
        "",
        "|variant|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        metrics = result.get("metrics", {})
        lines.append(
            "|{name}|{rc}|{gate}|{growth:.6%}|{trades}|{win:.2%}|{pf}|{dd:.2%}|{share:.2%}|{failures}|".format(
                name=result["name"], rc=result["returncode"], gate=metrics.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", -1.0)), trades=metrics.get("trades"),
                win=float(metrics.get("win_rate", 0.0)), pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                share=float(metrics.get("largest_positive_trade_share", 1.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            )
        )
    for record in frozen:
        metrics = record.get("metrics", {})
        lines.extend(
            [
                "",
                f"## Frozen week {int(record['week_index']) + 1}",
                "",
                f"- gate: `{record.get('gate_passed')}`",
                f"- geometric daily NAV growth: `{metrics.get('geometric_daily_nav_growth')}`",
                f"- trades: `{metrics.get('trades')}`",
                f"- win rate: `{metrics.get('win_rate')}`",
                f"- maximum drawdown: `{metrics.get('max_drawdown_nav')}`",
                f"- failures: `{metrics.get('gate_failures')}`",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-06/equilibrium-matrix"))
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = _base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))

    results: list[dict[str, Any]] = []
    for name, description, mutate in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        mutate(config)
        path = output / f"{name}.json"
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record = _run(path, output / name, 0, candidate_dir, repository)
        record.update({"name": name, "description": description})
        results.append(record)

    selected = next((record["name"] for record in results if record.get("gate_passed")), None)
    frozen: list[dict[str, Any]] = []
    locked_path: Path | None = None
    if selected is not None:
        locked = json.loads((output / f"{selected}.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.equilibrium.locked.json"
        locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for week_index in (1, 2):
            record = _run(locked_path, output / f"locked-week-{week_index + 1}", week_index, candidate_dir, repository)
            record["week_index"] = week_index
            frozen.append(record)

    all_three = selected is not None and len(frozen) == 2 and all(record.get("gate_passed") for record in frozen)
    summary = {
        "design": "session equilibrium targets with fixed causal ablations and two frozen weeks",
        "variant_priority": [name for name, _, _ in VARIANTS],
        "selection_rule": "first gate-qualified variant in fixed priority",
        "selected": selected,
        "locked_config": None if locked_path is None else str(locked_path.relative_to(repository)),
        "first_week_results": results,
        "frozen_validation": frozen,
        "all_three_weeks_passed": all_three,
        "long_evaluation_authorized": all_three,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "SUMMARY.md").write_text(_render(results, selected, frozen), encoding="utf-8")
    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
