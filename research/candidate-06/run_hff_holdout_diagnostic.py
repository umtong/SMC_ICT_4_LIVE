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
    records: list[dict[str, Any]] = []
    for variant in VARIANTS:
        config_path = source_root / f"{variant}.json"
        if not config_path.exists():
            raise FileNotFoundError(config_path)
        for week_index in (1, 2):
            run_output = output / variant / f"week-{week_index + 1}"
            record = _run(
                config_path,
                run_output,
                week_index,
                candidate_dir,
                repository,
            )
            record.update(
                {
                    "variant": variant,
                    "week_index": week_index,
                    "config_path": str(config_path.relative_to(repository)),
                },
            )
            records.append(record)

    summary = {
        "purpose": "diagnostic only; no thresholds, state rules, risk, targets, stops or execution assumptions changed after the first-week result",
        "source_variants": list(VARIANTS),
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
        "Diagnostic only. The first-week contracts are replayed without modification.",
        "",
        "|variant|week|geom/day|trades|win rate|PF|max DD|failures|",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for record in records:
        metrics = record.get("metrics", {})
        lines.append(
            "|{variant}|{week}|{growth:.6%}|{trades}|{win:.2%}|{pf}|{dd:.2%}|{failures}|".format(
                variant=record["variant"],
                week=int(record["week_index"]) + 1,
                growth=float(metrics.get("geometric_daily_nav_growth", -1.0)),
                trades=metrics.get("trades"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            ),
        )
    (output / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
