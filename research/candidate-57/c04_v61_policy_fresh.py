#!/usr/bin/env python3
"""Policy-fresh portability test for the strongest reusable Candidate-04 core.

Candidate-04 V56 failed its first frozen prospective week because two distinct
families both lost: EXTERNAL_SETTLED_FAILED_DISCOVERY_REVERSAL and
TRAPPED_COUNTERTREND_INVENTORY_RETEST_RESUMPTION.  This experiment does not
retune either family.  It removes those whole causal mechanisms and preserves
all remaining V56 entries, directions, state boundaries, stops, targets,
costs, current-NAV risk and Nautilus execution unchanged.

The remaining deterministic V56 prospective weeks were selected before any
prospective market result.  They are used in order as policy-fresh BTC regime
checks.  Results are classified causally rather than by a single pass gate:

* robust core: positive, no losing trade, and opportunity in both weeks;
* positive sparse specialist: positive but insufficient week coverage/density;
* rejected: any after-cost losing week or aggregate loss;
* no opportunity: no trades, therefore no evidence of a usable core.
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

ROOT = Path("artifacts/candidate-57-c04-v61-policy-fresh")
EVIDENCE = Path("research/candidate-57/evidence/c04-v61-policy-fresh")
SOURCE_FREEZE = "candidate-04/freeze-v56.json"
SOURCE_FAILED_WEEK = "candidate-04/evidence-v56-prospective.json"
REMOVED = frozenset(
    {
        "EXTERNAL_SETTLED_FAILED_DISCOVERY_REVERSAL",
        "TRAPPED_COUNTERTREND_INVENTORY_RETEST_RESUMPTION",
    }
)
# These were V56 prospective weeks 2 and 3, selected before prospective data.
WEEKS = (
    (2, "2024-07-13", "2024-07-15", "2024-07-21", "2024-07-21"),
    (3, "2025-11-08", "2025-11-10", "2025-11-16", "2025-11-16"),
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
        last_code = int(completed.returncode)
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


def remove_failed_families(source: Path, source_summary: Path, output: Path) -> dict[str, Any]:
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError("V56 routed signals are not a list")
    kept: list[dict[str, Any]] = []
    removed_counts: Counter[str] = Counter()
    for item in raw:
        if not isinstance(item, dict):
            continue
        scenario = str(item.get("scenario"))
        if scenario in REMOVED:
            removed_counts[scenario] += 1
            continue
        kept.append(dict(item))
    kept.sort(key=lambda item: int(item.get("observe_time_ns") or 0))
    upstream = json.loads(source_summary.read_text(encoding="utf-8"))
    summary = {
        "candidate": "candidate-57-c04-v61-policy-fresh-ablation",
        "source_router": upstream,
        "controlled_change": (
            "remove the two whole causal families that failed V56 prospective week 1; "
            "all remaining signal/order/risk semantics unchanged"
        ),
        "removed_scenarios": sorted(REMOVED),
        "removed_counts": dict(removed_counts),
        "input_signals": len(raw),
        "written_signals": len(kept),
        "performance_calculated": False,
        "future_information_used": False,
        "thresholds_changed": False,
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "signals.json", kept)
    write_json(output / "summary.json", summary)
    return summary


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
            "--symbol", "BTCUSDT",
            "--start", build_start,
            "--end", build_end,
            "--cache", f".cache/candidate-57-c04-v61/week_{order}/rich",
            "--output", str(week / "rich"),
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
            "--base-config", "research/candidate-04/inventory_transfer_config.json",
            "--impact-config", "research/candidate-04/impact_exhaustion_config.json",
            "--router-config", "research/candidate-04/auction_activity_router_config.json",
            "--rich-dir", str(week / "rich"),
            "--kline-dir", f".cache/candidate-57-c04-v61/week_{order}/klines",
            "--evaluation-start", evaluation_start,
            "--evaluation-end", evaluation_end,
            "--output", str(week / "v31"),
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
            "--input-signals", str(week / "v31/signals.json"),
            "--input-summary", str(week / "v31/summary.json"),
            "--remove", "STRESS_SETTLED_ACCEPTANCE_CONTINUATION",
            "--candidate", "candidate-04-v31-no-stress-continuation",
            "--output", str(week / "ablated"),
        ],
        log=week / "ablation.log",
        env=env,
    )

    run(
        [
            python,
            "research/candidate-04/causal_target_registry_enricher.py",
            "--signals", str(week / "ablated/signals.json"),
            "--base-config", "research/candidate-04/inventory_transfer_config.json",
            "--rich-dir", str(week / "rich"),
            "--kline-dir", f".cache/candidate-57-c04-v61/week_{order}/klines",
            "--build-start", build_start,
            "--build-end", build_end,
            "--output-dir", str(week / "v44"),
            "--download-klines",
            "--cost-rate", "0.00075",
            "--minimum-net-r", "1.20",
        ],
        log=week / "target.log",
        env=env,
        attempts=4,
    )

    run(
        [
            python,
            "research/candidate-04/prominence_state_router.py",
            "--signals", str(week / "v44/signals.json"),
            "--rich-dir", str(week / "rich"),
            "--output", str(week / "v56"),
        ],
        log=week / "router.log",
        env=env,
    )
    ablation = remove_failed_families(
        week / "v56/signals.json",
        week / "v56/summary.json",
        week / "signals",
    )

    nt_env = dict(env)
    nt_env["C04_SIGNALS_PATH"] = str((Path.cwd() / week / "signals/signals.json").resolve())
    run(
        [
            python,
            "research/candidate-04/nt_backtest_v56_prominence_state.py",
            "--config", "research/candidate-04/nt_liquidity_config.json",
            "--build-start", build_start,
            "--build-end", build_end,
            "--evaluation-start", evaluation_start,
            "--evaluation-end", evaluation_end,
            "--cache", f".cache/candidate-57-c04-v61/week_{order}/nautilus",
            "--output", str(week / "nautilus"),
        ],
        log=week / "nautilus.log",
        env=nt_env,
        attempts=4,
    )

    run(
        [
            python,
            "research/candidate-04/summarize_candidate_week.py",
            "--root", str(week),
            "--candidate", "candidate-57-c04-v61-policy-fresh",
            "--stage", f"policy_fresh_week_{order}",
            "--min-trades", "1",
            "--min-active-days", "1",
            "--min-win-rate", "0.0",
            "--min-geometric-daily", "0.0",
            "--output", str(week / "summary.json"),
        ],
        log=week / "summary.log",
        env=env,
    )

    summary = json.loads((week / "summary.json").read_text(encoding="utf-8"))
    events = json.loads((week / "nautilus/strategy_events.json").read_text(encoding="utf-8"))
    summary.update(
        {
            "order": order,
            "build_start": build_start,
            "evaluation_start": evaluation_start,
            "evaluation_end": evaluation_end,
            "build_end": build_end,
            "calendar_days": 7,
            "controlled_ablation": ablation,
            "execution_events": dict(Counter(str(item.get("event_type")) for item in events)),
        }
    )
    write_json(week / "summary.json", summary)
    return summary


def aggregate(rows: list[dict[str, Any]], error: str | None) -> dict[str, Any]:
    total_trades = sum(int(row.get("trades") or 0) for row in rows)
    total_wins = sum(int(row.get("wins") or 0) for row in rows)
    returns = [float(row.get("total_return") or 0.0) for row in rows]
    compounded = math.prod(1.0 + value for value in returns) - 1.0 if rows else 0.0
    days = sum(int(row.get("calendar_days") or 7) for row in rows)
    daily = (1.0 + compounded) ** (1.0 / days) - 1.0 if days and 1.0 + compounded > 0.0 else -1.0
    active_weeks = sum(int(row.get("trades") or 0) > 0 for row in rows)
    losing_weeks = sum(float(row.get("total_return") or 0.0) < 0.0 for row in rows)
    losing_trades = total_trades - total_wins
    mechanically_valid = bool(
        error is None
        and len(rows) == len(WEEKS)
        and all(bool(row.get("risk_pass")) for row in rows)
    )
    if not mechanically_valid:
        decision = "IMPLEMENTATION_FAILURE_NO_ALPHA_CONCLUSION"
    elif total_trades == 0:
        decision = "NO_POLICY_FRESH_OPPORTUNITY_NOT_A_USABLE_CORE"
    elif compounded <= 0.0 or losing_weeks > 0 or losing_trades > 0:
        decision = "POLICY_FRESH_PORTABILITY_REJECTED_NO_RETUNING"
    elif active_weeks == len(WEEKS) and total_trades >= 2:
        decision = "ROBUST_CORE_SCREEN_AUTHORIZED_FOR_FOUR_ASSET_ACCOUNT"
    else:
        decision = "POSITIVE_SPARSE_SPECIALIST_PRESERVE_NOT_CORE"
    return {
        "candidate": "candidate-57-c04-v61-policy-fresh",
        "source_freeze": SOURCE_FREEZE,
        "source_failed_week": SOURCE_FAILED_WEEK,
        "controlled_change": (
            "remove exactly EXTERNAL_SETTLED_FAILED_DISCOVERY_REVERSAL and "
            "TRAPPED_COUNTERTREND_INVENTORY_RETEST_RESUMPTION after both lost in "
            "the first V56 prospective week; no threshold or remaining policy change"
        ),
        "policy_fresh_interval_rule": "use preselected V56 prospective weeks 2 then 3",
        "weeks": rows,
        "implementation_error": error,
        "mechanically_valid": mechanically_valid,
        "calendar_days": days,
        "active_weeks": active_weeks,
        "losing_weeks": losing_weeks,
        "trades": total_trades,
        "wins": total_wins,
        "losses": losing_trades,
        "win_rate": total_wins / total_trades if total_trades else 0.0,
        "compounded_return": compounded,
        "geometric_daily_growth": daily,
        "decision": decision,
        "long_evaluation_authorized": False,
        "integration_authorized": False,
        "thresholds_searched": False,
    }


def write_result(result: dict[str, Any]) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    write_json(EVIDENCE / "result.json", result)
    lines = [
        "# Candidate-04 V61 policy-fresh portability",
        "",
        "The first frozen V56 prospective week lost in two distinct causal families. "
        "V61 removes those complete families and changes nothing else.",
        "",
        f"- decision: **{result['decision']}**",
        f"- mechanically valid: **{result['mechanically_valid']}**",
        f"- trades: {result['trades']} ({result['wins']} wins / {result['losses']} losses)",
        f"- active weeks: {result['active_weeks']} / {len(WEEKS)}",
        f"- compounded after-cost return: {result['compounded_return']}",
        f"- geometric daily growth: {result['geometric_daily_growth']}",
        "",
        "| week | interval | routed after ablation | trades | W/L | return | geo/day | MDD | scenarios |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in result.get("weeks", []):
        ablation = row.get("controlled_ablation", {})
        scenarios = row.get("scenario_metrics") or {}
        lines.append(
            f"| {row.get('order')} | {row.get('evaluation_start')}..{row.get('evaluation_end')} | "
            f"{ablation.get('written_signals')} | {row.get('trades')} | "
            f"{row.get('wins')}/{int(row.get('trades') or 0)-int(row.get('wins') or 0)} | "
            f"{row.get('total_return')} | {row.get('geometric_daily_growth')} | "
            f"{row.get('max_drawdown')} | `{json.dumps(scenarios, sort_keys=True)}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "A negative result closes this revised core without changing prominence, flow, target, stop, or hold rules. "
            "A positive but sparse result is preserved only as a specialist. Only positive opportunity in both preselected "
            "weeks authorizes a four-asset one-slot Nautilus screen. Long evaluation remains unauthorized.",
        ]
    )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    error: str | None = None
    try:
        for spec in WEEKS:
            rows.append(run_week(spec))
    except Exception as exc:  # persisted as implementation evidence
        error = repr(exc)
    result = aggregate(rows, error)
    write_result(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["mechanically_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
