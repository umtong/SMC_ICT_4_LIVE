#!/usr/bin/env python3
"""Deterministic, network-free checks for the restored policy and account router."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import reproduce

reproduce._activate_repo_paths()

import route_episode_policy as base
import route_episode_policy_causal as strict
from episode_policy_features import FEATURE_COLUMNS


def _ns(timestamp: pd.Timestamp) -> int:
    return int(timestamp.value)


def _row(
    *,
    sequence: int,
    period: str,
    role: str,
    order_time: pd.Timestamp,
    filled: bool,
    target: bool,
) -> dict[str, Any]:
    fill_time = order_time + pd.Timedelta(minutes=2) if filled else pd.NaT
    terminal_time = order_time + pd.Timedelta(minutes=30)
    resolution_time = order_time + pd.Timedelta(minutes=20) if filled else pd.NaT
    outcome = "TARGET_FIRST" if target else ("STOP_FIRST" if filled else "UNFILLED")
    net_r = 1.35 if target else (-1.0 if filled else np.nan)
    row: dict[str, Any] = {
        "action_id": f"A:{period}:{sequence:04d}",
        "state_id": f"S:{period}:{sequence:04d}",
        "episode_id": f"E:{period}:{sequence:04d}",
        "symbol": reproduce.SYMBOLS[sequence % len(reproduce.SYMBOLS)],
        "side": "LONG" if sequence % 2 == 0 else "SHORT",
        "family": (
            "FAILED_AUCTION_REVERSAL"
            if sequence % 3 == 0
            else "ACCEPTED_AUCTION_CONTINUATION"
        ),
        "period": period,
        "role": role,
        "order_time_ns": _ns(order_time),
        "fill_time_ns": _ns(fill_time) if filled else np.nan,
        "resolution_time_ns": _ns(resolution_time) if filled else np.nan,
        "order_terminal_time_ns": _ns(terminal_time),
        "outcome": outcome,
        "net_r": net_r,
        "gross_rr": 1.5,
        "planned_target_net_r": 1.35,
        "holding_minutes": 18.0 if filled else np.nan,
        "mechanism_coherence": 0.70 if target else -0.25,
        "control_composite": 0.65 if target else -0.20,
        "control_activity_ratio": 1.8 if filled else 0.4,
        "risk_bps": 22.0 + float(sequence % 7),
        "order_exists": True,
    }
    for index, column in enumerate(FEATURE_COLUMNS):
        if column not in row:
            signal = 1.0 if target else -1.0
            row[column] = signal * ((sequence + index) % 11) / 10.0
    return row


def synthetic_universe() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    dev_start = pd.Timestamp("2023-01-01T00:00:00Z")
    for sequence in range(180):
        filled = sequence % 4 != 0
        target = filled and sequence % 2 == 0
        rows.append(
            _row(
                sequence=sequence,
                period="dev-2023-synthetic",
                role="dev",
                order_time=dev_start + pd.Timedelta(hours=sequence),
                filled=filled,
                target=target,
            )
        )

    eval_start = pd.Timestamp("2024-01-01T00:00:00Z")
    for sequence in range(48):
        rows.append(
            _row(
                sequence=10_000 + sequence,
                period="eval-2024-synthetic",
                role="eval",
                order_time=eval_start + pd.Timedelta(minutes=5 * sequence),
                filled=True,
                target=sequence % 2 == 0,
            )
        )
    return pd.DataFrame(rows)


def run() -> dict[str, Any]:
    orders = synthetic_universe()
    scored, diagnostics = strict.strict_causal_predictions(orders)

    dev = scored[scored["role"].eq("dev")]
    evaluation = scored[scored["role"].eq("eval")].copy()
    if dev["causal_models_ready"].any():
        raise RuntimeError("first development period traded without earlier history")
    if evaluation.empty or not evaluation["causal_models_ready"].all():
        raise RuntimeError("evaluation period did not receive strict causal predictions")
    if evaluation["prediction_source"].str.contains(
        "fallback", case=False, na=False
    ).any():
        raise RuntimeError("heuristic fallback leaked into strict evaluation routing")

    eval_diag = diagnostics["eval-2024-synthetic"]
    test_start = pd.Timestamp(eval_diag["test_start"])
    latest_fill = pd.Timestamp(eval_diag["fill_label_latest_available_time"])
    latest_target = pd.Timestamp(eval_diag["target_label_latest_available_time"])
    if latest_fill >= test_start or latest_target >= test_start:
        raise RuntimeError("a label became available at or after evaluation start")

    # Exercise the account arbiter independently of model profitability. Every
    # synthetic evaluation plan is marked eligible so overlapping lifecycles
    # must be resolved by the one global slot.
    evaluation["policy_eligible"] = True
    evaluation["expected_log_growth"] = np.linspace(0.01, 0.02, len(evaluation))
    evaluation["probability_edge"] = 0.10
    selected_a, closed_a, rejected_a, account_a = base.route_account(evaluation)
    selected_b, _, _, account_b = base.route_account(evaluation.copy())

    ids_a = selected_a["action_id"].astype(str).tolist()
    ids_b = selected_b["action_id"].astype(str).tolist()
    if ids_a != ids_b or account_a != account_b:
        raise RuntimeError("account arbitration is not deterministic")
    if selected_a["episode_id"].astype(str).duplicated().any():
        raise RuntimeError("one episode occupied the account more than once")

    if len(selected_a) > 1:
        ordered = selected_a.sort_values("order_time")
        order_times = pd.to_datetime(
            ordered["order_time_ns"], unit="ns", utc=True
        ).tolist()
        terminal_times = pd.to_datetime(
            ordered["order_terminal_time_ns"], unit="ns", utc=True
        ).tolist()
        for position in range(1, len(ordered)):
            if order_times[position] < terminal_times[position - 1]:
                raise RuntimeError("global pending/position lifecycles overlapped")

    payload = {
        **reproduce._source_identity(),
        "synthetic_rows": int(len(orders)),
        "development_rows": int(len(dev)),
        "evaluation_rows": int(len(evaluation)),
        "evaluation_models_ready": bool(evaluation["causal_models_ready"].all()),
        "fallback_used_for_evaluation": False,
        "latest_fill_label_before_evaluation": str(latest_fill),
        "latest_target_label_before_evaluation": str(latest_target),
        "evaluation_start": str(test_start),
        "selected_orders": int(len(selected_a)),
        "closed_trades": int(len(closed_a)),
        "rejected_orders": int(len(rejected_a)),
        "one_global_slot_non_overlapping": True,
        "one_plan_per_episode": True,
        "deterministic_selected_action_ids_sha256": hashlib.sha256(
            "\n".join(ids_a).encode("utf-8")
        ).hexdigest(),
        "account": account_a,
        "model_diagnostics": diagnostics,
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = run()
    reproduce._write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
