#!/usr/bin/env python3
"""Run the frozen V56 BTC prospective sequence through NautilusTrader.

This file is an experiment orchestrator only.  It invokes the frozen feature
builder and causal signal compilers, then the trusted NautilusTrader runner.  It
never matches orders, simulates fills, calculates PnL or updates NAV.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path("artifacts/candidate-04-v56-prospective-r2")
EVIDENCE = Path("research/candidate-04/evidence-v56-prospective.json")
FROZEN_COMMIT = "4daf1f0ecf5b13017b7761eeef005f8d0b163d10"
WEEKS = (
    (1, "2023-05-27", "2023-05-29", "2023-06-04", "2023-06-04"),
    (2, "2024-07-13", "2024-07-15", "2024-07-21", "2024-07-21"),
    (3, "2025-11-08", "2025-11-10", "2025-11-16", "2025-11-16"),
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run(
    args: list[str],
    *,
    log: Path,
    env: dict[str, str] | None = None,
    attempts: int = 1,
) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    last_code = 1
    for attempt in range(1, attempts + 1):
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f"\n=== attempt {attempt}/{attempts}: {' '.join(args)} ===\n")
            stream.flush()
            completed = subprocess.run(
                args,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                check=False,
            )
        last_code = completed.returncode
        if last_code == 0:
            return
        if attempt < attempts:
            time.sleep(attempt * 8)
    raise RuntimeError(f"command failed ({last_code}): {' '.join(args)}")


def week_environment(
    build_start: str,
    evaluation_start: str,
    evaluation_end: str,
    build_end: str,
) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "C04_BUILD_START": build_start,
            "C04_BUILD_END": build_end,
            "C04_EVALUATION_START": evaluation_start,
            "C04_EVALUATION_END": evaluation_end,
        }
    )
    return env


def run_week(spec: tuple[int, str, str, str, str]) -> dict[str, Any]:
    order, build_start, evaluation_start, evaluation_end, build_end = spec
    week = ROOT / f"week_{order}"
    week.mkdir(parents=True, exist_ok=True)
    env = week_environment(build_start, evaluation_start, evaluation_end, build_end)
    python = sys.executable

    run(
        [
            python,
            "research/candidate-04/rich_features_for_symbol.py",
            "--symbol",
            "BTCUSDT",
            "--start",
            build_start,
            "--end",
            build_end,
            "--cache",
            f".cache/candidate-04-v56-prospective-r2/week_{order}/rich",
            "--output",
            str(week / "rich"),
        ],
        log=week / "rich.log",
        env=env,
        attempts=4,
    )
    if not (week / "rich/data_manifest.json").is_file():
        raise RuntimeError("rich feature manifest missing")

    run(
        [
            python,
            "research/candidate-04/boundary_negotiation_expansion_compiler.py",
            "--base-config",
            "research/candidate-04/inventory_transfer_config.json",
            "--impact-config",
            "research/candidate-04/impact_exhaustion_config.json",
            "--router-config",
            "research/candidate-04/auction_activity_router_config.json",
            "--rich-dir",
            str(week / "rich"),
            "--kline-dir",
            f".cache/candidate-04-v56-prospective-r2/week_{order}/klines",
            "--evaluation-start",
            evaluation_start,
            "--evaluation-end",
            evaluation_end,
            "--output",
            str(week / "v31"),
            "--download-klines",
        ],
        log=week / "v31.log",
        env=env,
        attempts=4,
    )

    run(
        [
            python,
            "research/candidate-04/ablate_compiled_scenario.py",
            "--input-signals",
            str(week / "v31/signals.json"),
            "--input-summary",
            str(week / "v31/summary.json"),
            "--remove",
            "STRESS_SETTLED_ACCEPTANCE_CONTINUATION",
            "--candidate",
            "candidate-04-v31-no-stress-continuation",
            "--output",
            str(week / "ablated"),
        ],
        log=week / "ablation.log",
        env=env,
    )

    run(
        [
            python,
            "research/candidate-04/causal_target_registry_enricher.py",
            "--signals",
            str(week / "ablated/signals.json"),
            "--base-config",
            "research/candidate-04/inventory_transfer_config.json",
            "--rich-dir",
            str(week / "rich"),
            "--kline-dir",
            f".cache/candidate-04-v56-prospective-r2/week_{order}/klines",
            "--build-start",
            build_start,
            "--build-end",
            build_end,
            "--output-dir",
            str(week / "v44"),
            "--download-klines",
            "--cost-rate",
            "0.00075",
            "--minimum-net-r",
            "1.20",
        ],
        log=week / "target.log",
        env=env,
        attempts=4,
    )

    run(
        [
            python,
            "research/candidate-04/prominence_state_router.py",
            "--signals",
            str(week / "v44/signals.json"),
            "--rich-dir",
            str(week / "rich"),
            "--output",
            str(week / "signals"),
        ],
        log=week / "router.log",
        env=env,
    )

    nt_env = dict(env)
    nt_env["C04_SIGNALS_PATH"] = str(
        (Path.cwd() / week / "signals/signals.json").resolve()
    )
    run(
        [
            python,
            "research/candidate-04/nt_backtest_v56_prominence_state.py",
            "--config",
            "research/candidate-04/nt_liquidity_config.json",
            "--build-start",
            build_start,
            "--build-end",
            build_end,
            "--evaluation-start",
            evaluation_start,
            "--evaluation-end",
            evaluation_end,
            "--cache",
            f".cache/candidate-04-v56-prospective-r2/week_{order}/nautilus",
            "--output",
            str(week / "nautilus"),
        ],
        log=week / "nautilus.log",
        env=nt_env,
        attempts=4,
    )

    run(
        [
            python,
            "research/candidate-04/summarize_candidate_week.py",
            "--root",
            str(week),
            "--candidate",
            "candidate-04-v56-prominence-state-router",
            "--stage",
            f"prospective_week_{order}",
            "--min-trades",
            "1",
            "--min-active-days",
            "1",
            "--min-win-rate",
            "0.75",
            "--min-geometric-daily",
            "0.0",
            "--output",
            str(week / "summary.json"),
        ],
        log=week / "summary.log",
        env=env,
    )

    summary = json.loads((week / "summary.json").read_text())
    router = json.loads((week / "signals/summary.json").read_text())
    events = json.loads((week / "nautilus/strategy_events.json").read_text())
    summary.update(
        {
            "order": order,
            "build_start": build_start,
            "evaluation_start": evaluation_start,
            "evaluation_end": evaluation_end,
            "build_end": build_end,
            "router": router,
            "execution_events": dict(
                Counter(str(item.get("event_type")) for item in events)
            ),
            "frozen_commit": FROZEN_COMMIT,
        }
    )
    summary["progressive_pass"] = bool(
        summary.get("risk_pass")
        and int(summary.get("trades") or 0) >= 1
        and int(summary.get("wins") or 0) == int(summary.get("trades") or 0)
        and float(summary.get("total_return") or 0.0) > 0.0
    )
    write_json(week / "summary.json", summary)
    return summary


def aggregate(rows: list[dict[str, Any]], implementation_error: str | None) -> dict[str, Any]:
    returns = [float(row.get("total_return") or 0.0) for row in rows]
    compounded = math.prod(1.0 + value for value in returns) - 1.0 if rows else 0.0
    days = sum(int(row.get("calendar_days") or 7) for row in rows)
    daily = (
        (1.0 + compounded) ** (1.0 / days) - 1.0
        if days and 1.0 + compounded > 0.0
        else 0.0
    )
    trades = sum(int(row.get("trades") or 0) for row in rows)
    wins = sum(int(row.get("wins") or 0) for row in rows)
    active = sum(int(row.get("active_days") or 0) for row in rows)
    completed = len(rows)
    all_pass = completed == 3 and all(bool(row.get("progressive_pass")) for row in rows)
    robust = bool(
        implementation_error is None
        and all_pass
        and trades >= 4
        and active >= 4
        and wins / trades >= 0.80
        and compounded > 0.0
        and all(bool(row.get("risk_pass")) for row in rows)
    ) if trades else False
    return {
        "candidate": "candidate-04-v56-prominence-state-router",
        "stage": "frozen_progressive_prospective_btc",
        "frozen_commit": FROZEN_COMMIT,
        "engine": "NautilusTrader 1.230.0 BacktestNode",
        "weeks": rows,
        "implementation_error": implementation_error,
        "implementation_pass": implementation_error is None,
        "completed_weeks": completed,
        "calendar_days": days,
        "trades": trades,
        "wins": wins,
        "win_rate": wins / trades if trades else 0.0,
        "active_days": active,
        "compounded_return": compounded,
        "geometric_daily_growth": daily,
        "three_week_robust_pass": robust,
        "multi_asset_screen_allowed": robust,
        "long_evaluation_allowed": False,
        "project_target_reached": False,
        "decision": (
            "PROMOTE_V56_TO_FROZEN_FOUR_ASSET_SCREEN"
            if robust
            else "IMPLEMENTATION_FAILURE_FIX_AND_RERUN_IDENTICAL_SEQUENCE"
            if implementation_error is not None
            else f"STOP_V56_AFTER_{completed}_PROSPECTIVE_WEEKS"
        ),
    }


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    error: str | None = None
    try:
        for spec in WEEKS:
            summary = run_week(spec)
            rows.append(summary)
            if not bool(summary["progressive_pass"]):
                break
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        write_json(ROOT / "implementation_failure.json", {"error": error})
    evidence = aggregate(rows, error)
    write_json(EVIDENCE, evidence)
    write_json(ROOT / "decision.json", evidence)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    if error is not None:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
