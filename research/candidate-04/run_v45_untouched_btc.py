#!/usr/bin/env python3
"""Run frozen V44 on three predeclared untouched BTC weeks via NautilusTrader.

Only completed-data compilers emit intents. NautilusTrader remains sole owner of
orders, fills, costs, positions, PnL and NAV. One scenario-family ablation is
allowed only after a valid but economically failed first week, and the identical
week must pass before later weeks are opened.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, asdict
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
C04 = ROOT / "research/candidate-04"
FROZEN = "f77186180addc1e44ad89599bcae057719ce6cc3"
BASE_REMOVE = "STRESS_SETTLED_ACCEPTANCE_CONTINUATION"
GATE = {"trades": 4, "active_days": 3, "win_rate": 0.55, "daily": 0.0}
TARGET_DAILY = 0.01


@dataclass(frozen=True, slots=True)
class Week:
    name: str
    build_start: str
    build_end: str
    start: str
    end: str


WEEKS = (
    Week("untouched_1_2025_07", "2025-07-19", "2025-07-27", "2025-07-21", "2025-07-27"),
    Week("untouched_2_2024_09", "2024-09-21", "2024-09-29", "2024-09-23", "2024-09-29"),
    Week("untouched_3_2023_01", "2023-01-28", "2023-02-05", "2023-01-30", "2023-02-05"),
)


class StageFailure(RuntimeError):
    def __init__(self, stage: str, code: int) -> None:
        super().__init__(f"{stage} failed with exit code {code}")
        self.stage, self.code = stage, code


def call(cmd: list[str], env: dict[str, str], log: Path, stage: str) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if result.returncode:
        raise StageFailure(stage, result.returncode)


def env_for(week: Week) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(C04),
        C04_BUILD_START=week.build_start,
        C04_BUILD_END=week.build_end,
        C04_EVALUATION_START=week.start,
        C04_EVALUATION_END=week.end,
    )
    return env


def compile_and_enrich(week: Week, root: Path, cache: Path) -> Path:
    env, base = env_for(week), root / week.name
    rich, raw, core, signals = (
        base / "rich",
        base / "v31_all",
        base / "v31_core",
        base / "v44_signals",
    )
    call(
        [sys.executable, str(C04 / "rich_features_v2.py"), "--symbol", "BTCUSDT",
         "--start", week.build_start, "--end", week.build_end,
         "--cache", str(cache / "rich" / week.name), "--output", str(rich)],
        env, base / "rich.log", f"{week.name}:rich",
    )
    call(
        [sys.executable, str(C04 / "boundary_negotiation_expansion_compiler.py"),
         "--base-config", str(C04 / "inventory_transfer_config.json"),
         "--impact-config", str(C04 / "impact_exhaustion_config.json"),
         "--router-config", str(C04 / "auction_activity_router_config.json"),
         "--rich-dir", str(rich), "--kline-dir", str(cache / "compiler" / week.name),
         "--evaluation-start", week.start, "--evaluation-end", week.end,
         "--output", str(raw), "--download-klines"],
        env, base / "compiler.log", f"{week.name}:compiler",
    )
    call(
        [sys.executable, str(C04 / "ablate_compiled_scenario.py"),
         "--input-signals", str(raw / "signals.json"),
         "--input-summary", str(raw / "summary.json"),
         "--remove", BASE_REMOVE, "--candidate", "candidate-04-v45-v44-core",
         "--output", str(core)],
        env, base / "core-ablation.log", f"{week.name}:core-ablation",
    )
    call(
        [sys.executable, str(C04 / "causal_target_registry_enricher.py"),
         "--signals", str(core / "signals.json"),
         "--base-config", str(C04 / "inventory_transfer_config.json"),
         "--rich-dir", str(rich), "--kline-dir", str(cache / "enrich" / week.name),
         "--build-start", week.build_start, "--build-end", week.build_end,
         "--output-dir", str(signals), "--download-klines",
         "--cost-rate", "0.00075", "--minimum-net-r", "1.20"],
        env, base / "enrich.log", f"{week.name}:enrich",
    )
    return signals


def remove_scenario(source: Path, scenario: str, output: Path, env: dict[str, str]) -> Path:
    call(
        [sys.executable, str(C04 / "ablate_compiled_scenario.py"),
         "--input-signals", str(source / "signals.json"),
         "--input-summary", str(source / "summary.json"),
         "--remove", scenario, "--candidate", "candidate-04-v45-one-ablation",
         "--output", str(output)],
        env, output.parent / "dynamic-ablation.log", "first-week:dynamic-ablation",
    )
    return output


def execute(week: Week, signals: Path, route: str, root: Path, cache: Path) -> dict[str, Any]:
    env, out = env_for(week), root / week.name / "routes" / route
    copied = out / "signals"
    copied.mkdir(parents=True, exist_ok=True)
    for name in ("signals.json", "summary.json"):
        (copied / name).write_bytes((signals / name).read_bytes())
    env["C04_SIGNALS_PATH"] = str((copied / "signals.json").resolve())
    call(
        [sys.executable, str(C04 / "nt_backtest_v34_expected_fill_only.py"),
         "--config", str(C04 / "nt_liquidity_config.json"),
         "--build-start", week.build_start, "--build-end", week.build_end,
         "--evaluation-start", week.start, "--evaluation-end", week.end,
         "--cache", str(cache / "nautilus" / week.name / route),
         "--output", str(out / "nautilus")],
        env, out / "nautilus.log", f"{week.name}:{route}:nautilus",
    )
    call(
        [sys.executable, str(C04 / "summarize_candidate_week.py"),
         "--root", str(out), "--candidate", "candidate-04-v45-frozen-v44",
         "--stage", f"{week.name}:{route}",
         "--min-trades", str(GATE["trades"]),
         "--min-active-days", str(GATE["active_days"]),
         "--min-win-rate", str(GATE["win_rate"]),
         "--min-geometric-daily", str(GATE["daily"]),
         "--output", str(out / "summary.json")],
        env, out / "summary.log", f"{week.name}:{route}:summary",
    )
    summary = json.loads((out / "summary.json").read_text())
    events = json.loads((out / "nautilus/strategy_events.json").read_text())
    return {
        "week": asdict(week),
        "route": route,
        **summary,
        "signal_summary": json.loads((copied / "summary.json").read_text()),
        "execution_events": dict(Counter(str(e.get("event_type")) for e in events)),
    }


def worst_negative(row: dict[str, Any]) -> str | None:
    values = []
    for scenario, metrics in (row.get("scenario_metrics") or {}).items():
        try:
            pnl = float((metrics or {}).get("net_pnl"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(pnl) and pnl < 0:
            values.append((pnl, str(scenario)))
    return min(values)[1] if values else None


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [float(row.get("total_return") or 0) for row in rows]
    compounded = math.prod(1 + value for value in returns) - 1 if rows else 0.0
    days = 7 * len(rows)
    daily = (1 + compounded) ** (1 / days) - 1 if days and compounded > -1 else -1.0
    trades = sum(int(row.get("trades") or 0) for row in rows)
    wins = sum(int(row.get("wins") or 0) for row in rows)
    active = sum(int(row.get("active_days") or 0) for row in rows)
    return {
        "weeks": len(rows), "calendar_days": days, "compounded_return": compounded,
        "geometric_daily_growth": daily, "target_geometric_daily_growth": TARGET_DAILY,
        "trades": trades, "wins": wins,
        "win_rate": wins / trades if trades else 0.0, "active_days": active,
        "passed": bool(
            len(rows) == 3 and all(row.get("candidate_pass") for row in rows)
            and all(row.get("risk_pass") for row in rows)
            and daily >= TARGET_DAILY and trades >= 12 and active >= 9
            and (wins / trades if trades else 0) >= GATE["win_rate"]
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root, cache = args.output_root.resolve(), args.cache_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    baseline: dict[str, Any] | None = None
    ablated: dict[str, Any] | None = None
    removed: str | None = None

    for index, week in enumerate(WEEKS):
        try:
            signals = compile_and_enrich(week, root, cache)
            selected = remove_scenario(
                signals, removed, root / week.name / "selected-signals", env_for(week)
            ) if removed else signals
            row = execute(week, selected, "full" if not removed else f"without_{removed}", root, cache)
            if index == 0:
                baseline = row
                if not row.get("candidate_pass") and row.get("risk_pass"):
                    candidate = worst_negative(row)
                    if candidate:
                        alternative = remove_scenario(
                            signals, candidate, root / week.name / "first-ablation", env_for(week)
                        )
                        ablated = execute(week, alternative, f"ablation_without_{candidate}", root, cache)
                        if ablated.get("candidate_pass"):
                            removed, row = candidate, ablated
                if not row.get("candidate_pass"):
                    break
            elif not row.get("candidate_pass"):
                break
            rows.append(row)
        except Exception as exc:
            failures.append({
                "week": week.name,
                "stage": getattr(exc, "stage", type(exc).__name__),
                "return_code": getattr(exc, "code", None),
                "error": str(exc),
            })
            break

    combined = aggregate(rows)
    if failures:
        classification, decision = (
            "implementation_failure", "repair_and_rerun_identical_unopened_stage"
        )
    elif len(rows) < 3:
        classification, decision = (
            "logic_failure_untouched_gate", "discard_v45_frozen_v44_route"
        )
    elif not combined["passed"]:
        classification, decision = (
            "three_week_gate_failed", "discard_v45_frozen_v44_route"
        )
    else:
        classification, decision = (
            "three_week_survivor", "freeze_and_open_long_plus_four_asset_validation"
        )
    evidence = {
        "candidate": "candidate-04-v45-frozen-v44-untouched-btc",
        "engine": "NautilusTrader 1.230.0 BacktestNode",
        "source_commit": FROZEN,
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "selection_seed": "candidate-04-v45-frozen-untouched-btc-weeks-2026-08-07",
        "selection_seed_integer": 15814877332838526820,
        "predeclared_weeks": [asdict(week) for week in WEEKS],
        "preliminary_week_gate": GATE,
        "combined_target_geometric_daily_growth": TARGET_DAILY,
        "base_removed_scenario": BASE_REMOVE,
        "controlled_ablation": {
            "performed": removed is not None,
            "removed_scenario": removed,
            "baseline_first_week": baseline,
            "ablated_first_week": ablated,
        },
        "results": rows,
        "combined_gate": combined,
        "runtime_failures": failures,
        "classification": classification,
        "decision": decision,
        "project_target_reached": False,
        "final_validation_completed": False,
        "performance_recalculated_outside_nautilus": False,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    (root / "aggregate.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
