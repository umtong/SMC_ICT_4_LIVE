#!/usr/bin/env python3
"""Dependency-light ML2 contract checks, with an optional tiny CatBoost round trip."""
from __future__ import annotations

import argparse
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
import tempfile

import numpy as np
import pandas as pd

from build_ml2_dataset import build_dataset
from merge_ml2_datasets import merge_datasets
from ml2_context import FactorTransitionBook, inherited_preplan_factor_allows
from ml2_features import FEATURE_NAMES
from ml2_model import decision_from_probability, estimate_trade_economics


class Side:
    def __init__(self, name: str) -> None:
        self.name = name


def _event(plan_id: str, causal_id: str, ts_ns: int, outcome_win_r: float = 1.4) -> dict[str, object]:
    row: dict[str, object] = {
        "kind": "ml2_plan",
        "plan_id": plan_id,
        "causal_event_id": causal_id,
        "ts_ns": ts_ns,
        "symbol": "BTCUSDT",
        "family": "HORIZONTAL_FLIP_CONTINUATION",
        "side": "LONG",
        "scenario_path": "ACCEPTANCE",
        "ml2_causal_family": "ACCEPTED_BREAK",
        "ml2_required_log_probability": 0.45,
        "ml2_win_net_r": outcome_win_r,
        "ml2_loss_net_r": -1.05,
    }
    row.update({f"ml2f_{name}": 0.0 for name in FEATURE_NAMES})
    return row


def core_checks(root: Path) -> None:
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))
    assert not any("symbol" in name.lower() for name in FEATURE_NAMES)
    assert {"higher_strength", "lower_strength", "trigger_strength"}.issubset(FEATURE_NAMES)

    economics = estimate_trade_economics(
        side=Side("LONG"),
        entry=100.0,
        stop=99.0,
        target=101.5,
        tick_size=0.1,
        entry_fee_rate=0.0004,
        target_fee_rate=0.0004,
        stop_fee_rate=0.0004,
        entry_slippage_ticks=1,
        stop_slippage_ticks=1,
    )
    below = decision_from_probability(0.40, economics, risk_fraction=0.03)
    above = decision_from_probability(0.80, economics, risk_fraction=0.03)
    assert not below.accepted
    assert above.accepted
    assert 0.0 < above.required_probability < 1.0
    assert above.expected_log_growth > 0.0

    # A global provenance tuple must not make an unrelated rejection look like
    # a local continuation hidden-veto case.
    book = FactorTransitionBook()
    book.observe(10, SimpleNamespace(side=Side("SHORT"), event_time_ns=10))
    rejection = SimpleNamespace(
        family="STRUCTURE_REJECTION_FOOTPRINT_RETEST",
        scale_name="REJECTION",
        scenario_path="REJECTION",
        higher_zone_kind="HORIZONTAL_SUPPORT",
        lower_zone_kind="ORDER_BLOCK",
        trigger_zone_kind="FIRST_RESPONSE_ADVERSE_FLOW_ABSORBED",
        target_zone_kind="SWING_HIGH",
        rule_provenance=("LOCAL_AUCTION_CONTINUATION", "EFFICIENT_PULLBACK"),
        side=Side("LONG"),
        setup_observed_time_ns=10,
        observed_time_ns=120_000_000_010,
        trigger_timeframe_minutes=1,
    )
    assert inherited_preplan_factor_allows(rejection, book)

    events_path = root / "events.csv"
    labels_path = root / "labels.csv"
    output_path = root / "dataset.csv"
    summary_path = root / "dataset_summary.json"
    ts = int(pd.Timestamp("2025-01-01T00:00:00Z").value)
    pd.DataFrame([_event("p1", "episode-1", ts)]).to_csv(events_path, index=False)
    pd.DataFrame(
        [
            {
                "plan_id": "p1",
                "counterfactual_outcome": "AMBIGUOUS_SAME_MINUTE",
                "counterfactual_resolution_time": "2025-01-01T00:03:00+00:00",
                "counterfactual_minutes_to_resolution": 3.0,
            },
        ],
    ).to_csv(labels_path, index=False)
    summary = build_dataset(
        events_path=events_path,
        counterfactual_path=labels_path,
        output_path=output_path,
        summary_path=summary_path,
    )
    dataset = pd.read_csv(output_path)
    assert summary["resolved_rows"] == 1
    assert dataset.loc[0, "label"] == 0.0
    assert dataset.loc[0, "observed_outcome_net_r"] == -1.05
    assert dataset.loc[0, "event_group_id"] == "BTCUSDT|episode-1"

    # Independent runner-local plan counters are namespaced during merge.
    second = dataset.copy()
    second["event_time_ns"] += 86_400_000_000_000
    second["label_end_ns"] += 86_400_000_000_000
    second["event_date"] = "2025-01-02"
    second["decision_bucket_id"] = second["event_time_ns"].astype(str)
    second_path = root / "dataset2.csv"
    second.to_csv(second_path, index=False)
    merge_path = root / "merged.csv"
    merge_summary = root / "merged.json"
    result = merge_datasets(
        inputs=[output_path, second_path],
        output=merge_path,
        summary=merge_summary,
    )
    merged = pd.read_csv(merge_path)
    assert result["rows"] == 2
    assert merged["plan_id"].nunique() == 2
    assert merged["event_group_id"].nunique() == 2


