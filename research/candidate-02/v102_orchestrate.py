#!/usr/bin/env python3
"""Run v102 development controls and its one permitted logic ablation.

This is orchestration only. Every fill, fee, position and NAV transition is
produced by the existing v53 NautilusTrader runner in a fresh subprocess.
"""
from __future__ import annotations

import copy
from datetime import date
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import pandas as pd

from v53_nt_core import load_feature_matrix, load_raw_one_minute

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "research/candidate-02"
TOOL_SOURCE = ROOT / ".candidate-02-v75"
INPUTS = ROOT / "inputs"
ARTIFACTS = ROOT / "artifacts"
TMP = Path("/tmp/candidate-02-v102")
WEEKS = {"week1": date(2024, 9, 16), "week2": date(2024, 1, 29)}
RESPONSES = (2, 3, 4)
DATE_PATTERN = re.compile(
    r"EVALUATION_START\s*=\s*date\(\s*\d{4}\s*,\s*\d{1,2}\s*,\s*\d{1,2}\s*\)"
)


def run(command: list[str], *, accepted: set[int] | None = None) -> int:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    valid = accepted if accepted is not None else {0}
    if result.returncode not in valid:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")
    return result.returncode


def materialize_tools(label: str, start: date) -> Path:
    target = TMP / f"tools-{label}"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(TOOL_SOURCE, target)
    replacements = {
        "inputs/v75-first-week": f"inputs/v102-{label}",
        "candidate-02-v75-first-week": f"candidate-02-v102-{label}",
        "v75-first-week": f"v102-{label}",
        "candidate-02-v75-quarter-hour-algorithmic-opening-auction":
            "candidate-02-v102-quarter-hour-impact-retention",
    }
    count = 0
    for path in target.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".json", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        if path.name.startswith("collect") and path.suffix == ".py":
            text, changed = DATE_PATTERN.subn(
                f"EVALUATION_START = date({start.year}, {start.month}, {start.day})",
                text,
            )
            count += changed
        path.write_text(text, encoding="utf-8")
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one date constructor, found {count}")
    for path in (target / "collect_first_week.py", target / "build_features.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    return target


def collect_and_build(label: str, start: date) -> Path:
    input_root = INPUTS / f"v102-{label}"
    if input_root.exists():
        shutil.rmtree(input_root)
    tools = materialize_tools(label, start)
    run([sys.executable, str(tools / "collect_first_week.py")])
    run([sys.executable, str(tools / "build_features.py")])
    feature_root = input_root / "candidate-02-v48-first-week"
    raw_root = input_root / f".cache/candidate-02/v102-{label}/binance_1m"
    required_files = [feature_root / "v48_features.npz", feature_root / "columns.json"]
    if not all(path.is_file() for path in required_files):
        raise RuntimeError(f"{label}: missing feature matrix")
    for kind in ("aggTrades", "bookDepth"):
        if len(list((input_root / f"direct/{kind}").glob("*.zip"))) != 10:
            raise RuntimeError(f"{label}: incomplete {kind}")
    if len(list(raw_root.glob("*.zip"))) != 10:
        raise RuntimeError(f"{label}: incomplete one-minute bars")
    verify_coverage(label, start, feature_root, raw_root)
    return input_root


def verify_coverage(label: str, start_date: date, feature_root: Path, raw_root: Path) -> None:
    features = load_feature_matrix(feature_root / "v48_features.npz", feature_root / "columns.json")
    raw = load_raw_one_minute(raw_root)
    start = pd.Timestamp(start_date, tz="UTC")
    end = start + pd.Timedelta(days=7)
    required = {
        "close", "aggressive_signed_quote_1m", "aggressive_total_quote_1m",
        "signed_flow_ratio_1m", "ask_depth_change_1m", "bid_depth_change_1m",
        "depth_imbalance_1pct", "qh_opening_10s_signed_quote",
        "qh_opening_10s_total_quote", "qh_opening_10s_flow_ratio",
        "qh_opening_10s_return", "qh_opening_10s_abs_return",
        "qh_opening_10s_round_share_2", "qh_opening_10s_eligible_round_2",
        "qh_rest_50s_flow_ratio", "qh_full_minute_return",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise RuntimeError(f"{label}: missing features {missing}")
    feature_eval = features.loc[(features.index >= start) & (features.index < end)]
    raw_eval = raw.loc[(raw.index >= start) & (raw.index < end)]
    if len(feature_eval) < 7 * 24 * 60 * 0.995:
        raise RuntimeError(f"{label}: feature coverage {len(feature_eval)}")
    if len(raw_eval) < 7 * 24 * 60 * 0.995:
        raise RuntimeError(f"{label}: raw coverage {len(raw_eval)}")
    if features.index.min() > start - pd.Timedelta(days=2) + pd.Timedelta(minutes=2):
        raise RuntimeError(f"{label}: warmup missing")
    if features.index.max() < end:
        raise RuntimeError(f"{label}: features end early")


def config_for(*, label: str, start: date, response: int, ablation: bool) -> Path:
    base = json.loads((RESEARCH / "v102_base_config.json").read_text(encoding="utf-8"))
    cfg = copy.deepcopy(base)
    suffix = "no-response-flow" if ablation else "retained-impact"
    cfg["candidate"] = f"candidate-02-v102-{label}-{suffix}-{response}m"
    cfg["scenario"]["response_minutes"] = response
    if ablation:
        cfg["scenario"]["minimum_response_flow_alignment"] = -0.999
    cfg["validation"]["first_week_start"] = start.isoformat()
    cfg["validation"]["selection_stage"] = (
        "revealed same-week single-variable response-flow ablation"
        if ablation else "revealed development control only; not promotion evidence"
    )
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / f"{label}-{response}m-{'ablate-flow' if ablation else 'base'}.json"
    path.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
    return path


def run_variant(*, label: str, start: date, response: int, ablation: bool) -> dict[str, Any]:
    suffix = "ablate-response-flow" if ablation else "base"
    output = ARTIFACTS / f"candidate-02-v102-{label}-{response}m-{suffix}"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    config = config_for(label=label, start=start, response=response, ablation=ablation)
    input_root = INPUTS / f"v102-{label}"
    exit_code = run(
        [
            sys.executable,
            str(RESEARCH / "v102_nt_backtest.py"),
            "--config", str(config),
            "--input-root", str(input_root),
            "--output", str(output),
        ],
        accepted={0, 2},
    )
    (output / "validation_exit_code.txt").write_text(f"{exit_code}\n", encoding="utf-8")
    metrics_path = output / "metrics.json"
    if not metrics_path.is_file():
        raise RuntimeError(f"{label}-{response}m: NautilusTrader metrics missing")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if not str(metrics.get("engine", "")).startswith("NautilusTrader"):
        raise RuntimeError("non-Nautilus performance result")
    if metrics.get("custom_backtest_engine") is not False:
        raise RuntimeError("custom engine result rejected")
    if abs(float(metrics.get("risk_fraction", 0.0)) - 0.03) > 1e-12:
        raise RuntimeError("risk fraction drift")
    return metrics


def positive_path(m: dict[str, Any]) -> bool:
    return (
        float(m.get("trades_per_day", 0.0)) >= 0.75
        and float(m.get("win_rate", 0.0)) >= 0.45
        and float(m.get("profit_factor_after_cost", 0.0)) >= 1.15
        and float(m.get("geometric_daily_growth_after_cost", -1.0)) > 0.0
        and abs(float(m.get("maximum_mark_to_market_drawdown", -1.0))) <= 0.25
    )


def project_gate(m: dict[str, Any]) -> bool:
    return (
        float(m.get("trades_per_day", 0.0)) >= 0.75
        and float(m.get("win_rate", 0.0)) >= 0.50
        and float(m.get("profit_factor_after_cost", 0.0)) >= 1.50
        and float(m.get("geometric_daily_growth_after_cost", -1.0)) >= 0.01
        and abs(float(m.get("maximum_mark_to_market_drawdown", -1.0))) <= 0.25
    )


def compact(m: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "candidate", "scheduled_signals", "submitted_signals", "trades", "trades_per_day",
        "wins", "losses", "win_rate", "profit_factor_after_cost",
        "geometric_daily_growth_after_cost", "maximum_mark_to_market_drawdown",
        "starting_nav_usdt", "final_nav_usdt", "maximum_planned_loss_to_budget",
        "maximum_effective_notional_multiple", "engine", "custom_backtest_engine",
        "risk_fraction", "target_met", "decision",
    )
    return {key: m.get(key) for key in keys}


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    run(["smc4", "doctor"])
    run([sys.executable, "-m", "py_compile", str(RESEARCH / "v102_impact_retention_core.py"), str(RESEARCH / "v102_nt_backtest.py")])

    for label, start in WEEKS.items():
        collect_and_build(label, start)

    baseline: dict[str, dict[str, dict[str, Any]]] = {label: {} for label in WEEKS}
    for label, start in WEEKS.items():
        for response in RESPONSES:
            baseline[label][str(response)] = run_variant(
                label=label, start=start, response=response, ablation=False
            )

    central_both = all(positive_path(baseline[label]["3"]) for label in WEEKS)
    adjacent_both = any(
        all(positive_path(baseline[label][str(response)]) for label in WEEKS)
        for response in (2, 4)
    )
    full_gate_both = all(project_gate(baseline[label]["3"]) for label in WEEKS)

    ablation: dict[str, dict[str, Any]] = {}
    if not (central_both and adjacent_both):
        for label, start in WEEKS.items():
            ablation[label] = run_variant(
                label=label, start=start, response=3, ablation=True
            )

    if central_both and adjacent_both:
        status = "STRUCTURAL_PATH_FOUND_LOCK_NEW_RANDOM_BTC_WEEK"
    else:
        ablation_path = bool(ablation) and all(positive_path(m) for m in ablation.values())
        status = (
            "ABLATION_PATH_FOUND_REDESIGN_AND_RESCREEN"
            if ablation_path
            else "DISCARD_V102_NO_STRUCTURAL_PATH"
        )

    decision = {
        "status": status,
        "candidate_family": "candidate-02-v102-quarter-hour-impact-retention",
        "performance_engine": "NautilusTrader 1.230.0",
        "custom_backtest_engine": False,
        "risk_fraction": 0.03,
        "global_pending_entry_plus_position_limit": 1,
        "development_controls_are_not_promotion_evidence": True,
        "baseline": {
            label: {response: compact(metrics) for response, metrics in values.items()}
            for label, values in baseline.items()
        },
        "central_positive_both_revealed_weeks": central_both,
        "adjacent_positive_both_revealed_weeks": adjacent_both,
        "central_full_project_week_gate_both": full_gate_both,
        "single_ablation": {
            "removed_variable": "minimum_response_flow_alignment",
            "all_other_scenario_variables_unchanged": True,
            "central_response_minutes": 3,
            "rows": {label: compact(metrics) for label, metrics in ablation.items()},
        } if ablation else None,
        "failure_interpretation": (
            "If baseline and ablation fail, retained opening impact is not sufficient evidence of durable directional delivery; the event clock remains useful but direction requires a different causal state."
            if status == "DISCARD_V102_NO_STRUCTURAL_PATH" else None
        ),
    }
    decision_path = ROOT / "artifacts-v102-development-decision.json"
    decision_path.write_text(json.dumps(decision, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
