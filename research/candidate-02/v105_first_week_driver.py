#!/usr/bin/env python3
"""Prospective v105 first-week orchestration; performance remains in NautilusTrader."""
from __future__ import annotations

import copy
from collections import defaultdict
from datetime import date
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "research/candidate-02"
LOCK_PATH = CANDIDATE / "v105_auction_state_lock.json"
BASE_CONFIG = CANDIDATE / "v105_base_config.json"
INPUT_ROOT = ROOT / "inputs/v105-first-week"
CONFIG_ROOT = Path("/tmp/v105-configs")
ARTIFACT_ROOT = ROOT / "artifacts"
SUMMARY = Path(os.environ.get("GITHUB_STEP_SUMMARY", "/tmp/v105-summary.md"))


def run(
    command: list[str],
    *,
    allowed: tuple[int, ...] = (0,),
    log: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=process_env,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if output:
        print(output, end="")
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(output, encoding="utf-8")
    if result.returncode not in allowed:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")
    return result.returncode


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def metric_pass(metrics: Mapping[str, Any]) -> bool:
    return (
        float(metrics.get("trades_per_day", 0.0)) >= 0.75
        and float(metrics.get("win_rate", 0.0)) >= 0.50
        and float(metrics.get("profit_factor_after_cost", 0.0)) >= 1.50
        and float(metrics.get("geometric_daily_growth_after_cost", -1.0)) >= 0.01
        and abs(float(metrics.get("maximum_mark_to_market_drawdown", -1.0))) <= 0.25
        and str(metrics.get("engine", "")).startswith("NautilusTrader 1.230.0")
        and metrics.get("custom_backtest_engine") is False
        and abs(float(metrics.get("risk_fraction", 0.0)) - 0.03) <= 1e-12
        and bool(metrics.get("flat_at_end"))
        and float(metrics.get("maximum_planned_loss_to_budget", 2.0)) <= 1.000000001
    )


def verify_lock() -> tuple[dict[str, Any], dict[str, Any]]:
    run(["smc4", "doctor"])
    for path in (
        CANDIDATE / "core.py",
        CANDIDATE / "v53_nt_core.py",
        CANDIDATE / "v53_nt_strategy.py",
        CANDIDATE / "v53_nt_backtest.py",
        CANDIDATE / "v105_auction_state_core.py",
        CANDIDATE / "v105_nt_strategy.py",
        CANDIDATE / "v105_nt_backtest.py",
        CANDIDATE / "v105_first_week_driver.py",
        ROOT / ".candidate-02-v75/collect_first_week.py",
        ROOT / ".candidate-02-v75/build_features.py",
        ROOT / ".candidate-02-v89/collect_spot.py",
        ROOT / ".candidate-02-v89/augment_cross_market.py",
    ):
        run([sys.executable, "-m", "py_compile", str(path)])
    run([sys.executable, str(CANDIDATE / "tests/run_v105_tests.py")])

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    config = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    assert lock["status"] == "LOCKED_BEFORE_FIRST_WEEK_COLLECTION"
    assert lock["first_week"]["start_utc"] == "2025-08-25T00:00:00Z"
    assert lock["first_week"]["end_utc"] == "2025-09-01T00:00:00Z"
    assert lock["first_week"]["raw_data_status_at_lock"] == "NOT_COLLECTED_FOR_V105"
    assert lock["performance_engine"] == "NautilusTrader 1.230.0"
    assert lock["custom_backtest_engine"] is False
    assert float(lock["risk_fraction"]) == 0.03
    assert int(lock["global_pending_entry_plus_position_limit"]) == 1
    execution = lock["execution_contract"]
    assert int(execution["activation_delay_minutes"]) == 1
    assert execution["target_must_be_known_by_decision"] is True
    assert execution["target_must_remain_active_at_activation"] is True
    assert execution["activation_bar_structural_invalidation_rejected"] is True
    assert execution["exchange_price_increment_applied_before_sizing"] is True
    assert config["validation"]["first_week_start"] == "2025-08-25"
    assert int(config["validation"]["selection_seed"]) == 20260807107
    assert float(config["risk"]["risk_fraction"]) == 0.03
    assert config["risk"]["maximum_notional_cap"] is None
    assert config["risk"]["score_risk_multiplier"] is None
    assert int(config["scenario"]["activation_delay_minutes"]) == 1
    selection = json.loads((CANDIDATE / "v105_week_selection.json").read_text(encoding="utf-8"))
    chosen = [value["start_utc"] for value in selection["selections"]]
    assert chosen == [
        "2025-08-25T00:00:00Z",
        "2025-05-19T00:00:00Z",
        "2024-07-08T00:00:00Z",
    ]

    for key, expected in lock["source_git_blob_sha"].items():
        relative = lock["source_files"][key]
        path = ROOT / relative
        actual = subprocess.check_output(["git", "hash-object", str(path)], cwd=ROOT, text=True).strip()
        if actual != expected:
            raise RuntimeError(f"locked blob changed for {key}: {actual} != {expected}")
    return lock, config


def materialize_futures_tools(lock: Mapping[str, Any], base: Mapping[str, Any]) -> Path:
    tools = Path("/tmp/v105-futures-tools")
    shutil.rmtree(tools, ignore_errors=True)
    shutil.rmtree(INPUT_ROOT, ignore_errors=True)
    shutil.copytree(ROOT / ".candidate-02-v75", tools)
    start = date.fromisoformat(str(lock["first_week"]["start_utc"])[:10])
    warmup_days = int(base["validation"]["warmup_days"])
    expected_archives = warmup_days + 8
    constructor = re.compile(r"EVALUATION_START\s*=\s*date\(\s*\d{4}\s*,\s*\d{1,2}\s*,\s*\d{1,2}\s*\)")
    replacements = {
        "inputs/v75-first-week": "inputs/v105-first-week",
        "candidate-02-v75-first-week": "candidate-02-v105-first-week",
        "v75-first-week": "v105-first-week",
        "candidate-02-v75-quarter-hour-algorithmic-opening-auction":
            "candidate-02-v105-auction-state-continuation-reversal",
    }
    date_changes = 0
    warmup_changes = 0
    for path in tools.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".json", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        if path.name == "collect_first_week.py":
            text, count = constructor.subn(
                f"EVALUATION_START = date({start.year}, {start.month}, {start.day})",
                text,
            )
            date_changes += count
            text, count = re.subn(
                r"DATA_START\s*=\s*EVALUATION_START\s*-\s*timedelta\(days=\d+\)",
                f"DATA_START = EVALUATION_START - timedelta(days={warmup_days})",
                text,
            )
            warmup_changes += count
        if path.name == "build_features.py":
            archive_before = (
                '    if len(agg_frames) != 10 or len(book_frames) != 10:\n'
                '        raise ValueError("expected ten daily direct-data archives per source")'
            )
            archive_after = (
                f'    if len(agg_frames) != {expected_archives} or len(book_frames) != {expected_archives}:\n'
                f'        raise ValueError("expected {expected_archives} daily direct-data archives per source")'
            )
            if text.count(archive_before) != 1:
                raise RuntimeError("v75 feature-builder archive assertion materialization mismatch")
            text = text.replace(archive_before, archive_after)
        path.write_text(text, encoding="utf-8")
    if date_changes != 1 or warmup_changes != 1:
        raise RuntimeError(f"collector materialization mismatch: date={date_changes}, warmup={warmup_changes}")
    for name in ("collect_first_week.py", "build_features.py"):
        run([sys.executable, "-m", "py_compile", str(tools / name)])
    return tools


def collect_inputs(tools: Path, lock: Mapping[str, Any], base: Mapping[str, Any]) -> None:
    run([sys.executable, str(tools / "collect_first_week.py")])
    run([sys.executable, str(tools / "build_features.py")])
    expected_archives = int(base["validation"]["warmup_days"]) + 8
    if len(list((INPUT_ROOT / "direct/aggTrades").glob("*.zip"))) != expected_archives:
        raise RuntimeError("futures aggTrades archive count mismatch")
    if len(list((INPUT_ROOT / "direct/bookDepth").glob("*.zip"))) != expected_archives:
        raise RuntimeError("futures bookDepth archive count mismatch")
    raw_root = INPUT_ROOT / ".cache/candidate-02/v105-first-week/binance_1m"
    if len(list(raw_root.glob("*.zip"))) != expected_archives:
        raise RuntimeError("futures kline archive count mismatch")

    spot_collect = Path("/tmp/v105_collect_spot.py")
    spot_augment = Path("/tmp/v105_augment_cross_market.py")
    shutil.copy2(ROOT / ".candidate-02-v89/collect_spot.py", spot_collect)
    shutil.copy2(ROOT / ".candidate-02-v89/augment_cross_market.py", spot_augment)
    replacements = {
        "research/candidate-02/v89_cross_market_impact_lock.json":
            "research/candidate-02/v105_auction_state_lock.json",
        "inputs/v89-first-week": "inputs/v105-first-week",
        "candidate-02-v89-first-week": "candidate-02-v105-first-week",
        "v89 first-week": "v105 first-week",
        "for v89": "for v105",
    }
    for path in (spot_collect, spot_augment):
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        text = text.replace(
            "data_start = evaluation_start - timedelta(days=2)",
            f"data_start = evaluation_start - timedelta(days={int(base['validation']['warmup_days'])})",
        )
        text = text.replace(
            "if len(archives) != 10:",
            f"if len(archives) != {expected_archives}:",
        ).replace(
            'raise ValueError(f"expected ten spot aggTrade archives, found {len(archives)}")',
            f'raise ValueError(f"expected {expected_archives} spot aggTrade archives, found {{len(archives)}}")',
        )
        path.write_text(text, encoding="utf-8")
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
        "close",
        "aggressive_total_quote_1m",
        "signed_flow_ratio_1m",
        "spot_close",
        "spot_signed_flow_ratio_1m",
        "perp_spot_log_basis",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise RuntimeError(f"missing v105 features: {missing}")
    evaluation = features.loc[(features.index >= start) & (features.index < end)]
    raw_eval = raw.loc[(raw.index >= start) & (raw.index < end)]
    if len(evaluation) < 7 * 24 * 60 * 0.995 or len(raw_eval) < 7 * 24 * 60 * 0.995:
        raise RuntimeError("insufficient evaluation coverage")
    expected_warmup = start - pd.Timedelta(days=int(base["validation"]["warmup_days"]))
    if features.index.min() > expected_warmup + pd.Timedelta(minutes=2):
        raise RuntimeError("missing v105 warmup")
    if features.index.max() < end + pd.Timedelta(minutes=int(base["validation"]["exit_buffer_minutes"])):
        raise RuntimeError("features end before exit buffer")


def execute_variant(label: str, config_path: Path) -> dict[str, Any]:
    output = ARTIFACT_ROOT / f"candidate-02-v105-{label}"
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)
    code = run(
        [
            sys.executable,
            str(CANDIDATE / "v105_nt_backtest.py"),
            "--config", str(config_path),
            "--input-root", str(INPUT_ROOT),
            "--output", str(output),
        ],
        allowed=(0, 2),
        log=ROOT / f"artifacts-v105-{label}.log",
        env={"V105_SIGNAL_DIAGNOSTICS": str(output / "signal_build_diagnostics.json")},
    )
    (output / "validation_exit_code.txt").write_text(str(code) + "\n", encoding="utf-8")
    metrics_path = output / "metrics.json"
    if not metrics_path.exists():
        raise RuntimeError(f"missing NautilusTrader metrics for {label}")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    _write_trade_diagnostics(output)
    _write_attribution(output)
    return metrics


