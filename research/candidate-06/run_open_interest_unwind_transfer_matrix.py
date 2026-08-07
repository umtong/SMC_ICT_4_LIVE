#!/usr/bin/env python3
"""Predeclared OIUT unwind-only matrix using the validated OIIR state machine."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping

from run_open_interest_inventory_matrix import (
    _base as _inventory_base,
    _counts,
    _diagnose,
    _run,
)


VARIANTS = (
    (
        "oiut_unwind_transfer_full",
        (
            "Extreme OI contraction plus aligned price/flow; later completed "
            "response selects persistent deleveraging continuation or counter-"
            "inventory reversal only after OI re-expands."
        ),
        True,
        True,
        True,
    ),
    (
        "oiut_unwind_continuation_only",
        (
            "Branch attribution: persistent OI contraction and price discovery; "
            "counter-inventory reversal disabled."
        ),
        False,
        True,
        False,
    ),
    (
        "oiut_reversal_without_counter_inventory_ablation",
        (
            "One core-variable ablation: identical unwind bifurcation, but "
            "reversal may trigger on price/flow reclaim without completed OI "
            "re-expansion."
        ),
        True,
        True,
        False,
    ),
)


def _base(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = _inventory_base(raw)
    config["version"] = "5.2.0"
    config["hypothesis"] = (
        "Extreme completed OI contraction is forced inventory transfer. It may "
        "continue only when OI keeps contracting with price discovery, or reverse "
        "only when opposite inventory is visibly rebuilt through completed OI "
        "re-expansion and reclaim."
    )
    config["logic"]["oiir_enable_build"] = False
    return config


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v5.2 Open-Interest Unwind Transfer",
        "",
        f"Terminal status: `{summary.get('terminal_status')}`",
        (
            f"Selected: `{summary.get('selected')}`"
            if summary.get("selected")
            else "Selected: none"
        ),
        "",
        (
            "|variant|week|eligible|gate|geom/day|trades|wins|"
            "win rate|PF|max DD|failures|"
        ),
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    records = [
        *summary.get("first_week_results", []),
        *summary.get("frozen_validation", []),
    ]
    for record in records:
        metrics = record.get("metrics", {})
        lines.append(
            (
                "|{name}|{week}|{eligible}|{gate}|{growth:.6%}|{trades}|"
                "{wins}|{win:.2%}|{pf}|{dd:.2%}|{failures}|"
            ).format(
                name=record.get("name"),
                week=int(record.get("week_index", 0)) + 1,
                eligible=record.get("eligible_for_selection"),
                gate=record.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", 0.0)),
                trades=metrics.get("trades"),
                wins=metrics.get("wins"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            ),
        )
    lines.extend(["", "## Diagnoses", ""])
    for name, diagnosis in summary.get("diagnoses", {}).items():
        lines.append(
            f"- **{name}**: `{diagnosis.get('classification')}` — "
            f"`{diagnosis}`",
        )
    return "\n".join(lines) + "\n"


def _write(root: Path, summary: Mapping[str, Any]) -> None:
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/oiut-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    base = _base(raw)
    configs: dict[str, dict[str, Any]] = {}
    first: list[dict[str, Any]] = []

    for (
        name,
        description,
        enable_reversal,
        enable_continuation,
        require_counter_rebuild,
    ) in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "oiir_enable_build": False,
                "oiir_enable_unwind": True,
                "oiir_enable_unwind_reversal": enable_reversal,
                "oiir_enable_unwind_continuation": enable_continuation,
                "oiir_require_counter_inventory_rebuild": require_counter_rebuild,
            },
        )
        configs[name] = config
        config_dir = root / "configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{name}-week-1.json"
        config_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_output = root / "runs" / name / "week-1"
        record = _run(
            config_path,
            run_output,
            0,
            candidate_dir,
            repository,
        )
        record.update(
            {
                "name": name,
                "description": description,
                "eligible_for_selection": name == "oiut_unwind_transfer_full",
                "week_index": 0,
                "config_path": str(config_path.relative_to(repository)),
                "causal_counts": _counts(run_output),
            },
        )
        first.append(record)

    diagnoses = {record["name"]: _diagnose(record) for record in first}
    implementation_ok = all(
        int(record.get("returncode", 1)) == 0
        and isinstance(record.get("metrics"), Mapping)
        for record in first
    )
    base_summary: dict[str, Any] = {
        "candidate": "candidate-06-oiut-v5.2",
        "design": (
            "completed OI contraction and aligned price/flow -> later persistent "
            "contraction continuation or completed counter-inventory rebuild "
            "reversal -> structural objective"
        ),
        "first_week_results": first,
        "frozen_validation": [],
        "diagnoses": diagnoses,
        "selected": None,
        "all_three_weeks_passed": False,
        "long_evaluation_authorized": False,
    }
    if not implementation_ok:
        summary = {
            **base_summary,
            "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE",
        }
        _write(root, summary)
        return 5

    full = next(
        record
        for record in first
        if record["name"] == "oiut_unwind_transfer_full"
    )
    if not full.get("gate_passed"):
        summary = {
            **base_summary,
            "terminal_status": "FIRST_WEEK_LOGIC_GATE_FAILED",
            "discarded": {
                "oiut_unwind_transfer_full": diagnoses[
                    "oiut_unwind_transfer_full"
                ],
            },
        }
        _write(root, summary)
        return 2

    selected = "oiut_unwind_transfer_full"
    locked = copy.deepcopy(configs[selected])
    locked.setdefault("validation", {})["stage"] = "three_week_validation"
    locked_path = candidate_dir / "config.oiut.locked.json"
    locked_path.write_text(
        json.dumps(locked, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    frozen: list[dict[str, Any]] = []
    for week_index in (1, 2):
        run_output = root / "runs" / selected / f"week-{week_index + 1}"
        config_path = root / "configs" / f"{selected}-week-{week_index + 1}.json"
        config_path.write_text(
            json.dumps(locked, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        record = _run(
            config_path,
            run_output,
            week_index,
            candidate_dir,
            repository,
        )
        record.update(
            {
                "name": selected,
                "description": VARIANTS[0][1],
                "eligible_for_selection": True,
                "week_index": week_index,
                "config_path": str(config_path.relative_to(repository)),
                "causal_counts": _counts(run_output),
            },
        )
        frozen.append(record)

    all_three = len(frozen) == 2 and all(
        record.get("gate_passed")
        for record in frozen
    )
    summary = {
        **base_summary,
        "selected": selected,
        "locked_config": str(locked_path.relative_to(repository)),
        "frozen_validation": frozen,
        "all_three_weeks_passed": all_three,
        "long_evaluation_authorized": all_three,
        "terminal_status": (
            "THREE_WEEK_GATE_PASSED"
            if all_three
            else "FROZEN_HOLDOUT_LOGIC_GATE_FAILED"
        ),
        "holdout_diagnoses": {
            f"week-{record['week_index'] + 1}": _diagnose(record)
            for record in frozen
        },
    }
    _write(root, summary)
    return 0 if all_three else 3


if __name__ == "__main__":
    raise SystemExit(main())
