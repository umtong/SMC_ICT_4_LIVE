#!/usr/bin/env python3
"""First-week variable-control test for failed-impact reacceptance exit.

The strict online impact-failure plans are generated unchanged.  Execution is
run twice with identical entry, stop, target, fees, risk and one-position gate:

* baseline: emergency structural stop / target / time exit only;
* reacceptance: after a completed event closes back outside the failed boundary,
  exit at the next event open, with live stop/target precedence on gaps.

No detection parameter, price target or risk fraction changes.  This isolates
whether the missing scenario invalidation explains the remaining first-week
shortfall.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_clock import (  # noqa: E402
    calibrate_target_from_minutes,
    iter_volume_bars,
    minute_quote_totals,
)
from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_failure_candidate import ImpactFailureStateMachine  # noqa: E402
from impact_regime_probe import (  # noqa: E402
    CLOCK_CALIBRATION_MINUTES,
    ImpactRegimeDetector,
    simulate,
)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    evaluation_start = parse_utc_date(str(research["discovery_week"]))
    evaluation_end = evaluation_start + timedelta(days=7)
    warmup_start = evaluation_start - timedelta(days=1)
    warmup_ns = int(pd.Timestamp(warmup_start).as_unit("ns").value)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    warmup_minutes = minute_quote_totals(
        iter_downloads(records),
        start_ns=warmup_ns,
        end_ns=start_ns,
    )
    target_quote = calibrate_target_from_minutes(
        warmup_minutes,
        minutes_per_event=CLOCK_CALIBRATION_MINUTES,
    )
    bars = list(
        iter_volume_bars(
            iter_downloads(records),
            target_quote_notional=target_quote,
            include_partial=False,
        ),
    )

    detector = ImpactRegimeDetector()
    strict = ImpactFailureStateMachine(
        include_intermediate_extremes=True,
        reject_consumed_target=True,
    )
    for index, bar in enumerate(bars):
        detector_plans = detector.on_bar(bar)
        feature = detector.features[-1]
        initiatives = [plan for plan in detector_plans if plan.response == "CONTINUATION"]
        strict.on_feature(
            index=index,
            feature=feature,
            new_initiative_plans=initiatives,
        )

    variants = {
        "strict-baseline": False,
        "strict-reacceptance": True,
    }
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(asdict(row) for row in strict.transitions).to_csv(
        output / "strict_transitions.csv",
        index=False,
    )
    results: dict[str, Any] = {}
    for label, managed in variants.items():
        trades, metrics, daily, rejections = simulate(
            features=detector.features,
            plans=strict.plans,
            evaluation_start_ns=start_ns,
            evaluation_end_ns=end_ns,
            starting_nav=float(execution["starting_nav"]),
            cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
            exit_on_boundary_reacceptance=managed,
        )
        destination = output / label
        destination.mkdir(parents=True, exist_ok=True)
        trades.to_csv(destination / "trades.csv", index=False)
        daily.to_csv(destination / "daily_nav.csv", index=False)
        rejections.to_csv(destination / "rejections.csv", index=False)
        atomic_json(destination / "metrics.json", metrics)
        results[label] = metrics

    payload = {
        "diagnosis": "strict failed-impact boundary reacceptance exit",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target_quote,
        "strict_plan_count": len(strict.plans),
        "strict_state_counts": dict(strict.counts),
        "results": results,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_failure_reacceptance_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-aggtrades",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-impact-failure-reacceptance",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