def _write_trade_diagnostics(output: Path) -> None:
    from v53_nt_core import load_raw_one_minute

    trades = read_jsonl(output / "trades.jsonl")
    raw_root = INPUT_ROOT / ".cache/candidate-02/v105-first-week/binance_1m"
    raw = load_raw_one_minute(raw_root)
    rows: list[dict[str, Any]] = []
    for trade in trades:
        planned = trade.get("planned_signal") or {}
        side = str(planned.get("side", ""))
        entry = float(trade.get("avg_px_open", 0.0))
        stop = float(planned.get("stop_price", entry))
        target = float(planned.get("target_price", entry))
        risk = abs(entry - stop)
        start = pd.Timestamp(int(trade["entry_time_ns"]), unit="ns", tz="UTC")
        end = pd.Timestamp(int(trade["exit_time_ns"]), unit="ns", tz="UTC")
        path = raw.loc[(raw.index >= start.floor("min")) & (raw.index <= end.ceil("min"))]
        if path.empty or risk <= 0.0:
            mfe_r = mae_r = None
            natural_target_reached = False
        elif side == "BUY":
            mfe_r = (float(path["high"].max()) - entry) / risk
            mae_r = (float(path["low"].min()) - entry) / risk
            natural_target_reached = bool((path["high"] >= target).any())
        else:
            mfe_r = (entry - float(path["low"].min())) / risk
            mae_r = (entry - float(path["high"].max())) / risk
            natural_target_reached = bool((path["low"] <= target).any())
        details = planned.get("details") or {}
        rows.append(
            {
                "scenario_id": trade.get("scenario_id"),
                "side": side,
                "entry_time_utc": start.isoformat(),
                "exit_time_utc": end.isoformat(),
                "net_pnl_after_cost": float(trade.get("net_pnl_after_cost", 0.0)),
                "mfe_r": mfe_r,
                "mae_r": mae_r,
                "natural_target_reached": natural_target_reached,
                "boundary_families": details.get("liquidity_families", []),
                "target_family": details.get("selected_nearest_external_target_family"),
                "session_diagnostic_only": details.get("session_diagnostic_only"),
                "volatility_regime_diagnostic_only": details.get("volatility_regime_diagnostic_only"),
            }
        )
    path = output / "trade_diagnostics.jsonl"
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _write_attribution(output: Path) -> None:
    rows = read_jsonl(output / "trade_diagnostics.jsonl")
    groups: dict[str, dict[str, float]] = defaultdict(lambda: {"trades": 0.0, "net_pnl": 0.0, "wins": 0.0})
    for row in rows:
        keys = [f"target:{row.get('target_family')}"]
        keys.extend(f"boundary:{name}" for name in row.get("boundary_families", []))
        keys.append(f"session:{row.get('session_diagnostic_only')}")
        keys.append(f"volatility:{row.get('volatility_regime_diagnostic_only')}")
        pnl = float(row.get("net_pnl_after_cost", 0.0))
        for key in keys:
            groups[key]["trades"] += 1
            groups[key]["net_pnl"] += pnl
            groups[key]["wins"] += float(pnl > 0)
    output_obj = {}
    for key, value in sorted(groups.items()):
        trades = int(value["trades"])
        output_obj[key] = {
            "trades": trades,
            "wins": int(value["wins"]),
            "win_rate": value["wins"] / trades if trades else 0.0,
            "net_pnl_after_cost": value["net_pnl"],
        }
    write_json(output / "scenario_attribution.json", output_obj)


