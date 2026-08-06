#!/usr/bin/env python3
"""Prospective first-week v103 orchestration; performance stays in NautilusTrader."""
from __future__ import annotations

import copy
from datetime import date
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pandas as pd

ROOT = Path.cwd()
CANDIDATE = ROOT / "research/candidate-02"
LOCK_PATH = CANDIDATE / "v103_endogenous_flow_clock_lock.json"
BASE_CONFIG = CANDIDATE / "v103_base_config.json"
INPUT_ROOT = ROOT / "inputs/v103-first-week"
CONFIG_ROOT = Path("/tmp/v103-configs")
ARTIFACT_ROOT = ROOT / "artifacts"
SUMMARY = Path(os.environ.get("GITHUB_STEP_SUMMARY", "/tmp/v103-summary.md"))


def run(command: list[str], *, allowed: tuple[int, ...] = (0,), log: Path | None = None) -> int:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    output = (result.stdout or "") + (result.stderr or "")
    if output:
        print(output, end="")
    if log is not None:
        log.write_text(output, encoding="utf-8")
    if result.returncode not in allowed:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")
    return result.returncode


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def metric_pass(m: dict) -> bool:
    return (
        m.get("trades_per_day", 0) >= 0.75
        and m.get("win_rate", 0) >= 0.50
        and m.get("profit_factor_after_cost", 0) >= 1.50
        and m.get("geometric_daily_growth_after_cost", -1) >= 0.01
        and abs(m.get("maximum_mark_to_market_drawdown", -1)) <= 0.25
        and str(m.get("engine", "")).startswith("NautilusTrader")
        and m.get("custom_backtest_engine") is False
        and abs(float(m.get("risk_fraction", 0)) - 0.03) <= 1e-12
    )


def verify_lock() -> tuple[dict, dict]:
    run(["smc4", "doctor"])
    for path in (
        CANDIDATE / "v103_endogenous_flow_clock_core.py",
        CANDIDATE / "v103_nt_backtest.py",
        ROOT / ".candidate-02-v89/collect_spot.py",
        ROOT / ".candidate-02-v89/augment_cross_market.py",
    ):
        run([sys.executable, "-m", "py_compile", str(path)])
    lock = json.loads(LOCK_PATH.read_text())
    config = json.loads(BASE_CONFIG.read_text())
    assert lock["status"] == "LOCKED_BEFORE_FIRST_WEEK_COLLECTION"
    assert lock["first_week"]["start_utc"] == "2025-11-17T00:00:00Z"
    assert lock["first_week"]["end_utc"] == "2025-11-24T00:00:00Z"
    assert lock["first_week"]["raw_data_status_at_lock"] == "NOT_COLLECTED_FOR_V103"
    assert lock["performance_engine"] == "NautilusTrader 1.230.0"
    assert lock["custom_backtest_engine"] is False
    assert float(lock["risk_fraction"]) == 0.03
    assert int(lock["global_pending_entry_plus_position_limit"]) == 1
    assert config["validation"]["first_week_start"] == "2025-11-17"
    assert int(config["validation"]["selection_seed"]) == 20260807103
    assert float(config["risk"]["risk_fraction"]) == 0.03
    assert config["risk"]["maximum_notional_cap"] is None
    assert config["risk"]["score_risk_multiplier"] is None
    expected = lock["source_git_blob_sha"]
    for key, path in (
        ("core", CANDIDATE / "v103_endogenous_flow_clock_core.py"),
        ("base_config", BASE_CONFIG),
    ):
        actual = subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()
        if actual != expected[key]:
            raise RuntimeError(f"{key} blob changed: {actual} != {expected[key]}")
    return lock, config


def materialize_futures_tools(lock: dict) -> Path:
    tools = Path("/tmp/v103-futures-tools")
    shutil.rmtree(tools, ignore_errors=True)
    shutil.rmtree(INPUT_ROOT, ignore_errors=True)
    shutil.copytree(ROOT / ".candidate-02-v75", tools)
    start = date.fromisoformat(lock["first_week"]["start_utc"][:10])
    constructor = re.compile(
        r"EVALUATION_START\s*=\s*date\(\s*\d{4}\s*,\s*\d{1,2}\s*,\s*\d{1,2}\s*\)"
    )
    replacements = {
        "inputs/v75-first-week": "inputs/v103-first-week",
        "candidate-02-v75-first-week": "candidate-02-v103-first-week",
        "v75-first-week": "v103-first-week",
        "candidate-02-v75-quarter-hour-algorithmic-opening-auction":
            "candidate-02-v103-endogenous-turnover-clock-order-flow-regimes",
    }
    count = 0
    for path in tools.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".json", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        if path.name.startswith("collect") and path.suffix == ".py":
            text, changes = constructor.subn(
                f"EVALUATION_START = date({start.year}, {start.month}, {start.day})",
                text,
            )
            count += changes
        path.write_text(text, encoding="utf-8")
    if count != 1:
        raise RuntimeError(f"expected one evaluation-date constructor, found {count}")
    collector = next(tools.glob("collect*.py"))
    expected = f"EVALUATION_START = date({start.year}, {start.month}, {start.day})"
    materialized = collector.read_text()
    if expected not in materialized or "inputs/v103-first-week" not in materialized:
        raise RuntimeError("locked v103 week was not materialized")
    run([sys.executable, "-m", "py_compile", str(tools / "collect_first_week.py")])
    run([sys.executable, "-m", "py_compile", str(tools / "build_features.py")])
    return tools


