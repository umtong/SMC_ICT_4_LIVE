#!/usr/bin/env python3
"""Stage CIRB parent-frozen 1m-versus-5s response-resolution validation."""
from __future__ import annotations

import argparse
from functools import reduce
import json
from operator import mul
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


def _run(command: list[str], *, cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-8000:],
        "stderr_tail": completed.stderr[-16000:],
    }


def _metrics(path: Path) -> dict[str, Any] | None:
    target = path / "metrics.json"
    return json.loads(target.read_text(encoding="utf-8")) if target.exists() else None


def _baseline(
    *,
    config: Path,
    output: Path,
    week_index: int,
    candidate_dir: Path,
    repository: Path,
) -> dict[str, Any]:
    record = _run(
        [
            sys.executable,
            str(candidate_dir / "run_crowding_inventory_response_validation.py"),
            "--config",
            str(config),
            "--output",
            str(output),
            "--week-index",
            str(week_index),
            "--allow-gate-fail",
        ],
        cwd=repository,
    )
    record.update(
        {
            "kind": "cirb_full_1m_baseline",
            "week_index": week_index,
            "output": str(output.relative_to(repository)),
            "metrics": _metrics(output),
        }
    )
    return record


def _five_second(
    *,
    config: Path,
    baseline_output: Path,
    output: Path,
    week_index: int,
    variant: str,
    candidate_dir: Path,
    repository: Path,
) -> dict[str, Any]:
    record = _run(
        [
            sys.executable,
            str(candidate_dir / "run_cirb_execution_resolution_week.py"),
            "--config",
            str(config),
            "--baseline-events",
            str(baseline_output / "scenario_events.jsonl"),
            "--output",
            str(output),
            "--week-index",
            str(week_index),
            "--variant",
            variant,
            "--allow-gate-fail",
        ],
        cwd=repository,
    )
    record.update(
        {
            "kind": (
                "cirb_full_5s_response_resolution"
                if variant == "full"
                else "cirb_discharge_only_5s_attribution"
            ),
            "week_index": week_index,
            "variant": variant,
            "output": str(output.relative_to(repository)),
            "metrics": _metrics(output),
        }
    )
    return record


def _valid(record: Mapping[str, Any]) -> bool:
    metrics = record.get("metrics")
    return int(record.get("returncode", 1)) == 0 and isinstance(metrics, Mapping)


def _resolution(record: Mapping[str, Any]) -> Mapping[str, Any]:
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        return {}
    diagnostics = metrics.get("diagnostics", {})
    return diagnostics.get("cirb_execution_resolution", {}) if isinstance(diagnostics, Mapping) else {}


def _w2_rescue(record: Mapping[str, Any]) -> bool:
    if not _valid(record):
        return False
    metrics = record["metrics"]
    resolution = _resolution(record)
    return (
        bool(resolution.get("parent_identity_passed"))
        and int(resolution.get("semantic_drift_count", 1)) == 0
        and int(resolution.get("child_5s_candidates", 0)) > 0
        and int(metrics.get("trades", 0)) > 0
        and int(resolution.get("rescued_by_5s_closed_trades", 0)) > 0
        and float(metrics.get("net_pnl_after_cost", 0.0)) > 0.0
    )