def publish_decision(baseline: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    baseline_pass = metric_pass(baseline)
    decision = {
        "candidate_family": "candidate-02-v105-auction-state-continuation-reversal",
        "baseline": {
            "metrics": baseline,
            "passes_all_first_week_gates": baseline_pass,
        },
        "single_precommitted_ablation": None,
        "status": (
            "FIRST_WEEK_PROMOTION_ELIGIBLE"
            if baseline_pass
            else "FIRST_WEEK_REJECT"
        ),
        "next_week_allowed": baseline_pass,
        "long_evaluation_allowed": False,
        "risk_fraction": 0.03,
        "performance_engine": "NautilusTrader 1.230.0",
        "custom_backtest_engine": False,
        "project_target_met": False,
    }
    write_json(ROOT / "artifacts-v105-first-week-decision.json", decision)
    return decision

def write_summary(decision: Mapping[str, Any]) -> None:
    baseline = decision["baseline"]["metrics"]
    rows = [
        "# Candidate-02 v105 first BTC week",
        "",
        "| Variant | Signals | Trades/day | Win | PF | Growth/day | MDD | Gate |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
        (
            f"| baseline | {baseline.get('scheduled_signals', 0)} | "
            f"{float(baseline.get('trades_per_day', 0)):.3f} | "
            f"{float(baseline.get('win_rate', 0)):.2%} | "
            f"{float(baseline.get('profit_factor_after_cost', 0)):.3f} | "
            f"{float(baseline.get('geometric_daily_growth_after_cost', 0)):.3%} | "
            f"{float(baseline.get('maximum_mark_to_market_drawdown', 0)):.2%} | "
            f"{decision['baseline']['passes_all_first_week_gates']} |"
        ),
    ]
    ablation = decision.get("single_precommitted_ablation")
    if ablation:
        metrics = ablation["metrics"]
        rows.append(
            f"| no equal swings | {metrics.get('scheduled_signals', 0)} | "
            f"{float(metrics.get('trades_per_day', 0)):.3f} | "
            f"{float(metrics.get('win_rate', 0)):.2%} | "
            f"{float(metrics.get('profit_factor_after_cost', 0)):.3f} | "
            f"{float(metrics.get('geometric_daily_growth_after_cost', 0)):.3%} | "
            f"{float(metrics.get('maximum_mark_to_market_drawdown', 0)):.2%} | diagnostic only |"
        )
    rows += ["", "## Decision", "```json", json.dumps(decision, indent=2, sort_keys=True), "```"]
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    lock, base = verify_lock()
    tools = materialize_futures_tools(lock, base)
    collect_inputs(tools, lock, base)
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    baseline_path = CONFIG_ROOT / "baseline.json"
    write_json(baseline_path, base)
    baseline = execute_variant("baseline", baseline_path)
    decision = publish_decision(baseline, base)
    write_summary(decision)
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
