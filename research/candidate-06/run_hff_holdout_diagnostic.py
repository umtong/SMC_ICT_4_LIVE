"""Unchanged holdout replay for the strongest first-week HFF contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from run_equilibrium_matrix import _run


VARIANTS = (
    "hff_bias_response_flow",
    "hff_all_flow",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/hff-holdout-diagnostic"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    source_root = repository / "artifacts/candidate-06/hff-first-week"
    diagnostic_config_root = output / "locked-configs"
    diagnostic_config_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for variant in VARIANTS:
        source_config_path = source_root / f"{variant}.json"
        if not source_config_path.exists():
            raise FileNotFoundError(source_config_path)
        config = json.loads(source_config_path.read_text(encoding="utf-8"))
        # Only the research-stage seal is opened. The complete scenario,
        # thresholds, risk, target, stop and execution contract remain byte-for-
        # byte identical to the first-week configuration.
        config.setdefault("validation", {})["stage"] = "three_week_validation"
        diagnostic_config_path = diagnostic_config_root / f"{variant}.json"
        diagnostic_config_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for week_index in (1, 2):
            run_output = output / variant / f"week-{week_index + 1}"
            record = _run(
                diagnostic_config_path,
                run_output,
                week_index,
                candidate_dir,
                repository,
            )
            record.update(
                {
                    "variant": variant,
                    "week_index": week_index,
                    "source_config_path": str(source_config_path.relative_to(repository)),
                    "diagnostic_config_path": str(diagnostic_config_path.relative_to(repository)),
                    "only_stage_seal_changed": True,
                },
            )
            records.append(record)

    summary = {
        "purpose": "diagnostic only; no thresholds, state rules, risk, targets, stops or execution assumptions changed after the first-week result",
        "source_variants": list(VARIANTS),
        "only_stage_seal_changed": True,
        "records": records,
        "long_evaluation_authorized": False,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Candidate 06 HFF Unchanged Holdout Diagnostic",
        "",
        "Diagnostic only. The first-week contracts are replayed without modification; only the stage seal is opened.",
        "",
        "|variant|week|rc|geom/day|trades|win rate|PF|max DD|failures|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for record in records:
        metrics = record.get("metrics", {})
        lines.append(
            "|{variant}|{week}|{rc}|{growth:.6%}|{trades}|{win:.2%}|{pf}|{dd:.2%}|{failures}|".format(
                variant=record["variant"],
                week=int(record["week_index"]) + 1,
                rc=record.get("returncode"),
                growth=float(metrics.get("geometric_daily_nav_growth", -1.0)),
                trades=metrics.get("trades"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            ),
        )
    (output / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if all(record.get("returncode") == 0 for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