def collect_inputs(tools: Path, lock: dict) -> None:
    run([sys.executable, str(tools / "collect_first_week.py")])
    run([sys.executable, str(tools / "build_features.py")])
    if len(list((INPUT_ROOT / "direct/aggTrades").glob("*.zip"))) != 10:
        raise RuntimeError("aggTrades archive count mismatch")
    if len(list((INPUT_ROOT / "direct/bookDepth").glob("*.zip"))) != 10:
        raise RuntimeError("bookDepth archive count mismatch")
    raw_root = INPUT_ROOT / ".cache/candidate-02/v103-first-week/binance_1m"
    if len(list(raw_root.glob("*.zip"))) != 10:
        raise RuntimeError("futures kline archive count mismatch")

    spot_collect = Path("/tmp/v103_collect_spot.py")
    spot_augment = Path("/tmp/v103_augment_cross_market.py")
    shutil.copy2(ROOT / ".candidate-02-v89/collect_spot.py", spot_collect)
    shutil.copy2(ROOT / ".candidate-02-v89/augment_cross_market.py", spot_augment)
    replacements = {
        "research/candidate-02/v89_cross_market_impact_lock.json":
            "research/candidate-02/v103_endogenous_flow_clock_lock.json",
        "inputs/v89-first-week": "inputs/v103-first-week",
        "candidate-02-v89-first-week": "candidate-02-v103-first-week",
        "v89 first-week": "v103 first-week",
        "for v89": "for v103",
    }
    for path in (spot_collect, spot_augment):
        text = path.read_text()
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text)
        run([sys.executable, "-m", "py_compile", str(path)])
    run([sys.executable, str(spot_collect)])
    run([sys.executable, str(spot_augment)])

    from v53_nt_core import load_feature_matrix, load_raw_one_minute
    start = pd.Timestamp(lock["first_week"]["start_utc"])
    end = pd.Timestamp(lock["first_week"]["end_utc"])
    feature_root = INPUT_ROOT / "candidate-02-v48-first-week"
    features = load_feature_matrix(feature_root / "v48_features.npz", feature_root / "columns.json")
    raw = load_raw_one_minute(raw_root)
    required = {
        "close", "aggressive_signed_quote_1m", "aggressive_total_quote_1m",
        "ask_depth_1pct_end", "bid_depth_1pct_end", "spot_open", "spot_close",
        "spot_aggressive_signed_quote_1m", "spot_aggressive_total_quote_1m",
        "perp_spot_log_basis", "vpin_50",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise RuntimeError(f"missing v103 features: {missing}")
    evaluation = features.loc[(features.index >= start) & (features.index < end)]
    raw_eval = raw.loc[(raw.index >= start) & (raw.index < end)]
    if len(evaluation) < 7 * 24 * 60 * 0.995 or len(raw_eval) < 7 * 24 * 60 * 0.995:
        raise RuntimeError("insufficient evaluation coverage")
    if features.index.min() > start - pd.Timedelta(days=2) + pd.Timedelta(minutes=2):
        raise RuntimeError("missing warmup")
    if features.index.max() < end:
        raise RuntimeError("features end early")


def materialize_configs(base: dict) -> dict[str, dict]:
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    variants = {
        "u6": {"packet_turnover_units": 6.0, "mode": "PORTFOLIO"},
        "u8": {"packet_turnover_units": 8.0, "mode": "PORTFOLIO"},
        "u10": {"packet_turnover_units": 10.0, "mode": "PORTFOLIO"},
        "retained8": {"packet_turnover_units": 8.0, "mode": "RETAINED_DISCOVERY"},
        "absorbed8": {"packet_turnover_units": 8.0, "mode": "ABSORBED_EXHAUSTION"},
    }
    for label, override in variants.items():
        config = copy.deepcopy(base)
        config["candidate"] = f"candidate-02-v103-{label}"
        config["scenario"].update(override)
        write_json(CONFIG_ROOT / f"{label}.json", config)
    write_json(ROOT / "artifacts-v103-variant-manifest.json", {
        "base": str(BASE_CONFIG.relative_to(ROOT)), "variants": variants,
    })
    return variants


def execute_variant(label: str, config_path: Path) -> dict:
    output = ARTIFACT_ROOT / f"candidate-02-v103-{label}"
    output.mkdir(parents=True, exist_ok=True)
    code = run(
        [
            sys.executable,
            str(CANDIDATE / "v103_nt_backtest.py"),
            "--config", str(config_path),
            "--input-root", str(INPUT_ROOT),
            "--output", str(output),
        ],
        allowed=(0, 2),
        log=ROOT / f"artifacts-v103-{label}.log",
    )
    (output / "validation_exit_code.txt").write_text(str(code))
    metrics = output / "metrics.json"
    if not metrics.exists():
        raise RuntimeError(f"missing metrics for {label}")
    return json.loads(metrics.read_text())


def publish_decision(rows: dict[str, dict], base: dict) -> dict:
    central_pass = metric_pass(rows["u8"])
    adjacent_pass = metric_pass(rows["u6"]) or metric_pass(rows["u10"])
    component_checks = {}
    for label in ("retained8", "absorbed8"):
        metrics = rows[label]
        trades = int(metrics.get("trades", 0))
        component_checks[label] = {
            "trades": trades,
            "nonnegative_if_active": trades < 2 or (
                metrics.get("profit_factor_after_cost", 0) >= 1.0
                and metrics.get("geometric_daily_growth_after_cost", -1) >= 0.0
            ),
        }
    component_sane = all(value["nonnegative_if_active"] for value in component_checks.values())
    promotion = central_pass and adjacent_pass and component_sane
    baseline = {
        "status": "FIRST_WEEK_PROMOTION_ELIGIBLE" if promotion else "FIRST_WEEK_REJECT_RUN_SINGLE_ABLATION",
        "central_pass": central_pass,
        "adjacent_pass": adjacent_pass,
        "component_sane": component_sane,
        "component_checks": component_checks,
        "rows": rows,
        "risk_fraction": 0.03,
        "performance_engine": "NautilusTrader 1.230.0",
        "custom_backtest_engine": False,
    }
    write_json(ROOT / "artifacts-v103-baseline-decision.json", baseline)

    ablation = None
    if not promotion:
        config = copy.deepcopy(base)
        config["candidate"] = "candidate-02-v103-u8-no-depth-refill-ceiling"
        config["scenario"]["maximum_front_depth_refill"] = 1000.0
        path = CONFIG_ROOT / "u8_ablation.json"
        write_json(path, config)
        ablation = execute_variant("u8-ablation", path)

    final = {
        "candidate_family": "candidate-02-v103-endogenous-turnover-clock-order-flow-regimes",
        "baseline": baseline,
        "single_ablation": (
            {"removed_variable": "maximum_front_depth_refill", "metrics": ablation}
            if ablation is not None else None
        ),
        "status": baseline["status"],
        "next_week_allowed": promotion,
        "long_evaluation_allowed": False,
    }
    if ablation is not None:
        final["ablation_passes_weekly_gate"] = metric_pass(ablation)
        final["status"] = "FIRST_WEEK_REJECT_AFTER_SINGLE_ABLATION"
    write_json(ROOT / "artifacts-v103-first-week-decision.json", final)
    return final


def write_summary(rows: dict[str, dict], decision: dict) -> None:
    lines = [
        "# Candidate-02 v103 endogenous turnover clock",
        "| Variant | Signals | Trades/day | Win | PF | Growth/day | MDD |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, metrics in rows.items():
        lines.append(
            f"| {label} | {metrics['scheduled_signals']} | "
            f"{metrics['trades_per_day']:.3f} | {metrics['win_rate']:.2%} | "
            f"{metrics['profit_factor_after_cost']:.3f} | "
            f"{metrics['geometric_daily_growth_after_cost']:.3%} | "
            f"{metrics['maximum_mark_to_market_drawdown']:.2%} |"
        )
    lines += ["", "## Final decision", "```json", json.dumps(decision, indent=2, sort_keys=True), "```"]
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    lock, base = verify_lock()
    tools = materialize_futures_tools(lock)
    collect_inputs(tools, lock)
    variants = materialize_configs(base)
    rows = {label: execute_variant(label, CONFIG_ROOT / f"{label}.json") for label in variants}
    decision = publish_decision(rows, base)
    write_summary(rows, decision)
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
