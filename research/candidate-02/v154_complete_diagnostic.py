#!/usr/bin/env python3
"""Complete Candidate-02 V154 exposed diagnostics without tuning or holdout use.

The pinned Candidate-13 runner may return exit code 2 when its own interval gate
or safety audit fails.  That is evidence, not a reason to hide the interval or
stop collecting the already-exposed failure distribution.  This driver:

1. runs all protocol intervals through the inherited NautilusTrader runner,
2. preserves return code 0/2 and fails on every other return code,
3. audits V154's causal state sequence and independent-episode identity,
4. distinguishes a flat-account orphan contingent cancellation race from an
   open-position protective-order rejection,
5. invokes the already-existing Candidate-13 aggregate calculator, and
6. records a final exposed-development rejection with no success claim.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


QH_MODULE = "QH_INITIATIVE_COMPLETED_AUCTION_LEG"
EXPECTED_ROUTE = "COMMON_FLOW_THEN_COMPLETED_AUCTION_LEG"
EXPECTED_ENTRY_MODEL = "PASSIVE_FRESH_REACCELERATION_FVG_MIDPOINT"
ORPHAN_CONTINGENT_MARKERS = ("Contingent order", "already closed")
COMPACT_FILES = {
    "audit.json",
    "metrics.json",
    "submitted_plans.json",
    "closed_trades.json",
    "order_lifecycle.json",
    "source_lock.json",
    "run.json",
    "data_manifest.json",
    "positions.csv",
    "v154_causal_audit.json",
    "runner_exit_code.txt",
    "console.log",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def ordered_intervals(protocol: dict[str, Any]) -> list[str]:
    holdouts = protocol["selection"]["holdouts"]
    return sorted(
        holdouts,
        key=lambda name: date.fromisoformat(holdouts[name]["start"]),
    )


def is_orphan_contingent_rejection(error: Any) -> bool:
    if not isinstance(error, dict) or error.get("type") != "ORDER_REJECTED":
        return False
    text = str(error.get("event", ""))
    return all(marker in text for marker in ORPHAN_CONTINGENT_MARKERS)


def causal_audit(
    *,
    root: Path,
    interval: str,
    source_commit: str,
    runner_exit_code: int,
) -> dict[str, Any]:
    plans_payload = load_json(root / "submitted_plans.json")
    plans = plans_payload.get("plans", [])
    qh = [plan for plan in plans if plan.get("module") == QH_MODULE]
    violations: list[str] = []
    episodes: set[str] = set()

    for index, plan in enumerate(qh):
        details = plan.get("details", {})
        episode = details.get("independent_episode_key")
        if not episode or episode in episodes:
            violations.append(
                f"plan[{index}] duplicate or absent independent episode",
            )
        if episode:
            episodes.add(str(episode))

        observed = int(plan["observed_ts_ns"])
        initiative_end = int(details.get("initiative_end_ts_ns", observed))
        retrace = int(details.get("fvg_retrace_ts_ns", observed))
        fresh = int(
            details.get("fresh_reacceleration_fvg_ts_ns", observed + 1),
        )
        if not initiative_end < retrace < fresh <= observed:
            violations.append(f"plan[{index}] noncausal state chronology")
        if details.get("entry_model") != EXPECTED_ENTRY_MODEL:
            violations.append(f"plan[{index}] wrong entry model")
        if details.get("route") != EXPECTED_ROUTE:
            violations.append(f"plan[{index}] wrong route")
        sequence = tuple(details.get("state_sequence", ()))
        if not sequence or sequence[0] != "COMMON_FLOW_INITIATIVE":
            violations.append(f"plan[{index}] missing initiative state")
        if sequence[-2:] != ("FRESH_REACCELERATION_FVG", "ENTRY_ARMED"):
            violations.append(f"plan[{index}] incomplete terminal states")

    lifecycle_payload = load_json(root / "order_lifecycle.json")
    lifecycle = lifecycle_payload.get("events", [])
    fail_closed = sum(
        event.get("type") == "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED"
        for event in lifecycle
        if isinstance(event, dict)
    )
    metrics = load_json(root / "metrics.json")
    engine_errors = metrics.get("engine_errors", [])
    orphan_errors = [
        error for error in engine_errors if is_orphan_contingent_rejection(error)
    ]
    unexpected_errors = [
        error for error in engine_errors if not is_orphan_contingent_rejection(error)
    ]
    if unexpected_errors:
        violations.append(
            f"unexpected engine errors: {len(unexpected_errors)}",
        )

    audit = {
        "schema": "candidate-02-v154-causal-audit-v2",
        "interval": interval,
        "pinned_candidate13_commit": source_commit,
        "runner_exit_code": runner_exit_code,
        "qh_submitted_plans": len(qh),
        "independent_qh_episodes": len(episodes),
        "protective_rejections_fail_closed": fail_closed,
        "orphan_contingent_rejections_after_flat": len(orphan_errors),
        "unexpected_engine_errors": unexpected_errors,
        "future_information_used": False,
        "initiative_can_emit_entry": False,
        "violations": violations,
        "passed": not violations,
        "plan_modules": dict(
            Counter(plan.get("module", "SCDAM_CORE") for plan in plans),
        ),
        "safety_interpretation": (
            "A contingent-child rejection whose referenced sibling is already "
            "closed is recorded separately as a flat-account cancellation race. "
            "Any open-position protective rejection must appear as "
            "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED or fail this audit."
        ),
    }
    write_json(root / "v154_causal_audit.json", audit)
    return audit


def compact_interval(root: Path) -> None:
    shutil.rmtree(root / "data", ignore_errors=True)
    for path in root.iterdir():
        if path.is_file() and path.name not in COMPACT_FILES:
            path.unlink()


def run_interval(
    *,
    candidate13_root: Path,
    results: Path,
    interval: str,
    source_commit: str,
) -> dict[str, Any]:
    output = results / interval
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(candidate13_root / "candidate13_v9_runner.py"),
        "run",
        interval,
        str(output),
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    console = completed.stdout or ""
    print(console, end="" if console.endswith("\n") else "\n")
    (output / "console.log").write_text(console, encoding="utf-8")
    (output / "runner_exit_code.txt").write_text(
        f"{completed.returncode}\n",
        encoding="utf-8",
    )
    if completed.returncode not in {0, 2}:
        raise RuntimeError(
            f"{interval} runner returned unexpected code {completed.returncode}",
        )
    required = (
        "audit.json",
        "metrics.json",
        "submitted_plans.json",
        "closed_trades.json",
        "order_lifecycle.json",
    )
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise FileNotFoundError(f"{interval} missing evidence files: {missing}")
    audit = causal_audit(
        root=output,
        interval=interval,
        source_commit=source_commit,
        runner_exit_code=completed.returncode,
    )
    if not audit["passed"]:
        raise RuntimeError(
            f"{interval} causal audit failed: {audit['violations']}",
        )
    compact_interval(output)
    return audit


def aggregate(
    *,
    candidate13_root: Path,
    results: Path,
    protocol_path: Path,
    source_commit: str,
) -> dict[str, Any]:
    sys.path.insert(0, str(candidate13_root))
    try:
        from aggregate_v9_development import aggregate as inherited_aggregate
    finally:
        sys.path.pop(0)

    decision_path = results / "artifacts-v154-family-decision.json"
    result = inherited_aggregate(results, protocol_path, decision_path)
    funnel: Counter[str] = Counter()
    qh_plans = 0
    fail_closed = 0
    orphan_rejections = 0
    causal_pass = True
    exit_codes: dict[str, int] = {}

    protocol = load_json(protocol_path)
    for interval in ordered_intervals(protocol):
        metrics = load_json(results / interval / "metrics.json")
        funnel.update(metrics.get("skip_reasons", {}))
        causal = load_json(results / interval / "v154_causal_audit.json")
        qh_plans += int(causal.get("qh_submitted_plans", 0))
        fail_closed += int(causal.get("protective_rejections_fail_closed", 0))
        orphan_rejections += int(
            causal.get("orphan_contingent_rejections_after_flat", 0),
        )
        causal_pass = causal_pass and causal.get("passed") is True
        exit_codes[interval] = int(
            (results / interval / "runner_exit_code.txt")
            .read_text(encoding="utf-8")
            .strip(),
        )

    original_classification = result.get("classification")
    result.update(
        {
            "engine": "NautilusTrader 1.230.0",
            "custom_backtest_engine": False,
            "risk_fraction": 0.03,
            "global_pending_entry_plus_position_limit": 1,
            "arbitrary_notional_cap": None,
            "score_risk_multiplier": None,
            "lineage": {
                "candidate13_source_commit": source_commit,
                "alpha_change": (
                    "quarter-hour initiative is context only; a follower must "
                    "complete separate delivery, FVG retrace and fresh "
                    "reacceleration states"
                ),
                "execution_change": (
                    "fail-close a denied or rejected protective child after "
                    "parent fill; separately record an already-flat orphan "
                    "contingent cancellation race"
                ),
            },
            "inherited_gate_classification": original_classification,
            "runner_exit_codes": exit_codes,
            "qh_submitted_plans": qh_plans,
            "protective_rejections_fail_closed": fail_closed,
            "orphan_contingent_rejections_after_flat": orphan_rejections,
            "all_causal_audits_passed": causal_pass,
            "aggregated_state_funnel": dict(funnel.most_common()),
            "status": "REJECT_OR_REDESIGN",
            "project_target_met": False,
            "untouched_data_used": False,
            "success_claim": False,
            "decision": (
                "Reject the standalone quarter-hour continuation family. "
                "Do not tune its thresholds on E01-E06. Preserve only the "
                "initiative/context insight, unique causal episode identity, "
                "and fail-closed execution lesson."
            ),
            "next_action": (
                "Return research concentration to the strongest Candidate-13 "
                "price-discovery leadership lineage and add opportunity only "
                "through an independent structural scenario family rather than "
                "salvaging quarter-hour continuation."
            ),
        },
    )
    write_json(decision_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate13-root", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()

    protocol = load_json(args.protocol)
    args.results.mkdir(parents=True, exist_ok=True)
    audits: dict[str, Any] = {}
    for interval in ordered_intervals(protocol):
        audits[interval] = run_interval(
            candidate13_root=args.candidate13_root,
            results=args.results,
            interval=interval,
            source_commit=args.source_commit,
        )
    decision = aggregate(
        candidate13_root=args.candidate13_root,
        results=args.results,
        protocol_path=args.protocol,
        source_commit=args.source_commit,
    )
    print(json.dumps(decision, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