def _combined(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    metrics = [record["metrics"] for record in records if isinstance(record.get("metrics"), Mapping)]
    if not metrics:
        return {}
    nav_multiples = [
        float(item["ending_nav"]) / float(item["starting_nav"])
        for item in metrics
        if float(item["starting_nav"]) > 0.0
    ]
    multiple = reduce(mul, nav_multiples, 1.0)
    days = 7.0 * len(metrics)
    trades = sum(int(item.get("trades", 0)) for item in metrics)
    wins = sum(int(item.get("wins", 0)) for item in metrics)
    return {
        "weeks": len(metrics),
        "nav_multiple": multiple,
        "geometric_daily_nav_growth": multiple ** (1.0 / days) - 1.0 if multiple > 0.0 else -1.0,
        "trades": trades,
        "wins": wins,
        "win_rate": wins / trades if trades else 0.0,
        "worst_week_drawdown": max(float(item.get("max_drawdown_nav", 0.0)) for item in metrics),
        "all_weekly_gates_passed": all(bool(item.get("gate_passed")) for item in metrics),
        "all_parent_identities_passed": all(
            bool(
                item.get("diagnostics", {})
                .get("cirb_execution_resolution", {})
                .get("parent_identity_passed")
            )
            for item in metrics
        ),
        "semantic_drift_count": sum(
            int(
                item.get("diagnostics", {})
                .get("cirb_execution_resolution", {})
                .get("semantic_drift_count", 0)
            )
            for item in metrics
        ),
        "child_5s_candidates": sum(
            int(
                item.get("diagnostics", {})
                .get("cirb_execution_resolution", {})
                .get("child_5s_candidates", 0)
            )
            for item in metrics
        ),
        "rescued_by_5s_closed_trades": sum(
            int(
                item.get("diagnostics", {})
                .get("cirb_execution_resolution", {})
                .get("rescued_by_5s_closed_trades", 0)
            )
            for item in metrics
        ),
        "still_rr_eroded": sum(
            int(
                item.get("diagnostics", {})
                .get("cirb_execution_resolution", {})
                .get("still_rr_eroded", 0)
            )
            for item in metrics
        ),
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 CIRB parent-frozen response-resolution ablation",
        "",
        f"Terminal status: `{summary.get('terminal_status')}`",
        f"Selected: `{summary.get('selected')}`",
        "",
        "|kind|week|gate|geom/day|trades|wins|win rate|PF|max DD|child candidates|rescued|RR-eroded|",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for record in summary.get("records", []):
        metrics = record.get("metrics") or {}
        resolution = _resolution(record)
        lines.append(
            "|{kind}|{week}|{gate}|{growth:.6%}|{trades}|{wins}|{win:.2%}|{pf}|{dd:.2%}|{children}|{rescued}|{eroded}|".format(
                kind=record.get("kind"),
                week=int(record.get("week_index", 0)) + 1,
                gate=metrics.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", 0.0)),
                trades=metrics.get("trades"),
                wins=metrics.get("wins"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                children=resolution.get("child_5s_candidates", ""),
                rescued=resolution.get("rescued_by_5s_closed_trades", ""),
                eroded=resolution.get("still_rr_eroded", ""),
            )
        )
    lines.extend(
        [
            "",
            "## Fixed interpretation",
            "",
            "- The one-minute Nautilus run determines the parent-event population before 5-second data is scored.",
            "- Five-second bars cannot create, remove, relabel, or reverse a parent crowding branch.",
            "- The event bar cannot trade; only a later completed five-second response may arm an entry.",
            "- Stop, objective family, fees, adverse ticks, 3% whole-NAV planned loss and one global slot remain unchanged.",
            "- Discharge-only is attribution evidence and is not selectable in this experiment.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write(root: Path, summary: Mapping[str, Any]) -> None:
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/cirb-execution-resolution"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    config = candidate_dir / "config.cirb.locked.json"
    records: list[dict[str, Any]] = []

    # W2 is the decisive execution-scarcity week: the frozen one-minute system
    # armed five entries but every one lost cost-after-delay geometry.
    w2 = 1
    baseline_w2_root = root / "baseline" / "week-2"
    baseline_w2 = _baseline(
        config=config,
        output=baseline_w2_root,
        week_index=w2,
        candidate_dir=candidate_dir,
        repository=repository,
    )
    records.append(baseline_w2)
    if not _valid(baseline_w2):
        summary = {
            "terminal_status": "BASELINE_REPRODUCTION_FAILED",
            "selected": None,
            "records": records,
            "long_evaluation_authorized": False,
        }
        _write(root, summary)
        return 5

    w2_full = _five_second(
        config=config,
        baseline_output=baseline_w2_root,
        output=root / "five-second" / "full" / "week-2",
        week_index=w2,
        variant="full",
        candidate_dir=candidate_dir,
        repository=repository,
    )
    w2_discharge = _five_second(
        config=config,
        baseline_output=baseline_w2_root,
        output=root / "five-second" / "discharge-only" / "week-2",
        week_index=w2,
        variant="discharge-only",
        candidate_dir=candidate_dir,
        repository=repository,
    )
    records.extend((w2_full, w2_discharge))
    if not _valid(w2_full) or not _valid(w2_discharge):
        summary = {
            "terminal_status": "FIVE_SECOND_IMPLEMENTATION_OR_DATA_FAILURE",
            "selected": None,
            "records": records,
            "long_evaluation_authorized": False,
        }
        _write(root, summary)
        return 5

    if not (_w2_rescue(w2_full) or _w2_rescue(w2_discharge)):
        summary = {
            "terminal_status": "W2_EXECUTION_RESOLUTION_HYPOTHESIS_REJECTED",
            "selected": None,
            "records": records,
            "w2_full_rescue": _w2_rescue(w2_full),
            "w2_discharge_rescue": _w2_rescue(w2_discharge),
            "long_evaluation_authorized": False,
        }
        _write(root, summary)
        return 2

    for week_index in (0, 2):
        week_number = week_index + 1
        baseline_root = root / "baseline" / f"week-{week_number}"
        baseline = _baseline(
            config=config,
            output=baseline_root,
            week_index=week_index,
            candidate_dir=candidate_dir,
            repository=repository,
        )
        records.append(baseline)
        if not _valid(baseline):
            summary = {
                "terminal_status": "BASELINE_REPRODUCTION_FAILED",
                "selected": None,
                "records": records,
                "long_evaluation_authorized": False,
            }
            _write(root, summary)
            return 5
        records.append(
            _five_second(
                config=config,
                baseline_output=baseline_root,
                output=root / "five-second" / "full" / f"week-{week_number}",
                week_index=week_index,
                variant="full",
                candidate_dir=candidate_dir,
                repository=repository,
            )
        )
        records.append(
            _five_second(
                config=config,
                baseline_output=baseline_root,
                output=root / "five-second" / "discharge-only" / f"week-{week_number}",
                week_index=week_index,
                variant="discharge-only",
                candidate_dir=candidate_dir,
                repository=repository,
            )
        )

    if not all(_valid(record) for record in records):
        summary = {
            "terminal_status": "LATER_WEEK_IMPLEMENTATION_OR_DATA_FAILURE",
            "selected": None,
            "records": records,
            "long_evaluation_authorized": False,
        }
        _write(root, summary)
        return 5

    full = sorted(
        [record for record in records if record.get("kind") == "cirb_full_5s_response_resolution"],
        key=lambda item: int(item["week_index"]),
    )
    discharge = sorted(
        [record for record in records if record.get("kind") == "cirb_discharge_only_5s_attribution"],
        key=lambda item: int(item["week_index"]),
    )
    combined_full = _combined(full)
    combined_discharge = _combined(discharge)
    selected = None
    terminal = "THREE_WEEK_LOGIC_GATE_FAILED"
    if (
        combined_full.get("all_weekly_gates_passed")
        and combined_full.get("all_parent_identities_passed")
        and int(combined_full.get("semantic_drift_count", 1)) == 0
        and float(combined_full.get("geometric_daily_nav_growth", 0.0)) >= 0.01
    ):
        selected = "cirb_full_5s_response_resolution"
        terminal = "THREE_WEEK_GATE_PASSED"
    summary = {
        "terminal_status": terminal,
        "selected": selected,
        "records": records,
        "combined_full": combined_full,
        "combined_discharge_attribution": combined_discharge,
        "w2_full_rescue": _w2_rescue(w2_full),
        "w2_discharge_rescue": _w2_rescue(w2_discharge),
        "long_evaluation_authorized": selected is not None,
    }
    _write(root, summary)
    return 0 if selected is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