def training_round_trip(root: Path) -> None:
    from ml2_model import CatBoostProbabilityModel
    from train_ml2 import train

    rng = np.random.default_rng(1234)
    start = pd.Timestamp("2025-01-01T00:00:00Z")
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    families = ("SWEEP_RECLAIM", "ACCEPTED_BREAK", "RANGE_ROTATION", "OTHER")
    rows: list[dict[str, object]] = []
    for index in range(420):
        event = start + pd.Timedelta(hours=index)
        signal = math_signal = np.sin(index / 9.0) + rng.normal(0.0, 0.35)
        label = int(math_signal > 0.0)
        row: dict[str, object] = {
            "plan_id": f"plan-{index}",
            "event_group_id": f"event-{index // 2}",
            "decision_bucket_id": str(int(event.value)),
            "symbol": symbols[index % len(symbols)],
            "family": f"family-{index % 6}",
            "side": "LONG" if index % 2 == 0 else "SHORT",
            "ml2_causal_family": families[index % len(families)],
            "event_time_ns": int(event.value),
            "label_end_ns": int((event + pd.Timedelta(minutes=15)).value),
            "event_date": event.strftime("%Y-%m-%d"),
            "counterfactual_minutes_to_resolution": 15.0,
            "label": label,
            "observed_outcome_net_r": 1.5 if label else -1.0,
            "ml2_win_net_r": 1.5,
            "ml2_loss_net_r": -1.0,
        }
        for name in FEATURE_NAMES:
            value = rng.normal(0.0, 0.1)
            if name == "tf1_side_return_z":
                value = signal
            elif name == "scenario_acceptance":
                value = float(index % 3 == 0)
            row[f"ml2f_{name}"] = float(value)
        rows.append(row)
    dataset = root / "train.csv"
    pd.DataFrame(rows).to_csv(dataset, index=False)
    model_path = root / "model.cbm"
    metadata_path = root / "model.json"
    report_path = root / "report.json"
    metadata, report = train(
        Namespace(
            dataset=dataset,
            model_output=model_path,
            metadata_output=metadata_path,
            report_output=report_path,
            train_fraction=0.60,
            calibration_fraction=0.20,
            embargo_minutes=0,
            minimum_samples=300,
            minimum_split_samples=60,
            iterations=24,
            depth=4,
            learning_rate=0.08,
            l2_leaf_reg=5.0,
            random_strength=0.2,
            subsample=0.9,
            random_seed=1729,
            risk_fraction=0.03,
        ),
    )
    runtime = CatBoostProbabilityModel(metadata_path)
    runtime.assert_selectable()
    assert runtime.model_id == metadata["model_id"]
    assert report["runtime_parity_rows"] > 0
    assert report["splits"]["test"]["calibrated_prediction"]["rows"] >= 60


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-training", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="easychart-ml2-selfcheck-") as directory:
        root = Path(directory)
        core_checks(root)
        if args.include_training:
            training_round_trip(root)
    print(
        f"ML2 selfcheck passed: features={len(FEATURE_NAMES)} "
        f"training_round_trip={args.include_training}",
    )


if __name__ == "__main__":
    main()
