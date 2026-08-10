#!/usr/bin/env python3
"""Conditional four-asset one-slot screen for Candidate04 V61.

Runs only when the BTC policy-fresh V61 result explicitly authorizes a
four-asset account.  It reuses Candidate04's exact V44 compiler, V56 state
router, current-NAV 3% risk sizing, and NautilusTrader global coordinator.  The
sole policy difference is the already-frozen removal of the two V56 families
that both lost in prospective week 1.
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

AUTHORIZATION = Path("research/candidate-57/evidence/c04-v61-policy-fresh/result.json")
ROOT = Path("artifacts/candidate-57-c04-v61-four-asset")
EVIDENCE = Path("research/candidate-57/evidence/c04-v61-four-asset")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
REMOVED = frozenset(
    {
        "EXTERNAL_SETTLED_FAILED_DISCOVERY_REVERSAL",
        "TRAPPED_COUNTERTREND_INVENTORY_RETEST_RESUMPTION",
    }
)
WEEKS = (
    (2, "2024-07-13", "2024-07-15", "2024-07-21", "2024-07-21"),
    (3, "2025-11-08", "2025-11-10", "2025-11-16", "2025-11-16"),
)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def run(args: list[str], *, log: Path, env: dict[str, str] | None = None, attempts: int = 1) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    code = 1
    for attempt in range(1, attempts + 1):
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f"\n=== attempt {attempt}/{attempts}: {' '.join(args)} ===\n")
            completed = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, text=True, env=env, check=False)
        code = int(completed.returncode)
        if code == 0:
            return
        if attempt < attempts:
            time.sleep(attempt * 8)
    raise RuntimeError(f"command failed ({code}): {' '.join(args)}")


def ablate(source: Path, upstream_summary: Path, output: Path) -> dict[str, Any]:
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError(f"signals are not a list: {source}")
    kept = []
    removed = Counter()
    for row in raw:
        if not isinstance(row, dict):
            continue
        scenario = str(row.get("scenario"))
        if scenario in REMOVED:
            removed[scenario] += 1
        else:
            kept.append(dict(row))
    kept.sort(key=lambda row: int(row.get("observe_time_ns") or 0))
    summary = {
        "candidate": "candidate-57-c04-v61-four-asset-ablation",
        "input_signals": len(raw),
        "written_signals": len(kept),
        "removed_scenarios": sorted(REMOVED),
        "removed_counts": dict(removed),
        "upstream": json.loads(upstream_summary.read_text(encoding="utf-8")),
        "thresholds_changed": False,
        "performance_calculated": False,
    }
    output.mkdir(parents=True, exist_ok=True)
    dump(output / "signals.json", kept)
    dump(output / "summary.json", summary)
    return summary


def compile_symbol(
    week: Path,
    symbol: str,
    build_start: str,
    build_end: str,
    evaluation_start: str,
    evaluation_end: str,
) -> dict[str, Any]:
    python = sys.executable
    source = week / "compiled" / symbol
    final = week / "signals" / symbol
    env = {
        **os.environ,
        "PYTHONPATH": str((Path.cwd() / "research/candidate-04").resolve()),
        "C04_SYMBOL": symbol,
        "C04_BUILD_START": build_start,
        "C04_BUILD_END": build_end,
        "C04_EVALUATION_START": evaluation_start,
        "C04_EVALUATION_END": evaluation_end,
    }
    run(
        [
            python,
            "research/candidate-04/compile_v44_symbol.py",
            "--symbol", symbol,
            "--build-start", build_start,
            "--build-end", build_end,
            "--evaluation-start", evaluation_start,
            "--evaluation-end", evaluation_end,
            "--cache", str(Path(f".cache/candidate-57-c04-v61-four-asset/{week.name}/{symbol}")),
            "--output", str(source),
        ],
        log=source / "compile.log",
        env=env,
        attempts=4,
    )
    run(
        [
            python,
            "research/candidate-04/prominence_state_router_for_symbol.py",
            "--signals", str(source / "signals/signals.json"),
            "--rich-dir", str(source / "rich"),
            "--output", str(source / "v56"),
        ],
        log=source / "v56.log",
        env=env,
    )
    result = ablate(source / "v56/signals.json", source / "v56/summary.json", final)
    result["symbol"] = symbol
    return result


def run_week(spec: tuple[int, str, str, str, str]) -> dict[str, Any]:
    order, build_start, evaluation_start, evaluation_end, build_end = spec
    week = ROOT / f"week_{order}"
    week.mkdir(parents=True, exist_ok=True)
    signal_summaries = {
        symbol: compile_symbol(week, symbol, build_start, build_end, evaluation_start, evaluation_end)
        for symbol in SYMBOLS
    }
    python = sys.executable
    output = week / "nautilus"
    env = {
        **os.environ,
        "PYTHONPATH": str((Path.cwd() / "research/candidate-04").resolve()),
    }
    run(
        [
            python,
            "research/candidate-04/nt_multi_asset_v56.py",
            "--config", "research/candidate-04/nt_liquidity_config.json",
            "--signals-root", str(week / "signals"),
            "--build-start", build_start,
            "--build-end", build_end,
            "--evaluation-start", evaluation_start,
            "--evaluation-end", evaluation_end,
            "--cache", str(Path(f".cache/candidate-57-c04-v61-four-asset/{week.name}/account")),
            "--output", str(output),
            "--min-trades", "1",
            "--min-active-days", "1",
            "--min-win-rate", "0.0",
            "--min-geometric-daily", "0.0",
        ],
        log=week / "nautilus.log",
        env=env,
        attempts=4,
    )
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    result = {
        "order": order,
        "build_start": build_start,
        "build_end": build_end,
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "calendar_days": 7,
        "signal_summaries": signal_summaries,
        "metrics": metrics,
    }
    dump(week / "summary.json", result)
    return result


def classify(weeks: list[dict[str, Any]], error: str | None) -> dict[str, Any]:
    metrics = [row.get("metrics") or {} for row in weeks]
    mechanically_valid = bool(
        error is None
        and len(weeks) == len(WEEKS)
        and all(bool(m.get("global_entry_pass")) and bool(m.get("risk_pass")) for m in metrics)
    )
    trades = sum(int(m.get("trades") or 0) for m in metrics)
    wins = sum(int(m.get("wins") or 0) for m in metrics)
    losses = sum(int(m.get("losses") or 0) for m in metrics)
    returns = [float(m.get("total_return") or 0.0) for m in metrics]
    compounded = math.prod(1.0 + value for value in returns) - 1.0 if metrics else 0.0
    days = sum(int(row.get("calendar_days") or 7) for row in weeks)
    daily = (1.0 + compounded) ** (1.0 / days) - 1.0 if days and 1.0 + compounded > 0.0 else -1.0
    active_weeks = sum(int(m.get("trades") or 0) > 0 for m in metrics)
    losing_weeks = sum(float(m.get("total_return") or 0.0) < 0.0 for m in metrics)
    max_mdd = max((float(m.get("max_drawdown") or 0.0) for m in metrics), default=0.0)
    if not mechanically_valid:
        decision = "IMPLEMENTATION_FAILURE_NO_ALPHA_CONCLUSION"
    elif trades == 0:
        decision = "NO_FOUR_ASSET_OPPORTUNITY_NOT_USABLE"
    elif compounded <= 0.0 or losing_weeks > 0:
        decision = "FOUR_ASSET_PORTABILITY_REJECTED_NO_RETUNING"
    elif trades >= days and daily >= 0.01 and max_mdd <= 0.20:
        decision = "FOUR_ASSET_TARGET_SHAPE_AUTHORIZE_30D_PRESSURE_TEST"
    elif trades >= max(7, days // 2) and active_weeks == len(WEEKS):
        decision = "FOUR_ASSET_POSITIVE_DENSE_COMPONENT_NOT_LONG_READY"
    else:
        decision = "FOUR_ASSET_POSITIVE_SPARSE_SPECIALIST_ONLY"
    return {
        "candidate": "candidate-57-c04-v61-four-asset",
        "source_authorization": str(AUTHORIZATION),
        "weeks": weeks,
        "implementation_error": error,
        "mechanically_valid": mechanically_valid,
        "calendar_days": days,
        "active_weeks": active_weeks,
        "losing_weeks": losing_weeks,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / trades if trades else 0.0,
        "compounded_return": compounded,
        "geometric_daily_growth": daily,
        "maximum_week_drawdown": max_mdd,
        "decision": decision,
        "long_evaluation_authorized": False,
        "integration_authorized": False,
        "thresholds_searched": False,
    }


def write_result(result: dict[str, Any]) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    dump(EVIDENCE / "result.json", result)
    lines = [
        "# Candidate-04 V61 four-asset one-slot screen",
        "",
        f"- decision: **{result['decision']}**",
        f"- mechanically valid: **{result['mechanically_valid']}**",
        f"- trades: {result.get('trades')} ({result.get('wins')} wins / {result.get('losses')} losses)",
        f"- compounded return: {result.get('compounded_return')}",
        f"- geometric daily growth: {result.get('geometric_daily_growth')}",
        f"- maximum weekly MDD: {result.get('maximum_week_drawdown')}",
        "",
        "| week | interval | trades | W/L | PF | return | geo/day | MDD | global slot |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in result.get("weeks", []):
        m = row.get("metrics") or {}
        lines.append(
            f"| {row.get('order')} | {row.get('evaluation_start')}..{row.get('evaluation_end')} | "
            f"{m.get('trades')} | {m.get('wins')}/{m.get('losses')} | {m.get('profit_factor')} | "
            f"{m.get('total_return')} | {m.get('geometric_daily_growth')} | {m.get('max_drawdown')} | "
            f"{m.get('global_entry_pass')} |"
        )
    lines.extend(
        [
            "",
            "The result is one-account Nautilus evidence. Positive sparse output is retained only as a specialist; "
            "negative output closes the revised Candidate04 core without threshold relaxation. A 30-day test is "
            "authorized only if the actual four-asset account already shows the target frequency/growth shape.",
        ]
    )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if not AUTHORIZATION.exists():
        (EVIDENCE / "STATUS.txt").write_text("WAITING_FOR_C04_V61_POLICY_FRESH_RESULT\n", encoding="utf-8")
        return 0
    authorization = json.loads(AUTHORIZATION.read_text(encoding="utf-8"))
    if authorization.get("decision") != "ROBUST_CORE_SCREEN_AUTHORIZED_FOR_FOUR_ASSET_ACCOUNT":
        result = {
            "candidate": "candidate-57-c04-v61-four-asset",
            "authorization_decision": authorization.get("decision"),
            "decision": "FOUR_ASSET_SCREEN_NOT_AUTHORIZED",
            "mechanically_valid": True,
            "weeks": [],
            "long_evaluation_authorized": False,
            "integration_authorized": False,
        }
        write_result(result)
        return 0
    ROOT.mkdir(parents=True, exist_ok=True)
    weeks: list[dict[str, Any]] = []
    error: str | None = None
    try:
        for spec in WEEKS:
            weeks.append(run_week(spec))
    except Exception as exc:
        error = repr(exc)
    result = classify(weeks, error)
    write_result(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["mechanically_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
