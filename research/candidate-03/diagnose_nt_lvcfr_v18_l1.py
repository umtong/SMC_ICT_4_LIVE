#!/usr/bin/env python3
"""Diagnose frozen V18 L1 routing without changing or scoring trades.

The report reproduces the frozen candidate-local quartiles and records which
vacuum-continuation and absorption conditions each expansion pre-candidate
satisfied. It emits no signal schedule, order, fill, PnL, or NAV result.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from derive_nt_lvcfr_v18_signals import (
    BASELINE_BLOCKS,
    L1Features,
    _quantile,
    collect_candidate_blocks,
    derive_expansion_pre_candidates,
)


def threshold_payload(baseline: list[L1Features]) -> dict[str, float]:
    return {
        "progress_q25": _quantile([x.progress_bp for x in baseline], 0.25),
        "progress_q75": _quantile([x.progress_bp for x in baseline], 0.75),
        "ofi_q75": _quantile([x.directional_ofi_norm for x in baseline], 0.75),
        "efficiency_q75": _quantile([x.impact_efficiency for x in baseline], 0.75),
        "replenishment_q50": _quantile([x.opposing_replenishment_norm for x in baseline], 0.50),
        "replenishment_q75": _quantile([x.opposing_replenishment_norm for x in baseline], 0.75),
        "depletion_q50": _quantile([x.opposing_depletion_norm for x in baseline], 0.50),
        "depth_ratio_q50": _quantile([x.opposing_depth_ratio for x in baseline], 0.50),
        "spread_q75": _quantile([x.spread_end_bp for x in baseline], 0.75),
    }


def evaluate_conditions(
    observation: L1Features,
    thresholds: dict[str, float],
) -> tuple[dict[str, bool], dict[str, bool]]:
    continuation = {
        "positive_upper_quartile_progress": observation.progress_bp > max(0.0, thresholds["progress_q75"]),
        "positive_microprice": observation.microprice_bp > 0.0,
        "upper_quartile_efficiency": observation.impact_efficiency >= thresholds["efficiency_q75"],
        "weak_opposing_replenishment": observation.opposing_replenishment_norm <= thresholds["replenishment_q50"],
        "strong_opposing_depletion": observation.opposing_depletion_norm >= thresholds["depletion_q50"],
        "low_opposing_depth": observation.opposing_depth_ratio <= thresholds["depth_ratio_q50"],
        "normal_spread": observation.spread_end_bp <= thresholds["spread_q75"],
    }
    absorption = {
        "upper_quartile_directional_ofi": observation.directional_ofi_norm >= thresholds["ofi_q75"],
        "poor_lower_quartile_progress": observation.progress_bp <= min(0.0, thresholds["progress_q25"]),
        "nonpositive_microprice": observation.microprice_bp <= 0.0,
        "strong_opposing_replenishment": observation.opposing_replenishment_norm >= thresholds["replenishment_q75"],
        "restored_opposing_depth": observation.opposing_depth_ratio >= thresholds["depth_ratio_q50"],
        "normal_spread": observation.spread_end_bp <= thresholds["spread_q75"],
    }
    return continuation, absorption


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    manifest = json.loads((prepared / "data_manifest.json").read_text(encoding="utf-8"))
    candidates = derive_expansion_pre_candidates(
        raw_root=prepared / "raw",
        evaluation_start_ns=int(manifest["evaluation_start_ns"]),
        evaluation_end_ns=int(manifest["evaluation_end_ns"]),
    )
    contexts = collect_candidate_blocks(
        book_ticker_paths=sorted((prepared / "raw/book_ticker").glob("*.zip")),
        candidates=candidates,
    )

    continuation_condition_passes: Counter[str] = Counter()
    absorption_condition_passes: Counter[str] = Counter()
    continuation_pass_count: Counter[int] = Counter()
    absorption_pass_count: Counter[int] = Counter()
    rows: list[dict[str, Any]] = []
    insufficient = 0
    for context in contexts:
        baseline = [
            features
            for block in context.blocks[:BASELINE_BLOCKS]
            if (features := block.features()) is not None
        ]
        observation = context.blocks[BASELINE_BLOCKS].features()
        if observation is None or len(baseline) < BASELINE_BLOCKS // 2:
            insufficient += 1
            continue
        thresholds = threshold_payload(baseline)
        continuation, absorption = evaluate_conditions(observation, thresholds)
        for name, passed in continuation.items():
            continuation_condition_passes[name] += int(passed)
        for name, passed in absorption.items():
            absorption_condition_passes[name] += int(passed)
        continuation_pass_count[sum(continuation.values())] += 1
        absorption_pass_count[sum(absorption.values())] += 1
        rows.append(
            {
                "scenario_id": context.signal["scenario_id"],
                "direction": int(context.signal["direction"]),
                "valid_baseline_blocks": len(baseline),
                "observation": {
                    name: getattr(observation, name)
                    for name in L1Features.__dataclass_fields__
                },
                "thresholds": thresholds,
                "continuation_conditions": continuation,
                "continuation_pass_count": sum(continuation.values()),
                "absorption_conditions": absorption,
                "absorption_pass_count": sum(absorption.values()),
            }
        )

    payload = {
        "candidate": "candidate-03-nt-lvcfr-v18-order-book-resilience-router",
        "engine_status": "detector_diagnostics_only_no_backtest",
        "expansion_pre_candidate_count": len(candidates),
        "diagnosed_candidates": len(rows),
        "insufficient_l1_context": insufficient,
        "continuation_condition_passes": dict(sorted(continuation_condition_passes.items())),
        "absorption_condition_passes": dict(sorted(absorption_condition_passes.items())),
        "continuation_pass_count_distribution": {str(k): v for k, v in sorted(continuation_pass_count.items())},
        "absorption_pass_count_distribution": {str(k): v for k, v in sorted(absorption_pass_count.items())},
        "fully_confirmed_continuations": sum(row["continuation_pass_count"] == 7 for row in rows),
        "fully_confirmed_absorptions": sum(row["absorption_pass_count"] == 6 for row in rows),
        "threshold_policy": "frozen candidate-local quartiles",
        "performance_metrics_calculated": False,
        "candidate_details": rows,
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in payload.items() if key != "candidate_details"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
