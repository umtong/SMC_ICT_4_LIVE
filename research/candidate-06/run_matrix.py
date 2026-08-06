"""Predeclared controlled first-week experiments, then frozen week-2/week-3 validation.

Every performance result is produced by ``run_validation.py``, whose only execution
path is NautilusTrader's BacktestEngine.  This module does not contain a parallel
simulator or PnL engine.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


def _variant(base: dict[str, Any], name: str, description: str, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    config = copy.deepcopy(base)
    config["candidate_variant"] = name
    config["variant_description"] = description
    mutate(config)
    return config


def _baseline(_: dict[str, Any]) -> None:
    return None


def _srr_only(config: dict[str, Any]) -> None:
    logic = config["logic"]
    logic["acceptance_close_atr"] = 100.0
    logic["acceptance_body_atr"] = 100.0
    logic["acceptance_flow_ratio"] = 1.0


def _sac_only(config: dict[str, Any]) -> None:
    logic = config["logic"]
    logic["rejection_wick_fraction"] = 1.1
    logic["large_range_override_atr"] = 100.0
    logic["absorption_flow_ratio"] = 1.0


def _price_structure_only(config: dict[str, Any]) -> None:
    logic = config["logic"]
    for key in ("acceptance_flow_ratio", "confirm_flow_ratio", "absorption_flow_ratio", "retest_max_opposing_flow"):
        if key in logic:
            logic[key] = 0.0


def _higher_selectivity(config: dict[str, Any]) -> None:
    logic = config["logic"]
    for key in ("sweep_min_atr", "min_relative_volume", "acceptance_body_atr", "acceptance_close_atr", "confirm_body_atr", "confirm_flow_ratio"):
        if key in logic:
            logic[key] = float(logic[key]) * 1.25


def _higher_coverage(config: dict[str, Any]) -> None:
    logic = config["logic"]
    for key in ("sweep_min_atr", "min_relative_volume", "acceptance_body_atr", "acceptance_close_atr", "confirm_body_atr", "confirm_flow_ratio"):
        if key in logic:
            floor = 0.02 if "flow" in key else 0.05
            logic[key] = max(floor, float(logic[key]) * 0.80)
    logic["cooldown_bars"] = max(1, int(logic.get("cooldown_bars", 1)) * 3 // 4)


def _faster_structure(config: dict[str, Any]) -> None:
    logic = config["logic"]
    logic["fast_lookback"] = max(10, int(logic["fast_lookback"] * 0.67))
    logic["slow_lookback"] = max(logic["fast_lookback"] * 3, int(logic["slow_lookback"] * 0.67))


def _slower_structure(config: dict[str, Any]) -> None:
    logic = config["logic"]
    logic["fast_lookback"] = int(logic["fast_lookback"] * 1.50)
    logic["slow_lookback"] = int(logic["slow_lookback"] * 1.50)


VARIANTS: tuple[tuple[str, str, Callable[[dict[str, Any]], None]], ...] = (
    ("baseline", "Unchanged causal specification.", _baseline),
    ("srr_only", "Ablation: sweep-reject-reversal family only.", _srr_only),
    ("sac_only", "Ablation: sweep-accept-continuation family only.", _sac_only),
    ("price_structure_only", "Ablation: the taker-buy proxy is removed from directional gates.", _price_structure_only),
    ("higher_selectivity", "Controlled test: 25% stronger normalized activity and response evidence.", _higher_selectivity),
    ("higher_coverage", "Controlled test: 20% lower normalized activity and response evidence.", _higher_coverage),
    ("faster_structure", "Controlled test: 33% shorter fast/slow liquidity memory.", _faster_structure),
    ("slower_structure", "Controlled test: 50% longer fast/slow liquidity memory.", _slower_structure),
)


def _run(config_path: Path, output: Path, week_index: int, candidate_dir: Path, repository: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(candidate_dir / "run_validation.py"),
        "--config",
        str(config_path),
        "--output",
        str(output),
        "--week-index",
        str(week_index),
        "--allow-gate-fail",
    ]
    completed = subprocess.run(command, cwd=repository, text=True, capture_output=True, check=False)
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


def _render_matrix(results: list[dict[str, Any]], selected: str | None) -> str:
    lines = [
        "# Candidate 06 controlled first-week matrix",
        "",
        "Selection is predeclared priority, not maximum-return optimization.",
        "",
        f"Selected: `{selected}`" if selected else "Selected: none",
        "",
        "|variant|engine rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        metrics = result.get("metrics", {})
        lines.append(
            "|{name}|{returncode}|{gate}|{growth:.6%}|{trades}|{win_rate:.2%}|{pf}|{drawdown:.2%}|{share:.2%}|{failures}|".format(
                name=result["name"],
                returncode=result["returncode"],
                gate=metrics.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", -1.0)),
                trades=metrics.get("trades"),
                win_rate=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                drawdown=float(metrics.get("max_drawdown_nav", 0.0)),
                share=float(metrics.get("largest_positive_trade_share", 1.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-06/development-matrix"))
    args = parser.parse_args()

    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))

    results: list[dict[str, Any]] = []
    for name, description, mutate in VARIANTS:
        config = _variant(base, name, description, mutate)
        config_path = output / f"{name}.json"
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record = _run(config_path, output / name, 0, candidate_dir, repository)
        record.update({"name": name, "description": description})
        results.append(record)

    # Fixed causal priority: preserve the baseline whenever it qualifies.  The
    # first qualifying controlled hypothesis is locked; return is never used to
    # choose among passing variants.
    selected = next((result["name"] for result in results if result.get("gate_passed")), None)
    locked_path: Path | None = None
    frozen_validation: list[dict[str, Any]] = []
    if selected is not None:
        locked = json.loads((output / f"{selected}.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.locked.json"
        locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for week_index in (1, 2):
            record = _run(locked_path, output / f"locked-week-{week_index + 1}", week_index, candidate_dir, repository)
            record["week_index"] = week_index
            frozen_validation.append(record)

    all_frozen_passed = bool(selected) and all(record.get("gate_passed") for record in frozen_validation) and len(frozen_validation) == 2
    summary = {
        "design": "predeclared controlled first-week matrix followed by two frozen weeks",
        "selection_rule": "first gate-qualified variant in fixed causal priority",
        "variant_priority": [name for name, _, _ in VARIANTS],
        "selected": selected,
        "locked_config": None if locked_path is None else str(locked_path.relative_to(repository)),
        "first_week_results": results,
        "frozen_validation": frozen_validation,
        "all_three_weeks_passed": all_frozen_passed,
        "long_evaluation_authorized": all_frozen_passed,
    }
    (output / "matrix_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    (output / "MATRIX.md").write_text(_render_matrix(results, selected), encoding="utf-8")

    lines = [
        "# Candidate 06 staged decision",
        "",
        f"First-week selected variant: `{selected}`" if selected else "No first-week variant passed the gate.",
        "",
        f"All three frozen weeks passed: `{all_frozen_passed}`",
        "",
        f"Long evaluation authorized: `{all_frozen_passed}`",
        "",
    ]
    for record in frozen_validation:
        metrics = record.get("metrics", {})
        lines.extend(
            [
                f"## Frozen week {int(record['week_index']) + 1}",
                "",
                f"- gate: `{record.get('gate_passed')}`",
                f"- geometric daily NAV growth: `{metrics.get('geometric_daily_nav_growth')}`",
                f"- trades: `{metrics.get('trades')}`",
                f"- win rate: `{metrics.get('win_rate')}`",
                f"- max drawdown: `{metrics.get('max_drawdown_nav')}`",
                f"- failures: `{metrics.get('gate_failures')}`",
                "",
            ]
        )
    (output / "DECISION.md").write_text("\n".join(lines), encoding="utf-8")

    if selected is None:
        return 2
    if not all_frozen_passed:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
